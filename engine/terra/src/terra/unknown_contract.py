"""Unknowns — named gaps in map understanding.

Requirement 1 of the map layer: track what we do not know.
Silent thrashing without an open unknown is a process failure.
"""

from __future__ import annotations

from typing import Any

UNKNOWN_SCHEMA_VERSION = 1
UNKNOWN_ROLES = frozenset({"unknown", "assumption"})

UNKNOWN_STATUSES = frozenset(
    {
        "open",
        "probing",
        "blocked",
        "resolved",
        "wont_care",
    }
)

# Statuses that still demand attention / block "we're done understanding"
ACTIVE_STATUSES = frozenset({"open", "probing", "blocked"})


def validate_unknown_record(data: Any, *, expected_id: str | None = None) -> list[str]:
    """Hard bar for one unknown JSON object. Returns block messages."""
    blocks: list[str] = []

    if not isinstance(data, dict):
        return ["unknown must be a JSON object"]

    if data.get("schema_version") != UNKNOWN_SCHEMA_VERSION:
        blocks.append(
            f"schema_version must be {UNKNOWN_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )

    role = data.get("role", "unknown")
    if role not in UNKNOWN_ROLES:
        blocks.append(f"role must be one of {sorted(UNKNOWN_ROLES)}, got {role!r}")
    if role == "assumption":
        if data.get("type") not in ("number", "boolean"):
            blocks.append("assumption type must be number or boolean")
        if "assumed_value" not in data:
            blocks.append("assumption requires assumed_value")
        elif data.get("type") == "number" and (
            not isinstance(data.get("assumed_value"), (int, float))
            or isinstance(data.get("assumed_value"), bool)
        ):
            blocks.append("number assumption assumed_value must be numeric")
        elif data.get("type") == "boolean" and not isinstance(
            data.get("assumed_value"), bool
        ):
            blocks.append("boolean assumption assumed_value must be true or false")
        reason = data.get("assumption_reason")
        if not isinstance(reason, str) or not reason.strip():
            blocks.append("assumption requires assumption_reason")
        if data.get("blocks_build") is not False:
            blocks.append("assumption blocks_build must be false")
        revisions = data.get("assumption_revisions")
        if revisions is not None and not isinstance(revisions, list):
            blocks.append("assumption_revisions must be a list")

    uid = data.get("id")
    if not isinstance(uid, str) or not uid.strip():
        blocks.append("id must be a non-empty string")
    elif expected_id is not None and uid != expected_id:
        blocks.append(
            f"id {uid!r} does not match filename stem {expected_id!r}"
        )

    claim = data.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        blocks.append(
            "claim must be a non-empty string "
            "(what we do not know / what mystery is open)"
        )

    status = data.get("status")
    if status not in UNKNOWN_STATUSES:
        blocks.append(
            f"status must be one of {sorted(UNKNOWN_STATUSES)}, got {status!r}"
        )

    if "blocks_build" in data and not isinstance(data.get("blocks_build"), bool):
        blocks.append("blocks_build must be a boolean")

    # evidence_needed: recommended always; required when open/probing
    ev = data.get("evidence_needed")
    if status in ("open", "probing"):
        if not isinstance(ev, str) or not ev.strip():
            blocks.append(
                "evidence_needed required for open/probing unknowns "
                "(what reading would resolve this?)"
            )

    # resolved must not be silent — structured trail preferred
    if status == "resolved":
        if not has_resolve_trail(data):
            blocks.append(
                "status=resolved requires a trail: resolved_by, notes, "
                "probe_id/probe_ids, or run_ids (no silent resolve)"
            )

    # probe_id optional string (primary); probe_ids optional multi-link list
    if "probe_id" in data and data["probe_id"] is not None:
        if not isinstance(data["probe_id"], str) or not data["probe_id"].strip():
            blocks.append("probe_id must be a non-empty string or null")

    if "probe_ids" in data and data["probe_ids"] is not None:
        pids = data["probe_ids"]
        if not isinstance(pids, list):
            blocks.append("probe_ids must be a list of strings")
        else:
            for i, item in enumerate(pids):
                if not isinstance(item, str) or not item.strip():
                    blocks.append(
                        f"probe_ids[{i}] must be a non-empty string"
                    )

    # run_ids: first-class evidence links; primary_run_id optional
    if "run_ids" in data and data["run_ids"] is not None:
        rids = data["run_ids"]
        if not isinstance(rids, list):
            blocks.append("run_ids must be a list of strings")
        else:
            for i, item in enumerate(rids):
                if not isinstance(item, str) or not item.strip():
                    blocks.append(f"run_ids[{i}] must be a non-empty string")

    if "primary_run_id" in data and data["primary_run_id"] is not None:
        prim = data["primary_run_id"]
        if not isinstance(prim, str) or not prim.strip():
            blocks.append("primary_run_id must be a non-empty string or null")
        else:
            rids = data.get("run_ids") or []
            if isinstance(rids, list) and prim not in rids:
                blocks.append(
                    f"primary_run_id {prim!r} must appear in run_ids"
                )

    for ts_key in ("created_at", "updated_at"):
        if ts_key in data and data[ts_key] is not None:
            if not isinstance(data[ts_key], str) or not data[ts_key].strip():
                blocks.append(f"{ts_key} must be a non-empty string if set")

    return blocks


def has_resolve_trail(data: dict[str, Any]) -> bool:
    """True if unknown has structured or prose evidence for resolve."""
    resolved_by = data.get("resolved_by")
    notes = data.get("notes")
    probe_id = data.get("probe_id")
    probe_ids = data.get("probe_ids") or []
    run_ids = data.get("run_ids") or []
    has_probe_list = (
        isinstance(probe_ids, list)
        and any(isinstance(x, str) and x.strip() for x in probe_ids)
    )
    has_run_list = (
        isinstance(run_ids, list)
        and any(isinstance(x, str) and x.strip() for x in run_ids)
    )
    has_prose = any(
        isinstance(x, str) and x.strip()
        for x in (resolved_by, notes, probe_id)
    )
    return has_prose or has_probe_list or has_run_list
