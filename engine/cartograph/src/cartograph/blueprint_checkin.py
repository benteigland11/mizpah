"""
Blueprint checkin: push an edited blueprint into the local library.

Task #6 scope: local library only. Cloud publish/baseline is a later
task; this module assumes the local library is the source of truth.

Differences from widget checkin:
  - Manifest is `blueprint.json`, fields are top-level (no `meta` block).
  - Library record is not yet indexed in `carto.widgets` (which globs
    widget.json), so the update-vs-new check is filesystem-based.
  - Domains are derived from dep widgets and written at checkin time.
  - No-op / version-conflict guards run against the library copy directly.
"""

import datetime
import json
import logging
import os
import shutil
from .safefs import robust_rmtree

from .languages import get_engine
from .validation_stamp import has_valid_stamp, write_stamp

log = logging.getLogger("cartograph")


def checkin_blueprint(carto, path: str, reason: str = "", version_bump: str = "minor",
                      override_warnings: bool = False, override_reason: str = "") -> dict:
    """Push an edited blueprint into the local library."""
    if not os.path.isdir(path):
        return {"status": "error", "message": f"Path not found: {path}"}

    manifest_path = os.path.join(path, "blueprint.json")
    if not os.path.exists(manifest_path):
        return {"status": "error", "message": "No blueprint.json found."}

    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Invalid blueprint.json: {e}"}

    item_id = (data.get("id") or "").strip()
    if not item_id:
        return {"status": "error", "message": "blueprint.json is missing 'id'"}

    language = (data.get("language") or "python").lower()
    engine = get_engine(language)

    # --- Dep integrity gate ---
    # If any dep widget in the project's cg/ has been edited since its last
    # checkin (status reports modified=True), refuse: a blueprint must only
    # ship pinned to a published widget state. "outdated" is fine - that's
    # a newer library version available, not local drift.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    modified_deps = []
    for dep in data.get("dependencies", []) or []:
        dep_id = (dep.get("id") or "").strip() if isinstance(dep, dict) else ""
        if not dep_id:
            continue
        st = carto.widget_status(dep_id, project_root, check_cloud=False)
        if isinstance(st, dict) and st.get("modified"):
            modified_deps.append(dep_id)
    if modified_deps:
        return {
            "status": "error",
            "message": (
                "Cannot check in blueprint: "
                + ", ".join(f"'{d}'" for d in modified_deps)
                + " ha"
                + ("s" if len(modified_deps) == 1 else "ve")
                + " uncommitted modifications. Check "
                + ("it" if len(modified_deps) == 1 else "them")
                + " in first, then re-run `blueprint add-dep` to re-pin "
                  "and re-validate the blueprint."
            ),
            "modified_deps": modified_deps,
        }

    # --- Validate (skip on fresh stamp) ---
    if engine and has_valid_stamp(path, language):
        log.info("Validation stamp is fresh — skipping re-validation")
    else:
        val = carto.validate_blueprint(path)
        if val.get("status") != "success":
            return val

    # --- Resolve baseline (filesystem-based; blueprints aren't in the index) ---
    dest_path = os.path.join(carto.library_path, item_id)
    is_update = os.path.isdir(dest_path)
    baseline_version = None
    baseline_data = None
    if is_update:
        baseline_manifest = os.path.join(dest_path, "blueprint.json")
        if os.path.isfile(baseline_manifest):
            try:
                with open(baseline_manifest, encoding="utf-8") as f:
                    baseline_data = json.load(f)
                baseline_version = baseline_data.get("version")
            except Exception:
                baseline_data = None

    if baseline_version is not None:
        local_version = data.get("version", "0.0.0")
        if local_version != baseline_version:
            return {
                "status": "error",
                "message": (
                    f"Version conflict: local is v{local_version} but library is "
                    f"v{baseline_version}. Restore from history first or sync versions."
                ),
            }

    # --- No-op guard: identical content blocks ---
    if is_update and baseline_data is not None:
        try:
            current_hash = carto._calculate_implementation_hash(path)
            baseline_hash = carto._calculate_implementation_hash(dest_path)
            code_identical = current_hash == baseline_hash
        except Exception:
            code_identical = False
        meta_identical = (
            code_identical
            and _manifest_core(data) == _manifest_core(baseline_data)
        )
        if code_identical and meta_identical:
            return {
                "status": "error",
                "message": (
                    f"{item_id} v{baseline_version} is already in the library "
                    f"with identical content. Make your changes before checking in."
                ),
            }

    # --- Contamination scan (kind-aware) ---
    from .contamination import scan_contamination
    scan = scan_contamination(path)
    if scan["blocks"]:
        return {
            "status": "error",
            "message": "Checkin blocked: project-specific content detected.",
            "blocks": scan["blocks"],
        }
    if scan["warnings"] and not override_warnings:
        return {
            "status": "warnings",
            "message": (
                "Checkin paused: potential contamination found. "
                "Review warnings below, then re-run with "
                "--override-warnings --override-reason \"<why these warnings are acceptable>\"."
            ),
            "warnings": scan["warnings"],
        }
    if override_warnings and not override_reason:
        return {
            "status": "error",
            "message": "--override-reason is required when using --override-warnings.",
        }

    # --- Version bump ---
    version = data.get("version", "0.1.0")
    should_bump = baseline_version is not None
    if should_bump:
        try:
            from packaging.version import Version as _Version
            parsed = _Version(version)
            major, minor, patch = parsed.major, parsed.minor, parsed.micro
        except Exception:
            return {
                "status": "error",
                "message": f"Cannot bump malformed version '{version}'. Fix to X.Y.Z first.",
            }
        if version_bump == "major":
            major, minor, patch = major + 1, 0, 0
        elif version_bump == "minor":
            minor, patch = minor + 1, 0
        elif version_bump == "patch":
            patch += 1
        new_version = f"{major}.{minor}.{patch}"
    else:
        new_version = version

    data["version"] = new_version

    # --- Derive domains from deps (overwrite — this is auto-managed) ---
    # Resolve domains from the project's cg/ — the same source the validator
    # used. No library fallback: blueprint validation is strictly project-local.
    from .blueprints import derive_domains
    from .blueprint_validator import _scan_project_cg, _read_widget_meta
    project_widgets = _scan_project_cg(path)
    deps = data.get("dependencies", [])
    data["domains"] = derive_domains(
        deps,
        lambda i: _read_widget_meta(project_widgets.get(i, {}).get("path")),
    )

    # --- Stamp validation metadata into blueprint.json ---
    validation_block = {
        "engine_version": getattr(engine, "validation_version", None),
        "validated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if engine:
        rv = engine.runtime_version()
        if rv:
            validation_block["runtime"] = rv
    data["validation"] = validation_block

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # --- Archive current library version to history/ ---
    if is_update:
        history_path = os.path.join(dest_path, "history", baseline_version or "unknown")
        if os.path.exists(history_path):
            robust_rmtree(history_path)
        os.makedirs(history_path, exist_ok=True)
        ignore = shutil.ignore_patterns(
            ".venv", "__pycache__", ".pytest_cache", "*.pyc",
            "history", "changelog.json", ".validation_stamp.json", ".dep_cache.json",
            ".cartograph_source", "cg",
        )
        per_item_skips = {"history", "changelog.json", ".validation_stamp.json", ".dep_cache.json",
                          ".cartograph_source", ".venv", "__pycache__",
                          ".pytest_cache", "cg"}
        for item in os.listdir(dest_path):
            if item in per_item_skips:
                continue
            src = os.path.join(dest_path, item)
            dst = os.path.join(history_path, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=ignore)
            else:
                shutil.copy2(src, dst)

    # --- Copy working copy → library (never move) ---
    ignore = shutil.ignore_patterns(
        ".venv", "__pycache__", ".pytest_cache", "*.pyc",
        ".cartograph_source", "cg",
    )
    per_item_skips = {"history", "changelog.json", ".validation_stamp.json", ".dep_cache.json",
                      ".cartograph_source", ".venv", "__pycache__",
                      ".pytest_cache", "cg"}
    os.makedirs(dest_path, exist_ok=True)
    for item in os.listdir(path):
        if item in per_item_skips:
            continue
        src = os.path.join(path, item)
        dst = os.path.join(dest_path, item)
        if os.path.isdir(src):
            if os.path.exists(dst):
                robust_rmtree(dst)
            shutil.copytree(src, dst, ignore=ignore)
        else:
            shutil.copy2(src, dst)

    # --- Changelog ---
    changelog_entry = {
        "version": new_version,
        "reason": reason or ("No reason provided" if is_update else "Initial release"),
        "timestamp": datetime.datetime.now().isoformat(),
    }
    if override_warnings and override_reason:
        changelog_entry["override_reason"] = override_reason
    changelog_path = os.path.join(dest_path, "changelog.json")
    changelog = []
    if os.path.exists(changelog_path):
        try:
            with open(changelog_path, encoding="utf-8") as f:
                changelog = json.load(f)
        except Exception:
            pass
    changelog.insert(0, changelog_entry)
    with open(changelog_path, "w", encoding="utf-8") as f:
        json.dump(changelog, f, indent=2)

    # --- Write fresh stamp at library path ---
    if engine:
        try:
            write_stamp(dest_path, language, engine)
        except OSError as e:
            log.warning("Could not write validation stamp after blueprint checkin: %s", e)

    action = "updated" if is_update else "registered"
    log.info("Successfully %s blueprint %s → v%s", action, item_id, new_version)
    messages = {
        "registered": f"Added {item_id} v{new_version} to local library (first-time entry).",
        "updated": f"Bumped {item_id} to v{new_version} in local library.",
    }
    result = {
        "status": "success",
        "kind": "blueprint",
        "id": item_id,
        "version": new_version,
        "action": action,
        "message": messages[action],
        "path": dest_path,
        "domains": data["domains"],
    }
    if scan["warnings"] and override_warnings:
        result["overridden_warnings"] = scan["warnings"]
        result["override_reason"] = override_reason
    return result


def _manifest_core(data: dict) -> dict:
    """Strip auto-managed fields so a no-op compare ignores stamp churn."""
    drop = {"version", "validation", "domains"}
    return {k: v for k, v in data.items() if k not in drop}
