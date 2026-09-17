"""Suites — ordered probe recipes with a shared default `to`.

Composition only: no domain knowledge. A suite is probe ids + optional default_to.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    ensure_suites_store,
    probe_dir,
    suite_path,
    suites_root,
)
from .probe_run import DEFAULT_RUN_TIMEOUT_S, run_probe
from .probe_validate import validate_probe_dir

SUITE_SCHEMA_VERSION = 1
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_probe_list(raw: str) -> list[str]:
    """Parse comma/space-separated probe ids."""
    parts = [p.strip() for chunk in raw.replace(",", " ").split() for p in [chunk]]
    out = [p for p in parts if p]
    if not out:
        raise ValueError("probe list is empty")
    for p in out:
        if not _SLUG_RE.match(p):
            raise ValueError(
                f"probe id {p!r} must be a slug (a-z, then a-z0-9_)"
            )
    return out


def validate_suite_record(data: Any, *, expected_id: str | None = None) -> list[str]:
    blocks: list[str] = []
    if not isinstance(data, dict):
        return ["suite must be a JSON object"]
    if data.get("schema_version") != SUITE_SCHEMA_VERSION:
        blocks.append(
            f"schema_version must be {SUITE_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )
    sid = data.get("id")
    if not isinstance(sid, str) or not sid.strip():
        blocks.append("id must be a non-empty string")
    elif expected_id is not None and sid != expected_id:
        blocks.append(f"id {sid!r} does not match filename {expected_id!r}")
    probes = data.get("probes")
    if not isinstance(probes, list) or len(probes) < 1:
        blocks.append("probes must be a non-empty list of probe ids")
    else:
        for i, p in enumerate(probes):
            if not isinstance(p, str) or not p.strip():
                blocks.append(f"probes[{i}] must be a non-empty string")
            elif not _SLUG_RE.match(p):
                blocks.append(f"probes[{i}] {p!r} is not a valid slug")
    # default_to optional; if present must be non-null object/string for freeform
    if "default_to" in data and data["default_to"] is not None:
        dto = data["default_to"]
        if isinstance(dto, dict) and len(dto) == 0:
            blocks.append("default_to must not be an empty object if set")
        if isinstance(dto, str) and not dto.strip():
            blocks.append("default_to string must be non-empty if set")
    return blocks


def create_suite(
    project_root: Path,
    suite_id: str,
    *,
    probes: list[str],
    default_to: Any | None = None,
    purpose: str = "",
    force: bool = False,
) -> Path:
    if not _SLUG_RE.match(suite_id):
        raise ValueError(f"suite id {suite_id!r} must match {_SLUG_RE.pattern}")
    if not probes:
        raise ValueError("probes list must be non-empty")
    ensure_suites_store(project_root)
    path = suite_path(project_root, suite_id)
    if path.exists() and not force:
        raise FileExistsError(f"suite already exists: {path}")

    # Soft check probes exist at create time (warn via missing later on run)
    missing = [p for p in probes if not probe_dir(project_root, p).is_dir()]
    if missing:
        raise FileNotFoundError(
            "unknown probe(s) — create them first: " + ", ".join(missing)
        )

    now = _now()
    record: dict[str, Any] = {
        "schema_version": SUITE_SCHEMA_VERSION,
        "id": suite_id,
        "purpose": (purpose or "").strip()
        or f"ordered run of {', '.join(probes)}",
        "probes": list(probes),
        "default_to": default_to,
        "created_at": now,
        "updated_at": now,
    }
    blocks = validate_suite_record(record, expected_id=suite_id)
    if blocks:
        raise ValueError("invalid suite:\n  - " + "\n  - ".join(blocks))
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_suite(project_root: Path, suite_id: str) -> dict[str, Any]:
    path = suite_path(project_root, suite_id)
    if not path.is_file():
        raise FileNotFoundError(f"suite not found: {suite_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_suites(project_root: Path) -> list[dict[str, Any]]:
    root = suites_root(project_root)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            out.append({"id": path.stem, "ok": False, "blocks": [str(e)], "record": None})
            continue
        blocks = validate_suite_record(data, expected_id=path.stem)
        out.append(
            {
                "id": path.stem,
                "ok": len(blocks) == 0,
                "blocks": blocks,
                "record": data,
            }
        )
    return out


def validate_suite(project_root: Path, suite_id: str) -> dict[str, Any]:
    """Validate suite meta + level-1 each listed probe (design bar, not live run)."""
    path = suite_path(project_root, suite_id)
    if not path.is_file():
        return {
            "ok": False,
            "id": suite_id,
            "path": str(path),
            "blocks": [f"suite not found: {suite_id}"],
            "probes": [],
            "record": None,
        }
    try:
        suite = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {
            "ok": False,
            "id": suite_id,
            "path": str(path),
            "blocks": [f"unreadable suite: {e}"],
            "probes": [],
            "record": None,
        }

    blocks = validate_suite_record(suite, expected_id=suite_id)
    probe_results: list[dict[str, Any]] = []
    for pid in suite.get("probes") or []:
        if not isinstance(pid, str):
            continue
        pdir = probe_dir(project_root, pid)
        if not pdir.is_dir():
            msg = f"probe {pid!r} missing (no {pdir})"
            blocks.append(msg)
            probe_results.append(
                {"id": pid, "ok": False, "blocks": [msg], "path": str(pdir)}
            )
            continue
        pr = validate_probe_dir(pdir)
        probe_results.append(pr)
        if not pr.get("ok"):
            for b in pr.get("blocks") or []:
                blocks.append(f"probe {pid}: {b}")

    return {
        "ok": len(blocks) == 0,
        "id": suite_id,
        "path": str(path),
        "blocks": blocks,
        "probes": probe_results,
        "record": suite,
    }


def validate_all_suites(project_root: Path) -> dict[str, Any]:
    rows = []
    for item in list_suites(project_root):
        sid = item["id"]
        rows.append(validate_suite(project_root, sid))
    any_fail = any(not r["ok"] for r in rows)
    return {
        "ok": not any_fail,
        "blocks": [],
        "suites": rows,
        "count": len(rows),
    }


def run_suite(
    project_root: Path,
    suite_id: str,
    *,
    to: Any | None = None,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
    dry_run: bool = False,
    stop_on_error: bool = True,
    strict_to: bool = False,
    strict_status: bool = False,
) -> dict[str, Any]:
    """Run each probe in order with shared `to` (override suite default_to).

    Returns summary with per-probe stamps or errors. Does not invent domain logic.
    """
    suite = load_suite(project_root, suite_id)
    blocks = validate_suite_record(suite, expected_id=suite_id)
    if blocks:
        raise ValueError("invalid suite:\n  - " + "\n  - ".join(blocks))

    target = to if to is not None else suite.get("default_to")
    if target is None:
        target = {"kind": "default", "suite": suite_id}

    probes: list[str] = list(suite["probes"])
    results: list[dict[str, Any]] = []
    started = _now()
    ok_all = True

    for pid in probes:
        entry: dict[str, Any] = {"probe_id": pid, "ok": False, "run_id": None, "error": None}
        try:
            stamp = run_probe(
                project_root,
                pid,
                to=target,
                timeout_s=timeout_s,
                dry_run=dry_run,
                strict_to=strict_to,
                strict_status=strict_status,
            )
            entry["ok"] = True
            entry["run_id"] = stamp.get("id")
            entry["status"] = stamp.get("status")
            entry["warnings"] = stamp.get("warnings") or []
            entry["path"] = stamp.get("_path")
        except Exception as e:  # noqa: BLE001 — surface per-probe failure
            ok_all = False
            entry["error"] = f"{type(e).__name__}: {e}"
            results.append(entry)
            if stop_on_error:
                break
            continue
        results.append(entry)

    finished = _now()
    summary = {
        "suite_id": suite_id,
        "ok": ok_all and all(r.get("ok") for r in results) and len(results) == len(probes),
        "to": target,
        "started_at": started,
        "finished_at": finished,
        "probes": probes,
        "results": results,
        "run_ids": [r["run_id"] for r in results if r.get("run_id")],
        "stop_on_error": stop_on_error,
        "dry_run": dry_run,
        "strict_to": strict_to,
        "strict_status": strict_status,
    }
    return summary
