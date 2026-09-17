"""
Widget inspection and popularity queries.
"""

import glob
import json
import os


def list_popular(carto, limit=10):
    """Top widgets and blueprints by install count.

    Surfaces both kinds in one ranked list. `kind` is on each entry so the
    consumer can tell them apart without a second lookup.
    """
    pool = (
        [{**w, "_kind": "widget"} for w in carto.widgets]
        + [{**b, "_kind": "blueprint"} for b in carto.blueprints]
    )
    pool.sort(key=lambda x: x.get("install_count", 0), reverse=True)
    return {
        "top_assets": [
            {"id": w["id"], "name": w["name"],
             "version": w.get("version", "0.0.0"),
             "install_count": w.get("install_count", 0),
             "kind": w["_kind"]}
            for w in pool[:limit]
        ]
    }


def _read_dir(dirpath, prefix_filter=None):
    """Read files in a directory into a {filename: content} dict.

    prefix_filter: if given, only include filenames whose basename starts with that string.
    """
    out = {}
    if os.path.exists(dirpath):
        for fpath in glob.glob(os.path.join(dirpath, "*.*")):
            name = os.path.basename(fpath)
            if prefix_filter and not name.startswith(prefix_filter):
                continue
            try:
                with open(fpath, encoding="utf-8") as f:
                    out[name] = f.read()
            except Exception as e:
                out[name] = f"Error reading: {e}"
    return out


def _get_versions(widget_path, all_versions=False):
    """Return version list from history/ directory, newest first."""
    history_dir = os.path.join(widget_path, "history")
    if not os.path.exists(history_dir):
        return []
    versions = sorted(os.listdir(history_dir), reverse=True)
    if all_versions:
        return versions
    return versions[:5]


def inspect(carto, widget_id, show_source=False, show_all_versions=False,
            show_reviews=False, version=None):
    # Kind-aware: blueprints have a different manifest schema and a
    # dependency-pin section that widgets lack. Dispatch before the
    # widget-specific lookup.
    from .blueprints import is_blueprint_id
    if is_blueprint_id(widget_id):
        return inspect_blueprint(carto, widget_id, show_source=show_source,
                                 show_all_versions=show_all_versions,
                                 version=version)

    widget = next((w for w in carto.widgets if w["id"] == widget_id), None)
    if not widget:
        return {"error": "Widget not found"}

    widget_path = widget["path"]

    if version is not None:
        history_path = os.path.join(widget_path, "history", version)
        if not os.path.exists(history_path):
            available = _get_versions(widget_path, all_versions=True)
            return {"error": f"Version '{version}' not found. Available: {', '.join(available) or 'none'}"}
        read_path = history_path
    else:
        read_path = widget_path

    # Reviews always come from current widget, not history
    review_data = carto._load_reviews(widget_path)

    try:
        with open(os.path.join(read_path, "widget.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        manifest = {}
    meta = manifest.get("meta", manifest)

    result = {
        "id": widget["id"],
        "name": meta.get("name", widget["name"]),
        "description": manifest.get("description", widget["description"]),
        "language": manifest.get("tech_stack", {}).get("language", widget.get("language", "unknown")),
        "domain": meta.get("domain", widget["domain"]),
        "version": meta.get("version", version or widget.get("version", "0.0.0")),
        "dependencies": manifest.get("tech_stack", {}).get("dependencies", widget.get("dependencies", [])),
        "rating": review_data["rating"],
        "trend": review_data.get("trend"),
        "review_count": review_data["count"],
        "versions": _get_versions(widget_path, all_versions=show_all_versions),
        "examples": _read_dir(os.path.join(read_path, "examples"), prefix_filter="example_usage"),
        "usage_hints": _read_dir(os.path.join(read_path, "examples"), prefix_filter="usage_hint"),
    }

    result["kind"] = "widget"
    if show_source:
        result["source"] = _read_dir(os.path.join(read_path, "src"))
    if show_reviews:
        result["reviews"] = review_data["reviews"]

    return result


def inspect_blueprint(carto, blueprint_id, show_source=False,
                      show_all_versions=False, version=None):
    """Inspect a blueprint in the local library.

    Returns a blueprint-shaped view: composed dependency pins, multi-valued
    domains, the same examples + source surfaces as a widget. The dep
    section is the value-add — agents can see the exact version of every
    leaf widget this blueprint composes.
    """
    bp = next((b for b in carto.blueprints if b["id"] == blueprint_id), None)
    if not bp:
        return {"error": "Blueprint not found"}

    bp_path = bp["path"]
    if version is not None:
        history_path = os.path.join(bp_path, "history", version)
        if not os.path.exists(history_path):
            available = _get_versions(bp_path, all_versions=True)
            return {"error": (f"Version '{version}' not found. Available: "
                              f"{', '.join(available) or 'none'}")}
        read_path = history_path
    else:
        read_path = bp_path

    try:
        with open(os.path.join(read_path, "blueprint.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        manifest = {}

    result = {
        "id": bp["id"],
        "kind": "blueprint",
        "name": manifest.get("name", bp["name"]),
        "description": manifest.get("description", bp["description"]),
        "language": manifest.get("language", bp["language"]),
        "domains": manifest.get("domains", bp["domains"]),
        "version": manifest.get("version", version or bp["version"]),
        "tags": manifest.get("tags", bp["tags"]),
        "dependencies": manifest.get("dependencies", bp["dependencies"]),
        "versions": _get_versions(bp_path, all_versions=show_all_versions),
        "examples": _read_dir(os.path.join(read_path, "examples")),
    }
    if show_source:
        result["source"] = _read_dir(os.path.join(read_path, "src"))
    return result
