"""Brief (SSOT) + route (task DAG)."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.brief import (
    accept_proposal,
    brief_summary,
    init_brief,
    load_brief,
    propose_change,
    set_brief_fields,
)
from terra.paths import brief_path, route_path
from terra.route import (
    add_sector,
    add_task,
    complete_task,
    init_route,
    next_tasks,
    route_status,
    start_task,
)


def test_brief_init_and_propose_accept(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="YF-demo", mission="concept fighter study")
    assert brief_path(tmp_path).is_file()
    rec = load_brief(tmp_path)
    assert rec["version"] == 1
    assert rec["status"] == "active"
    s = brief_summary(rec)
    assert s["title"] == "YF-demo"

    prop = propose_change(
        tmp_path,
        summary="add stall gate",
        need="stall_kt formula holds",
    )
    assert prop["status"] == "open"
    rec2 = accept_proposal(tmp_path, prop["id"])
    assert rec2["version"] == 2
    assert "stall_kt formula holds" in rec2["needs"]


def test_route_deps_and_next(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="Demo", mission="m")
    init_route(tmp_path)
    assert route_path(tmp_path).is_file()

    add_task(
        tmp_path,
        "survey_aero",
        title="Survey aero toolchain",
        skill="terra-map",
        phase="concept",
    )
    add_task(
        tmp_path,
        "build_widget",
        title="Extract aero brick",
        skill="cg-plan",
        deps=["survey_aero"],
    )

    nxt = next_tasks(tmp_path)
    assert len(nxt) == 1
    assert nxt[0]["id"] == "survey_aero"

    with pytest.raises(ValueError, match="waiting"):
        start_task(tmp_path, "build_widget")

    start_task(tmp_path, "survey_aero")
    # claim-shaped (terra-map) task refuses prose-only completion
    with pytest.raises(ValueError, match="needs map evidence"):
        complete_task(tmp_path, "survey_aero", evidence="map status clean")
    complete_task(
        tmp_path,
        "survey_aero",
        evidence="map status clean",
        freehand="demo route, no survey ran",
    )
    nxt2 = next_tasks(tmp_path)
    assert any(t["id"] == "build_widget" for t in nxt2)

    st = route_status(tmp_path)
    assert st["counts"]["done"] == 1
    assert st["counts"]["pickable"] >= 1


def test_brief_set_appends_needs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="T", mission="m")
    set_brief_fields(tmp_path, needs=["a"])
    rec = set_brief_fields(tmp_path, needs=["b"])
    assert rec["needs"] == ["a", "b"]
    assert rec["version"] >= 3


def test_enablers_lifecycle(tmp_path: Path, monkeypatch):
    from terra.brief import set_enabler_status

    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="Jet", mission="study")
    rec = set_brief_fields(
        tmp_path,
        enablers=["print_harness:Multi-view orthographic harness:tools/make_prints.py"],
    )
    assert len(rec["enablers"]) == 1
    assert rec["enablers"][0]["id"] == "print_harness"
    assert rec["enablers"][0]["status"] == "needed"
    rec2 = set_enabler_status(
        tmp_path,
        "print_harness",
        "ready",
        path="tools/make_prints.py",
    )
    assert rec2["enablers"][0]["status"] == "ready"
    s = brief_summary(rec2)
    assert s["enablers_needed"] == []


def test_budget_points_and_task_buckets(tmp_path: Path, monkeypatch):
    from terra.route import lock_plan, set_task_effort, unlock_plan

    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="Budget demo", mission="m")
    rec = set_brief_fields(
        tmp_path,
        budget_points=100,
        budget_notes="~one week package",
    )
    assert rec["budget_points"] == 100
    assert rec["budget_notes"] == "~one week package"
    assert brief_summary(rec)["budget_points"] == 100

    init_route(tmp_path)
    add_task(
        tmp_path,
        "impl",
        title="Implement known wire-up",
        skill="any",
        bucket="low",
    )
    add_task(
        tmp_path,
        "prove",
        title="Validate with probes",
        skill="terra-probe",
        bucket="medium",
        deps=["impl"],
    )
    add_task(
        tmp_path,
        "explore",
        title="Novel architecture",
        skill="terra-map",
        points=21,  # high
    )

    t_low = next(t for t in route_status(tmp_path)["tasks"] if t["id"] == "impl")
    assert t_low["bucket"] == "low" and t_low["points"] == 3
    assert t_low["plan_bucket"] == "low" and t_low["plan_points"] == 3
    t_med = next(t for t in route_status(tmp_path)["tasks"] if t["id"] == "prove")
    assert t_med["bucket"] == "medium" and t_med["points"] == 8
    t_hi = next(t for t in route_status(tmp_path)["tasks"] if t["id"] == "explore")
    assert t_hi["bucket"] == "high" and t_hi["points"] == 21

    st = route_status(tmp_path)
    b = st["budget"]
    assert b["budget_points"] == 100
    assert b["points_plan"] == 32
    assert b["points_actual"] == 32
    assert b["over_budget"] is False
    assert b["plan_locked"] is False
    assert b["bucket_scale"] == {"low": 3, "medium": 8, "high": 21}

    start_task(tmp_path, "impl")
    complete_task(tmp_path, "impl", evidence="done")
    st2 = route_status(tmp_path)
    assert st2["budget"]["points_done"] == 3

    with pytest.raises(ValueError, match="points"):
        add_task(tmp_path, "bad", title="x", bucket="low", points=8)

    set_brief_fields(tmp_path, budget_points=32)
    with pytest.raises(ValueError, match="free pool|exceed"):
        add_task(tmp_path, "extra", title="no room", bucket="low")

    # lock plan, then rebucket working only — may exceed budget; plan stays 32
    lock_plan(tmp_path)
    assert route_status(tmp_path)["plan_locked"] is True
    with pytest.raises(ValueError, match="locked"):
        add_task(tmp_path, "nope", title="blocked by lock", bucket="low")

    set_task_effort(tmp_path, "prove", bucket="high")  # actual 3+21+21=45, plan 3+8+21=32
    st3 = route_status(tmp_path)
    assert st3["budget"]["over_budget"] is True
    assert st3["budget"]["over_plan"] is True
    assert st3["budget"]["points_plan"] == 32
    assert st3["budget"]["points_actual"] == 45
    prove = next(t for t in st3["tasks"] if t["id"] == "prove")
    assert prove["plan_points"] == 8 and prove["points"] == 21

    # unlock without confirm → warning
    with pytest.raises(ValueError, match="WARNING"):
        unlock_plan(tmp_path, confirm=False)
    unlock_plan(tmp_path, confirm=True)
    assert route_status(tmp_path)["plan_locked"] is False

    # unlocked set-effort rewrites plan too and enforces budget
    # budget still 32; raising impl low→high would make plan 21+8+21=50
    with pytest.raises(ValueError, match="free pool|exceed"):
        set_task_effort(tmp_path, "impl", bucket="high")
    # raise budget then replan prove back to medium
    set_brief_fields(tmp_path, budget_points=100)
    set_task_effort(tmp_path, "prove", bucket="medium")
    prove2 = next(
        t for t in route_status(tmp_path)["tasks"] if t["id"] == "prove"
    )
    assert prove2["plan_points"] == 8 and prove2["points"] == 8

    rec_clear = set_brief_fields(tmp_path, clear_budget_points=True)
    assert rec_clear["budget_points"] is None


def test_sectors_provision_and_explode(tmp_path: Path, monkeypatch):
    from terra.route import lock_plan

    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="Sectors", mission="m")
    set_brief_fields(tmp_path, budget_points=100, budget_notes="with CAD provision")
    init_route(tmp_path)

    add_sector(tmp_path, "cad", title="CAD package", reserved_points=40)
    add_sector(tmp_path, "thermal", title="Thermal", reserved_points=24)
    # free pool = 100 - 64 = 36
    add_task(tmp_path, "brief_lock", title="Lock", skill="any", bucket="low")  # 3 from free

    st = route_status(tmp_path)
    assert st["budget"]["sector_reserved_total"] == 64
    assert st["budget"]["free_pool"] == 36
    cad = next(s for s in st["budget"]["sectors"] if s["id"] == "cad")
    assert cad["reserved_points"] == 40 and cad["points_plan"] == 0

    # explode CAD while unlocked
    add_task(
        tmp_path,
        "cad_trays",
        title="Trays",
        skill="tooling",
        bucket="medium",
        sector_id="cad",
    )
    add_task(
        tmp_path,
        "cad_deploy",
        title="Deploy",
        skill="tooling",
        bucket="high",
        sector_id="cad",
    )  # 8+21=29 of 40

    st2 = route_status(tmp_path)
    cad2 = next(s for s in st2["budget"]["sectors"] if s["id"] == "cad")
    assert cad2["points_plan"] == 29
    assert cad2["remaining_reserve_plan"] == 11

    # cannot put medium (8) when only 11 left... wait 11 left, medium=8 OK
    # fill to 37 would exceed 40
    with pytest.raises(ValueError, match="sector 'cad'"):
        add_task(
            tmp_path,
            "cad_too_much",
            title="Overflow",
            skill="any",
            bucket="high",  # 21 > 11 remaining
            sector_id="cad",
        )

    lock_plan(tmp_path)
    # locked: unsectored add fails
    with pytest.raises(ValueError, match="locked"):
        add_task(tmp_path, "nope", title="x", bucket="low")
    # locked: explode remaining CAD reserve OK
    add_task(
        tmp_path,
        "cad_section",
        title="Section sheets",
        skill="deliverable",
        bucket="medium",
        sector_id="cad",
    )  # 8, remaining was 11
    st3 = route_status(tmp_path)
    cad3 = next(s for s in st3["budget"]["sectors"] if s["id"] == "cad")
    assert cad3["points_plan"] == 37
