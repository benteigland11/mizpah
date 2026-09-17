"""Shared Mathlib workspace for the Lean engine (glue).

One Mathlib pin per Cartograph release, one shared workspace per pin under
the platform data dir. Widgets that declare a ``mathlib`` dependency build
against this workspace; it is provisioned explicitly via
``cartograph setup-mathlib`` and never automatically (same policy as the ML
frameworks: heavy dependencies must be pre-installed, with a clear
"install it first" error otherwise).

Lifecycle logic lives in ``infra-mathlib-workspace-python``; this module
wires it to the data dir, the interprocess lock, and subprocess execution.
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Optional

from cg.infra_mathlib_workspace_python.src.mathlib_workspace import (
    ProvisionResult,
    WorkspaceStatus,
    provision,
    status,
    workspace_path,
)
from cg.infra_interprocess_lock_python.src.interprocess_lock import file_lock

# The one Mathlib revision this release of Cartograph validates against.
# Bumping it is a release decision: widgets re-validate on next checkin
# (engine-bump policy), never retroactively.
MATHLIB_PIN = "v4.32.0"
MATHLIB_TOOLCHAIN = "leanprover/lean4:v4.32.0"

_PROVISION_TIMEOUT = 3600  # cache download + root elaboration


def mathlib_root() -> str:
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), "lean-mathlib")


def mathlib_status() -> WorkspaceStatus:
    return status(mathlib_root(), MATHLIB_PIN, MATHLIB_TOOLCHAIN)


def mathlib_package_dir() -> str:
    """Path to the resolved Mathlib package inside the shared workspace."""
    ws = workspace_path(mathlib_root(), MATHLIB_PIN)
    return os.path.join(str(ws), ".lake", "packages", "mathlib")


def missing_workspace_error(state_reason: str) -> str:
    return (
        "This widget declares a `mathlib` dependency but the shared Mathlib "
        f"workspace is not ready ({state_reason}). Cartograph never "
        "downloads Mathlib automatically - provision it once with:\n"
        "    cartograph setup-mathlib\n"
        f"(pins Mathlib {MATHLIB_PIN} on toolchain {MATHLIB_TOOLCHAIN}; "
        "multi-GB download)"
    )


def setup_mathlib(
        runner: Optional[Callable] = None,
        progress: Optional[Callable[[str], None]] = None) -> ProvisionResult:
    """Provision the shared workspace (idempotent, lock-guarded).

    ``runner`` defaults to streaming subprocess execution; injectable for
    tests. ``progress`` receives one line per step when given.
    """
    root = mathlib_root()
    os.makedirs(root, exist_ok=True)

    def _default_runner(args, cwd):
        if progress:
            progress(f"running: {' '.join(args)}")
        proc = subprocess.run(list(args), cwd=cwd,
                              timeout=_PROVISION_TIMEOUT)
        return proc.returncode

    run = runner or _default_runner
    with file_lock(os.path.join(root, ".provision.lock")):
        current = mathlib_status()
        if current.ready:
            return ProvisionResult(status=current)
        return provision(root, MATHLIB_PIN, MATHLIB_TOOLCHAIN, run)
