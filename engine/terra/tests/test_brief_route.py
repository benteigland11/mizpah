"""Brief (SSOT) + route (task DAG)."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.brief import (
    accept_proposal,
    brief_summary,
    init_brief,
    load_brief,
    save_brief,
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


def test_issuing_locks_the_crew_on_the_brief(tmp_path: Path, monkeypatch):
    from terra.brief import set_brief_fields
    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="Piece", mission="m")
    set_brief_fields(tmp_path, status="draft")
    crew = {"worker": {"provider": "openai_chatgpt", "model": "gpt-5.6-luna", "effort": "high"},
            "controller": {"provider": "xai_grok", "model": "grok-4.6", "effort": None}}
    rec = set_brief_fields(tmp_path, status="active", signed_by="Ben, Administrator", crew=crew)
    assert rec["issued_crew"] == crew and rec["issued_by"] == "Ben, Administrator"
    # Not rewritten by a later set: it is what was signed.
    rec = set_brief_fields(tmp_path, status="active", crew={"worker": {"model": "other"}})
    assert rec["issued_crew"] == crew


def test_the_same_ask_is_decided_once(tmp_path: Path, monkeypatch):
    """Four budget asks at the same number are one change request: accepting one folds the rest; a reworded
    need is the same ask; a different number or a different key is not."""
    from terra.brief import reject_proposal, same_ask
    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="Piece", mission="m")
    set_brief_fields(tmp_path, budget_points=66)
    a = propose_change(tmp_path, summary="six more points: readings", budget_delta=6)
    b = propose_change(tmp_path, summary="six more points: the live checks", budget_delta=6)
    c = propose_change(tmp_path, summary="nine more", budget_delta=9)
    assert b["same_as"] == a["id"] and "same_as" not in a and "same_as" not in c
    rec = accept_proposal(tmp_path, b["id"], reason="fine", signed_by="Ben, Administrator")
    by_id = {p["id"]: p for p in rec["proposals"]}
    assert by_id[a["id"]]["status"] == "accepted" and by_id[a["id"]]["decided_with"] == b["id"]
    assert by_id[a["id"]]["decision_reason"] == "decided with " + b["id"] + ": fine"
    assert by_id[a["id"]]["signed_by"] == "Ben, Administrator"
    assert by_id[c["id"]]["status"] == "open"
    assert rec["budget_points"] == 72 and rec["version"] == 3   # applied once
    n1 = propose_change(tmp_path, summary="x", need="The two outstanding phrase-structure readings run against the current artifacts.")
    n2 = propose_change(tmp_path, summary="y", need="The two outstanding phrase-structure readings must run against the current artifacts")
    n3 = propose_change(tmp_path, summary="z", need="A tempo map with rubato.")
    assert same_ask(n1, n2) and not same_ask(n1, n3)
    rec = reject_proposal(tmp_path, n1["id"], reason="no")
    by_id = {p["id"]: p for p in rec["proposals"]}
    assert by_id[n2["id"]]["status"] == "rejected" and by_id[n3["id"]]["status"] == "open"
    assert by_id[c["id"]]["status"] == "open"


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


def test_proposal_edits_and_removes_entries_keeping_numbering_honest(tmp_path: Path, monkeypatch):
    """A proposal can rewrite an entry in place or remove one; removal renumbers the phases that own
    later entries and the map unknowns that cite them, and orphans a cite of the removed entry."""
    from terra.brief import add_phase
    from terra.paths import terra_root
    from terra.unknowns import create_unknown
    import json

    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="copy", mission="write it")
    set_brief_fields(tmp_path, needs=["word count", "documents its own readings", "banned words"])
    add_phase(tmp_path, "p1", needs=[1, 2, 3])
    create_unknown(tmp_path, "words", claim="word count", notes="cites need:1")
    create_unknown(tmp_path, "self_doc", claim="self documented", notes="cites need:2")
    create_unknown(tmp_path, "banned", claim="banned words", notes="cites need:3 | need:2")

    edit = propose_change(tmp_path, summary="sharpen 3", edit_need="3: banned words or marks")
    assert edit["patch"]["edit_need"] == {"index": 3, "text": "banned words or marks"}
    rec = accept_proposal(tmp_path, edit["id"])
    assert rec["needs"][2] == "banned words or marks"

    with pytest.raises(ValueError):
        propose_change(tmp_path, summary="no such", remove_need=9)
    remove = propose_change(tmp_path, summary="need 2 asks the file about itself", remove_need=2)
    rec = accept_proposal(tmp_path, remove["id"])
    assert rec["needs"] == ["word count", "banned words or marks"]
    assert rec["phases"][0]["needs"] == [1, 2]
    unknowns = terra_root(tmp_path) / "map" / "unknowns"
    notes = {p.stem: json.loads(p.read_text())["notes"] for p in unknowns.glob("*.json")}
    assert notes["words"] == "cites need:1"
    assert notes["self_doc"] == "cites need:removed"
    assert notes["banned"] == "cites need:2 | need:removed"


def test_replacing_needs_with_fewer_renumbers_like_a_removal(tmp_path: Path, monkeypatch):
    """The app's editor saves the whole list; dropping one entry renumbers phases and cites the same way
    an accepted remove proposal does, and a save that rewrites text renumbers nothing."""
    from terra.brief import add_phase
    from terra.paths import terra_root
    from terra.unknowns import create_unknown
    import json

    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="t", mission="m")
    set_brief_fields(tmp_path, needs=["a", "b", "c"])
    add_phase(tmp_path, "p1", needs=[1, 2, 3])
    create_unknown(tmp_path, "uc", claim="c", notes="cites need:3")
    set_brief_fields(tmp_path, needs=["a", "c"], replace_lists=True)
    rec = load_brief(tmp_path)
    assert rec["needs"] == ["a", "c"] and rec["phases"][0]["needs"] == [1, 2]
    notes = json.loads((terra_root(tmp_path) / "map" / "unknowns" / "uc.json").read_text())["notes"]
    assert notes == "cites need:2"
    set_brief_fields(tmp_path, needs=["a", "c sharper"], replace_lists=True)
    assert load_brief(tmp_path)["phases"][0]["needs"] == [1, 2]


def test_brief_names_its_environment(tmp_path: Path) -> None:
    """A brief names the gym environment it runs in; the host resolves the name. Empty clears it."""
    from terra.brief import init_brief, load_brief, set_brief_fields, brief_summary
    init_brief(tmp_path, title="Counting a bar", mission="count")
    assert load_brief(tmp_path)["environment"] == ""
    set_brief_fields(tmp_path, environment="piano")
    assert load_brief(tmp_path)["environment"] == "piano"
    assert brief_summary(load_brief(tmp_path))["environment"] == "piano"
    set_brief_fields(tmp_path, environment="")
    assert load_brief(tmp_path)["environment"] == ""


def test_a_budget_change_is_a_delta_added_at_accept(tmp_path: Path, monkeypatch):
    """No number is ever written over the budget: a proposal carries ±N, added to the budget as it stands when
    accepted — two accepted asks add up, and an ask does not reset what another one set."""
    monkeypatch.chdir(tmp_path)
    init_brief(tmp_path, title="Piece", mission="m")
    with pytest.raises(ValueError, match="no budget"):
        propose_change(tmp_path, summary="more", budget_delta=6)
    set_brief_fields(tmp_path, budget_points=120)
    with pytest.raises(ValueError, match="non-zero"):
        propose_change(tmp_path, summary="none", budget_delta=0)
    a = propose_change(tmp_path, summary="six more", budget_delta=6)
    assert a["patch"] == {"budget_delta": 6, "was_budget_points": 120}
    b = propose_change(tmp_path, summary="ten fewer", budget_delta=-10)
    rec = accept_proposal(tmp_path, b["id"], reason="ok")
    assert rec["budget_points"] == 110
    rec = accept_proposal(tmp_path, a["id"], reason="ok")   # added to 110, not to the 120 it was asked against
    assert rec["budget_points"] == 116
    by_id = {p["id"]: p for p in rec["proposals"]}
    assert by_id[a["id"]]["patch"]["budget_before"] == 110 and by_id[a["id"]]["patch"]["was_budget_points"] == 120
    # A pre-delta proposal on an old brief still applies as it was written.
    old = propose_change(tmp_path, summary="legacy", need="n")
    rec = load_brief(tmp_path)
    for p in rec["proposals"]:
        if p["id"] == old["id"]:
            p["patch"] = {"budget_points": 90, "was_budget_points": 116}
    save_brief(tmp_path, rec)
    assert accept_proposal(tmp_path, old["id"], reason="ok")["budget_points"] == 90
