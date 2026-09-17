"""Tests for kind-aware search and inspect.

Cheap tests only — they exercise the engine's library scan + search
backend wiring against checked-in fixtures planted directly into the
library directory (no validation runs).
"""

import json
import os

import pytest

from cartograph.engine import Cartograph


def _stamp(path, language="python"):
    """Write a minimal validation stamp so the engine doesn't skip the artifact."""
    from cartograph.languages import get_engine
    from cartograph.validation_stamp import write_stamp
    engine = get_engine(language)
    write_stamp(path, language, engine)


def _plant_widget(lib_path, widget_id, version="1.0.0",
                  domain="backend", description="A widget for tests"):
    """Plant a fully-stamped widget directly into the library dir."""
    py_dir = widget_id.replace("-", "_")
    wdir = os.path.join(lib_path, py_dir)
    os.makedirs(os.path.join(wdir, "src"))
    os.makedirs(os.path.join(wdir, "tests"))
    os.makedirs(os.path.join(wdir, "examples"))
    name = widget_id.split("-", 1)[1].rsplit("-", 1)[0]
    manifest = {
        "meta": {"id": widget_id, "name": name, "version": version,
                 "domain": domain, "tags": ["a", "b", "c"]},
        "tech_stack": {"language": "python", "dependencies": []},
        "description": description,
    }
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(wdir, "src", "__init__.py"), "w").close()
    with open(os.path.join(wdir, "src", "mod.py"), "w") as f:
        f.write("def go():\n    return 'ok'\n")
    with open(os.path.join(wdir, "tests", "test_mod.py"), "w") as f:
        f.write("def test_x(): assert True\n")
    with open(os.path.join(wdir, "examples", "example_usage.py"), "w") as f:
        f.write("print('hi')\n")
    _stamp(wdir)
    return wdir


def _plant_blueprint(lib_path, name="auth-flow", deps=None,
                     domains=None, description="Composed feature"):
    """Plant a fully-stamped blueprint directly into the library dir."""
    deps = deps or [{"id": "backend-x-python", "version": "1.0.0"}]
    domains = domains or ["backend"]
    bp_id = f"bp-{name}-python"
    py_dir = bp_id.replace("-", "_")
    bp = os.path.join(lib_path, py_dir)
    os.makedirs(os.path.join(bp, "src"))
    os.makedirs(os.path.join(bp, "tests"))
    os.makedirs(os.path.join(bp, "examples"))
    manifest = {
        "id": bp_id, "name": name, "language": "python", "version": "0.1.0",
        "description": description,
        "tags": ["composed", "test", "fixture"],
        "dependencies": deps, "domains": domains,
    }
    with open(os.path.join(bp, "blueprint.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(bp, "src", "__init__.py"), "w").close()
    with open(os.path.join(bp, "src", "mod.py"), "w") as f:
        f.write("def shout():\n    return 'OK'\n")
    with open(os.path.join(bp, "examples", "example_usage.py"), "w") as f:
        f.write("print('hi')\n")
    _stamp(bp)
    return bp


@pytest.fixture
def lib_with_both(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _plant_widget(str(lib), "backend-x-python",
                  description="basic backend widget")
    _plant_widget(str(lib), "security-audit-python", domain="security",
                  description="security audit logger")
    _plant_blueprint(str(lib), name="auth-flow",
                     deps=[{"id": "backend-x-python", "version": "1.0.0"},
                           {"id": "security-audit-python", "version": "1.0.0"}],
                     domains=["backend", "security"],
                     description="composed auth flow blueprint")
    return Cartograph(library_path=str(lib))


# --- engine library load --------------------------------------------------


def test_engine_loads_blueprints_into_self_blueprints(lib_with_both):
    bp_ids = {b["id"] for b in lib_with_both.blueprints}
    assert "bp-auth-flow-python" in bp_ids
    bp = next(b for b in lib_with_both.blueprints if b["id"] == "bp-auth-flow-python")
    assert bp["domains"] == ["backend", "security"]
    assert bp["language"] == "python"
    assert len(bp["dependencies"]) == 2


def test_engine_widgets_excludes_blueprints(lib_with_both):
    """Blueprints must not bleed into self.widgets."""
    widget_ids = {w["id"] for w in lib_with_both.widgets}
    assert "bp-auth-flow-python" not in widget_ids


def test_engine_loads_widget_id_containing_history_and_skips_history_archive(tmp_path):
    lib = tmp_path / "history_enabled_library"
    lib.mkdir()
    _plant_widget(str(lib), "universal-reversible-state-history-python")
    archived = lib / "ordinary_widget" / "history" / "0.9.0"
    archived.mkdir(parents=True)
    _plant_widget(str(archived), "backend-archived-python")

    carto = Cartograph(library_path=str(lib))

    widget_ids = {widget["id"] for widget in carto.widgets}
    assert "universal-reversible-state-history-python" in widget_ids
    assert "backend-archived-python" not in widget_ids


def test_engine_loads_blueprint_id_containing_history_and_skips_history_archive(tmp_path):
    lib = tmp_path / "history_enabled_library"
    lib.mkdir()
    _plant_blueprint(str(lib), name="state-history")
    archived = lib / "ordinary_blueprint" / "history" / "0.9.0"
    archived.mkdir(parents=True)
    _plant_blueprint(str(archived), name="archived-history")

    carto = Cartograph(library_path=str(lib))

    blueprint_ids = {blueprint["id"] for blueprint in carto.blueprints}
    assert "bp-state-history-python" in blueprint_ids
    assert "bp-archived-history-python" not in blueprint_ids


# --- search ---------------------------------------------------------------


def test_search_returns_blueprints_with_kind(lib_with_both):
    res = lib_with_both.search("auth flow")
    ids = {r["id"]: r for r in res["results"]}
    assert "bp-auth-flow-python" in ids
    assert ids["bp-auth-flow-python"]["kind"] == "blueprint"


def test_search_widget_results_carry_kind(lib_with_both):
    res = lib_with_both.search("backend basic widget")
    widget_hit = next(r for r in res["results"] if r["id"] == "backend-x-python")
    assert widget_hit["kind"] == "widget"


def test_search_blueprint_includes_domains_field(lib_with_both):
    res = lib_with_both.search("composed auth flow")
    bp_hit = next(r for r in res["results"] if r["id"] == "bp-auth-flow-python")
    assert set(bp_hit["domains"]) == {"backend", "security"}


def test_search_filter_by_domain_matches_blueprint(lib_with_both):
    """A blueprint with domains=[backend, security] must match domain=security."""
    res = lib_with_both.search("auth", domain_filter="security")
    ids = {r["id"] for r in res["results"]}
    assert "bp-auth-flow-python" in ids


# --- inspect --------------------------------------------------------------


def test_inspect_blueprint_returns_blueprint_view(lib_with_both):
    res = lib_with_both.inspect("bp-auth-flow-python")
    assert res["kind"] == "blueprint"
    assert res["language"] == "python"
    assert set(res["domains"]) == {"backend", "security"}
    assert len(res["dependencies"]) == 2
    pins = {d["id"]: d["version"] for d in res["dependencies"]}
    assert pins == {"backend-x-python": "1.0.0",
                    "security-audit-python": "1.0.0"}


def test_inspect_blueprint_with_source_includes_files(lib_with_both):
    res = lib_with_both.inspect("bp-auth-flow-python", show_source=True)
    assert "source" in res
    assert any("mod" in name for name in res["source"])


def test_inspect_widget_still_marked_kind(lib_with_both):
    res = lib_with_both.inspect("backend-x-python")
    assert res["kind"] == "widget"


def test_inspect_blueprint_not_found(lib_with_both):
    res = lib_with_both.inspect("bp-missing-python")
    assert "error" in res
    assert "blueprint" in res["error"].lower()


# --- list_popular ---------------------------------------------------------


def test_list_popular_includes_blueprints_with_kind(lib_with_both):
    res = lib_with_both.list_popular()
    assets = res["top_assets"]
    kinds = {a["id"]: a["kind"] for a in assets}
    assert kinds.get("bp-auth-flow-python") == "blueprint"
    assert kinds.get("backend-x-python") == "widget"
