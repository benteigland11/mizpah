"""Route hardening: evidence refs on complete, dep cycles, aging attention."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import graduate_unknown
from terra.probe_init import init_probe
from terra.probe_run import run_probe, void_run
from terra.route import (
    add_task,
    complete_task,
    init_route,
    load_route,
    route_attention,
    route_status,
    save_route,
    start_task,
)
from terra.unknowns import create_unknown, link_run


def _write_measure_probe(root: Path, probe_id: str, *, quantity="q", value=1.0):
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
        f"            'measures': [{{'quantity': {quantity!r}, 'value': {value!r}}}]}}\n",
        encoding="utf-8",
    )


def _run(tmp_path: Path, probe_id: str = "p") -> str:
    return run_probe(tmp_path, probe_id, to={"kind": "region"}).get("id")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p")
    init_route(tmp_path)
    return tmp_path


def test_claim_shaped_complete_requires_refs(proj):
    add_task(proj, "survey", title="Survey", skill="terra-map", bucket="low")
    start_task(proj, "survey")
    with pytest.raises(ValueError, match="needs map evidence"):
        complete_task(proj, "survey", evidence="looks good")
    rid = _run(proj)
    t = complete_task(proj, "survey", run_ids=[rid])
    assert t["status"] == "done"
    assert t["evidence"][0]["runs"] == [rid]


def test_freehand_recorded(proj):
    add_task(proj, "survey", title="S", skill="terra-probe", bucket="low")
    t = complete_task(proj, "survey", freehand="no instrument applies here")
    assert t["evidence"][0]["freehand"] == "no instrument applies here"


def test_voided_run_rejected_as_evidence(proj):
    add_task(proj, "survey", title="S", skill="terra-map", bucket="low")
    rid = _run(proj)
    void_run(proj, rid, reason="bad", cascade=False)
    with pytest.raises(ValueError, match="voided"):
        complete_task(proj, "survey", run_ids=[rid])


def test_known_ref_validated(proj):
    add_task(proj, "survey", title="S", skill="terra-map", bucket="low")
    with pytest.raises(ValueError, match="known not found"):
        complete_task(proj, "survey", known_ids=["ghost"])
    rid = _run(proj)
    create_unknown(
        proj, "u", claim="q?", evidence_needed="e",
        map_type="number", quantity="q",
    )
    link_run(proj, "u", rid)
    graduate_unknown(proj, "u", known_id="fact")
    t = complete_task(proj, "survey", known_ids=["fact"])
    assert t["evidence"][0]["knowns"] == ["fact"]


def test_non_claim_task_completes_on_prose(proj):
    add_task(proj, "wire", title="Wire glue", skill="any", bucket="low")
    t = complete_task(proj, "wire", evidence="wired and ran")
    assert t["status"] == "done"


def test_dep_cycle_rejected(proj):
    add_task(proj, "a", title="A", bucket="low")
    add_task(proj, "b", title="B", deps=["a"], bucket="low")
    rec = load_route(proj)
    for t in rec["tasks"]:
        if t["id"] == "a":
            t["deps"] = ["b"]
    with pytest.raises(ValueError, match="dependency cycle"):
        save_route(proj, rec)


def test_route_attention_blocked_and_stalled(proj):
    add_task(proj, "old", title="Old", bucket="low")
    start_task(proj, "old")
    rec = load_route(proj)
    for t in rec["tasks"]:
        if t["id"] == "old":
            t["updated_at"] = "2020-01-01T00:00:00Z"
    save_route(proj, rec)
    # save_route bumps route-level updated_at, not task-level
    add_task(proj, "stuck", title="Stuck", bucket="low")
    from terra.route import block_task

    block_task(proj, "stuck", reason="waiting on vendor")
    st = route_status(proj)
    kinds = {(a["kind"], a["id"]) for a in st["attention"]}
    assert ("task_stalled", "old") in kinds
    assert ("task_blocked", "stuck") in kinds


def test_route_log_chronological_with_evidence(proj):
    from terra.route import block_task, route_log

    add_task(proj, "a", title="First", skill="terra-map", bucket="low")
    add_task(proj, "b", title="Second", skill="any", bucket="low")
    add_task(proj, "c", title="Stuck", skill="any", bucket="low")
    start_task(proj, "a")
    rid = _run(proj)
    complete_task(proj, "a", evidence="measured q", run_ids=[rid])
    start_task(proj, "b")
    complete_task(proj, "b", evidence="wired up")
    block_task(proj, "c", reason="waiting on parts")

    out = route_log(proj)
    assert out["command"] == "route.log"
    assert out["counts"]["events"] == 3
    kinds = [(e["task"], e["kind"]) for e in out["events"]]
    assert ("a", "complete") in kinds
    assert ("b", "complete") in kinds
    assert ("c", "blocked") in kinds
    # chronological ascending
    ats = [e["at"] for e in out["events"]]
    assert ats == sorted(ats)
    ev_a = next(e for e in out["events"] if e["task"] == "a")
    assert ev_a["runs"] == [rid]
    assert ev_a["note"] == "measured q"
    blocked = next(e for e in out["events"] if e["task"] == "c")
    assert blocked["reason"] == "waiting on parts"

    limited = route_log(proj, limit=1)
    assert limited["counts"]["shown"] == 1
    assert limited["counts"]["events"] == 3


def test_route_log_done_without_evidence_still_logged(proj):
    add_task(proj, "bare", title="Bare", skill="any", bucket="low")
    start_task(proj, "bare")
    complete_task(proj, "bare")
    from terra.route import route_log

    out = route_log(proj)
    assert [e["task"] for e in out["events"]] == ["bare"]
    assert out["events"][0]["kind"] == "complete"
    assert "note" not in out["events"][0]
