"""
Check-in workflow: push edits from an installed widget back to the library.

Primary flow:
    install(widget_id) → edit files in place → checkin(path, reason="why")

checkin() auto-detects the widget ID from widget.json and whether it already
exists in the library (update) or is new. It scans for project-specific
contamination before accepting anything.

Contamination scanning
----------------------
Hard blocks (checkin fails, no override):
  - Absolute paths baked into source strings (/home/, /Users/, C:\\, C:/)
  - Apparent credential assignments (api_key = "...", token = "abc123", etc.)

Warnings (checkin pauses, agent must pass override_warnings=True + override_reason):
  - os.getenv / os.environ calls
  - Hardcoded non-local URLs / IP addresses
  - Imports that aren't stdlib, listed dependencies, or the widget's own src/

On override the reason is recorded in the changelog entry as an audit trail.
"""

import datetime
import json
import logging
import os
import shutil

from .engine import semver_key as _semver_key, _is_os_metadata
from .languages import get_engine
from .safefs import widget_lock, staged_dir, robust_rmtree, LockTimeout
from .scaffolding import _library_notes as _canonical_library_notes
from .validation_stamp import is_stamp_valid, write_stamp, STAMP_FILE as _STAMP_FILE
from .dep_cache import DEP_STAMP_FILE as _DEP_STAMP_FILE

log = logging.getLogger("cartograph")


def _bump_version(version: str, kind: str) -> str | None:
    """Apply a semver bump (major/minor/patch) to ``version``.

    Returns the bumped "X.Y.Z" string, or None if ``version`` is malformed.
    Single source of truth for how checkin increments versions, so the
    conflict check and the actual bump stay in lockstep.
    """
    try:
        from packaging.version import Version as _Version
        parsed = _Version(version)
        major, minor, patch = parsed.major, parsed.minor, parsed.micro
    except Exception:
        return None
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    elif kind == "patch":
        patch += 1
    return f"{major}.{minor}.{patch}"


class _CheckinAbort(Exception):
    """Internal: abort the staged library write and return a clean error.

    Raised inside the staged build so the exception unwinds ``staged_dir``
    (discarding the half-built directory, leaving the library untouched) and is
    converted to an error result by the caller.
    """


def _artifact_skip_set(language: str | None) -> set[str]:
    """Return the build-artifact dirs to skip when copying widget files.

    Sources from the universal-build-artifact-ignore widget so updates flow
    everywhere at once. Falls back to a minimal hardcoded set if the widget
    can't be imported (e.g. partial install).
    """
    try:
        from cg.universal_build_artifact_ignore_python.src.build_artifact_ignore import (
            excludes_for,
        )
        return set(excludes_for(language=language))
    except ImportError:
        return {"__pycache__", ".pytest_cache", "node_modules", ".git"}


def _os_metadata_patterns() -> tuple:
    """Glob patterns for OS-scattered metadata (.DS_Store, AppleDouble
    `._*`, __MACOSX, Thumbs.db). Finder drops these into working copies;
    without this filter they get checked into the library and propagate
    to every consumer on install."""
    try:
        from cg.universal_build_artifact_ignore_python.src.build_artifact_ignore import (
            os_metadata_glob_patterns,
        )
        return os_metadata_glob_patterns()
    except ImportError:
        return (".DS_Store", "._*", "__MACOSX", "Thumbs.db", "desktop.ini")


def _restore_library_notes(manifest_path: str) -> None:
    """Overwrite library_notes in widget.json with the canonical version.

    Called just before copying to the library so agents cannot drift or
    remove the library-wide standards even if they edited widget.json.
    """
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    language = data.get("tech_stack", {}).get("language", "")
    if isinstance(language, list):
        language = language[0] if language else ""
    domain = data.get("meta", {}).get("domain", "")
    canonical = _canonical_library_notes(language, domain)
    if canonical:
        data["library_notes"] = canonical
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


# Contamination scanning is now in contamination.py, delegated to per-language engines.
# Kept as import alias for backwards compatibility within this module.
from .contamination import scan_contamination as _scan_contamination


def _manifest_core(data: dict) -> dict:
    """Normalized widget.json for no-op comparison. Strips auto-managed fields
    that change during checkin itself (version bump, stamp writes) so legitimate
    metadata edits (description, tags, dependencies) are detected as real content.
    """
    out = json.loads(json.dumps(data))  # deep copy
    meta = out.get("meta", {})
    meta.pop("version", None)
    return out


def _lookup_cloud_baseline(path: str, item_id: str,
                           library_path: str | None = None) -> dict | None:
    """Return {"version": ..., "implementation_hash": ..., "source": "cloud"} if a
    `.cartograph_source` sidecar points at a cloud record for this widget, else None.

    `implementation_hash` is only populated when the registry echoes it back in the
    inspect response (issue #15). When present, it lets the no-op guard and the
    three-way status view run against the cloud baseline without downloading the
    widget zip. When absent, callers fall back to their pre-echo behavior gracefully.

    Checks `path` for a sidecar first (cloud-installed widgets), then falls back to
    `library_path` (widgets installed from the local library whose library copy was
    published to cloud). Falls back silently when offline, unauthenticated, or the
    widget isn't resolvable in the registry.
    """
    sidecar_path = os.path.join(path, ".cartograph_source")
    if not os.path.isfile(sidecar_path):
        if library_path:
            sidecar_path = os.path.join(library_path, ".cartograph_source")
        if not os.path.isfile(sidecar_path):
            return None
    try:
        with open(sidecar_path, encoding="utf-8") as f:
            source = json.load(f)
    except Exception:
        return None
    owner = source.get("owner")
    registry_url = source.get("registry_url")
    if not owner:
        return None
    try:
        from . import cloud, auth
        if not cloud.is_available():
            return None
    except Exception:
        return None
    try:
        remote = cloud.inspect(owner, item_id, manifest=True,
                               registry_url=registry_url)
    except Exception:
        return None
    if not remote or remote.get("error"):
        return None
    version = remote.get("version")
    if not version:
        return None
    result = {"version": version, "source": "cloud"}
    impl_hash = remote.get("implementation_hash")
    if impl_hash:
        result["implementation_hash"] = impl_hash
    # Raw widget.json from the cloud zip. When present, the no-op guard
    # can detect metadata-only edits (description/tags/deps). When absent
    # (older server, missing zip), guard falls back to skipping the meta
    # compare — no false positives.
    manifest = remote.get("manifest")
    if isinstance(manifest, dict):
        result["manifest"] = manifest
    return result


# ---------------------------------------------------------------------------
# checkin
# ---------------------------------------------------------------------------

def checkin(carto, path: str, reason: str = "", version_bump: str = "minor",
            override_warnings: bool = False, override_reason: str = "") -> dict:
    """
    Push an edited widget back to the library.

    Detects update vs new from whether the widget ID already exists in the
    library. Never deletes or moves the source — the installed copy is left
    intact after a successful checkin.

    Args:
        path:              Directory containing widget.json and src/
        reason:            Human/agent description of what changed
        version_bump:      "major" | "minor" | "patch"  (default "minor")
        override_warnings: Pass True to proceed despite contamination warnings
        override_reason:   Required when override_warnings=True — recorded in changelog
    """
    if not os.path.isdir(path):
        return {"status": "error", "message": f"Path not found: {path}"}

    # --- Read manifest ---
    manifest_path = os.path.join(path, "widget.json")
    if not os.path.exists(manifest_path):
        return {"status": "error", "message": "No widget.json found."}

    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Invalid widget.json: {e}"}

    meta = data.get("meta", {})
    item_id = meta.get("id", "").strip()
    if not item_id:
        return {"status": "error", "message": "widget.json is missing meta.id"}

    # --- Validate (skip if a fresh stamp exists) ---
    tech_stack = data.get("tech_stack", {})
    language = tech_stack.get("language", "python").lower()
    engine = get_engine(language)
    if engine and is_stamp_valid(path, language, engine):
        log.info("Validation stamp is fresh — skipping re-validation")
    else:
        val = carto.validate_item(path)
        if val["status"] != "success":
            return val

    # --- Resolve bump baseline: cloud (via sidecar) wins over local library ---
    # If a .cartograph_source sidecar exists, the widget was installed from the
    # cloud and cloud is the source of truth for "what version exists." Local
    # library is the fallback when there's no sidecar.
    widget_record = next((w for w in carto.widgets if w["id"] == item_id), None)
    cloud_baseline = _lookup_cloud_baseline(path, item_id)

    baseline_version = None
    baseline_source = None
    if cloud_baseline:
        baseline_version = cloud_baseline["version"]
        baseline_source = "cloud"
    elif widget_record:
        baseline_version = widget_record.get("version", "0.0.0")
        baseline_source = "library"

    if baseline_version is not None:
        local_version = meta.get("version", "0.0.0")
        if local_version != baseline_version:
            local_behind = _semver_key(local_version) < _semver_key(baseline_version)
            if local_behind:
                # The widget already exists at a newer version. Re-applying edits
                # on a stale copy would clobber that newer work, so rebase first:
                # upgrade pulls the latest baseline, then re-apply and checkin.
                return {
                    "status": "error",
                    "message": f"{item_id}: local is v{local_version} but {baseline_source} "
                               f"already has v{baseline_version}. Run "
                               f"`cartograph upgrade {item_id}` to get the latest, "
                               f"re-apply your changes, then checkin.",
                }
            # local ahead of baseline — the user hand-bumped meta.version
            # instead of letting --bump do it. Accept it as a pre-applied bump
            # IFF it exactly matches the bump they asked for, applied to the
            # resolved baseline (cloud when the sidecar routes there, else
            # library). Otherwise their manual version is inconsistent with the
            # requested --bump and we error with the exact value to use.
            expected = _bump_version(baseline_version, version_bump)
            if expected is None:
                return {"status": "error",
                        "message": f"Cannot compare against malformed {baseline_source} "
                                   f"version '{baseline_version}'."}
            if local_version != expected:
                # Figure out which --bump level the manual edit corresponds to,
                # so we can hand the user the exact command to run.
                intended = next(
                    (k for k in ("patch", "minor", "major")
                     if _bump_version(baseline_version, k) == local_version),
                    None,
                )
                if intended:
                    fix = (f"Your edit is a {intended} bump of v{baseline_version} - "
                           f"re-run with `--bump {intended}`.")
                else:
                    fix = (f"v{local_version} isn't a clean patch/minor/major bump of "
                           f"v{baseline_version} - reset meta.version to v{baseline_version} "
                           f"and let `--bump` set it.")
                return {
                    "status": "error",
                    "message": f"{item_id}: local is v{local_version} but a {version_bump} "
                               f"bump of {baseline_source} v{baseline_version} is v{expected}. "
                               + fix,
                }
            # Valid pre-applied bump: fall through. new_version is computed from
            # the baseline below, so it lands on exactly v{expected} == local
            # with no double-bump.
        # --- No-op guard ---
        # Runs against whichever baseline we resolved, as long as that baseline
        # has an implementation_hash to compare against. Library always does;
        # cloud does once the registry starts echoing it (issue #15). If the
        # cloud baseline has no hash yet, the guard is silently skipped for
        # that case — no false-positive bumps, no missed real changes.
        if cloud_baseline:
            baseline_hash = cloud_baseline.get("implementation_hash")
        else:
            baseline_hash = widget_record.get("implementation_hash")
        if baseline_hash:
            current_hash = carto._calculate_implementation_hash(path)
            code_identical = current_hash == baseline_hash
            # implementation_hash covers src/tests/examples but not widget.json,
            # so the guard also needs to compare manifest metadata (description,
            # tags, dependencies, etc.) to catch "widget.json only" edits. Strip
            # auto-managed fields from both sides before comparing. For cloud
            # baselines, widget.json is fetched via ?include_manifest=true on
            # inspect (closes issue #15); for library baselines, it's read
            # from disk. If the baseline manifest can't be obtained, the meta
            # compare is skipped rather than false-positive as identical.
            meta_identical = False
            if code_identical:
                baseline_manifest = None
                if cloud_baseline:
                    baseline_manifest = cloud_baseline.get("manifest")
                elif widget_record is not None:
                    lib_manifest = os.path.join(widget_record["path"], "widget.json")
                    try:
                        with open(lib_manifest, encoding="utf-8") as f:
                            baseline_manifest = json.load(f)
                    except Exception:
                        baseline_manifest = None
                if baseline_manifest is not None:
                    meta_identical = (
                        _manifest_core(data) == _manifest_core(baseline_manifest)
                    )
            if code_identical and meta_identical:
                return {
                    "status": "error",
                    "message": f"{item_id} v{baseline_version} is already in the {baseline_source} with identical content. "
                               f"Make your changes before checking in."
                }

    # --- Contamination scan ---
    scan = _scan_contamination(path)

    if scan["blocks"]:
        return {
            "status": "error",
            "message": "Checkin blocked: project-specific content detected.",
            "blocks": scan["blocks"],
        }

    if scan["warnings"] and not override_warnings:
        return {
            "status": "warnings",
            "message": "Checkin paused: potential contamination found. "
                       "Review warnings below, then re-run with "
                       "--override-warnings --override-reason \"<why these warnings are acceptable>\".",
            "warnings": scan["warnings"],
        }

    if override_warnings and not override_reason:
        return {"status": "error",
                "message": "--override-reason is required when using --override-warnings."}

    # --- Determine update vs new ---
    # is_update still controls local file placement (dest path, archive) and must
    # reflect whether a local library copy exists. Bumping is gated separately
    # on baseline_version — which includes cloud — so a widget known only to
    # cloud still gets bumped correctly.
    is_update = widget_record is not None
    should_bump = baseline_version is not None

    # --- Version bump (whenever a baseline exists, local or cloud) ---
    # Bump from the resolved baseline, NOT the local manifest version. They're
    # equal in the normal case; when the user pre-bumped meta.version (already
    # validated above to match this --bump), bumping the baseline lands on the
    # same value with no double-bump.
    version = meta.get("version", "1.0.0")
    if should_bump:
        new_version = _bump_version(baseline_version, version_bump)
        if new_version is None:
            return {"status": "error", "message": f"Cannot bump malformed version '{baseline_version}'. Fix the {baseline_source} version to X.Y.Z first."}
    else:
        new_version = version

    data["meta"]["version"] = new_version

    # --- Stamp validation metadata into widget.json ---
    validation_block = {
        "engine_version": getattr(engine, "validation_version", None),
        "validated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if engine:
        rv = engine.runtime_version()
        if rv:
            validation_block["runtime"] = rv
    data["validation"] = validation_block

    # --- Determine destination ---
    if is_update:
        dest_path = widget_record["path"]
    else:
        dest_path = os.path.join(carto.library_path, item_id)
        if os.path.exists(dest_path):
            return {"status": "error",
                    "message": f"Directory already exists but widget is not in index: {dest_path}"}

    changelog_entry = {
        "version": new_version,
        "reason": reason or ("No reason provided" if is_update else "Initial release"),
        "timestamp": datetime.datetime.now().isoformat(),
    }
    if override_warnings and override_reason:
        changelog_entry["override_reason"] = override_reason

    artifact_skips = _artifact_skip_set(language)
    # Files that live only in the library (not the working copy) and are carried
    # over / regenerated rather than copied from source.
    carry_skips = artifact_skips | {
        "history", "changelog.json", _STAMP_FILE, _DEP_STAMP_FILE,
        ".cartograph_source",
    }
    os_metadata = _os_metadata_patterns()
    copy_ignore = shutil.ignore_patterns(
        *artifact_skips, *os_metadata, "*.pyc", _DEP_STAMP_FILE,
        ".cartograph_source")
    snapshot_ignore = shutil.ignore_patterns(
        *artifact_skips, *os_metadata, "*.pyc",
        "history", "changelog.json", _STAMP_FILE, _DEP_STAMP_FILE,
        ".cartograph_source",
    )

    # Mutation is scoped to THIS widget's lock, and the new widget directory is
    # built in a staged sibling then swapped in atomically: the live entry is
    # never torn down before its replacement is ready, and a concurrent
    # checkin/sync/delete of the SAME widget can't interleave - while checkins
    # of OTHER widgets proceed in parallel.
    diff = None
    try:
        with widget_lock(carto.library_path, item_id):
            diff = carto._diff_against_library(path, item_id) if is_update else None
            with staged_dir(dest_path) as staged:
                # 1. working copy -> staged (history/changelog/stamp/sidecar excluded)
                for item in os.listdir(path):
                    if item in carry_skips:
                        continue
                    src = os.path.join(path, item)
                    dst = os.path.join(staged, item)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, ignore=copy_ignore)
                    else:
                        shutil.copy2(src, dst)

                # 2. canonical library_notes into the staged manifest, then read
                #    it back so the source manifest written later matches exactly.
                staged_manifest = os.path.join(staged, "widget.json")
                with open(staged_manifest, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                try:
                    _restore_library_notes(staged_manifest)
                except Exception as e:
                    # Abort cleanly: the exception unwinds staged_dir (which
                    # discards the half-built dir, leaving the library intact)
                    # and is turned into an error result below.
                    raise _CheckinAbort(
                        f"Could not restore canonical library_notes: {e}") from e
                with open(staged_manifest, encoding="utf-8") as f:
                    data = json.load(f)

                # 3. carry history + sidecar from the existing library entry and
                #    archive the previous version into history/<old_version>/.
                old_changelog = []
                if is_update and os.path.exists(dest_path):
                    old_history = os.path.join(dest_path, "history")
                    if os.path.isdir(old_history):
                        shutil.copytree(old_history, os.path.join(staged, "history"))
                    old_version = widget_record.get("version", "unknown")
                    snap = os.path.join(staged, "history", old_version)
                    if os.path.exists(snap):
                        robust_rmtree(snap)
                    os.makedirs(snap, exist_ok=True)
                    for item in os.listdir(dest_path):
                        if item in carry_skips:
                            continue
                        s = os.path.join(dest_path, item)
                        d = os.path.join(snap, item)
                        if os.path.isdir(s):
                            shutil.copytree(s, d, ignore=snapshot_ignore)
                        else:
                            shutil.copy2(s, d)
                    sidecar = os.path.join(dest_path, ".cartograph_source")
                    if os.path.exists(sidecar):
                        shutil.copy2(sidecar, os.path.join(staged, ".cartograph_source"))

                    # Prior changelog: preserve a corrupt one (don't silently
                    # drop the history) by parking it next to the new changelog.
                    clog = os.path.join(dest_path, "changelog.json")
                    if os.path.exists(clog):
                        try:
                            with open(clog, encoding="utf-8") as f:
                                loaded = json.load(f)
                            if not isinstance(loaded, list):
                                raise ValueError("changelog.json is not a list")
                            old_changelog = loaded
                        except Exception as e:
                            log.warning("Existing changelog.json is unreadable (%s); "
                                        "preserving it as changelog.json.corrupt", e)
                            shutil.copy2(clog, os.path.join(staged, "changelog.json.corrupt"))

                # 4. changelog (newest first)
                changelog = [changelog_entry, *old_changelog]
                with open(os.path.join(staged, "changelog.json"), "w", encoding="utf-8") as f:
                    json.dump(changelog, f, indent=2)

                # 5. stamp over the staged (final) files
                if engine:
                    try:
                        write_stamp(staged, language, engine)
                    except OSError as e:
                        log.warning("Could not write validation stamp after checkin: %s", e)
            # staged_dir swapped the new directory into dest_path atomically here.

            # The library write succeeded; only now reflect the bump in the
            # source manifest. Done last so a mid-write failure leaves source and
            # library consistent at the old version rather than desynced.
            try:
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except OSError as e:
                log.warning("Library updated but could not update source manifest: %s", e)
    except LockTimeout as e:
        return {"status": "error", "message": str(e)}
    except _CheckinAbort as e:
        return {"status": "error", "message": str(e)}

    # --- Reload library so the in-memory index reflects the new widget ---
    carto.reload()

    action = "updated" if is_update else "registered"
    log.info("Successfully %s %s → v%s", action, item_id, new_version)

    messages = {
        "registered": f"Added {item_id} v{new_version} to local library (first-time entry).",
        "updated": f"Bumped {item_id} to v{new_version} in local library.",
    }
    result = {
        "status": "success",
        "id": item_id,
        "version": new_version,
        "action": action,
        "message": messages[action],
        "path": dest_path,
    }
    if scan["warnings"] and override_warnings:
        result["overridden_warnings"] = scan["warnings"]
        result["override_reason"] = override_reason
    if diff:
        result["diff"] = diff
        result["diff_prompt"] = (
            "Review this diff for any remaining project-specific code, hardcoded values, "
            "or patterns that would break generic reuse."
        )
    return result


# ---------------------------------------------------------------------------
# restore, add_review, widget_status
# ---------------------------------------------------------------------------

def restore(carto, item_id, version, reason):
    """Restore a historical version to become the new head."""
    item = next((w for w in carto.widgets if w["id"] == item_id), None)
    if not item:
        return {"status": "error", "message": f"Item '{item_id}' not found"}

    history_path = os.path.join(item["path"], "history", version)
    if not os.path.exists(history_path):
        return {"status": "error", "message": f"Version '{version}' not found in history for {item_id}"}

    # Copy history version to a temp dir, then checkin as update
    import tempfile
    temp_dir = tempfile.mkdtemp(prefix=f"cartograph_restore_{item_id}_")
    robust_rmtree(temp_dir)  # mkdtemp creates it, copytree needs it to not exist
    shutil.copytree(history_path, temp_dir)

    # Set manifest version to current library version so the version guard
    # passes, then checkin() bumps it normally (patch).
    current_version = item.get("version", "1.0.0")
    manifest_path = os.path.join(temp_dir, "widget.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["meta"]["version"] = current_version
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    result = checkin(carto, temp_dir, reason=f"RESTORE from v{version}: {reason}",
                     version_bump="patch")
    robust_rmtree(temp_dir, ignore_errors=True)
    return result


def _get_reviewer() -> str:
    """Return the authenticated user's handle, or empty string."""
    try:
        from .auth import is_authenticated
        if is_authenticated():
            from .cloud import whoami
            profile = whoami()
            return profile.get("owner", "") or profile.get("username", "")
    except Exception:
        pass
    return ""


def write_review(widget_path: str, score: float, version: str,
                 comment: str | None = None) -> dict:
    """Write a review entry to reviews.json at *widget_path*. Returns result dict."""
    author = _get_reviewer()
    entry = {
        "rating": score,
        "version": version,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    if author:
        entry["author"] = author
    if comment:
        entry["comment"] = comment

    review_path = os.path.join(widget_path, "reviews.json")
    reviews_data = {"reviews": []}
    if os.path.exists(review_path):
        try:
            with open(review_path, encoding="utf-8") as f:
                reviews_data = json.load(f)
        except Exception:
            pass

    reviews_data["reviews"].append(entry)
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(reviews_data, f, indent=2)

    avg = sum(r["rating"] for r in reviews_data["reviews"]) / len(reviews_data["reviews"])
    return {"status": "success", "rating": score, "avg_rating": round(avg, 1), "author": author}


def add_review(carto, widget_id, target_dir, score, comment=None):
    """Add a review to a widget. Must be installed at target_dir/widget_id/."""
    from .engine import python_dir_name, DEFAULT_INSTALL_DIR
    installed_path = os.path.join(target_dir, DEFAULT_INSTALL_DIR, python_dir_name(widget_id))
    if not os.path.exists(installed_path):
        return {"error": f"'{widget_id}' not found at {installed_path}. Install it first."}

    # Strip registry prefix (e.g. cg-foo-python -> foo-python) for library lookup
    from .installer import _resolve_registry
    resolved = _resolve_registry(widget_id)
    library_id = resolved[2] if resolved else widget_id

    widget = next((w for w in carto.widgets if w["id"] == library_id), None)
    if not widget:
        return {"error": f"Widget '{library_id}' not found in library."}

    try:
        score = float(score)
        if not (1 <= score <= 5):
            raise ValueError
    except (ValueError, TypeError):
        return {"error": "Score must be a number between 1 and 5."}

    return write_review(widget["path"], score, widget.get("version", "unknown"), comment)


def _modified_files(installed_path: str, library_path: str) -> dict:
    """Return a per-file breakdown of how an installed widget differs from
    its library version. Hashes each file under src/, tests/, examples/,
    and python/ and reports added/removed/changed lists. __pycache__ and
    .pyc are skipped — same exclusions as the implementation hash, so
    flags only fire on real content drift. python/ covers openscad's
    optional sidecar."""
    import hashlib

    def collect(base: str) -> dict:
        out = {}
        for subdir in ("src", "tests", "examples", "python"):
            root_path = os.path.join(base, subdir)
            if not os.path.isdir(root_path):
                continue
            for root, dirs, files in os.walk(root_path):
                dirs[:] = [d for d in dirs
                           if d != "__pycache__" and not _is_os_metadata(d)]
                for name in files:
                    if name.endswith(".pyc") or _is_os_metadata(name):
                        continue
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, base)
                    try:
                        with open(full, "rb") as f:
                            out[rel] = hashlib.md5(f.read()).hexdigest()
                    except Exception:
                        out[rel] = None
        return out

    lib = collect(library_path)
    local = collect(installed_path)
    added = sorted(set(local) - set(lib))
    removed = sorted(set(lib) - set(local))
    changed = sorted(p for p in (set(lib) & set(local)) if lib[p] != local[p])
    return {"added": added, "removed": removed, "changed": changed}


def widget_status(carto, widget_id, target_dir, check_cloud=True):
    """Check the status of an installed widget against the library."""
    from .engine import python_dir_name, DEFAULT_INSTALL_DIR
    installed_path = os.path.join(target_dir, DEFAULT_INSTALL_DIR, python_dir_name(widget_id))
    if not os.path.exists(installed_path):
        return {"error": f"'{widget_id}' not found at {installed_path}."}

    # Strip registry prefix (e.g. cg-foo-python -> foo-python) for library lookup
    from .installer import _resolve_registry
    resolved = _resolve_registry(widget_id)
    library_id = resolved[2] if resolved else widget_id

    widget = next((w for w in carto.widgets if w["id"] == library_id), None)
    if not widget:
        return {"error": f"'{library_id}' not found in library."}

    try:
        with open(os.path.join(installed_path, "widget.json"), encoding="utf-8") as f:
            installed_version = json.load(f).get("meta", {}).get("version", "unknown")
    except Exception as e:
        return {"error": f"Failed to read installed manifest: {e}"}

    library_version = widget.get("version", "0.0.0")
    installed_hash = carto._calculate_implementation_hash(installed_path)
    library_hash = widget.get("implementation_hash")

    outdated = installed_version != library_version
    modified = installed_hash != library_hash

    # Cloud three-way comparison. Falls back to library sidecar so locally-installed
    # widgets whose library copy is published to cloud also get a cloud version check.
    cloud_info = (_lookup_cloud_baseline(installed_path, library_id,
                                        library_path=widget.get("path"))
                  or {}) if check_cloud else {}
    cloud_version = cloud_info.get("version")
    cloud_hash = cloud_info.get("implementation_hash")

    response = {
        "widget_id": widget_id,
        "installed_version": installed_version,
        "library_version": library_version,
        **({"cloud_version": cloud_version,
            "outdated_vs_cloud": installed_version != cloud_version}
           if cloud_version else {}),
        **({"modified_vs_cloud": installed_hash != cloud_hash}
           if cloud_hash else {}),
        "outdated": outdated,
        "modified": modified,
    }
    # When modified, surface which files actually differ. Saves the user
    # from "modified=True, but what?" debugging. Library is the baseline.
    if modified:
        response["modifications"] = _modified_files(installed_path, widget["path"])
    return response
