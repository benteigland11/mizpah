"""
Tests for the checkin workflow: install → edit → push back to library.

Each test that modifies the library fixture uses a fresh tmp_path copy
so the session-scoped carto fixture is not polluted.
"""
import json
import os
import shutil
from unittest.mock import patch
import pytest


@pytest.fixture
def tmp_library(fixture_library, tmp_path):
    """A writable copy of the fixture Widget_Library."""
    dst = tmp_path / "Widget_Library"
    shutil.copytree(fixture_library, dst)
    return str(dst)


@pytest.fixture
def carto_tmp(tmp_library):
    """A Cartograph instance pointed at the writable library copy."""
    from cartograph import Cartograph
    return Cartograph(library_path=tmp_library)


@pytest.fixture
def installed_widget(carto_tmp, tmp_path):
    """Install http-client into tmp_path and return the installed dir."""
    install_dir = str(tmp_path / "myproject")
    result = carto_tmp.install("http-client", target_dir=install_dir)
    if result.get("status") != "success":
        pytest.fail(f"Fixture setup: install failed - {result}")
    return result["installed_at"]


@pytest.fixture
def modified_widget(installed_widget):
    """Install http-client and make a real source change so the hash differs.
    Uses a comment-only change to avoid triggering contamination warnings."""
    src = os.path.join(installed_widget, "src")
    py_files = [f for f in os.listdir(src) if f.endswith(".py") and not f.startswith("__")]
    target = os.path.join(src, py_files[0])
    with open(target, "a") as f:
        f.write("\n# improved error handling\n")
    return installed_widget


# ---------------------------------------------------------------------------
# Basic checkin
# ---------------------------------------------------------------------------

def test_checkin_clean_widget(carto_tmp, modified_widget):
    result = carto_tmp.checkin(modified_widget, reason="Added retry logic")
    assert result["status"] == "success"
    assert result["action"] == "updated"
    assert result["version"] > "1.2.0"   # bumped from fixture version


def test_checkin_bumps_version_minor(carto_tmp, modified_widget):
    result = carto_tmp.checkin(modified_widget, reason="Minor fix", version_bump="minor")
    assert result["status"] == "success"
    # 1.2.0 → 1.3.0
    assert result["version"] == "1.3.0"


def test_checkin_bumps_version_patch(carto_tmp, modified_widget):
    result = carto_tmp.checkin(modified_widget, reason="Patch", version_bump="patch")
    assert result["version"] == "1.2.1"


def test_checkin_bumps_version_major(carto_tmp, modified_widget):
    result = carto_tmp.checkin(modified_widget, reason="Breaking change", version_bump="major")
    assert result["version"] == "2.0.0"


def test_checkin_leaves_source_intact(carto_tmp, modified_widget):
    result = carto_tmp.checkin(modified_widget, reason="Test")
    assert result["status"] == "success", f"Checkin failed: {result}"
    assert os.path.isdir(modified_widget), "Source dir must be left in place after checkin"
    assert os.path.exists(os.path.join(modified_widget, "widget.json"))


def test_checkin_archives_old_version(carto_tmp, modified_widget):
    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    old_version = widget["version"]
    carto_tmp.checkin(modified_widget, reason="Update")
    history = os.path.join(widget["path"], "history", old_version)
    assert os.path.isdir(history), f"Expected history archive at {history}"


def test_checkin_writes_changelog(carto_tmp, modified_widget):
    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    carto_tmp.checkin(modified_widget, reason="Fixed timeout")
    changelog_path = os.path.join(widget["path"], "changelog.json")
    assert os.path.exists(changelog_path)
    with open(changelog_path) as f:
        log = json.load(f)
    assert log[0]["reason"] == "Fixed timeout"


def test_checkin_blocks_identical_content(carto_tmp, installed_widget):
    """Checking in with no changes must fail - prevents silent version inflation."""
    result = carto_tmp.checkin(installed_widget, reason="no changes")
    assert result["status"] == "error"
    assert "identical content" in result["message"]


def test_checkin_allows_example_only_change(carto_tmp, installed_widget):
    """Updating only examples/ is a legitimate checkin - not a no-op."""
    example_dir = os.path.join(installed_widget, "examples")
    os.makedirs(example_dir, exist_ok=True)
    with open(os.path.join(example_dir, "example_usage.py"), "a") as f:
        f.write("\n# improved example\nprint('better demo')\n")
    result = carto_tmp.checkin(installed_widget, reason="Improved example")
    assert result["status"] == "success"


def test_checkin_missing_path_errors(carto_tmp):
    result = carto_tmp.checkin("/nonexistent/path", reason="Test")
    assert result["status"] == "error"


def test_checkin_no_widget_json_errors(carto_tmp, tmp_path):
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    result = carto_tmp.checkin(empty, reason="Test")
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Contamination: hard blocks
# ---------------------------------------------------------------------------

def test_checkin_blocks_absolute_path(carto_tmp, installed_widget):
    src = os.path.join(installed_widget, "src")
    py_files = [f for f in os.listdir(src) if f.endswith(".py")]
    target = os.path.join(src, py_files[0])
    with open(target, "a") as f:
        f.write('\nLOG_DIR = "/home/user/logs/myapp"\n')
    result = carto_tmp.checkin(installed_widget, reason="Test")
    assert result["status"] == "error"
    assert "blocks" in result
    assert any("Absolute path" in b for b in result["blocks"])


def test_checkin_blocks_credential(carto_tmp, installed_widget):
    src = os.path.join(installed_widget, "src")
    py_files = [f for f in os.listdir(src) if f.endswith(".py")]
    target = os.path.join(src, py_files[0])
    with open(target, "a") as f:
        f.write('\napi_key = "sk-abc123verylongkey"\n')
    result = carto_tmp.checkin(installed_widget, reason="Test")
    assert result["status"] == "error"
    assert any("credential" in b.lower() for b in result["blocks"])


# ---------------------------------------------------------------------------
# Contamination: warnings + override
# ---------------------------------------------------------------------------

def _add_getenv(installed_widget):
    """Append a valid os.getenv call to the main src file."""
    src = os.path.join(installed_widget, "src")
    py_files = [f for f in os.listdir(src) if f.endswith(".py") and not f.startswith("__")]
    target = os.path.join(src, py_files[0])
    with open(target, "a") as f:
        f.write('\nimport os\n_TIMEOUT = int(os.getenv("TIMEOUT", "30"))\n')


def test_checkin_warns_on_os_getenv(carto_tmp, installed_widget):
    _add_getenv(installed_widget)
    result = carto_tmp.checkin(installed_widget, reason="Test")
    assert result["status"] == "warnings"
    assert any("getenv" in w for w in result["warnings"])


def test_checkin_override_warnings_requires_reason(carto_tmp, installed_widget):
    _add_getenv(installed_widget)
    result = carto_tmp.checkin(installed_widget, reason="Test",
                               override_warnings=True, override_reason="")
    assert result["status"] == "error"


def test_checkin_override_warnings_with_reason_succeeds(carto_tmp, installed_widget):
    _add_getenv(installed_widget)
    result = carto_tmp.checkin(
        installed_widget, reason="Configurable timeout",
        override_warnings=True,
        override_reason="os.getenv used for optional timeout, not project-specific",
    )
    assert result["status"] == "success"
    assert "override_reason" in result


def test_checkin_override_reason_in_changelog(carto_tmp, installed_widget):
    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    _add_getenv(installed_widget)
    carto_tmp.checkin(
        installed_widget, reason="Configurable timeout",
        override_warnings=True,
        override_reason="optional env var, safe",
    )
    with open(os.path.join(widget["path"], "changelog.json")) as f:
        log = json.load(f)
    assert log[0].get("override_reason") == "optional env var, safe"


# ---------------------------------------------------------------------------
# library_notes stamped at create and restored on checkin
# ---------------------------------------------------------------------------

def test_create_stamps_library_notes(carto_tmp, tmp_path):
    target = str(tmp_path)
    result = carto_tmp.create("new-widget", language="python", name="New Widget",
                               domain="backend", tags=[], target_dir=target)
    assert result["status"] == "success"
    with open(os.path.join(result["path"], "widget.json")) as f:
        data = json.load(f)
    notes = data.get("library_notes", {})
    assert notes.get("general"), "general notes should be stamped"
    assert notes.get("language"), "language notes should be stamped"
    assert "pytest" in notes["language"]


def test_checkin_restores_library_notes_if_edited(carto_tmp, modified_widget):
    # Agent tampers with library_notes in the installed copy
    manifest_path = os.path.join(modified_widget, "widget.json")
    with open(manifest_path) as f:
        data = json.load(f)
    data["library_notes"] = {"general": "do whatever", "language": "anything goes"}
    with open(manifest_path, "w") as f:
        json.dump(data, f)

    carto_tmp.checkin(modified_widget, reason="Tampered notes test")

    # Library copy should have canonical notes, not the tampered ones
    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    with open(os.path.join(widget["path"], "widget.json")) as f:
        lib_data = json.load(f)
    notes = lib_data.get("library_notes", {})
    assert notes.get("general") != "do whatever"
    assert "pytest" in notes.get("language", "")


def test_checkin_fails_if_library_notes_restore_fails(carto_tmp, modified_widget):
    with patch("cartograph.checkin._canonical_library_notes", side_effect=RuntimeError("boom")):
        result = carto_tmp.checkin(modified_widget, reason="library notes strictness")
    assert result["status"] == "error"
    assert "boom" in result["message"].lower() or "invalid" in result["message"].lower()


# ---------------------------------------------------------------------------
# Validation version stamped into widget.json
# ---------------------------------------------------------------------------

def test_checkin_stamps_validation_block(carto_tmp, modified_widget):
    result = carto_tmp.checkin(modified_widget, reason="Version stamp test")
    assert result["status"] == "success"

    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    with open(os.path.join(widget["path"], "widget.json")) as f:
        data = json.load(f)

    assert "validation" in data
    v = data["validation"]
    assert "engine_version" in v
    assert "validated_at" in v
    assert isinstance(v["engine_version"], int)
    assert v["engine_version"] >= 1


def test_checkin_stamps_correct_engine_version(carto_tmp, modified_widget):
    from cartograph.languages.python import PythonEngine
    result = carto_tmp.checkin(modified_widget, reason="Engine version check")
    assert result["status"] == "success"

    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    with open(os.path.join(widget["path"], "widget.json")) as f:
        data = json.load(f)

    assert data["validation"]["engine_version"] == PythonEngine.validation_version


def test_checkin_stamps_runtime_version(carto_tmp, modified_widget):
    import sys
    result = carto_tmp.checkin(modified_widget, reason="Runtime stamp test")
    assert result["status"] == "success"

    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    with open(os.path.join(widget["path"], "widget.json")) as f:
        data = json.load(f)

    v = data["validation"]
    assert "runtime" in v
    assert v["runtime"].startswith("python ")
    # Should match the running interpreter
    expected = f"python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    assert v["runtime"] == expected


# ---------------------------------------------------------------------------
# Rollback (restore)
# ---------------------------------------------------------------------------

def test_rollback_restores_old_version(carto_tmp, installed_widget):
    """Rollback should restore a previous version's source as a new patch release."""
    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    old_version = widget["version"]

    # Checkin to create a history entry
    src_file = os.path.join(installed_widget, "src", "http_client.py")
    with open(src_file, "a") as f:
        f.write("\n# v2 change\n")
    result = carto_tmp.checkin(installed_widget, reason="v2 change")
    assert result["status"] == "success"
    new_version = result["version"]

    # Rollback to old version
    from cartograph.checkin import restore
    result = restore(carto_tmp, "http-client", old_version, "reverting v2")
    assert result["status"] == "success"
    assert result["action"] == "updated"
    # Restore does a patch bump from the current library version (1.3.0 -> 1.3.1)
    assert result["version"] == "1.3.1"

    # Source should NOT contain the v2 change
    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    with open(os.path.join(widget["path"], "src", "http_client.py")) as f:
        content = f.read()
    assert "# v2 change" not in content


def test_rollback_records_reason_in_changelog(carto_tmp, modified_widget):
    """Rollback reason should appear in the changelog."""
    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    old_version = widget["version"]

    carto_tmp.checkin(modified_widget, reason="setup")

    from cartograph.checkin import restore
    restore(carto_tmp, "http-client", old_version, "broke prod")

    widget = next(w for w in carto_tmp.widgets if w["id"] == "http-client")
    with open(os.path.join(widget["path"], "changelog.json")) as f:
        log = json.load(f)
    assert any("RESTORE" in entry["reason"] and "broke prod" in entry["reason"]
               for entry in log)


def test_rollback_unknown_widget_errors(carto_tmp):
    from cartograph.checkin import restore
    result = restore(carto_tmp, "nonexistent-widget", "1.0.0", "test")
    assert result["status"] == "error"
    assert "not found" in result["message"].lower()


def test_rollback_unknown_version_errors(carto_tmp, installed_widget):
    from cartograph.checkin import restore
    result = restore(carto_tmp, "http-client", "99.99.99", "test")
    assert result["status"] == "error"
    assert "not found" in result["message"].lower()


# ---------------------------------------------------------------------------
# Cloud baseline (sidecar present) — top-3 priority gap #2
# ---------------------------------------------------------------------------

def _plant_sidecar(install_path, owner="alice",
                   registry_url="https://api.cartograph.tools"):
    with open(os.path.join(install_path, ".cartograph_source"), "w") as f:
        json.dump({"owner": owner, "registry_url": registry_url}, f)


def test_checkin_cloud_version_conflict(carto_tmp, modified_widget):
    """Sidecar present + cloud says v1.5.0 + local manifest at v1.2.0 (local
    BEHIND) must error with the cloud version and direction-aware guidance to
    `cartograph upgrade` — proves the cloud baseline was consulted and the
    message names the right rebase command, not `install`."""
    _plant_sidecar(modified_widget)
    with patch("cartograph.cloud.is_available", return_value=True), \
         patch("cartograph.cloud.inspect", return_value={
             "version": "1.5.0",
         }):
        result = carto_tmp.checkin(modified_widget, reason="x")
    assert result["status"] == "error"
    assert "v1.5.0" in result["message"]
    assert "v1.2.0" in result["message"]
    assert "cartograph upgrade" in result["message"]
    # Must not steer the agent to `install` on an already-installed widget.
    assert "install" not in result["message"].lower()


def _set_local_version(widget_dir, version):
    manifest_path = os.path.join(widget_dir, "widget.json")
    with open(manifest_path) as f:
        data = json.load(f)
    data["meta"]["version"] = version
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2)


def test_checkin_manual_bump_passes_when_it_matches(carto_tmp, modified_widget):
    """Library baseline is v1.2.0. User hand-bumped meta.version to v1.3.0 AND
    asked for --bump minor (1.2.0 -> 1.3.0). The manual edit lines up with the
    intent, so checkin passes through and publishes v1.3.0 - not a double-bump
    to v1.4.0."""
    _set_local_version(modified_widget, "1.3.0")
    result = carto_tmp.checkin(modified_widget, reason="x", version_bump="minor")
    assert result["status"] == "success", result
    assert result["version"] == "1.3.0"


def test_checkin_manual_bump_mismatch_suggests_right_command(carto_tmp, modified_widget):
    """Baseline v1.2.0, manual v1.3.0 (a minor bump) but the user ran --bump
    patch (expects v1.2.1). Mismatch must reject AND tell them their edit is a
    minor bump -> re-run with --bump minor."""
    _set_local_version(modified_widget, "1.3.0")
    result = carto_tmp.checkin(modified_widget, reason="x", version_bump="patch")
    assert result["status"] == "error"
    assert "1.3.0" in result["message"]
    assert "1.2.0" in result["message"]
    assert "--bump minor" in result["message"]
    # Wrong-direction advice must not leak in from the behind/equal cases.
    assert "install" not in result["message"].lower()
    assert "upgrade" not in result["message"].lower()


def test_checkin_manual_version_not_a_clean_bump(carto_tmp, modified_widget):
    """Baseline v1.2.0, manual v1.9.0 is not a clean patch/minor/major bump of
    the baseline under any --bump level. Reject with guidance to reset
    meta.version and let --bump set it."""
    _set_local_version(modified_widget, "1.9.0")
    result = carto_tmp.checkin(modified_widget, reason="x")
    assert result["status"] == "error"
    assert "1.9.0" in result["message"]
    assert "reset meta.version" in result["message"].lower()


def test_checkin_cloud_baseline_overrides_library(carto_tmp, installed_widget):
    """Library says v1.2.0, cloud says v2.0.0 — bumping must use the cloud
    baseline. Without sidecar precedence, this would error with a library
    version conflict."""
    # Bump local manifest to match cloud version so version-conflict passes.
    manifest_path = os.path.join(installed_widget, "widget.json")
    with open(manifest_path) as f:
        data = json.load(f)
    data["meta"]["version"] = "2.0.0"
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2)
    # Make a real change so the no-op guard doesn't fire.
    src = os.path.join(installed_widget, "src")
    py = [f for f in os.listdir(src) if f.endswith(".py") and not f.startswith("__")][0]
    with open(os.path.join(src, py), "a") as f:
        f.write("\n# cloud-baseline test\n")

    _plant_sidecar(installed_widget)
    with patch("cartograph.cloud.is_available", return_value=True), \
         patch("cartograph.cloud.inspect", return_value={
             "version": "2.0.0",
         }):
        result = carto_tmp.checkin(installed_widget, reason="x", version_bump="patch")
    assert result["status"] == "success", result
    assert result["version"] == "2.0.1"


def test_checkin_cloud_noop_guard_via_manifest(carto_tmp, installed_widget):
    """Cloud returns matching implementation_hash + manifest; checkin must
    block as a no-op. Proves the cloud-manifest path of the no-op guard works."""
    impl_hash = carto_tmp._calculate_implementation_hash(installed_widget)
    with open(os.path.join(installed_widget, "widget.json")) as f:
        local_manifest = json.load(f)

    _plant_sidecar(installed_widget)
    with patch("cartograph.cloud.is_available", return_value=True), \
         patch("cartograph.cloud.inspect", return_value={
             "version": local_manifest["meta"]["version"],
             "implementation_hash": impl_hash,
             "manifest": local_manifest,
         }):
        result = carto_tmp.checkin(installed_widget, reason="no changes")
    assert result["status"] == "error"
    assert "identical content" in result["message"]
    assert "cloud" in result["message"]


def test_checkin_cloud_unavailable_falls_back_to_library(carto_tmp, modified_widget):
    """Sidecar exists but cloud is offline — must fall back to library
    baseline rather than error or skip the check."""
    _plant_sidecar(modified_widget)
    with patch("cartograph.cloud.is_available", return_value=False):
        result = carto_tmp.checkin(modified_widget, reason="real change")
    # Library v1.2.0 + minor bump → 1.3.0
    assert result["status"] == "success", result
    assert result["version"] == "1.3.0"


def test_checkin_cloud_no_hash_skips_noop_guard(carto_tmp, installed_widget):
    """Cloud baseline without implementation_hash must NOT false-positive on
    no-op (older registry that doesn't echo the hash). Identical content is
    allowed through and bumps normally."""
    _plant_sidecar(installed_widget)
    with open(os.path.join(installed_widget, "widget.json")) as f:
        local_version = json.load(f)["meta"]["version"]
    with patch("cartograph.cloud.is_available", return_value=True), \
         patch("cartograph.cloud.inspect", return_value={
             "version": local_version,
             # no implementation_hash, no manifest
         }):
        result = carto_tmp.checkin(installed_widget, reason="metadata only")
    assert result["status"] == "success", result


# ---------------------------------------------------------------------------
# Per-item skip set: sidecar / history / changelog must not bleed into library
# ---------------------------------------------------------------------------

def test_checkin_does_not_copy_sidecar_to_library(carto_tmp, modified_widget):
    """`.cartograph_source` is install-local provenance and must never end up
    in the library copy — leaking it would attribute the library entry to a
    cloud owner that doesn't own it."""
    _plant_sidecar(modified_widget)
    result = carto_tmp.checkin(modified_widget, reason="x")
    assert result["status"] == "success", result
    assert not os.path.isfile(
        os.path.join(carto_tmp.library_path, "http-client", ".cartograph_source"))


def test_checkin_does_not_recursively_archive_history(carto_tmp, modified_widget):
    """Updating an existing widget archives the previous version under
    history/<old>. That archive must NOT itself contain a history/ dir
    (would compound on every checkin) or the changelog/stamp/sidecar files."""
    # First checkin → creates history/1.2.0/
    r1 = carto_tmp.checkin(modified_widget, reason="bump 1")
    assert r1["status"] == "success"
    # Second real change → creates history/1.3.0/, archiving the now-
    # current state which includes history/1.2.0/.
    src = os.path.join(modified_widget, "src")
    py = [f for f in os.listdir(src) if f.endswith(".py") and not f.startswith("__")][0]
    with open(os.path.join(src, py), "a") as f:
        f.write("\n# second pass\n")
    # Sync local manifest version to the just-bumped library version so the
    # version-conflict check passes.
    mp = os.path.join(modified_widget, "widget.json")
    with open(mp) as f:
        d = json.load(f)
    d["meta"]["version"] = r1["version"]
    with open(mp, "w") as f:
        json.dump(d, f, indent=2)

    r2 = carto_tmp.checkin(modified_widget, reason="bump 2")
    assert r2["status"] == "success", r2

    archive = os.path.join(carto_tmp.library_path, "http-client",
                           "history", r1["version"])
    assert os.path.isdir(archive)
    assert not os.path.isdir(os.path.join(archive, "history"))
    assert not os.path.isfile(os.path.join(archive, "changelog.json"))
    assert not os.path.isfile(os.path.join(archive, ".cartograph_source"))
    assert not os.path.isfile(os.path.join(archive, ".validation_stamp.json"))


# ---------------------------------------------------------------------------
# Fresh-checkin (new widget registration) — top-3 priority gap #3
# ---------------------------------------------------------------------------

def _retarget_install_as_new_widget(install_path, new_id):
    """Take an installed (valid) widget dir and rebrand its widget.json with a
    new, library-unknown id. Adds a comment to src so the implementation hash
    differs from the source widget (validator blocks duplicate-content adds)."""
    manifest_path = os.path.join(install_path, "widget.json")
    with open(manifest_path) as f:
        data = json.load(f)
    data["meta"]["id"] = new_id
    data["meta"]["version"] = "1.0.0"
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2)
    # Drop the stamp; id changed, so the prior stamp is no longer valid.
    stamp = os.path.join(install_path, ".validation_stamp.json")
    if os.path.isfile(stamp):
        os.remove(stamp)
    # Differentiate from the source widget so the duplicate-content guard
    # in the validator doesn't block this fresh registration.
    src = os.path.join(install_path, "src")
    py = [f for f in os.listdir(src) if f.endswith(".py") and not f.startswith("__")][0]
    with open(os.path.join(src, py), "a") as f:
        f.write(f"\n# distinct content for {new_id}\n")


def test_checkin_registers_new_widget(carto_tmp, installed_widget):
    """First-time checkin of a widget id that's not in the library must
    succeed, return action='registered', and create a library entry."""
    new_id = "backend-brand-new-thing-python"
    _retarget_install_as_new_widget(installed_widget, new_id)

    result = carto_tmp.checkin(installed_widget, reason="initial")
    assert result["status"] == "success", result
    assert result["action"] == "registered"
    assert result["id"] == new_id
    # Initial release: no bump from baseline (no baseline).
    assert result["version"] == "1.0.0"
    # Library now has the widget.
    assert os.path.isdir(os.path.join(carto_tmp.library_path, new_id))
    assert any(w["id"] == new_id for w in carto_tmp.widgets)


def test_checkin_new_widget_initial_changelog(carto_tmp, installed_widget):
    """Fresh checkin without an explicit reason must record 'Initial release'
    in the changelog (vs 'No reason provided' on updates)."""
    new_id = "backend-changelog-debut-python"
    _retarget_install_as_new_widget(installed_widget, new_id)

    result = carto_tmp.checkin(installed_widget, reason="")
    assert result["status"] == "success", result
    changelog_path = os.path.join(carto_tmp.library_path, new_id, "changelog.json")
    with open(changelog_path) as f:
        log_entries = json.load(f)
    assert log_entries[0]["reason"] == "Initial release"
    assert log_entries[0]["version"] == "1.0.0"


def test_checkin_new_widget_no_history_archive(carto_tmp, installed_widget):
    """First-time checkin must NOT create a history/ dir (nothing to archive)."""
    new_id = "backend-no-history-yet-python"
    _retarget_install_as_new_widget(installed_widget, new_id)

    result = carto_tmp.checkin(installed_widget, reason="initial")
    assert result["status"] == "success", result
    history_dir = os.path.join(carto_tmp.library_path, new_id, "history")
    assert not os.path.isdir(history_dir), "Fresh checkin should not archive history"


def test_checkin_new_widget_directory_collision_errors(carto_tmp, installed_widget, tmp_path):
    """If a directory with the new widget's id already exists in the library
    but isn't tracked in the index, checkin must refuse rather than silently
    overwrite. Guards against a partial-writeback footgun."""
    new_id = "backend-collision-test-python"
    # Plant an unrelated dir at the would-be library path.
    stale_dir = os.path.join(carto_tmp.library_path, new_id)
    os.makedirs(stale_dir)
    with open(os.path.join(stale_dir, "stale.txt"), "w") as f:
        f.write("not a widget")

    _retarget_install_as_new_widget(installed_widget, new_id)
    result = carto_tmp.checkin(installed_widget, reason="initial")
    assert result["status"] == "error"
    assert "directory already exists" in result["message"].lower()
    # Stale file untouched.
    assert os.path.isfile(os.path.join(stale_dir, "stale.txt"))


def test_checkin_new_widget_writes_stamp(carto_tmp, installed_widget):
    """Fresh registration must leave a validation stamp at the library copy
    so subsequent re-validation can short-circuit."""
    new_id = "backend-stamp-check-python"
    _retarget_install_as_new_widget(installed_widget, new_id)

    result = carto_tmp.checkin(installed_widget, reason="initial")
    assert result["status"] == "success", result
    stamp = os.path.join(carto_tmp.library_path, new_id, ".validation_stamp.json")
    assert os.path.isfile(stamp), "Library copy missing validation stamp after fresh checkin"


def test_checkin_malformed_version_errors(carto_tmp, modified_widget):
    """meta.version that can't be parsed by packaging.Version must error
    clearly rather than crash during bump. Routed via cloud baseline so the
    version-conflict check passes (matched) and we reach the bump logic."""
    manifest_path = os.path.join(modified_widget, "widget.json")
    with open(manifest_path) as f:
        data = json.load(f)
    data["meta"]["version"] = "not-a-real-version"
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2)
    _plant_sidecar(modified_widget)
    with patch("cartograph.cloud.is_available", return_value=True), \
         patch("cartograph.cloud.inspect", return_value={
             "version": "not-a-real-version",
         }):
        result = carto_tmp.checkin(modified_widget, reason="x")
    assert result["status"] == "error"
    assert "malformed version" in result["message"].lower()


# ---------------------------------------------------------------------------
# OS metadata (macOS Finder droppings) must never count as widget content
# ---------------------------------------------------------------------------

class TestOsMetadataIgnored:
    def _make_widget_dirs(self, tmp_path):
        src = tmp_path / "w" / "src"
        src.mkdir(parents=True)
        (src / "module.py").write_text("def f():\n    return 1\n")
        return tmp_path / "w"

    def test_hash_ignores_ds_store_and_appledouble(self, tmp_path):
        from cartograph.engine import calculate_implementation_hash
        w = self._make_widget_dirs(tmp_path)
        clean = calculate_implementation_hash(str(w))
        (w / "src" / ".DS_Store").write_bytes(b"finder junk")
        (w / "src" / "._module.py").write_bytes(b"\x00\x05\x16\x07fork")
        (w / "src" / "__MACOSX").mkdir()
        (w / "src" / "__MACOSX" / "extra").write_bytes(b"zip junk")
        assert calculate_implementation_hash(str(w)) == clean

    def test_hash_still_sees_real_changes(self, tmp_path):
        from cartograph.engine import calculate_implementation_hash
        w = self._make_widget_dirs(tmp_path)
        clean = calculate_implementation_hash(str(w))
        (w / "src" / "module.py").write_text("def f():\n    return 2\n")
        assert calculate_implementation_hash(str(w)) != clean

    def test_checkin_copy_excludes_os_metadata(self):
        import fnmatch
        from cartograph.checkin import _os_metadata_patterns
        patterns = _os_metadata_patterns()
        for junk in (".DS_Store", "._module.py", "__MACOSX", "Thumbs.db"):
            assert any(fnmatch.fnmatch(junk, p) for p in patterns), junk
        assert not any(fnmatch.fnmatch("module.py", p) for p in patterns)
