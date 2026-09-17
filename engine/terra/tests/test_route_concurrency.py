"""route.json is written by SEVERAL leads concurrently.

Two real failures observed on CG-01:
  - a lead hit "raw JSON parse errors mid-batch" while a peer wrote (torn read)
  - load->mutate->save is a read-modify-write, so simultaneous edits silently
    discarded one another with no error at all
"""
from __future__ import annotations

import json

import pytest

from terra.brief import init_brief, set_brief_fields
from terra.route import (
    ConcurrentRouteWrite,
    add_task,
    init_route,
    load_route,
    route_path,
    save_route,
)


@pytest.fixture()
def proj(tmp_path):
    init_brief(tmp_path, title="t", mission="m")
    set_brief_fields(tmp_path, budget_points=1000)
    init_route(tmp_path)
    return tmp_path


def test_lost_update_is_refused_loudly(proj):
    """THE one that matters. Agent A loads, agent B writes, agent A saves —
    A's save would silently discard B's task. It must refuse instead."""
    add_task(proj, "base", title="B", bucket="low")

    rec_a = load_route(proj)                       # agent A loads
    add_task(proj, "from_b", title="from B", bucket="low")   # agent B writes

    rec_a["tasks"].append(dict(rec_a["tasks"][0], id="from_a", title="from A"))
    with pytest.raises(ConcurrentRouteWrite, match="changed on disk"):
        save_route(proj, rec_a)

    # B's work survived; A's was not silently applied
    ids = {t["id"] for t in load_route(proj)["tasks"]}
    assert "from_b" in ids
    assert "from_a" not in ids


def test_reload_then_retry_succeeds(proj):
    """The prescribed recovery must actually work."""
    add_task(proj, "base", title="B", bucket="low")
    rec_a = load_route(proj)
    add_task(proj, "from_b", title="from B", bucket="low")
    with pytest.raises(ConcurrentRouteWrite):
        save_route(proj, rec_a)

    fresh = load_route(proj)                       # re-read, re-apply
    fresh["tasks"].append(dict(fresh["tasks"][0], id="from_a", title="from A"))
    save_route(proj, fresh)
    ids = {t["id"] for t in load_route(proj)["tasks"]}
    assert {"base", "from_b", "from_a"} <= ids


def test_uncontended_save_still_works(proj):
    """CAN-FAIL: the guard must not fire on ordinary sequential use, or every
    normal write breaks."""
    add_task(proj, "a", title="A", bucket="low")
    rec = load_route(proj)
    rec["tasks"][0]["title"] = "edited"
    save_route(proj, rec)
    assert load_route(proj)["tasks"][0]["title"] == "edited"


def test_freshly_built_record_has_no_baseline_and_saves(proj):
    """Records not produced by load_route (init paths) carry no baseline and
    must not be blocked."""
    rec = load_route(proj)
    rec.pop("_loaded_sha256", None)
    save_route(proj, rec)


def test_baseline_key_never_reaches_disk(proj):
    add_task(proj, "a", title="A", bucket="low")
    raw = json.loads(route_path(proj).read_text())
    assert "_loaded_sha256" not in raw


def test_write_is_atomic_no_partial_file(proj, monkeypatch):
    """A reader must never see a truncated route.json. Simulate a crash
    mid-write: the original file must be intact, not empty or partial."""
    import os as _os

    add_task(proj, "a", title="A", bucket="low")
    before = route_path(proj).read_text()

    real_replace = _os.replace

    def boom(src, dst):
        raise OSError("simulated crash after write, before publish")

    monkeypatch.setattr(_os, "replace", boom)
    rec = load_route(proj)
    rec["tasks"][0]["title"] = "should not land"
    with pytest.raises(OSError):
        save_route(proj, rec)
    monkeypatch.setattr(_os, "replace", real_replace)

    # file is byte-identical and still parses — no torn state
    assert route_path(proj).read_text() == before
    assert load_route(proj)["tasks"][0]["title"] == "A"
    # and no temp turds left behind
    leftovers = list(route_path(proj).parent.glob(".route.*.tmp"))
    assert leftovers == [], leftovers
