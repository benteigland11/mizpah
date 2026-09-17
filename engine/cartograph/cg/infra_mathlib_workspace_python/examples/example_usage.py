"""
Example usage of Mathlib Workspace.

Provisions a pinned Mathlib workspace into a temp directory using a fake
runner (no network, no Lean install needed), then shows the status
lifecycle a caller like a validator would rely on.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.mathlib_workspace import provision, status

PIN = "v4.32.0"
TOOLCHAIN = "leanprover/lean4:v4.32.0"


def fake_runner(args, cwd):
    # A real caller runs the command via subprocess; here we just show it.
    print(f"  would run: {' '.join(args)}  (in {os.path.basename(cwd)})")
    return 0


with tempfile.TemporaryDirectory() as root:
    print(f"before provision: {status(root, PIN).state}")

    result = provision(root, PIN, TOOLCHAIN, fake_runner)
    print(f"after provision:  {result.status.state}")

    check = status(root, PIN, TOOLCHAIN)
    print(f"ready for validation: {check.ready} ({check.reason})")

    other = status(root, PIN, "leanprover/lean4:v4.33.0")
    print(f"other toolchain sees: {other.state} ({other.reason})")
