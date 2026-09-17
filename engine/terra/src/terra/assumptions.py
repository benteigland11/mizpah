"""Consumable provisional values that keep their uncertainty loud."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import map_chain, scoped_map, unknown_path
from .unknowns import (
    create_unknown,
    describe_unknown,
    link_probe,
    link_run,
    list_role,
    load_unknown,
    save_unknown,
    unlink_run,
)


def create_assumption(
    project_root: Path,
    assumption_id: str,
    *,
    claim: str,
    map_type: str,
    quantity: str,
    value: Any,
    reason: str,
    evidence_needed: str,
    unit: str = "",
    probe_id: str | None = None,
    notes: str = "",
    force: bool = False,
) -> Path:
    return create_unknown(
        project_root,
        assumption_id,
        claim=claim,
        evidence_needed=evidence_needed,
        blocks_build=False,
        probe_id=probe_id,
        notes=notes,
        force=force,
        map_type=map_type,
        quantity=quantity,
        unit=unit,
        role="assumption",
        assumed_value=value,
        assumption_reason=reason,
    )


def load_assumption(project_root: Path, assumption_id: str) -> dict[str, Any]:
    rec = load_unknown(project_root, assumption_id)
    if rec.get("role", "unknown") != "assumption":
        raise ValueError(f"{assumption_id!r} is an unknown, not an assumption")
    return rec


def find_assumption(project_root: Path, assumption_id: str) -> tuple[dict[str, Any], str]:
    """Resolve child-first through the active map parent chain."""
    for map_id in map_chain(project_root):
        with scoped_map(map_id):
            if unknown_path(project_root, assumption_id).is_file():
                return load_assumption(project_root, assumption_id), map_id
    raise FileNotFoundError(
        f"assumption not found: {assumption_id} (searched map parent chain)"
    )


def read_assumption(project_root: Path, assumption_id: str) -> dict[str, Any]:
    """Consume an assumption. INSTRUMENTED — see the note below.

    `read_known` records every read into the active provenance sink; this did
    not, so a probe consuming an assumption produced a run stamped
    `conditional=false, assumptions=[]` while demonstrably depending on it.
    That defeats the entire point of an assumption: conditionality is supposed
    to PROPAGATE into every belief derived from the run, and `terra gate` was
    blind to it. Found live on CG-01 2026-08-08 — both mission probes consume
    `p1_cd0_cruise_pessimistic` (proven by poisoning the fallback) and recorded
    no assumption at all.
    """
    rec, owner = find_assumption(project_root, assumption_id)
    stats = rec.get("stats") or {}
    observed = stats.get("mean") if rec.get("type") == "number" else stats.get("rate")
    reading = {
        "id": rec["id"],
        "role": "assumption",
        "type": rec.get("type"),
        "quantity": rec.get("quantity"),
        "value": rec.get("assumed_value"),
        "unit": rec.get("unit") or "",
        "conditional": True,
        "assumptions": [rec["id"]],
        "reason": rec.get("assumption_reason"),
        "evidence_needed": rec.get("evidence_needed"),
        "observed_value": observed,
        "evidence_n": stats.get("n") or 0,
        "updated_at": rec.get("updated_at"),
        "map": owner,
    }
    try:
        from .readings import note_assumption_read

        note_assumption_read(assumption_id, owner, reading)
    except Exception:  # provenance must never break a measurement
        pass
    return reading


def set_assumption(
    project_root: Path, assumption_id: str, *, value: Any, reason: str
) -> dict[str, Any]:
    if not str(reason or "").strip():
        raise ValueError("assumption set requires --reason")
    rec = load_assumption(project_root, assumption_id)
    if rec.get("type") == "number" and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        raise ValueError("number assumption value must be numeric")
    if rec.get("type") == "boolean" and not isinstance(value, bool):
        raise ValueError("boolean assumption value must be true or false")
    revisions = list(rec.get("assumption_revisions") or [])
    from .unknowns import _now

    revisions.append({"at": _now(), "value": value, "reason": reason.strip()})
    rec["assumed_value"] = value
    rec["assumption_reason"] = reason.strip()
    rec["assumption_revisions"] = revisions
    save_unknown(project_root, rec)
    return load_assumption(project_root, assumption_id)


def list_assumptions(project_root: Path) -> list[dict[str, Any]]:
    return list_role(project_root, "assumption")


def describe_assumption(project_root: Path, assumption_id: str) -> dict[str, Any]:
    rec, owner = find_assumption(project_root, assumption_id)
    with scoped_map(owner):
        desc = describe_unknown(project_root, assumption_id)
    desc["reading"] = read_assumption(project_root, assumption_id)
    desc["record"] = rec
    return desc


def link_probe_assumption(
    project_root: Path, assumption_id: str, probe_id: str
) -> dict[str, Any]:
    load_assumption(project_root, assumption_id)
    return link_probe(project_root, assumption_id, probe_id)


def link_run_assumption(
    project_root: Path, assumption_id: str, run_id: str
) -> dict[str, Any]:
    load_assumption(project_root, assumption_id)
    return link_run(project_root, assumption_id, run_id)


def unlink_run_assumption(
    project_root: Path, assumption_id: str, run_id: str
) -> dict[str, Any]:
    load_assumption(project_root, assumption_id)
    return unlink_run(project_root, assumption_id, run_id)
