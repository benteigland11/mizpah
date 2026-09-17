"""
Blueprint installation: cascade-install a blueprint and its leaf deps.

A blueprint is installed by copying its directory into <target>/cg/<py_dir>/
and then installing each pinned leaf dep into the same cg/ at the pinned
version. The leaf installs delegate to the existing widget install path,
so registry routing and sidecar handling are reused, not duplicated.

Override semantics (newer-pin wins):
  If a leaf dep is already installed at a different version (pinned by
  another blueprint that's already in cg/), the new pin overrides the
  old one. The user is warned at install time. The older blueprint's
  pin-mismatch is surfaced later by `cartograph status`.
"""

import json
import os
import shutil
from .safefs import robust_rmtree

from .blueprints import is_blueprint_id


def install_blueprint(carto, blueprint_id, target_dir, version=None):
    """Install a blueprint and cascade-install its leaf deps.

    Returns a dict with status and a list of warnings. The blueprint and
    each leaf dep are installed into <target_dir>/cg/. On any failure
    after the cascade has started, partial installs are rolled back.
    """
    if not os.path.isabs(target_dir):
        return {"error": f"Target must be an absolute path, got: '{target_dir}'"}

    from .engine import PACKAGE_DIR, python_dir_name, DEFAULT_INSTALL_DIR
    if target_dir == os.path.abspath(carto.library_path):
        return {"error": f"Cannot install into the widget library "
                         f"({carto.library_path})."}
    if os.path.abspath(target_dir) == os.path.dirname(PACKAGE_DIR):
        return {"error": f"Cannot install into the engine source directory "
                         f"({os.path.dirname(PACKAGE_DIR)})."}

    # 1. Resolve the blueprint.
    bp = _resolve_blueprint(carto, blueprint_id, version)
    if "error" in bp:
        return bp

    deps = bp["dependencies"]
    bp_dir = bp["path"]

    # 2. Leaf-only enforcement (blueprint-on-blueprint banned in v0.7).
    for d in deps:
        dep_id = d["id"]
        if is_blueprint_id(dep_id):
            return {"error": (
                f"Blueprint '{blueprint_id}' depends on '{dep_id}', which is "
                f"itself a blueprint. v0.7 blueprints compose widgets only "
                f"(leaf-only). This blueprint cannot be installed."
            )}

    # 3. Plan against existing project state.
    plan = _plan_cascade(target_dir, deps)
    if "error" in plan:
        return plan

    # 4. Place the blueprint itself.
    bp_dest = os.path.join(target_dir, DEFAULT_INSTALL_DIR,
                           python_dir_name(blueprint_id))
    if os.path.exists(bp_dest):
        return {"error": f"'{blueprint_id}' already installed at {bp_dest}. "
                          "Uninstall first to reinstall."}

    placed_blueprint = False
    leaf_changes: list[dict] = []

    try:
        _copy_blueprint(bp_dir, bp_dest)
        carto._increment_install_count(blueprint_id)
        placed_blueprint = True

        # 5. Cascade install each leaf dep.
        for step in plan["steps"]:
            dep_id = step["id"]
            pinned = step["version"]
            action = step["action"]

            if action == "skip":
                continue

            if action == "override":
                # Existing version differs from this blueprint's pin.
                # Uninstall the old version before installing the pinned one.
                un = carto.uninstall(dep_id, target_dir)
                if "error" in un:
                    raise _CascadeError(
                        f"Could not remove existing {dep_id} "
                        f"(currently {step['existing_version']}) before override: "
                        f"{un['error']}"
                    )
                leaf_changes.append({
                    "id": dep_id,
                    "kind": "removed",
                    "version": step["existing_version"],
                })

            res = carto.install(dep_id, target_dir, version=pinned)
            if "error" in res:
                raise _CascadeError(
                    f"Failed to install leaf dep {dep_id}@{pinned}: {res['error']}"
                )
            leaf_changes.append({
                "id": dep_id, "kind": "installed", "version": pinned,
            })

    except _CascadeError as e:
        # Roll back: undo any leaf changes (in reverse order), then remove
        # the blueprint. Best-effort — restore previous versions if we can.
        _rollback(carto, target_dir, leaf_changes, placed_blueprint, bp_dest)
        return {"error": str(e), "rolled_back": True}

    return {
        "status": "success",
        "blueprint_id": blueprint_id,
        "version": bp["version"],
        "installed_at": bp_dest,
        "leaves_installed": [s for s in plan["steps"] if s["action"] != "skip"],
        "leaves_skipped": [s for s in plan["steps"] if s["action"] == "skip"],
        "warnings": plan["warnings"],
    }


# --- resolution -----------------------------------------------------------


def _resolve_blueprint(carto, blueprint_id, version):
    """Find a blueprint manifest at the requested version.

    Local-library lookups work today. Cloud-hosted blueprint *publish*
    is live, but cloud-hosted *install* needs an owner handle to fetch
    the tarball, and bare-id → owner resolution is a follow-up
    (either an `@handle/id` install syntax or a server-side lookup
    endpoint).
    """
    from .installer import _resolve_registry
    routed = _resolve_registry(blueprint_id)
    if routed is not None:
        registry_url, prefix, _bare = routed
        return {"error": (
            f"Cloud-blueprint install is not wired yet. '{blueprint_id}' "
            f"routes to '{prefix}' ({registry_url}); publish lands there, "
            f"but install needs owner-handle resolution before it can "
            f"fetch. Install from local library for now."
        )}

    bp = next((b for b in carto.blueprints if b["id"] == blueprint_id), None)
    if not bp:
        return {"error": (
            f"Blueprint '{blueprint_id}' not found in the local library. "
            f"Run `cartograph search` to see what's available, or check it "
            f"in via `cartograph checkin` if you authored it locally."
        )}

    if version and version != bp["version"]:
        # Pinned version: serve from history/<version>/ if present.
        hist = os.path.join(bp["path"], "history", version)
        if not os.path.isdir(hist):
            return {"error": (
                f"Version '{version}' of '{blueprint_id}' not found. "
                f"Latest in library: {bp['version']}."
            )}
        # Read the historical manifest to get its declared deps.
        try:
            with open(os.path.join(hist, "blueprint.json"), encoding="utf-8") as f:
                raw = json.load(f)
        except OSError as e:
            return {"error": f"Could not read historical blueprint: {e}"}
        return {
            "id": blueprint_id,
            "version": version,
            "path": hist,
            "dependencies": [
                {"id": d.get("id"), "version": d.get("version")}
                for d in (raw.get("dependencies") or [])
                if isinstance(d, dict)
            ],
        }

    return {
        "id": blueprint_id,
        "version": bp["version"],
        "path": bp["path"],
        "dependencies": list(bp["dependencies"]),
    }


# --- planning -------------------------------------------------------------


def _plan_cascade(target_dir, deps):
    """Decide what to do with each leaf dep and surface warnings.

    For each dep:
      - "install"  : not in cg/, fresh install at pinned version
      - "skip"     : already installed at exactly the pinned version
      - "override" : installed at a different version (warning emitted)
    """
    from cg.infra_pinned_deps_resolver_python.src.pinned_deps_resolver import (
        resolve_pins,
    )
    from .engine import DEFAULT_INSTALL_DIR, find_installed_widget_dir

    cg_root = os.path.join(target_dir, DEFAULT_INSTALL_DIR)
    steps: list[dict] = []
    warnings: list[str] = []

    pinning_blueprints = _scan_blueprint_pins(cg_root)

    def _installed_version(dep_id: str) -> str | None:
        wdir = find_installed_widget_dir(cg_root, dep_id)
        if wdir is None:
            return None
        return _read_widget_version(wdir)

    resolved = resolve_pins(deps, _installed_version)

    for r in resolved:
        dep_id = r.id
        pinned = r.pinned

        if r.state == "missing":
            steps.append({"id": dep_id, "version": pinned, "action": "install"})
            continue

        if r.state == "ok":
            steps.append({"id": dep_id, "version": pinned, "action": "skip",
                          "existing_version": r.found})
            continue

        existing = r.found
        prior_pinners = [
            (bp_id, bp_pin) for (bp_id, w_id, bp_pin) in pinning_blueprints
            if w_id == dep_id and bp_pin == existing
        ]
        if prior_pinners:
            pinner_list = ", ".join(f"{bp}@pin {pv}" for bp, pv in prior_pinners)
            warnings.append(
                f"{dep_id}@{pinned} will replace {dep_id}@{existing} "
                f"(pinned by {pinner_list}). Those blueprints may stop "
                f"validating until their authors run "
                f"`cartograph blueprint repin`. "
                f"Run `cartograph status` after install to verify."
            )
        else:
            warnings.append(
                f"{dep_id}@{pinned} will replace {dep_id}@{existing} "
                f"(currently installed). No blueprint owns the existing "
                f"pin; this is a direct override."
            )
        steps.append({
            "id": dep_id, "version": pinned, "action": "override",
            "existing_version": existing,
        })

    return {"steps": steps, "warnings": warnings}


def _scan_blueprint_pins(cg_root):
    """Walk cg/ and return [(blueprint_id, dep_widget_id, pinned_version), ...]."""
    out: list[tuple[str, str, str]] = []
    if not os.path.isdir(cg_root):
        return out
    for entry in os.listdir(cg_root):
        bpath = os.path.join(cg_root, entry, "blueprint.json")
        if not os.path.isfile(bpath):
            continue
        try:
            with open(bpath, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        bp_id = data.get("id", entry)
        for d in data.get("dependencies", []) or []:
            if isinstance(d, dict) and d.get("id") and d.get("version"):
                out.append((bp_id, d["id"], d["version"]))
    return out


def _read_widget_version(widget_dir):
    """Read meta.version from widget.json, or None if not present."""
    wpath = os.path.join(widget_dir, "widget.json")
    if not os.path.isfile(wpath):
        return None
    try:
        with open(wpath, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    meta = data.get("meta") or data
    return meta.get("version")


# --- copy -----------------------------------------------------------------


def _copy_blueprint(source_path, dest_path):
    """Copy a blueprint from library into a project's cg/ slot."""
    os.makedirs(dest_path, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache",
                                    "history", "changelog.json", "reviews.json")
    for folder in ("src", "tests", "examples"):
        src = os.path.join(source_path, folder)
        if os.path.exists(src):
            shutil.copytree(src, os.path.join(dest_path, folder),
                            dirs_exist_ok=True, ignore=ignore)
    manifest = os.path.join(source_path, "blueprint.json")
    if os.path.exists(manifest):
        shutil.copy2(manifest, dest_path)


# --- rollback -------------------------------------------------------------


class _CascadeError(Exception):
    """Raised inside install_blueprint to trigger rollback."""


def _rollback(carto, target_dir, leaf_changes, placed_blueprint, bp_dest):
    """Undo a partially-applied cascade install.

    Goes through leaf_changes in reverse:
      - "installed" -> uninstall it
      - "removed"   -> best-effort reinstall at the prior version
    Then removes the blueprint dir.
    Errors during rollback are swallowed; the user already has an error
    message describing what went wrong with the install.
    """
    for ch in reversed(leaf_changes):
        try:
            if ch["kind"] == "installed":
                carto.uninstall(ch["id"], target_dir)
            elif ch["kind"] == "removed":
                carto.install(ch["id"], target_dir, version=ch["version"])
        except Exception:
            pass
    if placed_blueprint and os.path.isdir(bp_dest):
        robust_rmtree(bp_dest, ignore_errors=True)
