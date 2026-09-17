"""Tests for blueprint install (cascade leaf deps + override warnings).

Cheap tests cover resolution, leaf-only enforcement, and plan generation.
Slow tests run an end-to-end checkin -> install round trip with a real
sandbox to prove the cascade actually plants widgets in the consumer cg/.
"""

import json
import os

import pytest

from cartograph.blueprint_install import (
    _plan_cascade,
    _read_widget_version,
    _resolve_blueprint,
    _scan_blueprint_pins,
    install_blueprint,
)
from cartograph.engine import Cartograph


# --- fixtures -------------------------------------------------------------


def _write_widget_install(project_dir, widget_id, version):
    """Plant an already-installed leaf widget into project_dir/cg/<py_dir>/."""
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


def _write_blueprint_install(project_dir, bp_id, deps):
    """Plant an already-installed blueprint into project_dir/cg/<py_dir>/."""
    py_dir = bp_id.replace("-", "_")
    bp = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(bp, "src"))
    manifest = {"id": bp_id, "language": "python", "version": "0.1.0",
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


# --- resolution -----------------------------------------------------------


def test_rejects_unknown_blueprint(carto, project):
    res = install_blueprint(carto, "bp-unknown-python", str(project))
    assert "error" in res
    assert "not found" in res["error"].lower()


def test_rejects_cloud_prefixed_blueprint(carto, project):
    res = install_blueprint(carto, "cg-bp-foo-python", str(project))
    assert "error" in res
    assert "phase 3" in res["error"].lower() or "cloud" in res["error"].lower()


def test_rejects_relative_target(carto):
    res = install_blueprint(carto, "bp-foo-python", "relative/path")
    assert "error" in res
    assert "absolute" in res["error"].lower()


def test_rejects_install_into_library(carto):
    res = install_blueprint(carto, "bp-foo-python", os.path.abspath(carto.library_path))
    assert "error" in res
    assert "library" in res["error"].lower()


# --- planning -------------------------------------------------------------


def test_plan_fresh_install(project):
    deps = [{"id": "backend-x-python", "version": "1.0.0"},
            {"id": "backend-y-python", "version": "2.0.0"}]
    plan = _plan_cascade(str(project), deps)
    assert all(s["action"] == "install" for s in plan["steps"])
    assert plan["warnings"] == []


def test_plan_skip_when_already_at_pin(project):
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    deps = [{"id": "backend-x-python", "version": "1.0.0"}]
    plan = _plan_cascade(str(project), deps)
    assert plan["steps"][0]["action"] == "skip"
    assert plan["warnings"] == []


def test_plan_override_when_pin_differs(project):
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    deps = [{"id": "backend-x-python", "version": "2.0.0"}]
    plan = _plan_cascade(str(project), deps)
    assert plan["steps"][0]["action"] == "override"
    assert plan["steps"][0]["existing_version"] == "1.0.0"
    assert len(plan["warnings"]) == 1
    msg = plan["warnings"][0]
    assert "2.0.0" in msg and "1.0.0" in msg


def test_plan_override_credits_prior_pinning_blueprint(project):
    """If the existing version was pinned by another blueprint, the warning
    names that blueprint and points the user at `repin`."""
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    _write_blueprint_install(
        str(project), "bp-old-python",
        deps=[{"id": "backend-x-python", "version": "1.0.0"}],
    )
    deps = [{"id": "backend-x-python", "version": "2.0.0"}]
    plan = _plan_cascade(str(project), deps)
    msg = plan["warnings"][0]
    assert "bp-old-python" in msg
    assert "repin" in msg


def test_plan_finds_widget_installed_under_prefixed_dir(project):
    """A widget pulled from a prefixed registry lives at cg/<prefix>_<name>/
    but blueprints pin the canonical id. The planner must recognize the
    prefixed install as satisfying the canonical pin instead of treating
    it as missing (which would cause a duplicate canonical install)."""
    cg = os.path.join(str(project), "cg")
    prefixed_dir = os.path.join(cg, "cg_backend_x_python")  # cg- prefix install
    os.makedirs(os.path.join(prefixed_dir, "src"))
    manifest = {
        "meta": {"id": "backend-x-python", "version": "1.0.0", "domain": "backend"},
        "tech_stack": {"language": "python"},
    }
    with open(os.path.join(prefixed_dir, "widget.json"), "w") as f:
        json.dump(manifest, f)

    deps = [{"id": "backend-x-python", "version": "1.0.0"}]
    plan = _plan_cascade(str(project), deps)
    assert plan["steps"][0]["action"] == "skip"
    assert plan["steps"][0]["existing_version"] == "1.0.0"


def test_plan_override_with_no_prior_pinner(project):
    """User installed a leaf manually, no blueprint owns it. Still warn,
    but message reflects there's no blueprint to repin."""
    _write_widget_install(str(project), "backend-x-python", "1.0.0")
    deps = [{"id": "backend-x-python", "version": "2.0.0"}]
    plan = _plan_cascade(str(project), deps)
    msg = plan["warnings"][0]
    assert "direct override" in msg.lower()


# --- helpers --------------------------------------------------------------


def test_scan_blueprint_pins_returns_tuples(project):
    _write_blueprint_install(
        str(project), "bp-a-python",
        deps=[{"id": "backend-x-python", "version": "1.0.0"},
              {"id": "backend-y-python", "version": "2.0.0"}],
    )
    pins = _scan_blueprint_pins(os.path.join(str(project), "cg"))
    assert ("bp-a-python", "backend-x-python", "1.0.0") in pins
    assert ("bp-a-python", "backend-y-python", "2.0.0") in pins


def test_scan_blueprint_pins_empty_when_no_cg(project):
    pins = _scan_blueprint_pins(os.path.join(str(project), "cg"))
    assert pins == []


def test_read_widget_version_returns_none_for_missing(project):
    assert _read_widget_version(os.path.join(str(project), "missing")) is None


def test_read_widget_version_handles_legacy_layout(project):
    """widget.json with top-level keys instead of meta.* should still be read."""
    wdir = os.path.join(str(project), "cg", "legacy")
    os.makedirs(wdir)
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump({"id": "x", "version": "9.9.9"}, f)
    assert _read_widget_version(wdir) == "9.9.9"


# --- end-to-end (slow) ----------------------------------------------------


def _write_widget_for_checkin(project_dir, widget_id, version,
                              src=None, tests=None, example=None):
    """Plant a fully-formed widget that can be checked into the library."""
    py_dir = widget_id.replace("-", "_")
    name = widget_id.split("-", 1)[1].rsplit("-", 1)[0]
    domain = widget_id.split("-")[0]
    wdir = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(wdir, "src"))
    os.makedirs(os.path.join(wdir, "tests"))
    os.makedirs(os.path.join(wdir, "examples"))
    manifest = {
        "meta": {
            "id": widget_id, "name": name, "version": version,
            "domain": domain, "tags": ["a", "b", "c"],
        },
        "tech_stack": {"language": "python", "dependencies": []},
        "description": "fixture widget for blueprint install tests",
    }
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(wdir, "src", "__init__.py"), "w").close()
    with open(os.path.join(wdir, "src", "mod.py"), "w") as f:
        f.write(src or "def go():\n    return 'ok'\n")
    with open(os.path.join(wdir, "tests", "test_mod.py"), "w") as f:
        f.write(tests or "from src.mod import go\n\ndef test_go():\n    assert go() == 'ok'\n")
    example_default = (
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.mod import go\n"
        "print(go())\n"
    )
    with open(os.path.join(wdir, "examples", "example_usage.py"), "w") as f:
        f.write(example or example_default)
    return wdir


def _write_blueprint_for_checkin(project_dir, name, deps):
    """Plant a fully-formed blueprint that can be checked in."""
    bp_id = f"bp-{name}-python"
    py_dir = bp_id.replace("-", "_")
    bp = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(bp, "src"))
    os.makedirs(os.path.join(bp, "tests"))
    os.makedirs(os.path.join(bp, "examples"))
    manifest = {
        "id": bp_id, "name": name, "language": "python", "version": "0.1.0",
        "description": "blueprint fixture for install round-trip",
        "tags": ["test", "fixture", "install"],
        "dependencies": deps, "domains": [],
    }
    with open(os.path.join(bp, "blueprint.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(bp, "src", "__init__.py"), "w").close()
    with open(os.path.join(bp, "src", "mod.py"), "w") as f:
        f.write("from cg.backend_greet_python.src.mod import go\n"
                "def shout():\n    return go().upper()\n")
    with open(os.path.join(bp, "tests", "test_mod.py"), "w") as f:
        f.write("from src.mod import shout\n\n"
                "def test_shout():\n    assert shout() == 'OK'\n")
    with open(os.path.join(bp, "examples", "example_mod.py"), "w") as f:
        f.write("from src.mod import shout\nprint(shout())\n")
    return bp


@pytest.mark.slow
def test_end_to_end_checkin_then_install(carto, tmp_path):
    """Author a blueprint, check it in, install it into a fresh project."""
    author_proj = tmp_path / "author"
    author_proj.mkdir()
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    # Check in the leaf widget first.
    _write_widget_for_checkin(str(author_proj), "backend-greet-python", "1.0.0")
    leaf_res = carto.checkin(
        path=os.path.join(str(author_proj), "cg", "backend_greet_python"),
        reason="initial",
    )
    assert leaf_res["status"] == "success", leaf_res
    carto.reload()

    # Now author and check in a blueprint that depends on it.
    bp = _write_blueprint_for_checkin(
        str(author_proj), "shouter",
        deps=[{"id": "backend-greet-python", "version": "1.0.0"}],
    )
    bp_res = carto.checkin_blueprint(path=bp, reason="initial")
    assert bp_res["status"] == "success", bp_res
    carto.reload()

    # Install it into the consumer project.
    res = carto.install("bp-shouter-python", str(consumer))
    assert res.get("status") == "success", res
    assert res["warnings"] == []
    assert os.path.isfile(os.path.join(
        str(consumer), "cg", "bp_shouter_python", "blueprint.json"))
    assert os.path.isfile(os.path.join(
        str(consumer), "cg", "backend_greet_python", "widget.json"))
    # Cascade installed the leaf at the pinned version.
    leaf_meta = os.path.join(
        str(consumer), "cg", "backend_greet_python", "widget.json")
    with open(leaf_meta) as f:
        assert json.load(f)["meta"]["version"] == "1.0.0"


@pytest.mark.slow
def test_uninstall_blueprint_leaves_dependencies(carto, tmp_path):
    """Uninstalling a blueprint removes only the blueprint dir.

    Leaf widgets the blueprint cascaded in stay put — the user's app code
    may be importing them, and we don't try to outsmart it. Orphan
    cleanup is surfaced via `cartograph status` for the agent to act on.
    """
    author_proj = tmp_path / "author"
    author_proj.mkdir()
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    _write_widget_for_checkin(str(author_proj), "backend-greet-python", "1.0.0")
    carto.checkin(path=os.path.join(str(author_proj), "cg", "backend_greet_python"),
                  reason="initial")
    carto.reload()
    bp = _write_blueprint_for_checkin(
        str(author_proj), "shouter",
        deps=[{"id": "backend-greet-python", "version": "1.0.0"}],
    )
    carto.checkin_blueprint(path=bp, reason="initial")
    carto.reload()

    carto.install("bp-shouter-python", str(consumer))
    bp_dir = os.path.join(str(consumer), "cg", "bp_shouter_python")
    leaf_dir = os.path.join(str(consumer), "cg", "backend_greet_python")
    assert os.path.isdir(bp_dir)
    assert os.path.isdir(leaf_dir)

    res = carto.uninstall("bp-shouter-python", str(consumer))
    assert res["status"] == "success", res
    assert not os.path.exists(bp_dir)
    # Leaf is intentionally orphaned, not removed — app code may use it.
    assert os.path.isdir(leaf_dir)


@pytest.mark.slow
def test_double_install_same_blueprint_blocked(carto, tmp_path):
    """Re-installing a blueprint over an existing install is rejected."""
    author_proj = tmp_path / "author"
    author_proj.mkdir()
    consumer = tmp_path / "consumer"
    consumer.mkdir()

    _write_widget_for_checkin(str(author_proj), "backend-greet-python", "1.0.0")
    carto.checkin(path=os.path.join(str(author_proj), "cg", "backend_greet_python"),
                  reason="initial")
    carto.reload()
    bp = _write_blueprint_for_checkin(
        str(author_proj), "shouter",
        deps=[{"id": "backend-greet-python", "version": "1.0.0"}],
    )
    carto.checkin_blueprint(path=bp, reason="initial")
    carto.reload()

    first = carto.install("bp-shouter-python", str(consumer))
    assert first["status"] == "success"
    second = carto.install("bp-shouter-python", str(consumer))
    assert "error" in second
    assert "already installed" in second["error"].lower()
