"""Regression tests for _zip_widget artifact filtering and size guard."""
import io
import json
import os
import zipfile

import pytest

from cartograph.cloud import _zip_widget, _ZIP_ERROR_BYTES


def _make_widget(tmp_path, language, extra_dirs=None, big_file=None):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "code").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test(): pass\n")
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "example_usage.py").write_text("print('ok')\n")
    (tmp_path / "widget.json").write_text(json.dumps({
        "meta": {"id": "test-widget", "version": "1.0.0"},
        "tech_stack": {"language": language, "dependencies": []},
    }))
    for d in extra_dirs or []:
        full = tmp_path / d
        full.mkdir(parents=True, exist_ok=True)
        (full / "junk.bin").write_text("x" * 100)
    if big_file:
        # Use random bytes — DEFLATE would compress repeated bytes by ~1000x
        # and never trip the size guard.
        (tmp_path / big_file[0]).write_bytes(os.urandom(big_file[1]))


def _names_in(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return set(zf.namelist())


def test_excludes_angular_cache(tmp_path):
    _make_widget(tmp_path, "angular", extra_dirs=["node_modules", ".angular/cache", "dist"])
    names = _names_in(_zip_widget(str(tmp_path)))
    assert not any(n.startswith("node_modules/") for n in names)
    assert not any(n.startswith(".angular/") for n in names)
    assert not any(n.startswith("dist/") for n in names)
    assert any(n.endswith("widget.json") for n in names)


def test_excludes_python_artifacts(tmp_path):
    _make_widget(tmp_path, "python", extra_dirs=["__pycache__", ".venv/lib", ".pytest_cache"])
    names = _names_in(_zip_widget(str(tmp_path)))
    assert not any("__pycache__" in n for n in names)
    assert not any(n.startswith(".venv/") for n in names)
    assert not any(n.startswith(".pytest_cache/") for n in names)


def test_excludes_php_vendor(tmp_path):
    _make_widget(tmp_path, "php", extra_dirs=["vendor/composer", ".phpunit.cache"])
    names = _names_in(_zip_widget(str(tmp_path)))
    assert not any(n.startswith("vendor/") for n in names)
    assert not any(n.startswith(".phpunit.cache/") for n in names)


def test_excludes_history_dir(tmp_path):
    _make_widget(tmp_path, "python", extra_dirs=["history/0.0.1"])
    names = _names_in(_zip_widget(str(tmp_path)))
    assert not any(n.startswith("history/") for n in names)


def test_excludes_metadata_files(tmp_path):
    _make_widget(tmp_path, "python")
    (tmp_path / ".validation_stamp.json").write_text("{}")
    (tmp_path / "reviews.json").write_text("[]")
    (tmp_path / "changelog.json").write_text("[]")
    names = _names_in(_zip_widget(str(tmp_path)))
    assert ".validation_stamp.json" not in names
    assert "reviews.json" not in names
    assert "changelog.json" not in names


def test_keeps_widget_essentials(tmp_path):
    _make_widget(tmp_path, "python")
    names = _names_in(_zip_widget(str(tmp_path)))
    assert "widget.json" in names
    assert any(n.endswith("code") for n in names)
    assert any(n.endswith("test_x.py") for n in names)
    assert any(n.endswith("example_usage.py") for n in names)


def test_unknown_language_falls_back_to_universal(tmp_path):
    _make_widget(tmp_path, "klingon", extra_dirs=[".git"])
    names = _names_in(_zip_widget(str(tmp_path)))
    assert not any(n.startswith(".git/") for n in names)


def test_size_guard_raises_above_error_threshold(tmp_path):
    _make_widget(tmp_path, "python", big_file=("blob.bin", _ZIP_ERROR_BYTES + 1))
    with pytest.raises(ValueError, match="exceeding"):
        _zip_widget(str(tmp_path))
