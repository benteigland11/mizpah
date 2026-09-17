"""Pinned big-dependency Lake workspace management for Lean 4 + Mathlib.

Owns the lifecycle of a shared, version-pinned Mathlib workspace under a
caller-supplied root directory: layout, readiness status, and provisioning.
The widget never spawns processes or touches the network itself - all
commands are executed through a caller-injected runner, and the caller is
responsible for concurrency control (locking) and atomic placement.

Layout: ``<root>/<pin>/`` holds one Lake project whose lakefile requires
Mathlib at ``pin``, with a matching ``lean-toolchain`` file and a JSON stamp
(``cartograph-workspace.json``) recording what was provisioned. A workspace
is ``ready`` only when the stamp exists, parses, and matches the requested
pin and toolchain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Union

STAMP_FILENAME = "cartograph-workspace.json"
STATE_MISSING = "missing"
STATE_CORRUPT = "corrupt"
STATE_STALE = "stale"
STATE_READY = "ready"

Runner = Callable[[Sequence[str], str], int]

_LAKEFILE_TEMPLATE = """\
name = "{project_name}"
defaultTargets = ["{project_name}"]

[[require]]
name = "mathlib"
scope = "leanprover-community"
rev = "{pin}"

[[lean_lib]]
name = "{project_name}"
"""


@dataclass(frozen=True)
class WorkspaceStatus:
    """Readiness of a pinned workspace: state plus a human-readable reason."""

    state: str
    reason: str
    path: Path

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY


@dataclass(frozen=True)
class ProvisionStep:
    """One command the caller's runner must execute inside the workspace."""

    args: List[str]
    description: str


@dataclass(frozen=True)
class ProvisionResult:
    """Outcome of provision(): final status plus the steps that ran."""

    status: WorkspaceStatus
    steps_run: List[ProvisionStep] = field(default_factory=list)
    failed_step: Optional[ProvisionStep] = None
    returncode: int = 0


def workspace_path(root: Union[str, Path], pin: str) -> Path:
    """Directory that holds (or will hold) the workspace for ``pin``."""
    _validate_pin(pin)
    return Path(root) / pin


def status(root: Union[str, Path], pin: str,
           toolchain: Optional[str] = None) -> WorkspaceStatus:
    """Classify the workspace for ``pin`` as missing/corrupt/stale/ready.

    ``toolchain``, when given, must match the stamped toolchain exactly;
    a mismatch is ``stale`` (the workspace was built for another toolchain).
    """
    path = workspace_path(root, pin)
    stamp_file = path / STAMP_FILENAME
    if not path.is_dir():
        return WorkspaceStatus(STATE_MISSING,
                               "workspace directory does not exist", path)
    if not stamp_file.is_file():
        return WorkspaceStatus(
            STATE_CORRUPT, "workspace exists but has no stamp - "
            "an interrupted provision; re-provision it", path)
    try:
        stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WorkspaceStatus(
            STATE_CORRUPT, "stamp file is unreadable or not valid JSON", path)
    if stamp.get("pin") != pin:
        return WorkspaceStatus(
            STATE_STALE,
            f"stamped pin {stamp.get('pin')!r} != requested {pin!r}", path)
    if toolchain is not None and stamp.get("toolchain") != toolchain:
        return WorkspaceStatus(
            STATE_STALE,
            f"stamped toolchain {stamp.get('toolchain')!r} != requested "
            f"{toolchain!r}", path)
    if not stamp.get("provisioned"):
        return WorkspaceStatus(
            STATE_CORRUPT, "stamp present but provision never completed", path)
    return WorkspaceStatus(STATE_READY,
                           "workspace matches pin and toolchain", path)


def provision_steps(project_name: str = "workspace") -> List[ProvisionStep]:
    """Ordered commands a provision must run inside the workspace directory."""
    _validate_identifier(project_name)
    return [
        ProvisionStep(["lake", "update", "mathlib"],
                      "resolve the pinned Mathlib requirement into the "
                      "manifest"),
        ProvisionStep(["lake", "exe", "cache", "get"],
                      "download prebuilt Mathlib binaries instead of "
                      "compiling"),
        ProvisionStep(["lake", "build", project_name],
                      "elaborate the workspace root against the cached "
                      "Mathlib"),
    ]


def write_workspace_files(path: Union[str, Path], pin: str, toolchain: str,
                          project_name: str = "workspace") -> None:
    """Write the Lake project skeleton (lakefile, toolchain pin, root
    module)."""
    _validate_pin(pin)
    _validate_identifier(project_name)
    base = Path(path)
    base.mkdir(parents=True, exist_ok=True)
    (base / "lakefile.toml").write_text(
        _LAKEFILE_TEMPLATE.format(project_name=project_name, pin=pin),
        encoding="utf-8", newline="\n")
    (base / "lean-toolchain").write_text(toolchain + "\n",
                                         encoding="utf-8", newline="\n")
    (base / f"{project_name.capitalize()}.lean").write_text(
        "import Mathlib.Tactic.Basic\n", encoding="utf-8", newline="\n")


def provision(root: Union[str, Path], pin: str, toolchain: str,
              runner: Runner,
              project_name: str = "workspace") -> ProvisionResult:
    """Provision the workspace for ``pin`` by executing steps via ``runner``.

    ``runner(args, cwd)`` must execute the command and return its exit code.
    The stamp is written only after every step succeeds, so an interrupted
    or failed provision is classified ``corrupt`` by status() and retried.
    Callers own locking and any atomic-placement strategy around this call.
    """
    path = workspace_path(root, pin)
    write_workspace_files(path, pin, toolchain, project_name)
    steps_run: List[ProvisionStep] = []
    for step in provision_steps(project_name):
        code = runner(step.args, str(path))
        steps_run.append(step)
        if code != 0:
            return ProvisionResult(
                status=WorkspaceStatus(
                    STATE_CORRUPT,
                    f"step failed ({step.description}) with exit code {code}",
                    path),
                steps_run=steps_run, failed_step=step, returncode=code)
    stamp = {"pin": pin, "toolchain": toolchain,
             "project_name": project_name, "provisioned": True}
    (path / STAMP_FILENAME).write_text(
        json.dumps(stamp, indent=2) + "\n", encoding="utf-8", newline="\n")
    return ProvisionResult(status=status(root, pin, toolchain),
                           steps_run=steps_run)


@dataclass(frozen=True)
class SeedPlan:
    """How to pre-seed a consumer project from a provisioned workspace:
    the rewritten lockfile text plus the package dir names to copy."""

    manifest_text: str
    package_names: List[str]


def seed_manifest(workspace_manifest_text: str, mathlib_dir: str) -> SeedPlan:
    """Rewrite a workspace lockfile for a consumer that path-requires mathlib.

    The workspace resolves ``mathlib`` as a registry/git dependency; a
    consumer project requires it by path instead. This replaces the mathlib
    entry with a path entry pointing at ``mathlib_dir`` (all other entries -
    mathlib's transitive dependencies - keep their exact pinned revisions)
    and lists the package dir names the caller must copy from the
    workspace's packages dir into the consumer's, so a subsequent build
    resolves fully from disk with no network fetches.
    """
    manifest = json.loads(workspace_manifest_text)
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise ValueError("workspace manifest has no packages list - "
                         "was the workspace provisioned?")
    names: List[str] = []
    rewritten = []
    found = False
    for entry in packages:
        name = entry.get("name")
        if name == "mathlib":
            found = True
            rewritten.append({
                "type": "path",
                "name": "mathlib",
                "manifestFile": entry.get("manifestFile",
                                          "lake-manifest.json"),
                "configFile": entry.get("configFile", "lakefile.toml"),
                "inherited": False,
                "dir": str(mathlib_dir).replace("\\", "/"),
            })
        else:
            rewritten.append(dict(entry))
            if name:
                names.append(name)
    if not found:
        raise ValueError("workspace manifest has no mathlib entry")
    manifest["packages"] = rewritten
    return SeedPlan(manifest_text=json.dumps(manifest, indent=1) + "\n",
                    package_names=names)


def _validate_pin(pin: str) -> None:
    if (not pin or any(c in pin for c in "/\\") or pin in (".", "..")
            or pin.strip() != pin):
        raise ValueError(
            f"invalid pin {pin!r}: must be a non-empty single path segment")


def _validate_identifier(name: str) -> None:
    if (not name or not name.replace("_", "").isalnum()
            or not name[0].isalpha()):
        raise ValueError(
            f"invalid project name {name!r}: letters, digits, underscores "
            "only")
