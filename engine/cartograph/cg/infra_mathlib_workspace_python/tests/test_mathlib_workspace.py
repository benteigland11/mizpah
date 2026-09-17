import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.mathlib_workspace import (
    STAMP_FILENAME,
    STATE_CORRUPT,
    STATE_MISSING,
    STATE_READY,
    STATE_STALE,
    provision,
    provision_steps,
    seed_manifest,
    status,
    workspace_path,
    write_workspace_files,
)

PIN = "v4.32.0"
TOOLCHAIN = "leanprover/lean4:v4.32.0"


def ok_runner(calls):
    def run(args, cwd):
        calls.append((list(args), cwd))
        return 0
    return run


def test_workspace_path_is_pin_segment(tmp_path):
    assert workspace_path(tmp_path, PIN) == tmp_path / PIN


@pytest.mark.parametrize("bad", ["", "a/b", "a\\b", ".", "..", " v1 "])
def test_workspace_path_rejects_bad_pins(tmp_path, bad):
    with pytest.raises(ValueError):
        workspace_path(tmp_path, bad)


def test_status_missing(tmp_path):
    assert status(tmp_path, PIN).state == STATE_MISSING


def test_status_corrupt_without_stamp(tmp_path):
    (tmp_path / PIN).mkdir()
    result = status(tmp_path, PIN)
    assert result.state == STATE_CORRUPT
    assert not result.ready


def test_status_corrupt_on_bad_json(tmp_path):
    ws = tmp_path / PIN
    ws.mkdir()
    (ws / STAMP_FILENAME).write_text("{not json", encoding="utf-8")
    assert status(tmp_path, PIN).state == STATE_CORRUPT


def test_status_stale_on_pin_mismatch(tmp_path):
    ws = tmp_path / PIN
    ws.mkdir()
    (ws / STAMP_FILENAME).write_text(
        json.dumps({"pin": "v4.31.0", "provisioned": True}), encoding="utf-8")
    assert status(tmp_path, PIN).state == STATE_STALE


def test_status_stale_on_toolchain_mismatch(tmp_path):
    ws = tmp_path / PIN
    ws.mkdir()
    (ws / STAMP_FILENAME).write_text(
        json.dumps({"pin": PIN, "toolchain": "other", "provisioned": True}),
        encoding="utf-8")
    assert status(tmp_path, PIN, TOOLCHAIN).state == STATE_STALE
    # No toolchain requested: stamped value is not checked.
    assert status(tmp_path, PIN).state == STATE_READY


def test_status_corrupt_when_not_provisioned(tmp_path):
    ws = tmp_path / PIN
    ws.mkdir()
    (ws / STAMP_FILENAME).write_text(
        json.dumps({"pin": PIN, "provisioned": False}), encoding="utf-8")
    assert status(tmp_path, PIN).state == STATE_CORRUPT


def test_write_workspace_files(tmp_path):
    ws = tmp_path / PIN
    write_workspace_files(ws, PIN, TOOLCHAIN, "workspace")
    lakefile = (ws / "lakefile.toml").read_text(encoding="utf-8")
    assert f'rev = "{PIN}"' in lakefile
    assert 'name = "mathlib"' in lakefile
    assert (ws / "lean-toolchain").read_text(
        encoding="utf-8").strip() == TOOLCHAIN
    assert (ws / "Workspace.lean").is_file()


def test_write_workspace_files_rejects_bad_project_name(tmp_path):
    with pytest.raises(ValueError):
        write_workspace_files(tmp_path / PIN, PIN, TOOLCHAIN, "1bad name")


def test_provision_steps_order():
    steps = [s.args for s in provision_steps("workspace")]
    assert steps == [["lake", "update", "mathlib"],
                     ["lake", "exe", "cache", "get"],
                     ["lake", "build", "workspace"]]


def test_provision_success_stamps_and_is_ready(tmp_path):
    calls = []
    result = provision(tmp_path, PIN, TOOLCHAIN, ok_runner(calls))
    assert result.status.state == STATE_READY
    assert result.status.ready
    assert result.failed_step is None
    assert len(calls) == 3
    assert all(cwd == str(tmp_path / PIN) for _, cwd in calls)
    assert status(tmp_path, PIN, TOOLCHAIN).state == STATE_READY


def test_provision_failure_leaves_corrupt_no_stamp(tmp_path):
    def failing(args, cwd):
        return 1 if args[:2] == ["lake", "exe"] else 0

    result = provision(tmp_path, PIN, TOOLCHAIN, failing)
    assert result.status.state == STATE_CORRUPT
    assert result.returncode == 1
    assert result.failed_step is not None
    assert result.failed_step.args == ["lake", "exe", "cache", "get"]
    assert len(result.steps_run) == 2
    assert not (tmp_path / PIN / STAMP_FILENAME).exists()
    assert status(tmp_path, PIN).state == STATE_CORRUPT


def test_reprovision_after_failure_recovers(tmp_path):
    provision(tmp_path, PIN, TOOLCHAIN, lambda a, c: 1)
    calls = []
    result = provision(tmp_path, PIN, TOOLCHAIN, ok_runner(calls))
    assert result.status.ready


WORKSPACE_MANIFEST = json.dumps({
    "version": "1.2.0",
    "packagesDir": ".lake/packages",
    "packages": [
        {"url": "https://example.org/helper-lib", "type": "git",
         "rev": "abc123", "name": "helper_lib",
         "manifestFile": "lake-manifest.json", "inherited": True,
         "configFile": "lakefile.toml"},
        {"url": "https://example.org/mathlib", "type": "git",
         "rev": "def456", "name": "mathlib",
         "manifestFile": "lake-manifest.json", "inherited": False,
         "configFile": "lakefile.lean"},
    ],
})


def test_seed_manifest_rewrites_mathlib_to_path():
    plan = seed_manifest(WORKSPACE_MANIFEST, "/data/ws/pkg/mathlib")
    result = json.loads(plan.manifest_text)
    entries = {e["name"]: e for e in result["packages"]}
    assert entries["mathlib"]["type"] == "path"
    assert entries["mathlib"]["dir"] == "/data/ws/pkg/mathlib"
    assert entries["mathlib"]["configFile"] == "lakefile.lean"
    assert entries["mathlib"]["inherited"] is False
    assert "url" not in entries["mathlib"]
    # transitive deps keep their exact pinned revisions
    assert entries["helper_lib"]["rev"] == "abc123"
    assert plan.package_names == ["helper_lib"]


def test_seed_manifest_normalizes_backslashes():
    plan = seed_manifest(WORKSPACE_MANIFEST, "ws\\pkg\\mathlib")
    entries = {e["name"]: e
               for e in json.loads(plan.manifest_text)["packages"]}
    assert entries["mathlib"]["dir"] == "ws/pkg/mathlib"


def test_seed_manifest_rejects_missing_mathlib():
    bad = json.dumps({"version": "1.2.0", "packages": []})
    with pytest.raises(ValueError, match="no mathlib entry"):
        seed_manifest(bad, "/x")


def test_seed_manifest_rejects_missing_packages():
    with pytest.raises(ValueError, match="packages"):
        seed_manifest(json.dumps({"version": "1.2.0"}), "/x")


def test_two_pins_coexist(tmp_path):
    provision(tmp_path, PIN, TOOLCHAIN, lambda a, c: 0)
    provision(tmp_path, "v4.33.0", "leanprover/lean4:v4.33.0",
              lambda a, c: 0)
    assert status(tmp_path, PIN, TOOLCHAIN).ready
    assert status(tmp_path, "v4.33.0", "leanprover/lean4:v4.33.0").ready
