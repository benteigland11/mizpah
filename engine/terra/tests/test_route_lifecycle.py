"""Route lifecycle: terminal-state integrity + phases as a real instrument.

Found by adversarially stress-walking a project lifecycle and asking one
question at every verb: can this SILENTLY do nothing, or report something
that isn't true? See §26 in terra-dev.
"""
from __future__ import annotations

import pytest

from terra.brief import add_phase, close_phase, init_brief, set_brief_fields
from terra.route import (
    add_task,
    block_task,
    cancel_task,
    complete_task,
    init_route,
    next_tasks,
    phase_rollup,
    route_attention,
    route_status,
    start_task,
    unblock_task,
)


@pytest.fixture()
def proj(tmp_path):
    init_brief(tmp_path, title="t", mission="m")
    set_brief_fields(tmp_path, budget_points=1000)
    init_route(tmp_path)
    return tmp_path


# --- terminal-state integrity -------------------------------------------
# `done` and `cancelled` each assert a settled outcome that later records
# hang off. The rule existed in exactly ONE place (cancel refusing on done)
# and was never applied systematically.


@pytest.mark.parametrize("verb", ["complete", "block", "unblock"])
def test_cancelled_task_cannot_be_resurrected(proj, verb):
    """The worst was cancelled->done: it launders a dead premise into a
    completion, and route_log then renders it as a genuine completion."""
    add_task(proj, "t", title="T", bucket="low")
    cancel_task(proj, "t", reason="dead premise")
    with pytest.raises(ValueError, match="terminal state"):
        if verb == "complete":
            complete_task(proj, "t", evidence="e")
        elif verb == "block":
            block_task(proj, "t", reason="r")
        else:
            unblock_task(proj, "t")
    assert route_status(proj)["tasks"][0]["status"] == "cancelled"


def test_cancelled_task_cannot_be_unblocked_back_into_the_queue(proj):
    """unblock on a cancelled task used to return it to status=ready —
    dead work silently back in the pickable queue."""
    add_task(proj, "t", title="T", bucket="low")
    cancel_task(proj, "t", reason="dead")
    with pytest.raises(ValueError):
        unblock_task(proj, "t")
    assert next_tasks(proj, limit=10) == []


@pytest.mark.parametrize("verb", ["block", "complete"])
def test_done_task_cannot_be_reopened(proj, verb):
    add_task(proj, "t", title="T", bucket="low")
    complete_task(proj, "t", evidence="did it")
    with pytest.raises(ValueError, match="terminal state"):
        if verb == "block":
            block_task(proj, "t", reason="r")
        else:
            complete_task(proj, "t", evidence="again")
    t = route_status(proj)["tasks"][0]
    assert t["status"] == "done"
    assert len(t["evidence"]) == 1   # no second completion record


def test_blocked_can_still_complete(proj):
    """CAN-FAIL / deliberate non-defect: blocking then completing is
    legitimate — the blocker is often resolved by the work itself. The
    guard must not over-reach into it."""
    add_task(proj, "t", title="T", bucket="low")
    block_task(proj, "t", reason="waiting on vendor")
    complete_task(proj, "t", evidence="vendor came through")
    assert route_status(proj)["tasks"][0]["status"] == "done"


def test_cancel_refuses_live_in_progress_work(proj):
    """start_task refuses to touch a task whose owner is alive (the
    double-writer interlock). cancel bypassed the same hazard entirely."""
    add_task(proj, "t", title="T", bucket="low")
    start_task(proj, "t", agent="worker-a")
    with pytest.raises(ValueError, match="heartbeat"):
        cancel_task(proj, "t", reason="changed my mind")
    assert route_status(proj)["tasks"][0]["status"] == "in_progress"
    # explicit override still available once the owner is verified dead
    cancel_task(proj, "t", reason="verified dead", force=True)
    assert route_status(proj)["tasks"][0]["status"] == "cancelled"


def test_done_before_deps_is_surfaced_not_refused(proj):
    """Deps are often soft ordering, so a hard refusal would push agents to
    freehand around the route. But the DAG and the record disagreeing must
    not be silent."""
    add_task(proj, "parent", title="P", bucket="low")
    add_task(proj, "child", title="C", bucket="low", deps=["parent"])
    complete_task(proj, "child", evidence="out of order")
    att = route_attention(route_status(proj)["tasks"])
    hits = [a for a in att if a["kind"] == "task_done_before_deps"]
    assert len(hits) == 1
    assert hits[0]["unmet_deps"] == ["parent"]

    # can-fail: in-order completion raises nothing
    complete_task(proj, "parent", evidence="done")
    att2 = route_attention(route_status(proj)["tasks"])
    assert not [a for a in att2 if a["kind"] == "task_done_before_deps"]


# --- phases as a lifecycle instrument -----------------------------------


def test_undeclared_phase_refused_when_phases_exist(proj):
    add_phase(proj, "design", title="Design")
    with pytest.raises(ValueError, match="not declared in the brief"):
        add_task(proj, "t", title="T", bucket="low", phase="typo_phase")
    add_task(proj, "ok", title="OK", bucket="low", phase="design")


def test_free_text_phase_still_allowed_when_none_declared(proj):
    """A project not using phases must not be broken into using them."""
    t = add_task(proj, "t", title="T", bucket="low", phase="whatever")
    assert t["phase"] == "whatever"


def test_phase_rollup_exit_readiness_and_current(proj):
    add_phase(proj, "design", title="Design")
    add_phase(proj, "build", title="Build")
    add_task(proj, "d1", title="D1", bucket="low", phase="design")
    add_task(proj, "d2", title="D2", bucket="low", phase="design")
    add_task(proj, "b1", title="B1", bucket="high", phase="build")

    roll = route_status(proj)["phases"]
    assert roll["current"] == "design"
    by = {r["id"]: r for r in roll["phases"]}
    assert by["design"]["exit_ready"] is False
    assert by["design"]["open"] == 2
    assert by["build"]["points_open"] == 21

    complete_task(proj, "d1", evidence="e")
    cancel_task(proj, "d2", reason="not needed")
    roll = route_status(proj)["phases"]
    by = {r["id"]: r for r in roll["phases"]}
    assert by["design"]["exit_ready"] is True   # done + cancelled both retire
    # exit_ready is a COMPUTED signal, not an authorization: the lifecycle
    # does NOT advance until a human declares the phase closed.
    assert roll["current"] == "design"
    close_phase(proj, "design", reason="all work retired, verified")
    assert route_status(proj)["phases"]["current"] == "build"


def test_empty_phase_is_not_exit_ready(proj):
    """A phase with no tasks has not been 'completed' — it has not been
    planned. Reporting it READY would let the lifecycle skip straight
    through unplanned work."""
    add_phase(proj, "design", title="D")
    roll = route_status(proj)["phases"]
    assert roll["phases"][0]["exit_ready"] is False
    assert roll["current"] == "design"


def test_unreachable_task_blocks_phase_exit_and_ALARMS(proj):
    """The interaction that matters: a phase full of routes stranded on
    cancelled deps otherwise reads as 'almost done, just waiting' forever."""
    add_phase(proj, "design", title="D")
    add_task(proj, "basis", title="B", bucket="low", phase="design")
    add_task(proj, "spine", title="S", bucket="low", phase="design",
             deps=["basis"])
    cancel_task(proj, "basis", reason="superseded")

    st = route_status(proj)
    by = {r["id"]: r for r in st["phases"]["phases"]}
    assert by["design"]["unreachable"] == 1
    assert by["design"]["exit_ready"] is False
    hits = [a for a in st["attention"]
            if a["kind"] == "phase_exit_blocked_by_unreachable"]
    assert len(hits) == 1 and hits[0]["id"] == "design"


def test_next_phase_filter(proj):
    add_phase(proj, "design", title="D")
    add_phase(proj, "build", title="B")
    add_task(proj, "d1", title="D1", bucket="low", phase="design")
    add_task(proj, "b1", title="B1", bucket="low", phase="build")
    assert [t["id"] for t in next_tasks(proj, limit=9, phase="design")] == ["d1"]
    assert [t["id"] for t in next_tasks(proj, limit=9, phase="build")] == ["b1"]


def test_unphased_open_tasks_are_declared_not_hidden(proj):
    """An unphased task blocks no phase exit and counts toward no phase —
    that is a real hole, so it must be stated, not discovered."""
    add_phase(proj, "design", title="D")
    add_task(proj, "orphan", title="O", bucket="low")
    st = route_status(proj)
    assert st["phases"]["unphased_open"] == 1
    assert [a for a in st["attention"] if a["kind"] == "tasks_unphased"]


def test_undeclared_phase_drift_is_surfaced(proj):
    """Legacy free-text phases predate declaration; they must never be
    silently treated as the current phase."""
    add_task(proj, "legacy", title="L", bucket="low", phase="old_freetext")
    add_phase(proj, "design", title="D")   # declared AFTER the task existed
    st = route_status(proj)
    assert st["phases"]["undeclared_used"] == ["old_freetext"]
    assert st["phases"]["current"] == "design"   # NOT the undeclared one
    assert [a for a in st["attention"] if a["kind"] == "task_phase_undeclared"]


def test_no_phase_attention_when_project_declares_none(proj):
    """Do not nag a project into using phases."""
    add_task(proj, "t", title="T", bucket="low")
    st = route_status(proj)
    assert not [a for a in st["attention"] if a["kind"].startswith("task_phase")]
    assert not [a for a in st["attention"] if a["kind"] == "tasks_unphased"]


def test_closure_is_declared_not_computed(proj):
    """A phase whose work predates tagging shows ZERO tasks and would pin
    `current` to an empty shell forever — that is the state CG-01 was in
    (p2..p5 held no tasks). Closure is the escape, and it is a decision."""
    add_phase(proj, "old", title="Old")
    add_phase(proj, "now", title="Now")
    assert route_status(proj)["phases"]["current"] == "old"   # jammed
    close_phase(proj, "old", reason="completed before phase tagging existed")
    roll = route_status(proj)["phases"]
    assert roll["current"] == "now"
    by = {r["id"]: r for r in roll["phases"]}
    assert by["old"]["closed"] is True
    assert by["old"]["exit_ready"] is False   # still honestly unplanned
    assert "before phase tagging" in by["old"]["closed_reason"]


def test_closure_requires_a_reason(proj):
    add_phase(proj, "p", title="P")
    with pytest.raises(ValueError, match="reason required"):
        close_phase(proj, "p", reason="")


def test_closing_unknown_phase_refused(proj):
    add_phase(proj, "p", title="P")
    with pytest.raises(ValueError, match="unknown phase"):
        close_phase(proj, "nope", reason="r")


def test_phase_closed_with_open_work_ALARMS(proj):
    """The one way closure can lie: it does NOT retire tasks."""
    add_phase(proj, "p", title="P")
    add_task(proj, "live", title="L", bucket="low", phase="p")
    close_phase(proj, "p", reason="declaring victory early")
    st = route_status(proj)
    hits = [a for a in st["attention"]
            if a["kind"] == "phase_closed_with_open_work"]
    assert len(hits) == 1
    assert hits[0]["severity"] == "block"
    assert hits[0]["open"] == 1
    # and the work is still genuinely pickable — closure changed nothing real
    assert [t["id"] for t in next_tasks(proj, limit=5)] == ["live"]


def test_reopen_clears_closure(proj):
    add_phase(proj, "p", title="P")
    add_phase(proj, "q", title="Q")
    close_phase(proj, "p", reason="done")
    assert route_status(proj)["phases"]["current"] == "q"
    close_phase(proj, "p", reason="was premature", reopen=True)
    roll = route_status(proj)["phases"]
    assert roll["current"] == "p"
    by = {r["id"]: r for r in roll["phases"]}
    assert by["p"]["closed"] is False and by["p"]["closed_reason"] is None


# --- derived fields must never be persisted ------------------------------


def test_derived_fields_are_not_written_to_disk(proj):
    """A lead repairing the DAG edited `waiting_on`, read the file back, saw
    its edit, and had it silently clobbered by the next terra command — its
    audit then still reported the unrepaired count. A computed field must not
    sit in the file inviting an edit."""
    import json as _json

    from terra.route import DERIVED_TASK_FIELDS, route_path

    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "spine", title="S", bucket="low", deps=["basis"])
    cancel_task(proj, "basis", reason="superseded")

    raw = _json.loads(route_path(proj).read_text())
    for t in raw["tasks"]:
        leaked = DERIVED_TASK_FIELDS & set(t)
        assert not leaked, f"{t['id']} persisted derived fields: {leaked}"

    # ...and they are still present after a load, computed fresh
    spine = {t["id"]: t for t in route_status(proj)["tasks"]}["spine"]
    assert spine["pickable"] is False
    assert spine["waiting_on"] == ["basis"]
    assert spine["waiting_on_cancelled"] == ["basis"]


def test_hand_edited_derived_field_cannot_fake_reachability(proj):
    """The exact trap: hand-clearing waiting_on must NOT make a dead route
    look alive."""
    import json as _json

    from terra.route import route_path

    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "spine", title="S", bucket="low", deps=["basis"])
    cancel_task(proj, "basis", reason="superseded")

    raw = _json.loads(route_path(proj).read_text())
    for t in raw["tasks"]:
        if t["id"] == "spine":
            t["waiting_on"] = []          # the edit that looked like it worked
            t["pickable"] = True
    route_path(proj).write_text(_json.dumps(raw))

    spine = {t["id"]: t for t in route_status(proj)["tasks"]}["spine"]
    assert spine["pickable"] is False              # truth wins
    assert spine["waiting_on_cancelled"] == ["basis"]
    assert next_tasks(proj, limit=5) == []


def test_start_refuses_task_with_unmet_deps(proj):
    """REGRESSION GUARD. When derived fields stopped being persisted, this
    interlock read None instead of False and silently stopped firing — a
    task with unmet deps could be claimed. Any change to how derived state
    is stored must keep this passing."""
    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "spine", title="S", bucket="low", deps=["basis"])
    with pytest.raises(ValueError, match="waiting"):
        start_task(proj, "spine", agent="a")
    complete_task(proj, "basis", evidence="e")
    start_task(proj, "spine", agent="a")   # now legitimately claimable
    assert route_status(proj)["tasks"][1]["status"] == "in_progress"
