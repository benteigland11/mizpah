"""Freshness and conditionality of declared probe inputs on stamped runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .map_inputs import resolve_map_bindings, snapshots_equal
from .paths import find_run_dir


def run_input_state(project_root: Path, run_meta: dict[str, Any]) -> dict[str, Any]:
    bindings = run_meta.get("input_bindings") or {}
    reasons: list[str] = []
    probe_id = str(run_meta.get("probe_id") or "")
    assumptions = list(run_meta.get("assumptions") or [])
    try:
        _, current, assumptions = resolve_map_bindings(
            project_root,
            bindings,
            consumer=f"probe:{probe_id}",
            record=False,
        )
        if bindings and not snapshots_equal(current, run_meta.get("inputs") or {}):
            reasons.append("declared input value or provenance changed")
    except (ValueError, FileNotFoundError) as exc:
        assumptions = list(run_meta.get("assumptions") or [])
        reasons.append(f"declared input unavailable: {exc}")
    if run_meta.get("source_type") == "calculation":
        calculation_id = str(run_meta.get("calculation_id") or "")
        try:
            from .calculations import calculation_source_hash

            current_hash = calculation_source_hash(project_root, calculation_id)
            if current_hash != run_meta.get("calculation_source_sha256"):
                reasons.append("calculation source or requirements changed")
        except (ValueError, FileNotFoundError, OSError) as exc:
            reasons.append(f"calculation unavailable: {exc}")
    return {
        "stale": bool(reasons),
        "reasons": reasons,
        "conditional": bool(assumptions),
        "assumptions": sorted(set(assumptions)),
    }


def record_input_state(project_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    stale_runs: list[dict[str, Any]] = []
    assumptions: set[str] = set()
    for run_id in record.get("run_ids") or []:
        visible = find_run_dir(project_root, run_id)
        if visible is None:
            continue
        try:
            meta = json.loads((visible[0] / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("voided"):
            continue
        state = run_input_state(project_root, meta)
        assumptions.update(state["assumptions"])
        if state["stale"]:
            stale_runs.append({"run_id": run_id, "reasons": state["reasons"]})
    return {
        "stale": bool(stale_runs),
        "stale_runs": stale_runs,
        "conditional": bool(assumptions),
        "assumptions": sorted(assumptions),
    }
