"""Route liveness: owner + heartbeat distinguish 'working' from 'died'.

status is not a liveness signal. An in_progress task with a fresh heartbeat
is alive; one whose heartbeat has gone quiet is a possible stranded/dead
lead and must be surfaced distinctly (the double-writer-corruption scar).
"""

from __future__ import annotations

import pytest

from terra.route import (
    HEARTBEAT_STALE_HOURS,
    add_task,
    block_task,
    complete_task,
    heartbeat_task,
    init_route,
    load_route,
    route_attention,
    route_status,
    save_route,
    start_task,
)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_route(tmp_path)
    return tmp_path


def _set_task(root, task_id, **fields):
    rec = load_route(root)
    for t in rec["tasks"]:
        if t["id"] == task_id:
            t.update(fields)
    save_route(root, rec)


def _hours_ago(hours: float) -> str:
    from datetime import datetime, timedelta, timezone

    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_start_claims_owner_and_opens_heartbeat(proj):
    add_task(proj, "a", title="A", bucket="low")
    t = start_task(proj, "a", agent="agent-7")
    assert t["status"] == "in_progress"
    assert t["owner_agent"] == "agent-7"
    assert t["started_at"] is not None
    assert t["last_heartbeat_at"] is not None


def test_start_without_agent_is_anonymous(proj):
    add_task(proj, "a", title="A", bucket="low")
    t = start_task(proj, "a")
    assert t["owner_agent"] is None
    assert t["last_heartbeat_at"] is not None  # still opens a heartbeat


def test_heartbeat_refreshes_stamp(proj):
    add_task(proj, "a", title="A", bucket="low")
    start_task(proj, "a", agent="agent-7")
    _set_task(proj, "a", last_heartbeat_at=_hours_ago(HEARTBEAT_STALE_HOURS + 2))
    t = heartbeat_task(proj, "a")
    # fresh again -> no stale attention
    assert not [
        it for it in route_attention([t]) if it["kind"] == "task_no_heartbeat"
    ]


def test_heartbeat_can_reassert_owner(proj):
    add_task(proj, "a", title="A", bucket="low")
    start_task(proj, "a")  # anonymous
    t = heartbeat_task(proj, "a", agent="agent-9")
    assert t["owner_agent"] == "agent-9"


def test_heartbeat_refused_when_not_in_progress(proj):
    add_task(proj, "a", title="A", bucket="low")
    with pytest.raises(ValueError, match="not in_progress"):
        heartbeat_task(proj, "a")


def test_stale_heartbeat_surfaces_as_no_heartbeat(proj):
    add_task(proj, "a", title="A", bucket="low")
    start_task(proj, "a", agent="agent-7")
    _set_task(proj, "a", last_heartbeat_at=_hours_ago(HEARTBEAT_STALE_HOURS + 1))
    st = route_status(proj)
    hits = [a for a in st["attention"] if a["kind"] == "task_no_heartbeat"]
    assert len(hits) == 1
    assert hits[0]["id"] == "a"
    assert hits[0]["owner"] == "agent-7"
    assert hits[0]["severity"] == "high"


def test_fresh_heartbeat_is_not_flagged(proj):
    add_task(proj, "a", title="A", bucket="low")
    start_task(proj, "a", agent="agent-7")
    st = route_status(proj)
    assert not [a for a in st["attention"] if a["kind"] == "task_no_heartbeat"]


def test_complete_clears_liveness(proj):
    add_task(proj, "a", title="A", bucket="low")
    start_task(proj, "a", agent="agent-7")
    t = complete_task(proj, "a", evidence="done")
    assert t["owner_agent"] is None
    assert t["last_heartbeat_at"] is None
    assert t["started_at"] is not None  # history preserved


def test_block_clears_liveness(proj):
    add_task(proj, "a", title="A", bucket="low")
    start_task(proj, "a", agent="agent-7")
    t = block_task(proj, "a", reason="waiting")
    assert t["owner_agent"] is None
    assert t["last_heartbeat_at"] is None


def test_legacy_task_without_heartbeat_not_flagged_for_heartbeat(proj):
    # A pre-liveness task: in_progress but no last_heartbeat_at at all.
    add_task(proj, "a", title="A", bucket="low")
    start_task(proj, "a")
    _set_task(proj, "a", last_heartbeat_at=None, updated_at=_hours_ago(1))
    st = route_status(proj)
    # no heartbeat stamp -> falls back to day-scale stall, not flagged here
    assert not [a for a in st["attention"] if a["kind"] == "task_no_heartbeat"]


def test_legacy_task_still_stalls_by_updated_at(proj):
    add_task(proj, "a", title="A", bucket="low")
    start_task(proj, "a")
    _set_task(proj, "a", last_heartbeat_at=None, updated_at="2020-01-01T00:00:00Z")
    st = route_status(proj)
    kinds = {a["kind"] for a in st["attention"] if a["id"] == "a"}
    assert "task_stalled" in kinds
    assert "task_no_heartbeat" not in kinds
