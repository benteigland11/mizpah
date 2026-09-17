"""
Tests for the upgrade workflow: install → new library version → upgrade.

Exercises the happy path, error branches, version pinning, and the
backup-restore safety net when a re-install fails.
"""
import json
import os
import shutil
from unittest.mock import patch
import pytest


@pytest.fixture
def tmp_library(fixture_library, tmp_path):
    """Writable copy of the fixture Widget_Library."""
    dst = tmp_path / "Widget_Library"
    shutil.copytree(fixture_library, dst)
    return str(dst)


@pytest.fixture
def carto_tmp(tmp_library):
    from cartograph import Cartograph
    return Cartograph(library_path=tmp_library)


@pytest.fixture
def installed(carto_tmp, tmp_path):
    """Install http-client v1.2.0 into a project dir and return (carto, target, install_path)."""
    target = str(tmp_path / "project")
    os.makedirs(target)
    result = carto_tmp.install("http-client", target_dir=target)
    assert result.get("status") == "success", result
    return carto_tmp, target, result["installed_at"]


def _bump_library_version(carto, widget_id, new_version):
    """Directly bump the library widget.json version and refresh in-memory index."""
    widget = next(w for w in carto.widgets if w["id"] == widget_id)
    manifest_path = os.path.join(widget["path"], "widget.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    manifest["meta"]["version"] = new_version
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    # Also copy current version into history/ so version-pin lookups resolve
    old_version = widget.get("version", "1.2.0")
    history_dir = os.path.join(widget["path"], "history", old_version)
    os.makedirs(history_dir, exist_ok=True)
    # Just copy src so history exists — the version lookup only needs the dir
    shutil.copytree(os.path.join(widget["path"], "src"),
                    os.path.join(history_dir, "src"),
                    dirs_exist_ok=True)
    # Write a minimal widget.json in history too
    hist_manifest = dict(manifest)
    hist_manifest["meta"] = dict(manifest["meta"])
    hist_manifest["meta"]["version"] = old_version
    with open(os.path.join(history_dir, "widget.json"), "w") as f:
        json.dump(hist_manifest, f, indent=2)
    # Re-stamp so the library-integrity gate admits the mutated widget
    from cartograph.languages import get_engine
    from cartograph.validation_stamp import write_stamp
    engine = get_engine("python")
    write_stamp(widget["path"], "python", engine)
    carto.reload()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_upgrade_to_latest(installed):
    carto, target, _ = installed
    _bump_library_version(carto, "http-client", "2.0.0")

    result = carto.upgrade("http-client", target_dir=target)

    assert result.get("status") == "success", result
    assert result.get("version") == "2.0.0"
    assert result.get("previous_version") == "1.2.0"


def test_upgrade_updates_installed_manifest(installed):
    carto, target, install_path = installed
    _bump_library_version(carto, "http-client", "2.0.0")

    carto.upgrade("http-client", target_dir=target)

    with open(os.path.join(install_path, "widget.json")) as f:
        manifest = json.load(f)
    assert manifest["meta"]["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# Error branches
# ---------------------------------------------------------------------------

def test_upgrade_not_installed(carto_tmp, tmp_path):
    """Upgrading a widget that was never installed should error, not crash."""
    target = str(tmp_path / "empty-project")
    os.makedirs(target)
    result = carto_tmp.upgrade("http-client", target_dir=target)
    assert "error" in result
    assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# Version pinning
# ---------------------------------------------------------------------------

def test_upgrade_to_specific_version(installed):
    """--version pins to an exact version from history."""
    carto, target, _ = installed
    _bump_library_version(carto, "http-client", "3.0.0")

    result = carto.upgrade("http-client", target_dir=target, version="1.2.0")

    assert result.get("status") == "success", result
    assert result.get("version") == "1.2.0"


def test_upgrade_unknown_version_errors(installed):
    """--version pointing at a non-existent version must error and not break the install."""
    carto, target, install_path = installed
    _bump_library_version(carto, "http-client", "2.0.0")

    result = carto.upgrade("http-client", target_dir=target, version="99.99.99")

    assert "error" in result
    # Widget should still be there (restore path) — not wiped out by the failed upgrade
    assert os.path.isdir(install_path), "failed upgrade left the widget missing"


# ---------------------------------------------------------------------------
# Backup-restore safety net
# ---------------------------------------------------------------------------

def test_upgrade_cleans_up_backup_on_success(installed):
    """A successful upgrade must remove the single-slot backup dir so it
    doesn't leak disk space across upgrades. Regression here would silently
    accumulate stale copies under <data_dir>/upgrade-backup/."""
    from cartograph.installer import _upgrade_backup_path
    carto, target, _ = installed
    _bump_library_version(carto, "http-client", "1.3.0")

    backup = _upgrade_backup_path("http-client")
    result = carto.upgrade("http-client", target_dir=target)
    assert result.get("status") == "success", result
    assert not os.path.exists(backup), \
        f"upgrade-backup dir leaked after success: {backup}"


def test_upgrade_restores_on_reinstall_failure(installed):
    """If the new install fails after the old copy is removed, upgrade must restore the backup."""
    carto, target, install_path = installed
    _bump_library_version(carto, "http-client", "2.0.0")

    # Force the re-install step to fail
    from cartograph import installer
    real_install = installer.install
    call_count = {"n": 0}

    def flaky_install(*args, **kwargs):
        call_count["n"] += 1
        return {"error": "simulated install failure"}

    with patch.object(installer, "install", side_effect=flaky_install):
        result = carto.upgrade("http-client", target_dir=target)

    assert "error" in result
    assert result.get("restored") is True
    assert result.get("restored_version") == "1.2.0"
    # Installed manifest still there
    with open(os.path.join(install_path, "widget.json")) as f:
        manifest = json.load(f)
    assert manifest["meta"]["version"] == "1.2.0"
