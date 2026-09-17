"""Validation stamps for probes.

A probe that validated once is not validated forever — the file moves and the
stamp must die with it. The stamp records the package hash that passed level-1
validation; `probe run` compares the live hash against it and refuses to
measure with an instrument that has not passed since it last changed.

Stamp lives inside the probe package (`.validation.json`) so it travels with
the instrument. It is NOT part of the hashed set.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

STAMP_NAME = ".validation.json"
STAMP_SCHEMA_VERSION = 1

# States returned by check_probe_stamp
STAMP_VALID = "valid"
STAMP_MISSING = "missing"
STAMP_STALE = "stale"
STAMP_FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _hashed_files(pdir: Path) -> list[Path]:
    """Everything that can change what the instrument measures.

    probe.json (declaration: entry, kind, duration, inputs) + every .py in the
    package + requirements.txt. Excludes __pycache__ and the stamp itself.
    """
    paths: list[Path] = []
    meta = pdir / "probe.json"
    if meta.is_file():
        paths.append(meta)
    req = pdir / "requirements.txt"
    if req.is_file():
        paths.append(req)
    for path in pdir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.is_file():
            paths.append(path)
    return sorted(set(paths), key=lambda p: str(p.relative_to(pdir)))


def probe_package_hash(pdir: Path) -> str:
    """sha256 over the probe package's declaration + source."""
    digest = hashlib.sha256()
    for path in _hashed_files(pdir):
        digest.update(str(path.relative_to(pdir)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def stamp_path(pdir: Path) -> Path:
    return pdir / STAMP_NAME


def read_probe_stamp(pdir: Path) -> dict[str, Any] | None:
    path = stamp_path(pdir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_probe_stamp(
    pdir: Path,
    *,
    ok: bool,
    probe_id: str | None = None,
    level: int = 1,
    blocks: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Record the outcome of a validation against the current package hash."""
    doc = {
        "schema_version": STAMP_SCHEMA_VERSION,
        "probe_id": probe_id or pdir.name,
        "ok": bool(ok),
        "level": level,
        "source_sha256": probe_package_hash(pdir),
        "validated_at": _now(),
        "terra_version": __version__,
        "blocks": list(blocks or []),
        "warnings": list(warnings or []),
    }
    try:
        stamp_path(pdir).write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        # A read-only instrument dir must not break validation itself; the
        # missing stamp simply means run() will revalidate.
        pass
    return doc


def check_probe_stamp(pdir: Path) -> dict[str, Any]:
    """Compare the live package hash against the stored validation stamp.

    Returns {state, current_sha256, stamp, reason} where state is one of
    valid | missing | stale | failed.
    """
    current = probe_package_hash(pdir)
    stamp = read_probe_stamp(pdir)
    if stamp is None:
        return {
            "state": STAMP_MISSING,
            "current_sha256": current,
            "stamp": None,
            "reason": "probe has never been validated (no validation stamp)",
        }
    stored = stamp.get("source_sha256")
    if stored != current:
        return {
            "state": STAMP_STALE,
            "current_sha256": current,
            "stamp": stamp,
            "reason": (
                "probe package changed since it was validated "
                f"(stamp {str(stored)[:12]}… vs current {current[:12]}…)"
            ),
        }
    if not stamp.get("ok"):
        return {
            "state": STAMP_FAILED,
            "current_sha256": current,
            "stamp": stamp,
            "reason": "last validation of this exact package FAILED",
        }
    return {
        "state": STAMP_VALID,
        "current_sha256": current,
        "stamp": stamp,
        "reason": "",
    }


def ensure_validated(pdir: Path, *, probe_id: str | None = None) -> dict[str, Any]:
    """Gate a probe run on a valid stamp; revalidate when it is not.

    Returns a provenance dict for the run record. Raises ValueError when the
    probe cannot pass level-1 validation.
    """
    check = check_probe_stamp(pdir)
    state = check["state"]
    if state == STAMP_VALID:
        stamp = check["stamp"] or {}
        return {
            "state": STAMP_VALID,
            "revalidated": False,
            "source_sha256": check["current_sha256"],
            "validated_at": stamp.get("validated_at"),
            "level": stamp.get("level", 1),
        }

    from .probe_validate import validate_probe_dir

    result = validate_probe_dir(pdir)
    if not result.get("ok"):
        blocks = result.get("blocks") or ["validation failed"]
        raise ValueError(
            f"probe {probe_id or pdir.name!r} is not validated "
            f"({check['reason']}) and re-validation FAILED — no run stamped:\n  - "
            + "\n  - ".join(str(b) for b in blocks)
            + "\n  fix probe.py, then: terra probe validate "
            + (probe_id or pdir.name)
        )
    stamp = read_probe_stamp(pdir) or {}
    return {
        "state": state,
        "revalidated": True,
        "source_sha256": stamp.get("source_sha256") or check["current_sha256"],
        "validated_at": stamp.get("validated_at"),
        "level": stamp.get("level", 1),
        "reason": check["reason"],
    }
