"""
Blueprint publish: pack a local blueprint into a deterministic tarball
and upload it to the cloud blueprints endpoint.

Composes three foundation widgets:
  - infra-directory-tarball-python    : reproducible pack + sha256
  - infra-version-bump-policy-python  : republish guard vs. cloud baseline
  - cg.infra_pinned_deps_resolver     : (read by install side, not publish)

Cloud transport lives in `cartograph.cloud`. This module is pure
local-pipeline glue: pack → classify → call cloud → return result.
The HTTP plumbing stays open-source in cartograph-cli; the Firestore /
GCS / auth plumbing it talks to lives in cartograph-cloud.
"""

import json
import os

from cg.infra_directory_tarball_python.src.directory_tarball import pack_directory
from cg.infra_version_bump_policy_python.src.version_bump_policy import (
    classify_bump,
)


_PACKED_DIRS = ("src", "tests", "examples")
_PACKED_FILES = ("blueprint.json",)
_EXCLUDE = ("__pycache__", "*.pyc", ".pytest_cache",
            "history", "changelog.json", "reviews.json",
            ".cartograph_source", ".git", ".DS_Store",
            ".cartograph_stamp")


def publish_blueprint(carto, path: str, *, dry_run: bool = False) -> dict:
    """Pack a blueprint dir, classify the bump, and upload.

    Versions are immutable. Same-version republish is rejected by the cloud
    unconditionally — the fix-and-bump model holds for blueprints just like
    widgets. The local pre-check here surfaces that error one round-trip
    earlier with a clearer message.

    Parameters
    ----------
    path:
        A blueprint directory containing blueprint.json. Either a working
        copy or an entry under the local library.
    dry_run:
        Skip the cloud upload; return the prepared payload only. Useful
        for inspecting determinism or wiring tests.
    """
    if not os.path.isdir(path):
        return {"status": "error", "message": f"Path not found: {path}"}

    manifest_path = os.path.join(path, "blueprint.json")
    if not os.path.isfile(manifest_path):
        return {"status": "error", "message": "No blueprint.json found."}

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "error", "message": f"Invalid blueprint.json: {e}"}

    blueprint_id = (manifest.get("id") or "").strip()
    local_version = (manifest.get("version") or "").strip()
    if not blueprint_id:
        return {"status": "error", "message": "blueprint.json missing 'id'"}
    if not local_version:
        return {"status": "error", "message": "blueprint.json missing 'version'"}

    # Preflight: does the target registry accept blueprints at all? The
    # public Cartograph registry (cartograph.tools) intentionally does not.
    # Skip on dry_run so format/determinism inspection works offline.
    if not dry_run:
        gate = _check_registry_allows_blueprints()
        if gate is not None:
            return {"status": "error", "message": gate}

    baseline_version = _fetch_cloud_version(carto, blueprint_id)

    bump = classify_bump(baseline_version, local_version) if baseline_version else "first-publish"
    if baseline_version and bump == "downgrade":
        return {"status": "error", "message": (
            f"Local version {local_version} is older than cloud "
            f"v{baseline_version}. Publishing would downgrade. "
            f"Bump the version in blueprint.json before retrying."
        )}

    payload = _pack(path, blueprint_id)

    result = {
        "status": "success" if dry_run else "pending",
        "blueprint_id": blueprint_id,
        "version": local_version,
        "baseline_version": baseline_version,
        "bump": bump,
        "sha256": payload.sha256,
        "size_bytes": len(payload.data),
        "entry_count": len(payload.entries),
    }

    if dry_run:
        result["dry_run"] = True
        return result

    # Versions are immutable — fix-and-bump model. The cloud is the source
    # of truth; this local pre-check just surfaces the error one round-trip
    # earlier with a clearer message. Runs only on real publish, never on
    # dry_run, so determinism inspection still works on already-published
    # blueprints.
    if baseline_version == local_version:
        return {**result, "status": "error", "message": (
            f"v{local_version} is already published. Versions are "
            f"immutable - bump the version in blueprint.json and retry."
        )}

    upload = _upload_to_cloud(carto, blueprint_id, local_version, payload)
    if "error" in upload:
        return {**result, "status": "error", "message": upload["error"]}
    result["status"] = "success"
    result["upload"] = upload
    return result


def _pack(path: str, blueprint_id: str):
    """Pack blueprint dir contents into a deterministic tarball.

    Each blueprint is packed under an arcname matching its id so the
    tarball is self-describing on the receiving end. Top-level entries
    outside the declared blueprint surfaces (src/tests/examples and
    blueprint.json) are skipped via the exclude list so unexpected
    sibling files are not silently shipped.
    """
    extra_excludes = tuple(
        entry for entry in os.listdir(path)
        if entry not in _PACKED_FILES and entry not in _PACKED_DIRS
    )
    return pack_directory(
        path,
        arcname=blueprint_id,
        exclude=_EXCLUDE + extra_excludes,
    )


def _whoami_handle() -> str | None:
    """Return the caller's cloud handle, or None if unauthenticated."""
    from . import cloud
    me = cloud.whoami()
    if me.get("error"):
        return None
    # whoami returns "owner" (handle) on success; older payloads used "handle".
    return me.get("owner") or me.get("handle")


def _publish_registry_url() -> str | None:
    """Return the configured publish-registry URL, or None for the public default."""
    from .config import get_value, get_registry_url_for_prefix
    configured_prefix, _ = get_value("publish-registry")
    if configured_prefix:
        return get_registry_url_for_prefix(configured_prefix)
    return None


def _check_registry_allows_blueprints() -> str | None:
    """Return None if the configured registry accepts blueprints, otherwise
    a user-facing error message. Older registries that don't expose the
    `allow_blueprints` field are assumed to allow them (backwards compat).
    """
    from . import cloud
    info = cloud.registry_info(registry_url=_publish_registry_url())
    if info.get("error"):
        # Can't reach the registry at all — let the upload fail with the
        # network error rather than swallowing it here. Return None so the
        # publish flow continues and surfaces the real issue.
        return None
    if info.get("allow_blueprints", True) is False:
        return (
            "This registry does not accept blueprints. Blueprints are a "
            "personal-template / org-internal concept - keep them local "
            "(no publish needed) or publish to a private/org registry "
            "that opts in. The public Cartograph registry intentionally "
            "hosts widgets only."
        )
    return None


def _fetch_cloud_version(carto, blueprint_id: str) -> str | None:
    """Return the cloud's currently-published version, or None.

    None means the blueprint has never been published (first-publish) OR
    the cloud is unreachable. Both cases are safe — the server-side
    version-immutability check is the source of truth; this is just an
    early hint for the local republish guard.
    """
    handle = _whoami_handle()
    if not handle:
        return None
    from . import cloud
    res = cloud.inspect_blueprint(handle, blueprint_id,
                                  registry_url=_publish_registry_url())
    if res.get("error"):
        return None
    ver = res.get("version")
    return ver if isinstance(ver, str) and ver else None


def _upload_to_cloud(carto, blueprint_id: str, version: str, payload) -> dict:
    """POST the packed tarball to the cloud blueprints endpoint.

    Returns the registry response on success ({"status": "success", ...})
    or {"error": ...} on failure. The cloud is also responsible for
    enforcing version-immutability — duplicate publishes should never
    pass through silently even if the local pre-check missed.
    """
    from . import cloud
    return cloud.publish_blueprint(
        blueprint_id,
        payload.data,
        visibility="public",
        registry_url=_publish_registry_url(),
    )
