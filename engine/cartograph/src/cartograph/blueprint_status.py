"""
Blueprint status: pin-health per dep + orphan-leaf detection.

Per blueprint:
  - For each declared dep, report whether the leaf widget is installed
    at the pinned version (`ok`), at a different version (`pin-mismatch`),
    or absent entirely (`missing`).

Across the project's cg/:
  - List leaves no installed blueprint references. We never auto-remove
    them — the user's app code may import them. Surfaced for the agent
    or human to triage.
"""

import json
import os


def blueprint_status(carto, blueprint_id, target_dir):
    """Return pin-health for an installed blueprint.

    Looks up <target_dir>/cg/<py_dir>/blueprint.json, walks its declared
    deps, and reports each dep's state against the leaf widgets actually
    in cg/.
    """
    from .engine import python_dir_name, DEFAULT_INSTALL_DIR, find_installed_widget_dir

    bp_path = os.path.join(target_dir, DEFAULT_INSTALL_DIR,
                           python_dir_name(blueprint_id))
    manifest = os.path.join(bp_path, "blueprint.json")
    if not os.path.isfile(manifest):
        return {"error": f"'{blueprint_id}' not found at {bp_path}."}

    try:
        with open(manifest, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"error": f"Failed to read blueprint manifest: {e}"}

    cg_root = os.path.join(target_dir, DEFAULT_INSTALL_DIR)
    deps_report: list[dict] = []
    broken = False

    for d in data.get("dependencies", []) or []:
        if not isinstance(d, dict):
            continue
        dep_id = d.get("id")
        pinned = d.get("version")
        if not dep_id or not pinned:
            continue
        wdir = find_installed_widget_dir(cg_root, dep_id)
        installed = _read_widget_version(wdir) if wdir else None
        if installed is None:
            state = "missing"
            broken = True
        elif installed != pinned:
            state = "pin-mismatch"
            broken = True
        else:
            state = "ok"
        deps_report.append({
            "id": dep_id, "pinned": pinned,
            "installed": installed, "state": state,
        })

    pin_health = "broken" if broken else "ok"
    response: dict = {
        "blueprint_id": blueprint_id,
        "kind": "blueprint",
        "version": data.get("version", "unknown"),
        "deps": deps_report,
        "pin_health": pin_health,
    }
    if broken:
        response["suggestion"] = _suggest_repair(deps_report, target_dir)
    return response


def orphan_leaves(target_dir):
    """Leaves in cg/ that no installed blueprint references.

    These are NOT auto-removed. Returned so an agent or `cartograph
    status` listing can flag them; the user decides whether their app
    code still needs them.
    """
    from .engine import DEFAULT_INSTALL_DIR

    cg_root = os.path.join(target_dir, DEFAULT_INSTALL_DIR)
    if not os.path.isdir(cg_root):
        return []

    leaves: list[str] = []      # widget_id, ...
    referenced: set[str] = set()  # widget_ids referenced by any blueprint
    has_blueprint = False

    for entry in os.listdir(cg_root):
        edir = os.path.join(cg_root, entry)
        wjson = os.path.join(edir, "widget.json")
        bjson = os.path.join(edir, "blueprint.json")
        if os.path.isfile(bjson):
            has_blueprint = True
            try:
                with open(bjson, encoding="utf-8") as f:
                    data = json.load(f)
                for d in data.get("dependencies", []) or []:
                    if isinstance(d, dict) and d.get("id"):
                        referenced.add(d["id"])
            except (OSError, json.JSONDecodeError):
                continue
        elif os.path.isfile(wjson):
            try:
                with open(wjson, encoding="utf-8") as f:
                    data = json.load(f)
                wid = (data.get("meta") or data).get("id")
                if wid:
                    leaves.append(wid)
            except (OSError, json.JSONDecodeError):
                continue

    # Orphan signal is only meaningful when blueprints are involved. Without
    # any installed blueprint, every leaf is "directly managed" by definition
    # and reporting them as orphans would be noise.
    if not has_blueprint:
        return []
    return sorted(set(leaves) - referenced)


def all_blueprint_status(carto, target_dir):
    """Status for every installed blueprint + project-wide orphans.

    Used by the listing form of `cartograph status` to surface blueprint
    pin-health alongside widget status and to flag orphan leaves once
    per call.
    """
    from .engine import DEFAULT_INSTALL_DIR

    cg_root = os.path.join(target_dir, DEFAULT_INSTALL_DIR)
    if not os.path.isdir(cg_root):
        return {"blueprints": [], "orphans": []}

    statuses: list[dict] = []
    for entry in os.listdir(cg_root):
        bjson = os.path.join(cg_root, entry, "blueprint.json")
        if not os.path.isfile(bjson):
            continue
        try:
            with open(bjson, encoding="utf-8") as f:
                bp_id = json.load(f).get("id")
        except (OSError, json.JSONDecodeError):
            continue
        if not bp_id:
            continue
        statuses.append(blueprint_status(carto, bp_id, target_dir))

    return {"blueprints": statuses, "orphans": orphan_leaves(target_dir)}


# --- helpers --------------------------------------------------------------


def _read_widget_version(widget_dir):
    if not widget_dir:
        return None
    wjson = os.path.join(widget_dir, "widget.json")
    if not os.path.isfile(wjson):
        return None
    try:
        with open(wjson, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    meta = data.get("meta") or data
    return meta.get("version")


def _suggest_repair(deps_report, target_dir):
    """Build a one-line action hint for a broken pin set."""
    missing = [d["id"] for d in deps_report if d["state"] == "missing"]
    mismatched = [d for d in deps_report if d["state"] == "pin-mismatch"]
    if missing:
        return (
            f"Re-install missing leaf{'s' if len(missing) > 1 else ''}: "
            + " ; ".join(
                f"cartograph install {mid} --version <pinned>" for mid in missing
            )
        )
    if mismatched:
        m = mismatched[0]
        return (
            f"{m['id']} is at {m['installed']} but the blueprint pins "
            f"{m['pinned']}. Re-install the pinned version, or contact the "
            f"blueprint owner to run `cartograph blueprint repin`."
        )
    return None
