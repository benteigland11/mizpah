"""Tests for the manifest loader and kind discriminator."""

import json
import os

import pytest

from cartograph.manifest import (
    BLUEPRINT_MANIFEST,
    KIND_BLUEPRINT,
    KIND_WIDGET,
    Manifest,
    ManifestError,
    WIDGET_MANIFEST,
    detect_kind,
    load_manifest,
    manifest_filename,
)


def _write_widget(path, **overrides):
    meta = {
        "id": "backend-foo-python",
        "name": "Foo",
        "version": "1.2.3",
        "domain": "backend",
        "tags": ["a", "b", "c"],
    }
    meta.update(overrides.get("meta", {}))
    data = {
        "meta": meta,
        "tech_stack": {"language": "python", "dependencies": []},
        "description": "test",
    }
    data.update({k: v for k, v in overrides.items() if k != "meta"})
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, WIDGET_MANIFEST), "w") as f:
        json.dump(data, f)


def _write_blueprint(path, **overrides):
    data = {
        "id": "bp-auth-flow-python",
        "name": "auth-flow",
        "language": "python",
        "version": "0.1.0",
        "description": "test blueprint",
        "tags": ["auth", "session"],
        "dependencies": [
            {"id": "backend-rate-limit-python", "version": "1.4.0"},
            {"id": "security-audit-log-python", "version": "1.0.1"},
        ],
        "domains": ["backend", "security"],
    }
    data.update(overrides)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, BLUEPRINT_MANIFEST), "w") as f:
        json.dump(data, f)


def test_detect_kind_widget(tmp_path):
    _write_widget(tmp_path)
    assert detect_kind(str(tmp_path)) == KIND_WIDGET


def test_detect_kind_blueprint(tmp_path):
    _write_blueprint(tmp_path)
    assert detect_kind(str(tmp_path)) == KIND_BLUEPRINT


def test_detect_kind_none(tmp_path):
    assert detect_kind(str(tmp_path)) is None


def test_detect_kind_both_is_error(tmp_path):
    _write_widget(tmp_path)
    _write_blueprint(tmp_path)
    with pytest.raises(ManifestError):
        detect_kind(str(tmp_path))


def test_manifest_filename_round_trip():
    assert manifest_filename(KIND_WIDGET) == WIDGET_MANIFEST
    assert manifest_filename(KIND_BLUEPRINT) == BLUEPRINT_MANIFEST


def test_manifest_filename_unknown():
    with pytest.raises(ManifestError):
        manifest_filename("app")


def test_load_widget(tmp_path):
    _write_widget(tmp_path)
    m = load_manifest(str(tmp_path))
    assert m.kind == KIND_WIDGET
    assert m.is_widget
    assert not m.is_blueprint
    assert m.id == "backend-foo-python"
    assert m.name == "Foo"
    assert m.language == "python"
    assert m.version == "1.2.3"
    assert m.domains == ["backend"]
    assert m.tags == ["a", "b", "c"]
    assert m.dependencies == []
    assert m.raw["tech_stack"]["language"] == "python"


def test_load_blueprint(tmp_path):
    _write_blueprint(tmp_path)
    m = load_manifest(str(tmp_path))
    assert m.kind == KIND_BLUEPRINT
    assert m.is_blueprint
    assert not m.is_widget
    assert m.id == "bp-auth-flow-python"
    assert m.name == "auth-flow"
    assert m.language == "python"
    assert m.version == "0.1.0"
    assert m.domains == ["backend", "security"]
    assert m.dependencies == [
        {"id": "backend-rate-limit-python", "version": "1.4.0"},
        {"id": "security-audit-log-python", "version": "1.0.1"},
    ]


def test_load_blueprint_filters_malformed_deps(tmp_path):
    _write_blueprint(
        tmp_path,
        dependencies=[
            {"id": "good-python", "version": "1.0.0"},
            {"id": "missing-version-python"},
            "string-instead-of-dict",
            {"version": "no-id"},
        ],
    )
    m = load_manifest(str(tmp_path))
    assert m.dependencies == [{"id": "good-python", "version": "1.0.0"}]


def test_load_missing_raises(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(str(tmp_path))


def test_load_invalid_json_raises(tmp_path):
    os.makedirs(tmp_path, exist_ok=True)
    (tmp_path / WIDGET_MANIFEST).write_text("{not json")
    with pytest.raises(ManifestError):
        load_manifest(str(tmp_path))


def test_load_widget_handles_missing_meta(tmp_path):
    """Defensive: widget.json with no meta block should still load (id="")."""
    os.makedirs(tmp_path, exist_ok=True)
    with open(os.path.join(tmp_path, WIDGET_MANIFEST), "w") as f:
        json.dump({"tech_stack": {"language": "python"}}, f)
    m = load_manifest(str(tmp_path))
    assert m.kind == KIND_WIDGET
    assert m.id == ""
    assert m.domains == []


def test_load_blueprint_handles_missing_optional_fields(tmp_path):
    """A blueprint manifest with no domains/tags fields should still load."""
    os.makedirs(tmp_path, exist_ok=True)
    minimal = {
        "id": "bp-foo-python",
        "name": "foo",
        "language": "python",
        "version": "0.1.0",
        "dependencies": [{"id": "backend-bar-python", "version": "1.0.0"}],
    }
    (tmp_path / BLUEPRINT_MANIFEST).write_text(json.dumps(minimal))
    m = load_manifest(str(tmp_path))
    assert m.kind == KIND_BLUEPRINT
    assert m.tags == []
    assert m.domains == []
    assert m.dependencies == [{"id": "backend-bar-python", "version": "1.0.0"}]


def test_load_blueprint_with_unicode_description(tmp_path):
    """Manifest content shouldn't be locale-fragile."""
    _write_blueprint(tmp_path, description="Auth flow — composes session + audit")
    m = load_manifest(str(tmp_path))
    assert "—" in m.raw["description"]


def test_detect_kind_with_extra_files_present(tmp_path):
    """Other files in the dir don't confuse kind detection."""
    _write_widget(tmp_path)
    (tmp_path / "README.md").write_text("hi")
    (tmp_path / ".cartograph_source").write_text("{}")
    assert detect_kind(str(tmp_path)) == KIND_WIDGET


def test_manifest_is_dataclass():
    m = Manifest(
        kind=KIND_WIDGET,
        path="/x",
        manifest_path="/x/widget.json",
        id="i",
        name="n",
        language="python",
        version="1.0.0",
        domains=["backend"],
        tags=[],
    )
    assert m.dependencies == []
    assert m.raw == {}
