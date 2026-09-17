"""cloud publish / module ref: directory path vs widget id resolution.

Regression: ``cartograph cloud publish cg/foo-python`` used to bind the
path string as widget_id and POST /v1/widgets/cg%2Ffoo-python/publish → 404.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cartograph import cli


def _write_widget(dirpath: str, widget_id: str = "backend-retry-python") -> str:
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, "widget.json"), "w", encoding="utf-8") as f:
        json.dump({
            "meta": {"id": widget_id, "name": "Retry", "version": "1.0.0",
                     "domain": "backend", "tags": ["retry"]},
            "description": "test widget",
            "tech_stack": {"language": "python", "dependencies": []},
        }, f)
    src = os.path.join(dirpath, "src")
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, "retry.py"), "w", encoding="utf-8") as f:
        f.write("def retry(x):\n    return x\n")
    return dirpath


def test_directory_positional_uses_manifest_id_not_path(tmp_path, monkeypatch):
    """``cloud publish cg/...`` must push the meta id, not the path string."""
    widget_dir = _write_widget(str(tmp_path / "cg" / "backend_retry_python"))
    monkeypatch.chdir(tmp_path)
    rel = os.path.join("cg", "backend_retry_python")

    pushed = {}

    def fake_push(path, widget_id, **kwargs):
        pushed["path"] = path
        pushed["widget_id"] = widget_id
        return {
            "namespaced_id": f"@me/{widget_id}",
            "version": "1.0.0",
            "visibility": "public",
        }

    args = SimpleNamespace(
        lib=False,
        widget_id=rel,
        path=".",
        visibility=None,
        governance=None,
        override_warnings=False,
        override_reason=None,
    )

    with patch("cartograph.auth.is_authenticated", return_value=True), \
         patch("cartograph.cli._preflight_from_path"), \
         patch("cartograph.validation_stamp.is_stamp_valid", return_value=True), \
         patch("cartograph.languages.get_engine", return_value=MagicMock()), \
         patch("cartograph.contamination.scan_contamination",
               return_value={"blocks": [], "warnings": []}), \
         patch("cartograph.cloud.push", side_effect=fake_push), \
         patch("cartograph.config.load_config", return_value={
             "publish": {"visibility": "public", "governance": "open"},
         }), \
         patch("cartograph.cli._read_source_meta", return_value=None), \
         patch("cartograph.cli._config_publish_registry_url", return_value=None):
        cli.cmd_cloud_publish(args)

    assert pushed["widget_id"] == "backend-retry-python"
    assert not str(pushed["widget_id"]).startswith("cg/")
    assert os.path.isfile(os.path.join(pushed["path"], "widget.json"))


def test_resolve_module_ref_dir_and_lib(tmp_path, monkeypatch):
    d = _write_widget(str(tmp_path / "cg" / "backend_retry_python"))
    monkeypatch.chdir(tmp_path)
    r = cli._resolve_module_ref("cg/backend_retry_python", path=".", lib=False)
    assert r.ok and r.id == "backend-retry-python" and r.via == "dir"

    with patch.object(cli, "_library_lookup",
                      return_value=(d, "widget")):
        r2 = cli._resolve_module_ref(
            "backend-retry-python", path=".", lib=True)
    assert r2.ok and r2.via == "lib"


def test_resolve_installed_accepts_id_and_path(tmp_path, monkeypatch):
    d = _write_widget(str(tmp_path / "cg" / "backend_retry_python"))
    monkeypatch.chdir(tmp_path)
    wid, target, name = cli._resolve_installed_widget(
        "backend-retry-python", str(tmp_path))
    assert wid == "backend-retry-python"
    assert target == str(tmp_path)
    assert name == "backend_retry_python"

    wid2, target2, _ = cli._resolve_installed_widget(
        "cg/backend_retry_python", str(tmp_path))
    assert wid2 == "backend-retry-python"
    assert target2 == str(tmp_path)
    assert d  # path exists
