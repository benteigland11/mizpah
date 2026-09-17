"""
Blueprint dep editor: `cartograph blueprint add-dep` / `remove-dep`.

Authoring guardrail (per v0.7 plan): both operations require the target
widget to already be installed in the project's `cg/`. The blueprint
author must have pulled the widget down before composing with it. This
keeps pinning honest — we read the version straight off the installed
widget's manifest — and gives the validator something concrete to read
from at the moment of pin.

After mutating `blueprint.json`, both verbs re-run the blueprint
validator unless `validate=False` is passed. If validation fails, the
manifest is reverted so authors aren't left with a half-broken state.
"""

import json
import logging
import os

from .engine import DEFAULT_INSTALL_DIR, find_installed_widget_dir
from .blueprints import is_blueprint_id, parse_blueprint_id
from .languages import get_engine

log = logging.getLogger("cartograph")


def _snapshot_manifests(engine, blueprint_path: str) -> dict:
    """Capture the current contents of the engine's manifest files so a failed
    validation can restore them (None marks a file that did not exist)."""
    snap = {}
    for name in getattr(engine, "manifest_patterns", []) or []:
        p = os.path.join(blueprint_path, name)
        snap[p] = open(p, encoding="utf-8").read() if os.path.isfile(p) else None
    return snap


def _restore_manifests(snap: dict) -> None:
    for p, content in snap.items():
        if content is None:
            if os.path.isfile(p):
                os.remove(p)
        else:
            with open(p, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)


def _load_blueprint(blueprint_path: str) -> tuple[dict | None, dict | None]:
    """Load the blueprint manifest, or return an error dict in the second slot."""
    if not os.path.isdir(blueprint_path):
        return None, {"status": "error", "message": f"Path not found: {blueprint_path}"}
    manifest_path = os.path.join(blueprint_path, "blueprint.json")
    if not os.path.exists(manifest_path):
        return None, {
            "status": "error",
            "message": (
                f"No blueprint.json at {blueprint_path}. "
                "Run this command from a blueprint directory (one created by "
                "`cartograph blueprint create`)."
            ),
        }
    try:
        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, {"status": "error", "message": f"Invalid blueprint.json: {e}"}


def _project_root_from_blueprint(blueprint_path: str) -> tuple[str | None, dict | None]:
    """Return the project root (parent of cg/) for a scaffolded blueprint.

    The scaffold places blueprints at `<project>/cg/<py_dir>/`, so the
    project root is two levels up. We verify the parent is named `cg`
    so authors get a clear error if they're running from somewhere else
    (e.g. a copy in /tmp).
    """
    blueprint_path = os.path.abspath(blueprint_path)
    parent = os.path.dirname(blueprint_path)
    if os.path.basename(parent) != DEFAULT_INSTALL_DIR:
        return None, {
            "status": "error",
            "message": (
                f"Blueprint at {blueprint_path} is not inside a cg/ directory. "
                "add-dep / remove-dep operate against widgets installed in the "
                "project's cg/. Run from a project where both the blueprint and "
                "the dep widget live under cg/."
            ),
        }
    return os.path.dirname(parent), None


def _read_installed_widget(project_root: str, widget_id: str) -> tuple[dict | None, str | None]:
    """Look up an installed widget's manifest under <project>/cg/."""
    cg_root = os.path.join(project_root, DEFAULT_INSTALL_DIR)
    widget_dir = find_installed_widget_dir(cg_root, widget_id)
    if widget_dir is None:
        return None, (
            f"Widget '{widget_id}' is not installed in this project's cg/. "
            f"Install it first with `cartograph install {widget_id}` so the "
            "version it pins to is the version you've actually run with."
        )
    manifest_path = os.path.join(widget_dir, "widget.json")
    try:
        with open(manifest_path, encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, f"Could not read installed widget manifest at {manifest_path}: {e}"


def _write_manifest(blueprint_path: str, data: dict) -> None:
    with open(os.path.join(blueprint_path, "blueprint.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _maybe_validate(carto, blueprint_path: str, before: dict, validate: bool) -> dict | None:
    """Run the validator. If it fails, restore `before` and return the error result."""
    if not validate:
        return None
    result = carto.validate_blueprint(blueprint_path)
    if result.get("status") != "success":
        _write_manifest(blueprint_path, before)
        result.setdefault(
            "message",
            "Blueprint validation failed; manifest reverted to its previous state.",
        )
        result["reverted"] = True
        return result
    return None


def add_dep(carto, blueprint_path: str, widget_id: str, validate: bool = True) -> dict:
    """Add a widget to the blueprint's dependencies, pinned to the locally installed version."""
    data, err = _load_blueprint(blueprint_path)
    if err:
        return err

    bp_id = data.get("id", "")
    try:
        bid = parse_blueprint_id(bp_id)
    except ValueError as e:
        return {"status": "error", "message": f"Manifest id '{bp_id}' is not a blueprint id: {e}"}

    if is_blueprint_id(widget_id):
        return {
            "status": "error",
            "message": (
                f"'{widget_id}' is a blueprint. v0.7 blueprints compose widgets "
                "only (leaf-only). Blueprint composition lands in a later version."
            ),
        }
    if not widget_id.endswith(f"-{bid.language}"):
        return {
            "status": "error",
            "message": (
                f"Dep '{widget_id}' is not a {bid.language} widget. v0.7 blueprints "
                "compose widgets in a single language."
            ),
        }
    if widget_id == bp_id:
        return {"status": "error", "message": f"A blueprint cannot depend on itself: {widget_id}"}

    project_root, err = _project_root_from_blueprint(blueprint_path)
    if err:
        return err

    widget_data, msg = _read_installed_widget(project_root, widget_id)
    if msg:
        return {"status": "error", "message": msg}

    meta = widget_data.get("meta") or widget_data
    pinned = (meta.get("version") or "").strip()
    if not pinned:
        return {
            "status": "error",
            "message": f"Installed widget '{widget_id}' has no version field; reinstall it.",
        }

    deps = list(data.get("dependencies", []))
    for d in deps:
        if isinstance(d, dict) and d.get("id") == widget_id:
            return {
                "status": "error",
                "message": (
                    f"'{widget_id}' is already a dep at v{d.get('version')}. "
                    "Use `blueprint remove-dep` then `blueprint add-dep` if you "
                    "want to re-pin to a different installed version."
                ),
            }

    before = json.loads(json.dumps(data))
    deps.append({"id": widget_id, "version": pinned})
    data["dependencies"] = deps
    _write_manifest(blueprint_path, data)

    # Wire the composed widget into the blueprint's language manifest (Cargo
    # path dep, go.mod require+replace, ...) so it compiles against the dep
    # without the author hand-editing. No-op for interpreted languages.
    engine = get_engine(bid.language)
    manifest_snap = _snapshot_manifests(engine, blueprint_path) if engine else {}
    if engine:
        cg_root = os.path.join(project_root, DEFAULT_INSTALL_DIR)
        dep_dir = find_installed_widget_dir(cg_root, widget_id)
        try:
            engine.wire_blueprint_dep(blueprint_path, widget_id, dep_dir)
        except Exception as e:
            _write_manifest(blueprint_path, before)
            _restore_manifests(manifest_snap)
            return {"status": "error",
                    "message": f"Failed to wire {widget_id} into the "
                               f"blueprint manifest: {e}"}

    failure = _maybe_validate(carto, blueprint_path, before, validate)
    if failure:
        _restore_manifests(manifest_snap)
        return failure

    log.info("blueprint %s: added dep %s@%s", bp_id, widget_id, pinned)
    return {
        "status": "success",
        "kind": "blueprint",
        "id": bp_id,
        "action": "add-dep",
        "added": {"id": widget_id, "version": pinned},
        "dependencies": data["dependencies"],
        "validated": validate,
        "message": f"Added {widget_id}@{pinned} to {bp_id}.",
    }


def remove_dep(carto, blueprint_path: str, widget_id: str, validate: bool = True) -> dict:
    """Remove a widget from the blueprint's dependencies."""
    data, err = _load_blueprint(blueprint_path)
    if err:
        return err

    bp_id = data.get("id", "")
    try:
        parse_blueprint_id(bp_id)
    except ValueError as e:
        return {"status": "error", "message": f"Manifest id '{bp_id}' is not a blueprint id: {e}"}

    deps = list(data.get("dependencies", []))
    match = next((d for d in deps if isinstance(d, dict) and d.get("id") == widget_id), None)
    if match is None:
        return {
            "status": "error",
            "message": f"'{widget_id}' is not a dep of {bp_id}. Nothing to remove.",
        }

    before = json.loads(json.dumps(data))
    new_deps = [d for d in deps if not (isinstance(d, dict) and d.get("id") == widget_id)]
    data["dependencies"] = new_deps
    _write_manifest(blueprint_path, data)

    # Unwire the composed widget from the blueprint's language manifest. No-op
    # for interpreted languages. bid from parse_blueprint_id above.
    bid = parse_blueprint_id(bp_id)
    engine = get_engine(bid.language)
    manifest_snap = _snapshot_manifests(engine, blueprint_path) if engine else {}
    if engine:
        try:
            engine.unwire_blueprint_dep(blueprint_path, widget_id)
        except Exception as e:
            _write_manifest(blueprint_path, before)
            _restore_manifests(manifest_snap)
            return {"status": "error",
                    "message": f"Failed to unwire {widget_id} from the "
                               f"blueprint manifest: {e}"}

    failure = _maybe_validate(carto, blueprint_path, before, validate)
    if failure:
        _restore_manifests(manifest_snap)
        return failure

    log.info("blueprint %s: removed dep %s", bp_id, widget_id)
    return {
        "status": "success",
        "kind": "blueprint",
        "id": bp_id,
        "action": "remove-dep",
        "removed": {"id": widget_id, "version": match.get("version")},
        "dependencies": data["dependencies"],
        "validated": validate,
        "message": f"Removed {widget_id} from {bp_id}.",
    }
