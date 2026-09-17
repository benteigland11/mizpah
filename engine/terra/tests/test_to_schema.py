"""Recommended to-schema: warn-only on live runs."""

from __future__ import annotations

from pathlib import Path

from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.to_schema import warn_to_shape


def test_warn_missing_kind():
    w = warn_to_shape({"id": "x"}, live=True)
    assert any("kind" in x for x in w)


def test_no_warn_on_dry_or_good_kind():
    assert warn_to_shape({"kind": "region"}, live=False) == []
    assert not any("missing" in x for x in warn_to_shape({"kind": "server"}, live=True))


def test_custom_kind_soft_note():
    w = warn_to_shape({"kind": "farm_plot"}, live=True)
    assert any("project-specific" in x for x in w)


def test_live_run_attaches_to_warnings(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    stamp = run_probe(tmp_path, "p", to={"uuid": "no-kind-here"}, dry_run=False)
    assert any("kind" in w for w in stamp.get("warnings") or [])
