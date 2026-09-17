"""Validate stamped probe runs (evidence-time bar)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import run_dir, runs_root
from .probe_contract import is_nonempty_to
from .probe_run import RUN_META_NAME, RUN_SCHEMA_VERSION


def validate_run_record(
    data: Any, *, expected_id: str | None = None
) -> list[str]:
    """Hard bar for one run meta.json. Returns block messages."""
    blocks: list[str] = []

    if not isinstance(data, dict):
        return ["run must be a JSON object"]

    if data.get("schema_version") != RUN_SCHEMA_VERSION:
        blocks.append(
            f"schema_version must be {RUN_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )

    rid = data.get("id")
    if not isinstance(rid, str) or not rid.strip():
        blocks.append("id must be a non-empty string")
    elif expected_id is not None and rid != expected_id:
        blocks.append(f"id {rid!r} does not match directory name {expected_id!r}")

    source_type = data.get("source_type") or "probe"
    if source_type == "calculation":
        calculation_id = data.get("calculation_id")
        if not isinstance(calculation_id, str) or not calculation_id.strip():
            blocks.append("calculation evidence requires calculation_id")
    else:
        probe_id = data.get("probe_id")
        if not isinstance(probe_id, str) or not probe_id.strip():
            blocks.append("probe_id must be a non-empty string")

    # time (substrate)
    time = data.get("time")
    if not isinstance(time, dict):
        blocks.append("time must be an object (substrate stamp)")
    else:
        has_when = any(
            isinstance(time.get(k), str) and time.get(k).strip()
            for k in ("captured_at", "started_at", "finished_at")
        )
        if not has_when:
            blocks.append(
                "time must include non-empty captured_at, started_at, or finished_at"
            )

    # from (substrate)
    frm = data.get("from")
    if not isinstance(frm, dict) or not frm:
        blocks.append("from must be a non-empty object (instrument + execution context)")
    else:
        source_key = "calculation_id" if source_type == "calculation" else "probe_id"
        if not isinstance(frm.get(source_key), str) or not str(frm.get(source_key)).strip():
            blocks.append(f"from.{source_key} must be a non-empty string")
        if not isinstance(frm.get("runner"), str) or not str(frm.get("runner")).strip():
            blocks.append("from.runner must be a non-empty string")

    # to (probe)
    if "to" not in data:
        blocks.append("missing to (what the probe pointed at)")
    elif not is_nonempty_to(data.get("to")):
        blocks.append("to must be a non-empty target")

    # status
    status = data.get("status")
    if not isinstance(status, str) or not status.strip():
        blocks.append("status must be a non-empty string")

    # artifacts
    arts = data.get("artifacts")
    if not isinstance(arts, list):
        blocks.append(f"artifacts must be a list, got {type(arts).__name__}")
    else:
        for i, item in enumerate(arts):
            if item is None:
                blocks.append(f"artifacts[{i}] is null")
            elif not isinstance(item, (str, dict)):
                blocks.append(
                    f"artifacts[{i}] must be str or dict, got {type(item).__name__}"
                )

    bindings = data.get("input_bindings")
    if bindings is not None:
        from .map_inputs import validate_map_bindings

        blocks.extend(validate_map_bindings(bindings))
        if not isinstance(data.get("inputs"), dict):
            blocks.append("inputs must stamp resolved declared input provenance")
        if not isinstance(data.get("assumptions"), list):
            blocks.append("assumptions must be a list")
        if not isinstance(data.get("conditional"), bool):
            blocks.append("conditional must be boolean")

    return blocks


def validate_run_file(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        return {
            "ok": False,
            "id": path.parent.name if path.name == RUN_META_NAME else path.stem,
            "path": str(path),
            "blocks": [f"not a file: {path}"],
            "record": None,
        }
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {
            "ok": False,
            "id": path.parent.name,
            "path": str(path),
            "blocks": [f"unreadable: {e}"],
            "record": None,
        }
    expected = path.parent.name if path.name == RUN_META_NAME else None
    blocks = validate_run_record(data, expected_id=expected)
    # Soft: warn if zero artifacts on non-dry runs (not a block — dry runs / health checks)
    warnings: list[str] = []
    if (
        isinstance(data, dict)
        and isinstance(data.get("artifacts"), list)
        and len(data["artifacts"]) == 0
        and not data.get("dry_run")
    ):
        warnings.append("zero artifacts — thin evidence for a live run")
    return {
        "ok": len(blocks) == 0,
        "id": data.get("id") if isinstance(data, dict) else path.parent.name,
        "path": str(path),
        "blocks": blocks,
        "warnings": warnings,
        "record": data if isinstance(data, dict) else None,
    }


def validate_run_id(project_root: Path, run_id: str) -> dict[str, Any]:
    path = run_dir(project_root, run_id) / RUN_META_NAME
    return validate_run_file(path)


def validate_all_runs(project_root: Path) -> dict[str, Any]:
    root = runs_root(project_root)
    if not root.is_dir():
        return {
            "ok": True,
            "blocks": [],
            "runs": [],
            "count": 0,
        }
    rows: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        meta = child / RUN_META_NAME
        if meta.is_file():
            rows.append(validate_run_file(meta))
    any_fail = any(not r["ok"] for r in rows)
    return {
        "ok": not any_fail,
        "blocks": [],
        "runs": rows,
        "count": len(rows),
    }
