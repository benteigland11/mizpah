"""
Tests for validate_item - both passing and failing cases.
validate_item returns {"status": "error", "message": ...} on failure
and {"status": "success", ...} on success.
"""
import os
import json
import shutil
from unittest.mock import patch
import pytest


def test_validate_valid_widget(carto, fixture_library):
    """A well-formed fixture widget should pass validation."""
    path = os.path.join(fixture_library, "http-client")
    result = carto.validate_item(path)
    assert result.get("status") == "success", f"Expected success, got: {result}"


def test_validate_missing_path(carto):
    """Validating a non-existent path should return status=error."""
    result = carto.validate_item("/tmp/totally-fake-widget-xyz")
    assert result.get("status") == "error"


def test_validate_missing_manifest(carto, tmp_path):
    """A widget dir with no widget.json should return status=error."""
    widget_dir = tmp_path / "bad-widget"
    widget_dir.mkdir()
    result = carto.validate_item(str(widget_dir))
    assert result.get("status") == "error"


def test_validate_minimal_widget(carto, tmp_path):
    """A widget with a manifest but no src/ should return status=error."""
    widget_dir = tmp_path / "minimal-widget"
    widget_dir.mkdir()
    manifest = {
        "meta": {"id": "minimal-widget", "name": "Minimal Widget",
                 "version": "1.0.0", "tags": ["test"], "domain": "backend"},
        "description": "A minimal widget for testing.",
        "tech_stack": {"language": "python", "dependencies": []},
    }
    (widget_dir / "widget.json").write_text(json.dumps(manifest))
    result = carto.validate_item(str(widget_dir))
    assert result.get("status") == "error"
    assert result.get("message"), "Error should include a message"


def test_validate_invalid_domain(carto, tmp_path):
    widget_dir = tmp_path / "bad-domain"
    widget_dir.mkdir()
    manifest = {
        "meta": {"id": "bad-domain", "name": "Bad Domain", "domain": "enterprise"},
        "description": "Test.",
        "tech_stack": {"language": "python", "dependencies": []},
    }
    (widget_dir / "widget.json").write_text(json.dumps(manifest))
    result = carto.validate_item(str(widget_dir))
    assert result.get("status") == "error"
    assert "domain" in result.get("message", "").lower()


def test_validate_hydrates_drifted_library_notes(carto, fixture_library, tmp_path):
    """library_notes drift is restored at validate time with a warning."""
    src_widget = os.path.join(fixture_library, "http-client")
    widget_dir = tmp_path / "http-client-tampered"
    shutil.copytree(src_widget, widget_dir)

    manifest_path = widget_dir / "widget.json"
    with open(manifest_path) as f:
        data = json.load(f)
    data["meta"]["id"] = "http-client-tampered"
    data["meta"]["name"] = "Tampered"
    data["library_notes"] = {"general": "AGENT-EDIT", "domain": "AGENT-EDIT"}
    with open(manifest_path, "w") as f:
        json.dump(data, f)

    # Perturb src so the duplicate-implementation check passes
    for fname in os.listdir(widget_dir / "src"):
        if fname.endswith(".py"):
            p = widget_dir / "src" / fname
            p.write_text(p.read_text() + "\n# perturb\n")
            break

    result = carto.validate_item(str(widget_dir))
    assert result.get("status") == "success", result
    warnings = result.get("warnings", [])
    assert any("library_notes drifted" in w for w in warnings), warnings

    # Canonical notes restored on disk
    with open(manifest_path) as f:
        post = json.load(f)
    assert post["library_notes"].get("general", "").startswith("Single responsibility")
    assert post["library_notes"]["domain"] != "AGENT-EDIT"


def test_validate_duplicate_implementation_fails(carto, fixture_library, tmp_path):
    """A widget with identical src/ implementation to another widget should fail."""
    src_widget = os.path.join(fixture_library, "http-client")
    widget_dir = tmp_path / "http-client-clone"
    shutil.copytree(src_widget, widget_dir)

    manifest_path = widget_dir / "widget.json"
    with open(manifest_path) as f:
        data = json.load(f)
    data["meta"]["id"] = "http-client-clone"
    data["meta"]["name"] = "HTTP Client Clone"
    with open(manifest_path, "w") as f:
        json.dump(data, f)

    result = carto.validate_item(str(widget_dir))
    assert result.get("status") == "error"
    assert "Identical code already exists" in result.get("message", "")


# ---------------------------------------------------------------------------
# Validation stamp invalidation on engine version bump
# ---------------------------------------------------------------------------

def test_stamp_invalidates_on_engine_version_bump(carto, fixture_library, tmp_path):
    """A stamp written with engine_version N should be stale if engine is now N+1."""
    import shutil
    from cartograph.languages.python import PythonEngine
    from cartograph.validation_stamp import write_stamp, is_stamp_valid

    # Copy fixture widget to a writable location
    widget_path = str(tmp_path / "http-client")
    shutil.copytree(os.path.join(fixture_library, "http-client"), widget_path)

    engine = PythonEngine()
    original_version = engine.validation_version

    # Write stamp at current version
    write_stamp(widget_path, "python", engine)
    assert is_stamp_valid(widget_path, "python", engine)

    # Simulate engine version bump
    engine.validation_version = original_version + 1
    assert not is_stamp_valid(widget_path, "python", engine)

    # Restore
    engine.validation_version = original_version



def test_validate_widget_with_dependency(carto, tmp_path):
    """Validation should install declared dependencies before running tests.

    Uses 'six' which is already installed, so pip just confirms it's satisfied.
    This exercises the install_deps code path that zero-dep fixture widgets skip.
    """
    widget_dir = tmp_path / "with-dep"
    src_dir = widget_dir / "src"
    tests_dir = widget_dir / "tests"
    examples_dir = widget_dir / "examples"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    examples_dir.mkdir()

    # Source that imports the dependency
    (src_dir / "__init__.py").write_text("")
    (src_dir / "compat.py").write_text(
        "import six\n\n"
        "def is_text(val):\n"
        "    return isinstance(val, six.text_type)\n"
    )

    # Tests
    (tests_dir / "test_compat.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))\n"
        "from compat import is_text\n\n"
        "def test_is_text():\n"
        "    assert is_text('hello')\n\n"
        "def test_not_text():\n"
        "    assert not is_text(123)\n"
    )

    # Example
    (examples_dir / "example_usage.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
        "from src.compat import is_text\n\n"
        "print(f'Is text: {is_text(\"hello\")}')\n"
    )

    # Manifest with a real dependency
    manifest = {
        "meta": {"id": "backend-compat-python", "name": "Compat", "domain": "backend",
                 "version": "1.0.0"},
        "tech_stack": {"language": "python", "dependencies": ["six>=1.0"]},
        "tags": ["compat", "python2", "python3"],
    }
    (widget_dir / "widget.json").write_text(json.dumps(manifest, indent=2))

    result = carto.validate_item(str(widget_dir))
    assert result["status"] == "success", f"Validation failed: {result}"


def test_validate_fails_if_stamp_write_fails(carto, fixture_library, tmp_path):
    widget_path = tmp_path / "http-client"
    shutil.copytree(os.path.join(fixture_library, "http-client"), widget_path)

    with patch("cartograph.validation_stamp._write_stamp", side_effect=OSError("disk full")):
        result = carto.validate_item(str(widget_path))
    assert result["status"] == "error"
    assert "validation stamp" in result["message"].lower()
