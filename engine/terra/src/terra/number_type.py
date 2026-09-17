"""Map types: number + boolean — samples in, substrate-computed stats out.

Probes stay open. Typed knowns/unknowns filter the world.
Agents do not author stats; Terra recomputes from runs.measures.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

# Leaf filter types for knowns/unknowns and for plan *legs*.
# Plans are a higher layer (see evidence_plan / plans), not a peer type.
# number/boolean: estimates; formula: observation as checkable predicate + vars
SCALAR_TYPES = frozenset({"number", "boolean"})
# relation: F(x) curves — samples are (x, y) pairs, ladder unit is sweeps
MAP_TYPES = frozenset({"number", "boolean", "formula", "relation"})
CONFIDENCE_LEVELS = ("low", "med", "high")
CONFIDENCE_SET = frozenset(CONFIDENCE_LEVELS)

KNOWN_STATUSES = frozenset(
    {"provisional", "active", "contested", "refuted", "superseded"}
)
# Tombstoned beliefs: kept as history, refused as a current value by the read
# path (readings._read_known_here). A soft alternative to destructive delete.
RETIRED_STATUSES = frozenset({"superseded", "refuted"})


# ---------------------------------------------------------------------------
# Number
# ---------------------------------------------------------------------------


def compute_number_stats(values: list[float]) -> dict[str, Any]:
    """From sample vector → n, mean, std (sample), min, max.

    std is null when n < 2 (n=1 cannot invent uncertainty).
    """
    clean = [
        float(v)
        for v in values
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    n = len(clean)
    if n == 0:
        return {
            "kind": "number",
            "n": 0,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "values": [],
        }
    mean = float(statistics.fmean(clean))
    std: float | None
    if n < 2:
        std = None
    else:
        std = float(statistics.stdev(clean))
    return {
        "kind": "number",
        "n": n,
        "mean": mean,
        "std": std,
        "min": float(min(clean)),
        "max": float(max(clean)),
        "values": clean,
    }


def derive_confidence_number(stats: dict[str, Any]) -> str:
    """low: n>=1; med: n>=3 or (n>=2 and std); high: n>=5, tight std/mean,
    AND >=2 methods agreeing (corroboration). Disagreeing methods → low."""
    from .corroboration import corroboration_gate_high, methods_disagree

    if methods_disagree(stats):
        return "low"
    n = int(stats.get("n") or 0)
    if n < 1:
        return "low"
    std = stats.get("std")
    mean = stats.get("mean")
    if n >= 5 and std is not None and mean is not None:
        tight = (
            (std == 0)
            if mean == 0
            else abs(float(std) / abs(float(mean))) <= 0.5
        )
        if tight and corroboration_gate_high(stats)[0]:
            return "high"
    if n >= 3 or (n >= 2 and std is not None):
        return "med"
    return "low"


# ---------------------------------------------------------------------------
# Boolean
# ---------------------------------------------------------------------------


def coerce_bool(v: Any) -> bool | None:
    """Accept true/false, 1/0, 'true'/'false' (case-insensitive)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and not isinstance(v, bool) and v in (0, 1):
        return bool(v)
    if isinstance(v, float) and v in (0.0, 1.0):
        return bool(int(v))
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0"):
            return False
    return None


def compute_boolean_stats(values: list[bool]) -> dict[str, Any]:
    """From trial vector → n, k_true, k_false, rate."""
    clean = [v for v in values if isinstance(v, bool)]
    n = len(clean)
    if n == 0:
        return {
            "kind": "boolean",
            "n": 0,
            "k_true": 0,
            "k_false": 0,
            "rate": None,
            "values": [],
        }
    k_true = sum(1 for v in clean if v)
    k_false = n - k_true
    return {
        "kind": "boolean",
        "n": n,
        "k_true": k_true,
        "k_false": k_false,
        "rate": float(k_true) / float(n),
        "values": clean,
    }


def derive_confidence_boolean(stats: dict[str, Any]) -> str:
    """low: n>=1; med: n>=3; high: n>=5 unanimous AND >=2 methods agreeing.
    Disagreeing methods → low."""
    from .corroboration import corroboration_gate_high, methods_disagree

    if methods_disagree(stats):
        return "low"
    n = int(stats.get("n") or 0)
    if n < 1:
        return "low"
    rate = stats.get("rate")
    if (
        n >= 5
        and rate is not None
        and (rate == 0.0 or rate == 1.0)
        and corroboration_gate_high(stats)[0]
    ):
        return "high"
    if n >= 3:
        return "med"
    return "low"


# ---------------------------------------------------------------------------
# Shared confidence API
# ---------------------------------------------------------------------------


def derive_confidence(stats: dict[str, Any], map_type: str | None = None) -> str:
    kind = map_type or stats.get("kind") or "number"
    if kind == "boolean":
        return derive_confidence_boolean(stats)
    if kind == "formula":
        from .formula_type import derive_confidence_formula

        return derive_confidence_formula(stats)
    if kind == "relation":
        from .relation_type import derive_confidence_relation

        return derive_confidence_relation(stats)
    return derive_confidence_number(stats)


def confidence_rank(level: str) -> int:
    order = {"low": 0, "med": 1, "high": 2}
    return order.get(level, 0)


def can_claim_confidence(
    stats: dict[str, Any],
    want: str,
    *,
    map_type: str | None = None,
) -> tuple[bool, str]:
    if want not in CONFIDENCE_SET:
        return False, f"confidence must be one of {sorted(CONFIDENCE_SET)}"
    derived = derive_confidence(stats, map_type=map_type)
    if confidence_rank(want) <= confidence_rank(derived):
        return True, derived
    kind = map_type or stats.get("kind") or "number"
    if kind == "formula":
        from .formula_type import can_claim_formula_confidence

        return can_claim_formula_confidence(stats, want)

    from .corroboration import corroboration_gate_high, methods_disagree

    if methods_disagree(stats):
        corr = stats.get("corroboration") or {}
        return (
            False,
            f"cannot claim confidence={want!r}: methods DISAGREE "
            f"(spread={corr.get('spread')!r} vs tolerance="
            f"{corr.get('tolerance')!r}) — one instrument is wrong; "
            f"void the bad evidence or fix the probe",
        )
    if want == "high":
        ok_corr, why = corroboration_gate_high(stats)
        if not ok_corr:
            return False, f"cannot claim confidence='high': {why}"
    if kind == "boolean":
        return (
            False,
            f"cannot claim confidence={want!r} with n={stats.get('n')}, "
            f"rate={stats.get('rate')} (derived max is {derived!r}; need more trials)",
        )
    if kind == "relation":
        return (
            False,
            f"cannot claim confidence={want!r} with sweeps={stats.get('n')}, "
            f"stations={stats.get('station_count')} (derived max is "
            f"{derived!r}; med needs >=3 sweeps and >=3 stations — repeat "
            f"the sweep, don't just densify it)",
        )
    return (
        False,
        f"cannot claim confidence={want!r} with n={stats.get('n')}, "
        f"std={stats.get('std')} (derived max is {derived!r}; need more samples)",
    )


# ---------------------------------------------------------------------------
# Measure extraction
# ---------------------------------------------------------------------------


def extract_number_measures_from_run_meta(
    run_meta: dict[str, Any],
    *,
    quantity: str | None = None,
) -> list[float]:
    values: list[float] = []
    raw = run_meta.get("measures")
    if not isinstance(raw, list):
        return values
    for item in raw:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            if quantity is None:
                values.append(float(item))
        elif isinstance(item, dict):
            q = item.get("quantity")
            v = item.get("value")
            if quantity is not None and q is not None and q != quantity:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                values.append(float(v))
    return values


def extract_boolean_measures_from_run_meta(
    run_meta: dict[str, Any],
    *,
    quantity: str | None = None,
) -> list[bool]:
    values: list[bool] = []
    raw = run_meta.get("measures")
    if not isinstance(raw, list):
        return values
    for item in raw:
        if isinstance(item, bool):
            if quantity is None:
                values.append(item)
            continue
        if isinstance(item, dict):
            q = item.get("quantity")
            v = item.get("value")
            if quantity is not None and q is not None and q != quantity:
                continue
            b = coerce_bool(v)
            if b is not None:
                values.append(b)
        else:
            b = coerce_bool(item)
            if b is not None and quantity is None:
                values.append(b)
    return values


def _load_measures_json(fpath: Path) -> Any:
    try:
        return json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _artifact_measure_blobs(
    run_dir: Path, run_meta: dict[str, Any]
) -> list[dict[str, Any]]:
    """Extra measure lists from artifacts/measures.json style files."""
    blobs: list[dict[str, Any]] = []
    for art in run_meta.get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        rel = art.get("path")
        role = art.get("role")
        if not isinstance(rel, str):
            continue
        if not (
            role == "measures"
            or rel.endswith("measures.json")
            or rel.endswith("/measures.json")
        ):
            continue
        fpath = run_dir / rel if not Path(rel).is_absolute() else Path(rel)
        if not fpath.is_file():
            fpath = run_dir / Path(rel).name
        if not fpath.is_file():
            continue
        data = _load_measures_json(fpath)
        if isinstance(data, list):
            blobs.append({"measures": data})
        elif isinstance(data, dict) and "measures" in data:
            blobs.append(data)
        elif isinstance(data, dict) and "value" in data:
            blobs.append({"measures": [data]})
    return blobs


def extract_measures_from_run_dir(
    run_dir: Path,
    run_meta: dict[str, Any],
    *,
    quantity: str | None = None,
    map_type: str = "number",
) -> list[Any]:
    """Meta measures + optional measures.json artifacts."""
    if map_type == "boolean":
        values: list[Any] = extract_boolean_measures_from_run_meta(
            run_meta, quantity=quantity
        )
        for blob in _artifact_measure_blobs(run_dir, run_meta):
            values.extend(
                extract_boolean_measures_from_run_meta(blob, quantity=quantity)
            )
        return values

    values = extract_number_measures_from_run_meta(run_meta, quantity=quantity)
    for blob in _artifact_measure_blobs(run_dir, run_meta):
        values.extend(
            extract_number_measures_from_run_meta(blob, quantity=quantity)
        )
    return values


# Back-compat alias used by older imports
def extract_measures_from_run_meta(
    run_meta: dict[str, Any],
    *,
    quantity: str | None = None,
) -> list[float]:
    return extract_number_measures_from_run_meta(run_meta, quantity=quantity)


def empty_stats(map_type: str = "number") -> dict[str, Any]:
    if map_type == "boolean":
        return compute_boolean_stats([])
    if map_type == "formula":
        from .formula_type import empty_formula_stats

        return empty_formula_stats()
    if map_type == "relation":
        from .relation_type import empty_relation_stats

        return empty_relation_stats()
    return compute_number_stats([])


def recompute_typed_node(
    record: dict[str, Any],
    *,
    project_root: Path,
    run_dir_fn,
) -> dict[str, Any]:
    """Rebuild stats + derived confidence from linked run_ids."""
    from .probe_run import RUN_META_NAME

    map_type = record.get("type") or "number"
    if map_type == "formula":
        from .formula_type import recompute_formula_node

        return recompute_formula_node(
            record, project_root=project_root, run_dir_fn=run_dir_fn
        )

    quantity = record.get("quantity")
    if not isinstance(quantity, str) or not quantity.strip():
        quantity = None
    else:
        quantity = quantity.strip()

    all_values: list[Any] = []
    sample_runs: list[dict[str, Any]] = []
    values_by_probe: dict[str, list[Any]] = {}
    input_assumptions: set[str] = set()
    for rid in record.get("run_ids") or []:
        if not isinstance(rid, str):
            continue
        rdir = run_dir_fn(project_root, rid)
        meta_path = rdir / RUN_META_NAME
        if not meta_path.is_file():
            sample_runs.append({"run_id": rid, "n": 0, "missing": True})
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            sample_runs.append({"run_id": rid, "n": 0, "missing": True})
            continue
        # Voided runs stay on disk for audit but never feed stats.
        if meta.get("voided"):
            sample_runs.append(
                {
                    "run_id": rid,
                    "n": 0,
                    "voided": True,
                    "void_reason": meta.get("void_reason"),
                }
            )
            continue
        from .run_inputs import run_input_state

        input_state = run_input_state(project_root, meta)
        if input_state["stale"]:
            sample_runs.append(
                {
                    "run_id": rid,
                    "n": 0,
                    "stale_inputs": True,
                    "stale_reasons": input_state["reasons"],
                }
            )
            continue
        input_assumptions.update(input_state["assumptions"])
        if map_type == "relation":
            from .relation_type import extract_relation_pairs_from_run_meta

            vals = extract_relation_pairs_from_run_meta(meta, quantity=quantity)
        else:
            vals = extract_measures_from_run_dir(
                rdir, meta, quantity=quantity, map_type=map_type
            )
        pid = (
            meta.get("probe_id")
            or (
                f"calculation:{meta.get('calculation_id')}"
                if meta.get("source_type") == "calculation"
                else "unknown_probe"
            )
        )
        sample_runs.append(
            {"run_id": rid, "probe_id": pid, "n": len(vals), "values": vals}
        )
        values_by_probe.setdefault(pid, []).extend(vals)
        all_values.extend(vals)

    if map_type == "relation":
        from .relation_type import compute_relation_stats

        def _sweeps(rows):
            return sum(1 for r in rows if (r.get("n") or 0) > 0)

        stats = compute_relation_stats(
            all_values, sweeps=_sweeps(sample_runs)
        )
        by_probe = {
            pid: compute_relation_stats(
                vals,
                sweeps=sum(
                    1
                    for r in sample_runs
                    if r.get("probe_id") == pid and (r.get("n") or 0) > 0
                ),
            )
            for pid, vals in values_by_probe.items()
        }
    elif map_type == "boolean":
        stats = compute_boolean_stats([v for v in all_values if isinstance(v, bool)])
        by_probe = {
            pid: compute_boolean_stats([v for v in vals if isinstance(v, bool)])
            for pid, vals in values_by_probe.items()
        }
    else:
        stats = compute_number_stats(
            [
                float(v)
                for v in all_values
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
        )
        by_probe = {
            pid: compute_number_stats(
                [
                    float(v)
                    for v in vals
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                ]
            )
            for pid, vals in values_by_probe.items()
        }
    stats["by_run"] = sample_runs

    # DIAGNOSTIC ONLY — does not touch n, confidence, or corroboration.
    #
    # `n` counts RUNS, not distinct samples. A deterministic probe re-run "to
    # be safe" produces a byte-identical reading and silently doubles the
    # evidence count the whole confidence ladder rests on. Measured on CG-01
    # (2026-07-28): 196 knowns carry duplicate-signature runs, 146 at `med` —
    # e.g. dq_frame_index_clean at n=22 from THREE distinct samples, and
    # sm_nominal_final at n=4 from four identical 8.27 readings minutes apart.
    #
    # Deliberately REPORTED, not corrected: recomputing `n` re-rates beliefs
    # program-wide, and that is a decision to take with the blast radius
    # measured, not a silent substrate change made at 3am. Surface first.
    _sigs: list[str] = []
    for _r in sample_runs:
        if (_r.get("n") or 0) <= 0:
            continue
        try:
            _sigs.append(json.dumps(_r.get("values"), sort_keys=True, default=str))
        except (TypeError, ValueError):
            _sigs.append(repr(_r.get("values")))
    _distinct = len(set(_sigs))
    stats["distinct_sample_signatures"] = _distinct
    stats["duplicate_sample_runs"] = len(_sigs) - _distinct

    # Corroboration: the second evidence axis (independent methods agree?)
    from .corroboration import compute_corroboration

    for g in by_probe.values():
        g.pop("by_run", None)
        g.pop("values", None)
    stats["by_probe"] = by_probe
    if map_type == "relation":
        from .relation_type import relation_corroboration

        corr = relation_corroboration(
            by_probe, tolerance=record.get("tolerance")
        )
    else:
        corr = compute_corroboration(
            by_probe, map_type=map_type, tolerance=record.get("tolerance")
        )
    from .corroboration import method_band, reconcile_accepted_spread

    band = method_band(by_probe)
    if band is not None:
        corr["band"] = band
    record = reconcile_accepted_spread(record, corr)
    stats["corroboration"] = corr
    record = dict(record)
    record["stats"] = stats
    record["conditional"] = bool(input_assumptions)
    record["assumptions"] = sorted(input_assumptions)
    record["confidence_derived"] = derive_confidence(stats, map_type=map_type)
    claimed = record.get("confidence") or "low"
    if claimed not in CONFIDENCE_SET:
        claimed = "low"
    if confidence_rank(claimed) > confidence_rank(record["confidence_derived"]):
        record["confidence"] = record["confidence_derived"]
    else:
        record["confidence"] = claimed
    return record


# Back-compat name
def recompute_number_node(
    record: dict[str, Any],
    *,
    project_root: Path,
    run_dir_fn,
) -> dict[str, Any]:
    return recompute_typed_node(
        record, project_root=project_root, run_dir_fn=run_dir_fn
    )
