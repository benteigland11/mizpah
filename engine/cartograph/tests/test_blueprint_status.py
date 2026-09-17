"""Tests for blueprint status: pin-health per dep + orphan leaves."""

import json
import os

import pytest

from cartograph.blueprint_status import (
    all_blueprint_status,
    blueprint_status,
    orphan_leaves,
)
from cartograph.engine import Cartograph


def _write_widget_install(project_dir, widget_id, version):
    py_dir = widget_id.replace("-", "_")
    wdir = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(wdir, "src"))
    manifest = {
        "meta": {"id": widget_id, "version": version, "domain": "backend"},
        "tech_stack": {"language": "python"},
    }
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump(manifest, f)
    return wdir


def _write_blueprint_install(project_dir, bp_id, deps, version="0.1.0"):
    py_dir = bp_id.replace("-", "_")
    bp = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(bp, "src"))
    manifest = {"id": bp_id, "language": "python", "version": version,
                "dependencies": deps}
    with open(os.path.join(bp, "blueprint.json"), "w") as f:
        json.dump(manifest, f)
    return bp


@pytest.fixture
def carto(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    return Cartograph(library_path=str(lib))


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


# --- per-blueprint status -------------------------------------------------


def test_status_reports_ok_when_pins_match(carto, project):
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    _write_blueprint_install(str(project), "bp-foo-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
    ])
    res = blueprint_status(carto, "bp-foo-python", str(project))
    assert res["pin_health"] == "ok"
    assert res["deps"][0]["state"] == "ok"
    assert res["deps"][0]["installed"] == "1.0.0"
    assert res["deps"][0]["pinned"] == "1.0.0"
    assert "suggestion" not in res


def test_status_flags_pin_mismatch(carto, project):
    _write_widget_install(str(project), "backend-x-python", "2.0.0")
    _write_blueprint_install(str(project), "bp-foo-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
    ])
    res = blueprint_status(carto, "bp-foo-python", str(project))
    assert res["pin_health"] == "broken"
    assert res["deps"][0]["state"] == "pin-mismatch"
    assert res["deps"][0]["installed"] == "2.0.0"
    assert res["deps"][0]["pinned"] == "1.0.0"
    assert "repin" in res["suggestion"]


def test_status_flags_missing_leaf(carto, project):
    _write_blueprint_install(str(project), "bp-foo-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
    ])
    res = blueprint_status(carto, "bp-foo-python", str(project))
    assert res["pin_health"] == "broken"
    assert res["deps"][0]["state"] == "missing"
    assert res["deps"][0]["installed"] is None
    assert "install" in res["suggestion"].lower()


def test_status_handles_multiple_deps_partially_broken(carto, project):
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    _write_widget_install(str(project), "backend-y-python", "9.9.9")
    _write_blueprint_install(str(project), "bp-foo-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
        {"id": "backend-y-python", "version": "1.0.0"},
        {"id": "backend-z-python", "version": "1.0.0"},
    ])
    res = blueprint_status(carto, "bp-foo-python", str(project))
    assert res["pin_health"] == "broken"
    states = {d["id"]: d["state"] for d in res["deps"]}
    assert states["backend-x-python"] == "ok"
    assert states["backend-y-python"] == "pin-mismatch"
    assert states["backend-z-python"] == "missing"


def test_status_returns_error_when_blueprint_not_installed(carto, project):
    res = blueprint_status(carto, "bp-foo-python", str(project))
    assert "error" in res
    assert "not found" in res["error"].lower()


# --- orphan leaves --------------------------------------------------------


def test_no_blueprints_means_no_orphans(project):
    """Leaves are only orphaned vs blueprints. Without any installed
    blueprint, no widget is considered orphaned by this check."""
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    assert orphan_leaves(str(project)) == []


def test_orphan_leaves_finds_unreferenced_widget(project):
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    _write_widget_install(str(project), "backend-y-python", "1.0.0")
    _write_blueprint_install(str(project), "bp-foo-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
    ])
    orphans = orphan_leaves(str(project))
    assert orphans == ["backend-y-python"]


def test_widget_referenced_by_any_blueprint_not_orphaned(project):
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    _write_blueprint_install(str(project), "bp-foo-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
    ])
    _write_blueprint_install(str(project), "bp-bar-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
    ])
    assert orphan_leaves(str(project)) == []


def test_orphan_leaves_handles_missing_cg(project):
    assert orphan_leaves(str(project)) == []


# --- aggregate ------------------------------------------------------------


def test_all_blueprint_status_returns_each_blueprint_and_orphans(carto, project):
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    _write_widget_install(str(project), "backend-y-python", "1.0.0")
    _write_widget_install(str(project), "backend-orphan-python", "1.0.0")
    _write_blueprint_install(str(project), "bp-foo-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
    ])
    _write_blueprint_install(str(project), "bp-bar-python", deps=[
        {"id": "backend-y-python", "version": "1.0.0"},
    ])
    res = all_blueprint_status(carto, str(project))
    bp_ids = {b["blueprint_id"] for b in res["blueprints"]}
    assert bp_ids == {"bp-foo-python", "bp-bar-python"}
    assert all(b["pin_health"] == "ok" for b in res["blueprints"])
    assert res["orphans"] == ["backend-orphan-python"]


def test_all_blueprint_status_empty_project(carto, project):
    res = all_blueprint_status(carto, str(project))
    assert res == {"blueprints": [], "orphans": []}


# --- engine dispatch ------------------------------------------------------


def test_widget_status_dispatches_blueprint(carto, project):
    """carto.widget_status should route blueprint IDs to blueprint_status."""
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    _write_blueprint_install(str(project), "bp-foo-python", deps=[
        {"id": "backend-x-python", "version": "1.0.0"},
    ])
    res = carto.widget_status("bp-foo-python", str(project))
    assert res.get("kind") == "blueprint"
    assert res["pin_health"] == "ok"
