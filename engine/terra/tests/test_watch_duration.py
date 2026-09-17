"""Watch duration: probe owns window; substrate injects deadline."""

from __future__ import annotations

from pathlib import Path

from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.watch_ctx import build_watch_ctx, effective_run_timeout


def test_build_watch_ctx_snapshot():
    ctx = build_watch_ctx({"kind": "watch", "duration_s": 0}, dry_run=False)
    assert ctx["watch_mode"] == "snapshot"
    assert ctx["duration_s"] == 0.0
    assert "deadline" not in ctx


def test_build_watch_ctx_window():
    ctx = build_watch_ctx({"kind": "watch", "duration_s": 5}, dry_run=False)
    assert ctx["watch_mode"] == "window"
    assert ctx["duration_s"] == 5.0
    assert "deadline" in ctx
    assert "deadline_unix" in ctx


def test_build_watch_ctx_dry_run_no_deadline():
    ctx = build_watch_ctx({"kind": "watch", "duration_s": 30}, dry_run=True)
    assert ctx["watch_mode"] == "window"
    assert "deadline" not in ctx  # must not wait under dry_run


def test_timeout_extends_for_window():
    t = effective_run_timeout({"kind": "watch", "duration_s": 60}, 10.0)
    assert t >= 65.0


def test_short_window_warns(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "listen", purpose="w", kind="watch", duration_s=5.0)
    # scaffold returns immediately → soft warn about ignored window
    stamp = run_probe(tmp_path, "listen", to={"kind": "server"}, dry_run=False)
    warns = stamp.get("warnings") or []
    assert any("watch window" in w or "deadline" in w for w in warns)
