"""
Validation stamp - short-circuit re-validation during checkin.

After a successful validate_item(), write_stamp() saves a fingerprint of all
watched files alongside the widget. checkin() calls is_stamp_valid() before
re-running validation; if the stamp is fresh the heavy pipeline is skipped.

Stamp is invalidated automatically when any watched file changes (content or
relative path), or when the recorded language differs from the widget manifest.

Delegates to the infra-file-stamp-python widget for fingerprinting and stamp
I/O. This module adds Cartograph-specific concerns: language engines for
watched_patterns, cartograph version tracking, and test result storage.
"""

import logging

from cg.infra_file_stamp_python.src.file_stamp import (
    write_stamp as _write_stamp,
    read_stamp as _read_stamp,
    is_stamp_valid as _is_stamp_valid,
    adopt_stamp as _adopt_stamp,
)

log = logging.getLogger("cartograph")

STAMP_FILE = ".validation_stamp.json"



def write_stamp(widget_path: str, language: str, engine,
                test_results: dict | None = None) -> None:
    """Write a fresh validation stamp. Called after successful validate_item()."""
    patterns = engine.watched_patterns(widget_path)
    from datetime import datetime, timezone
    rv = engine.runtime_version() if engine else None
    metadata = {
        "language": language,
        "engine_version": getattr(engine, "validation_version", None),
        "runtime": rv,
        "test_results": test_results or {},
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_stamp(widget_path, metadata=metadata, stamp_name=STAMP_FILE,
                 patterns=patterns)
    log.debug("Validation stamp written to %s", widget_path)


def read_stamp(widget_path: str) -> dict | None:
    """Read the validation stamp if it exists, else None."""
    return _read_stamp(widget_path, stamp_name=STAMP_FILE)


def is_stamp_valid(widget_path: str, language: str, engine) -> bool:
    """Return True if the stamp exists, language matches, and no watched file changed."""
    patterns = engine.watched_patterns(widget_path)
    valid = _is_stamp_valid(
        widget_path,
        metadata_match={
            "language": language,
            "engine_version": getattr(engine, "validation_version", None),
        },
        stamp_name=STAMP_FILE,
        patterns=patterns,
    )
    if not valid:
        log.debug("Validation stamp is stale or missing")
    return valid


def adopt_stamp(widget_path: str, stamp: dict, language: str) -> bool:
    """Adopt a stamp transported from a registry, if it matches local files.

    The registry stores the signed stamp uploaded at publish time; download
    returns it alongside the zip. Adopting it re-fingerprints the extracted
    files with the local engine's watched_patterns and writes the stamp only
    on an exact match - proving these bytes are the ones that passed
    validation at publish. Signature policy is the caller's concern (sync
    verifies the HMAC for own-account widgets; install cannot, since keys
    are per-user symmetric).

    Returns True if the stamp was verified and written.
    """
    if not isinstance(stamp, dict) or not stamp.get("fingerprint"):
        return False
    try:
        from .languages import get_engine
        engine = get_engine(language)
        if engine is None:
            return False
        patterns = engine.watched_patterns(widget_path)
    except Exception:
        return False
    return _adopt_stamp(widget_path, stamp, stamp_name=STAMP_FILE,
                        patterns=patterns)


def adopt_registry_stamp(widget_path: str, stamp: dict | None,
                         require_signature: bool = False) -> bool:
    """Adopt a registry-transported validation stamp after extraction.

    Verifies the stamp's fingerprint against the extracted files (always)
    and its HMAC signature against our signing key (when require_signature -
    sync pulls are own-account widgets, so the signature must check out;
    installs of other owners' widgets can't be HMAC-verified with a
    symmetric per-user key and rely on the fingerprint + TLS).

    Returns True if a stamp was adopted. Never raises: an unadoptable stamp
    just leaves the widget unstamped, which is today's behavior.
    """
    if not stamp:
        return False
    if require_signature:
        from .trust import verify_stamp, MissingSigningKeyError
        try:
            if not verify_stamp(stamp):
                log.warning(
                    "Registry stamp signature mismatch at %s - not adopting",
                    widget_path,
                )
                return False
        except MissingSigningKeyError:
            # No local key (e.g. token-only session): fingerprint still gates.
            pass
    import json
    import os
    try:
        with open(os.path.join(widget_path, "widget.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        language = manifest.get("tech_stack", {}).get("language", "")
        if isinstance(language, list):
            language = language[0] if language else ""
    except Exception:
        return False
    # Strip the signature so the adopted stamp matches locally-minted shape.
    stamp = {k: v for k, v in stamp.items() if k != "signature"}
    return adopt_stamp(widget_path, stamp, language)


def has_valid_stamp(widget_path: str, language: str) -> bool:
    """Lightweight scan-time check: does this widget carry a stamp whose
    fingerprint still matches its files?

    Used by the library-integrity gate (Day 40). Unlike is_stamp_valid(),
    this does NOT match on engine_version or language — a stale stamp still
    proves the widget was run through `cartograph checkin` at some point,
    which is what we care about here. A widget that was never checked in
    (attacker drops files into Widget_Library/) has no stamp at all and
    fails this check.

    Returns False on any error (missing engine, unreadable files, etc.)
    so unresolvable widgets stay out of the index.
    """
    try:
        from .languages import get_engine
        engine = get_engine(language)
        if engine is None:
            return False
        patterns = engine.watched_patterns(widget_path)
    except Exception:
        return False
    return _is_stamp_valid(
        widget_path,
        metadata_match=None,
        stamp_name=STAMP_FILE,
        patterns=patterns,
    )
