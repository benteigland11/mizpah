"""
Manifest loading and kind discrimination.

A library artifact lives under cg/<id>/ and carries one of:
  - widget.json     -> kind="widget"
  - blueprint.json  -> kind="blueprint"

The filename is the discriminator. There is no `kind:` field in either
manifest. This module is the single place that decides which kind a
given directory contains and exposes a uniform load surface for code
paths that need to handle both.

Widget code paths predate this module and read widget.json directly via
dict access. That is left intact. The structured `Manifest` view here
is primarily for blueprint code paths (validator, checkin, add-dep) and
for code that legitimately needs to be kind-aware (validate dispatch,
contamination scanner, install resolver).
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any

WIDGET_MANIFEST = "widget.json"
BLUEPRINT_MANIFEST = "blueprint.json"

KIND_WIDGET = "widget"
KIND_BLUEPRINT = "blueprint"


class ManifestError(Exception):
    """Raised when a manifest is missing, ambiguous, or malformed."""


@dataclass
class Manifest:
    """Uniform view of a widget.json or blueprint.json file on disk.

    `raw` is the parsed JSON dict so callers can reach into kind-specific
    fields without going back to disk. `kind` is the discriminator —
    branch on it for kind-specific behaviour.
    """

    kind: str
    path: str
    manifest_path: str
    id: str
    name: str
    language: str
    version: str
    domains: list[str]
    tags: list[str]
    dependencies: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blueprint(self) -> bool:
        return self.kind == KIND_BLUEPRINT

    @property
    def is_widget(self) -> bool:
        return self.kind == KIND_WIDGET


def detect_kind(path: str) -> str | None:
    """Return 'widget', 'blueprint', or None for the directory at `path`.

    Raises ManifestError if both manifests are present (illegal state).
    """
    has_widget = os.path.exists(os.path.join(path, WIDGET_MANIFEST))
    has_blueprint = os.path.exists(os.path.join(path, BLUEPRINT_MANIFEST))
    if has_widget and has_blueprint:
        raise ManifestError(
            f"{path} contains both widget.json and blueprint.json. "
            "An artifact must be one or the other."
        )
    if has_blueprint:
        return KIND_BLUEPRINT
    if has_widget:
        return KIND_WIDGET
    return None


def manifest_filename(kind: str) -> str:
    if kind == KIND_BLUEPRINT:
        return BLUEPRINT_MANIFEST
    if kind == KIND_WIDGET:
        return WIDGET_MANIFEST
    raise ManifestError(f"Unknown kind: {kind}")


def load_manifest(path: str) -> Manifest:
    """Load the manifest at `path` and return a structured view.

    Raises ManifestError if no manifest is found or if the file is
    invalid JSON.
    """
    kind = detect_kind(path)
    if kind is None:
        raise ManifestError(
            f"No manifest found in {path} (expected widget.json or blueprint.json)"
        )

    manifest_path = os.path.join(path, manifest_filename(kind))
    try:
        with open(manifest_path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ManifestError(f"Could not read {manifest_path}: {e}") from e

    if kind == KIND_WIDGET:
        return _from_widget(raw, path, manifest_path)
    return _from_blueprint(raw, path, manifest_path)


def _from_widget(raw: dict, path: str, manifest_path: str) -> Manifest:
    meta = raw.get("meta", {}) or {}
    tech_stack = raw.get("tech_stack", {}) or {}
    domain = meta.get("domain", "")
    return Manifest(
        kind=KIND_WIDGET,
        path=path,
        manifest_path=manifest_path,
        id=meta.get("id", ""),
        name=meta.get("name", ""),
        language=tech_stack.get("language", "").lower(),
        version=meta.get("version", ""),
        domains=[domain] if domain else [],
        tags=list(meta.get("tags", []) or []),
        dependencies=[],  # widgets cannot depend on widgets
        raw=raw,
    )


def _from_blueprint(raw: dict, path: str, manifest_path: str) -> Manifest:
    deps_raw = raw.get("dependencies", []) or []
    deps: list[dict[str, str]] = []
    for d in deps_raw:
        if isinstance(d, dict) and "id" in d and "version" in d:
            deps.append({"id": d["id"], "version": d["version"]})
    return Manifest(
        kind=KIND_BLUEPRINT,
        path=path,
        manifest_path=manifest_path,
        id=raw.get("id", ""),
        name=raw.get("name", ""),
        language=str(raw.get("language", "")).lower(),
        version=raw.get("version", ""),
        domains=list(raw.get("domains", []) or []),
        tags=list(raw.get("tags", []) or []),
        dependencies=deps,
        raw=raw,
    )
