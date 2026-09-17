"""Route priority: WHICH work, orthogonal to effort (bucket).

Every test here is mutation-checked — each asserts something that FAILS if
the corresponding line in route.py is reverted. See §24 in terra-dev.
"""
from __future__ import annotations

import json

import pytest

from terra.brief import init_brief, set_brief_fields
from terra.route import (
    DEFAULT_PRIORITY,
    PRIORITY_RANK,
    TASK_PRIORITIES,
    add_task,
    init_route,
    load_route,
    next_tasks,
    route_path,
    route_status,
    save_route,
    set_task_priority,
    validate_route,
)


@pytest.fixture()
def proj(tmp_path):
    init_brief(tmp_path, title="t", mission="m")
    set_brief_fields(tmp_path, budget_points=1000)
    init_route(tmp_path)
    return tmp_path


def test_default_is_p2_not_p0(proj):
    """A task added without --priority must land in the middle, never first."""
    t = add_task(proj, "a", title="A", bucket="low")
    assert t["priority"] == DEFAULT_PRIORITY == "p2"
    assert PRIORITY_RANK["p2"] > PRIORITY_RANK["p0"]


def test_next_sorts_p0_first_and_is_stable_within_rank(proj):
    add_task(proj, "back1", title="B1", bucket="low")
    add_task(proj, "spine", title="S", bucket="low", priority="p0")
    add_task(proj, "back2", title="B2", bucket="low")
    add_task(proj, "later", title="L", bucket="low", priority="p3")
    add_task(proj, "phase", title="P", bucket="low", priority="p1")
    ids = [t["id"] for t in next_tasks(proj, limit=10)]
    assert ids == ["spine", "phase", "back1", "back2", "later"]
    # within p2, insertion order preserved -> purely additive change
    assert ids.index("back1") < ids.index("back2")


def test_next_priority_filter(proj):
    add_task(proj, "a", title="A", bucket="low", priority="p0")
    add_task(proj, "b", title="B", bucket="low", priority="p3")
    assert [t["id"] for t in next_tasks(proj, limit=10, priority="p0")] == ["a"]
    assert [t["id"] for t in next_tasks(proj, limit=10, priority="p3")] == ["b"]


def test_legacy_task_backfills_to_default_not_urgent(proj):
    """THE can-fail that matters. A route.json written before priority
    existed must NOT come back as p0 — that would make the first sorted
    `next` a lie about what the program had decided to do."""
    add_task(proj, "legacy", title="L", bucket="low")
    raw = json.loads(route_path(proj).read_text())
    for t in raw["tasks"]:
        t.pop("priority", None)
    route_path(proj).write_text(json.dumps(raw))
    assert "priority" not in json.loads(route_path(proj).read_text())["tasks"][0]

    rec = load_route(proj)
    assert rec["tasks"][0]["priority"] == "p2"
    # and it must not jump the queue against a real p0
    add_task(proj, "spine", title="S", bucket="low", priority="p0")
    assert next_tasks(proj, limit=1)[0]["id"] == "spine"


def test_garbage_priority_is_refused_at_write_not_silently_repaired(proj):
    """save_route validates, so a bad priority cannot reach disk at all —
    stronger than repair-on-load, which would hide the caller's bug."""
    add_task(proj, "junk", title="J", bucket="low")
    rec = load_route(proj)
    rec["tasks"][0]["priority"] = "URGENT!!"
    with pytest.raises(ValueError, match="priority must be one of"):
        save_route(proj, rec)
    assert load_route(proj)["tasks"][0]["priority"] == "p2"


def test_hand_edited_garbage_still_sorts_as_default_not_first(proj):
    """Belt and braces: if garbage reaches route.json by hand-edit, it must
    sort as the default, never jump ahead of a real p0."""
    add_task(proj, "junk", title="J", bucket="low")
    add_task(proj, "spine", title="S", bucket="low", priority="p0")
    raw = json.loads(route_path(proj).read_text())
    raw["tasks"][0]["priority"] = "URGENT!!"
    route_path(proj).write_text(json.dumps(raw))
    assert load_route(proj)["tasks"][0]["priority"] == "p2"
    assert next_tasks(proj, limit=1)[0]["id"] == "spine"


def test_validate_rejects_bad_priority(proj):
    add_task(proj, "a", title="A", bucket="low")
    rec = load_route(proj)
    rec["tasks"][0]["priority"] = "p9"
    blocks = validate_route(rec)
    assert any("priority" in b for b in blocks)


def test_add_rejects_bad_priority(proj):
    with pytest.raises(ValueError, match="priority must be one of"):
        add_task(proj, "a", title="A", bucket="low", priority="urgent")


def test_prioritize_does_not_move_budget_or_effort(proj):
    """Priority is orthogonal to effort: re-ranking must not touch points,
    plan baseline, or the budget rollup."""
    add_task(proj, "a", title="A", bucket="high")
    before = route_status(proj)["budget"]
    t = set_task_priority(proj, ["a"], priority="p0")[0]
    after = route_status(proj)["budget"]
    assert t["priority"] == "p0"
    assert t["bucket"] == "high" and t["points"] == 21
    assert t["plan_bucket"] == "high" and t["plan_points"] == 21
    assert before == after


def test_prioritize_works_under_plan_lock(proj):
    """Deprioritizing a stale backlog must not require unlocking the plan —
    if it did, nobody would ever do it."""
    from terra.route import lock_plan

    add_task(proj, "a", title="A", bucket="low")
    lock_plan(proj)
    t = set_task_priority(proj, ["a"], priority="p3")[0]
    assert t["priority"] == "p3"
    assert load_route(proj)["plan_locked"] is True


def test_prioritize_bulk_and_unknown_id_is_atomic(proj):
    add_task(proj, "a", title="A", bucket="low")
    add_task(proj, "b", title="B", bucket="low")
    out = set_task_priority(proj, ["a", "b"], priority="p3")
    assert [t["priority"] for t in out] == ["p3", "p3"]
    with pytest.raises(ValueError, match="unknown task id"):
        set_task_priority(proj, ["a", "nope"], priority="p0")
    # 'a' must be untouched by the failed call
    assert load_route(proj)["tasks"][0]["priority"] == "p3"


def test_prioritize_reason_lands_in_route_log(proj):
    from terra.route import route_log

    add_task(proj, "a", title="A", bucket="low")
    set_task_priority(proj, ["a"], priority="p3", reason="not on the spine")
    events = route_log(proj, task_id="a")["events"]
    hits = [e for e in events if "not on the spine" in json.dumps(e)]
    assert hits, events
    # A re-rank must NOT be rendered as a completion — route_log used to
    # hardcode kind="complete" for every evidence row.
    assert hits[0]["kind"] == "priority"


def test_status_rollup_counts_every_open_task(proj):
    """Rollup is never truncated — what `next` hides must stay countable."""
    add_task(proj, "s", title="S", bucket="low", priority="p0")
    for i in range(7):
        add_task(proj, f"b{i}", title="B", bucket="low", priority="p3")
    counts = route_status(proj)["counts"]
    assert counts["by_priority_open"] == {"p0": 1, "p1": 0, "p2": 0, "p3": 7}
    assert sum(counts["by_priority_open"].values()) == 8
    # next(limit=5) shows 5 of 8 — the rollup is how you learn 3 are hidden
    assert len(next_tasks(proj, limit=5)) == 5
    assert set(counts["by_priority_open"]) == set(TASK_PRIORITIES)


# --- cancelled deps permanently strand dependents -----------------------
# Found on a live program: 22 of 118 open routes were transitively
# unreachable this way, including the only CFD route and the root of the
# whole flight-demo chain. Nothing alarmed — they read status=ready.


def test_cancelled_dep_strands_dependent_and_ALARMS(proj):
    from terra.route import cancel_task, route_attention

    add_task(proj, "basis", title="superseded approach", bucket="low")
    add_task(proj, "spine", title="THE SPINE", bucket="low",
             priority="p0", deps=["basis"])
    cancel_task(proj, "basis", reason="approach superseded")

    st = route_status(proj)
    spine = [t for t in st["tasks"] if t["id"] == "spine"][0]
    # still not pickable — we deliberately do NOT auto-unblock, because a
    # cancelled basis often means the dependent's premise died too
    assert spine["pickable"] is False
    assert spine["waiting_on_cancelled"] == ["basis"]
    # ...but the death must be VISIBLE. This is the whole fix.
    dead = [a for a in route_attention(st["tasks"])
            if a["kind"] == "task_dep_cancelled"]
    assert len(dead) == 1
    assert dead[0]["id"] == "spine"
    assert dead[0]["severity"] == "block"
    assert dead[0]["cancelled_deps"] == ["basis"]


def test_p0_ranking_cannot_rescue_a_dead_route(proj):
    """The point of the alarm: priority is not a fix for an unreachable
    task. `next` must still not surface it, so the attention item is the
    ONLY thing standing between a dead spine and silence."""
    from terra.route import cancel_task

    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "spine", title="S", bucket="low", priority="p0",
             deps=["basis"])
    cancel_task(proj, "basis", reason="dead")
    assert next_tasks(proj, limit=10, priority="p0") == []


def test_live_dep_does_not_false_alarm(proj):
    """Can-fail: an ordinary unfinished dep must NOT raise the alarm, or
    the signal is worthless."""
    from terra.route import route_attention

    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "spine", title="S", bucket="low", deps=["basis"])
    st = route_status(proj)
    spine = [t for t in st["tasks"] if t["id"] == "spine"][0]
    assert spine["waiting_on"] == ["basis"]
    assert "waiting_on_cancelled" not in spine
    assert not [a for a in route_attention(st["tasks"])
                if a["kind"] == "task_dep_cancelled"]


def test_alarm_clears_when_dep_is_repointed(proj):
    """The prescribed fix must actually clear the alarm."""
    from terra.route import cancel_task, complete_task, load_route, route_attention, save_route

    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "successor", title="live successor", bucket="low")
    add_task(proj, "spine", title="S", bucket="low", deps=["basis"])
    cancel_task(proj, "basis", reason="superseded by successor")
    complete_task(proj, "successor", evidence="done")
    rec = load_route(proj)
    for t in rec["tasks"]:
        if t["id"] == "spine":
            t["deps"] = ["successor"]
    save_route(proj, rec)
    st = route_status(proj)
    spine = [t for t in st["tasks"] if t["id"] == "spine"][0]
    assert spine["pickable"] is True
    assert not [a for a in route_attention(st["tasks"])
                if a["kind"] == "task_dep_cancelled"]


def test_dependents_of_finds_what_a_cancel_would_strand(proj):
    from terra.route import cancel_task, dependents_of

    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "d1", title="D1", bucket="low", deps=["basis"])
    add_task(proj, "d2", title="D2", bucket="low", deps=["basis"])
    add_task(proj, "unrelated", title="U", bucket="low")
    assert {t["id"] for t in dependents_of(proj, "basis")} == {"d1", "d2"}
    # already-cancelled dependents are not "stranded" by a new cancel
    cancel_task(proj, "d2", reason="dead")
    assert {t["id"] for t in dependents_of(proj, "basis")} == {"d1"}


def test_unreachability_is_transitive_to_closure(proj):
    """A task waiting on a stranded task is equally dead. Reporting only
    the direct hop understated a live program 9 vs 22."""
    from terra.route import cancel_task, route_attention

    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "root", title="root", bucket="low", deps=["basis"])
    add_task(proj, "mid", title="mid", bucket="low", deps=["root"])
    add_task(proj, "leaf", title="leaf", bucket="low", deps=["mid"])
    add_task(proj, "clean", title="clean", bucket="low")
    cancel_task(proj, "basis", reason="superseded")

    st = route_status(proj)
    by = {t["id"]: t for t in st["tasks"]}
    assert by["root"]["waiting_on_cancelled"] == ["basis"]
    assert "unreachable_via" not in by["root"]      # root names itself
    assert by["mid"]["unreachable_via"] == "root"
    assert by["leaf"]["unreachable_via"] == "root"  # points at the ROOT to fix
    assert "unreachable_via" not in by["clean"]     # can-fail
    assert by["clean"]["pickable"] is True

    att = route_attention(st["tasks"])
    roots = [a for a in att if a["kind"] == "task_dep_cancelled"]
    downstream = [a for a in att if a["kind"] == "task_unreachable"]
    assert [a["id"] for a in roots] == ["root"]
    assert {a["id"] for a in downstream} == {"mid", "leaf"}
    assert all(a["root"] == "root" for a in downstream)


def test_fixing_the_root_clears_the_whole_chain(proj):
    from terra.route import (
        cancel_task, complete_task, load_route, route_attention, save_route,
    )

    add_task(proj, "basis", title="B", bucket="low")
    add_task(proj, "successor", title="S", bucket="low")
    add_task(proj, "root", title="root", bucket="low", deps=["basis"])
    add_task(proj, "leaf", title="leaf", bucket="low", deps=["root"])
    cancel_task(proj, "basis", reason="superseded by successor")
    complete_task(proj, "successor", evidence="done")

    rec = load_route(proj)
    for t in rec["tasks"]:
        if t["id"] == "root":
            t["deps"] = ["successor"]
    save_route(proj, rec)

    st = route_status(proj)
    att = route_attention(st["tasks"])
    assert not [a for a in att
                if a["kind"] in ("task_dep_cancelled", "task_unreachable")]
    by = {t["id"]: t for t in st["tasks"]}
    assert by["root"]["pickable"] is True
    assert by["leaf"]["waiting_on"] == ["root"]   # ordinary waiting, no alarm
