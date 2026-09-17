"""Tests for `cartograph blueprint create` scaffolding."""

import json
import os

import pytest

from cartograph.engine import Cartograph
from cartograph.manifest import BLUEPRINT_MANIFEST


@pytest.fixture
def carto(tmp_path):
    """Cartograph instance with a tmp library, so create doesn't pollute the user's lib."""
    lib = tmp_path / "lib"
    lib.mkdir()
    return Cartograph(library_path=str(lib))


def test_create_blueprint_happy_path(carto, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    res = carto.create_blueprint(
        name="auth-flow", language="python", target_dir=str(project)
    )
    assert res["status"] == "success", res
    assert res["item_id"] == "bp-auth-flow-python"
    assert res["kind"] == "blueprint"
    bp_dir = project / "cg" / "bp_auth_flow_python"
    assert bp_dir.is_dir()
    assert (bp_dir / "src").is_dir()
    assert (bp_dir / "tests").is_dir()
    assert (bp_dir / "examples").is_dir()
    assert (bp_dir / BLUEPRINT_MANIFEST).is_file()
    # No widget.json — it's a blueprint, not a widget.
    assert not (bp_dir / "widget.json").exists()


def test_create_blueprint_manifest_shape(carto, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    carto.create_blueprint(
        name="auth-flow", language="python", target_dir=str(project)
    )
    manifest = json.loads(
        (project / "cg" / "bp_auth_flow_python" / BLUEPRINT_MANIFEST).read_text()
    )
    assert manifest["id"] == "bp-auth-flow-python"
    assert manifest["name"] == "auth-flow"
    assert manifest["language"] == "python"
    assert manifest["version"] == "0.1.0"
    assert manifest["dependencies"] == []
    assert manifest["domains"] == []
    # Authoring placeholders that the validator will block on until filled.
    assert "[TODO]" in manifest["description"]
    assert any("[TODO" in t for t in manifest["tags"])


def test_create_blueprint_python_skeleton_has_passing_tests(carto, tmp_path):
    """Blueprints reuse the widget scaffold (a blueprint is a widget that
    composes deps), so the skeleton mirrors the widget python template:
    src/__init__.py, src/<module>.py, tests/test_<module>.py,
    examples/example_usage.py."""
    project = tmp_path / "proj"
    project.mkdir()
    carto.create_blueprint(
        name="my-flow", language="python", target_dir=str(project)
    )
    bp_dir = project / "cg" / "bp_my_flow_python"
    assert (bp_dir / "src" / "__init__.py").is_file()
    assert (bp_dir / "src" / "my_flow.py").is_file()
    assert (bp_dir / "tests" / "test_my_flow.py").is_file()
    assert (bp_dir / "examples" / "example_usage.py").is_file()


def test_create_blueprint_strips_typed_bp_prefix(carto, tmp_path):
    """If the author types 'bp-foo' as the name, we shouldn't double-prefix."""
    project = tmp_path / "proj"
    project.mkdir()
    res = carto.create_blueprint(
        name="bp-foo", language="python", target_dir=str(project)
    )
    assert res["item_id"] == "bp-foo-python"


def test_create_blueprint_strips_typed_language_suffix(carto, tmp_path):
    """If the author types 'foo-python' as the name, we shouldn't double-suffix."""
    project = tmp_path / "proj"
    project.mkdir()
    res = carto.create_blueprint(
        name="foo-python", language="python", target_dir=str(project)
    )
    assert res["item_id"] == "bp-foo-python"


def test_create_blueprint_rejects_existing_dir(carto, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    carto.create_blueprint(name="foo", language="python", target_dir=str(project))
    res = carto.create_blueprint(name="foo", language="python", target_dir=str(project))
    assert res["status"] == "error"
    assert "already exists" in res["message"]


def test_create_blueprint_rejects_bad_name(carto, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    res = carto.create_blueprint(name="Bad_Name", language="python", target_dir=str(project))
    assert res["status"] == "error"


def test_create_blueprint_rejects_missing_language(carto, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    res = carto.create_blueprint(name="foo", language=None, target_dir=str(project))
    assert res["status"] == "error"


@pytest.mark.parametrize("language,expected_ext", [
    ("javascript", "js"),
    ("nim", "nim"),
    ("openscad", "scad"),
    ("php", "php"),
])
def test_create_blueprint_non_python_languages(carto, tmp_path, language, expected_ext):
    """Blueprint scaffolding works for every supported language. Engines
    without an opinionated blueprint_scaffold get the base default skeleton
    (placeholder files keyed off file_ext); engines with overrides get
    their richer skeleton. Either way, validate has something to drive."""
    project = tmp_path / "proj"
    project.mkdir()
    res = carto.create_blueprint(
        name="auth-flow", language=language, target_dir=str(project)
    )
    assert res["status"] == "success", res
    assert res["item_id"] == f"bp-auth-flow-{language}"
    # Python / Nim use underscores for importability; everything else keeps hyphens.
    if language in ("python", "nim"):
        bp_dir = project / "cg" / f"bp_auth_flow_{language}"
    else:
        bp_dir = project / "cg" / f"bp-auth-flow-{language}"
    assert bp_dir.is_dir()
    assert (bp_dir / "src").is_dir()
    assert (bp_dir / "tests").is_dir()
    assert (bp_dir / "examples").is_dir()
    # Skeleton wrote at least one file in each dir using the engine's ext.
    src_files = list((bp_dir / "src").glob(f"*.{expected_ext}"))
    test_files = list((bp_dir / "tests").glob(f"*.{expected_ext}"))
    example_files = list((bp_dir / "examples").glob(f"*.{expected_ext}"))
    assert src_files, f"no src/*.{expected_ext} written"
    assert test_files, f"no tests/*.{expected_ext} written"
    assert example_files, f"no examples/*.{expected_ext} written"


def test_create_blueprint_rejects_unknown_language(carto, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    res = carto.create_blueprint(
        name="foo", language="cobol", target_dir=str(project)
    )
    assert res["status"] == "error"
    assert "engine" in res["message"].lower()


def test_validate_on_blueprint_routes_to_blueprint_validator(carto, tmp_path, monkeypatch, capsys):
    """`cartograph validate` on a blueprint dir calls validate_blueprint,
    NOT the widget validate_item path."""
    project = tmp_path / "proj"
    project.mkdir()
    carto.create_blueprint(name="foo", language="python", target_dir=str(project))
    bp_dir = project / "cg" / "bp_foo_python"

    from cartograph import cli as cartograph_cli

    class Args:
        path = str(bp_dir)
        lib = False

    called = {"validate_item": False, "validate_blueprint": False}

    def fake_validate_item(self, *a, **kw):
        called["validate_item"] = True
        return {"status": "success"}

    def fake_validate_blueprint(self, *a, **kw):
        called["validate_blueprint"] = True
        return {"status": "success", "kind": "blueprint", "id": "bp-foo-python"}

    from cartograph.engine import Cartograph as _Carto
    monkeypatch.setattr(_Carto, "validate_item", fake_validate_item)
    monkeypatch.setattr(_Carto, "validate_blueprint", fake_validate_blueprint)

    cartograph_cli.cmd_validate(Args())
    assert called["validate_blueprint"] is True
    assert called["validate_item"] is False, (
        "blueprint dirs must NOT reach the widget validator"
    )


def test_checkin_on_blueprint_routes_to_blueprint_checkin(carto, tmp_path, monkeypatch):
    """`cartograph checkin` on a blueprint dir calls checkin_blueprint,
    NOT the widget checkin path."""
    project = tmp_path / "proj"
    project.mkdir()
    carto.create_blueprint(name="foo", language="python", target_dir=str(project))
    bp_dir = project / "cg" / "bp_foo_python"

    from cartograph import cli as cartograph_cli

    class Args:
        path = str(bp_dir)
        reason = "test"
        bump = "minor"
        override_warnings = False
        override_reason = None
        publish = False
        no_publish = False

    called = {"checkin": False, "checkin_blueprint": False}

    def fake_checkin(self, *a, **kw):
        called["checkin"] = True
        return {"status": "success", "action": "registered"}

    def fake_checkin_blueprint(self, *a, **kw):
        called["checkin_blueprint"] = True
        return {"status": "success", "action": "registered",
                "kind": "blueprint", "id": "bp-foo-python"}

    from cartograph.engine import Cartograph as _Carto
    monkeypatch.setattr(_Carto, "checkin", fake_checkin)
    monkeypatch.setattr(_Carto, "checkin_blueprint", fake_checkin_blueprint)

    # cmd_checkin needs _resolve_widget to handle a path; the create scaffold
    # placed the blueprint at <project>/cg/bp_foo_python which is a real dir,
    # so it resolves directly.
    cartograph_cli.cmd_checkin(Args())
    assert called["checkin_blueprint"] is True
    assert called["checkin"] is False
