"""Evidence plans — composition layer *above* scalar types.

Layering (bottom → top):

  number | boolean   leaf filter types (knowns / unknowns / plan legs)
  **plan**           multi-evidence (all) or sequential (prove A then B)

A plan is **not** a peer of number/boolean. Legs *use* those types.
Stored as first-class map objects under ``plans/`` (see ``terra.plans``).

Modes:
  - **all**       — multi-evidence: every leg must be satisfied (any order)
  - **sequence**  — sequential: prove leg 0, then 1, … (link blocked if prior open)
"""

from __future__ import annotations

import re
from typing import Any

from .number_type import (
    CONFIDENCE_SET,
    confidence_rank,
    derive_confidence,
    empty_stats,
    recompute_typed_node,
)
from .paths import run_dir

PLAN_MODES = frozenset({"all", "sequence"})
LEG_SCALAR_TYPES = frozenset({"number", "boolean"})
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_leg_spec(spec: str) -> dict[str, Any]:
    """Parse ``id:type:quantity`` with optional ``:n=N`` and ``:conf=LEVEL``.

    Examples:
      rcon:boolean:rcon_up
      hostiles:number:hostile_count:n=3:conf=med
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("leg spec must be a non-empty string")
    parts = [p.strip() for p in spec.strip().split(":") if p.strip() != ""]
    if len(parts) < 3:
        raise ValueError(
            f"leg must be id:type:quantity[:n=N][:conf=LEVEL], got {spec!r}"
        )
    leg_id, mtype, quantity = parts[0], parts[1], parts[2]
    if not _SLUG_RE.match(leg_id):
        raise ValueError(f"leg id {leg_id!r} must match {_SLUG_RE.pattern}")
    if mtype not in LEG_SCALAR_TYPES:
        raise ValueError(
            f"leg type must be one of {sorted(LEG_SCALAR_TYPES)}, got {mtype!r}"
        )
    if not quantity or not quantity.strip():
        raise ValueError("leg quantity required")
    min_n = 1
    min_confidence = "low"
    for extra in parts[3:]:
        if extra.startswith("n=") or extra.startswith("n:"):
            raw = extra.split("=", 1)[-1] if "=" in extra else extra.split(":", 1)[-1]
            try:
                min_n = int(raw)
            except ValueError as e:
                raise ValueError(f"invalid n= in leg {spec!r}") from e
            if min_n < 1:
                raise ValueError("leg min_n must be >= 1")
        elif extra.startswith("conf=") or extra.startswith("conf:"):
            min_confidence = (
                extra.split("=", 1)[-1] if "=" in extra else extra.split(":", 1)[-1]
            )
            if min_confidence not in CONFIDENCE_SET:
                raise ValueError(
                    f"leg conf must be one of {sorted(CONFIDENCE_SET)}, "
                    f"got {min_confidence!r}"
                )
        else:
            raise ValueError(
                f"unknown leg option {extra!r} in {spec!r} "
                f"(use n=N or conf=low|med|high)"
            )
    return empty_leg(
        leg_id,
        map_type=mtype,
        quantity=quantity.strip(),
        min_n=min_n,
        min_confidence=min_confidence,
    )


def empty_leg(
    leg_id: str,
    *,
    map_type: str,
    quantity: str,
    min_n: int = 1,
    min_confidence: str = "low",
    label: str = "",
) -> dict[str, Any]:
    return {
        "id": leg_id,
        "label": label or leg_id,
        "type": map_type,
        "quantity": quantity,
        "min_n": int(min_n),
        "min_confidence": min_confidence if min_confidence in CONFIDENCE_SET else "low",
        "run_ids": [],
        "primary_run_id": None,
        "stats": empty_stats(map_type),
        "confidence_derived": "low",
        "satisfied": False,
    }


def build_plan(
    *,
    mode: str,
    legs: list[dict[str, Any]],
) -> dict[str, Any]:
    if mode not in PLAN_MODES:
        raise ValueError(f"plan mode must be one of {sorted(PLAN_MODES)}, got {mode!r}")
    if not legs:
        raise ValueError("plan requires at least one leg")
    ids = [lg.get("id") for lg in legs]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate leg ids: {ids}")
    return {
        "mode": mode,
        "legs": legs,
        "satisfied_count": 0,
        "leg_count": len(legs),
        "all_satisfied": False,
        "next_leg": legs[0]["id"] if mode == "sequence" else None,
        "blocked_reason": None,
    }


def get_leg(plan: dict[str, Any], leg_id: str) -> dict[str, Any] | None:
    for lg in plan.get("legs") or []:
        if isinstance(lg, dict) and lg.get("id") == leg_id:
            return lg
    return None


def leg_index(plan: dict[str, Any], leg_id: str) -> int:
    for i, lg in enumerate(plan.get("legs") or []):
        if isinstance(lg, dict) and lg.get("id") == leg_id:
            return i
    return -1


def is_leg_satisfied(leg: dict[str, Any]) -> bool:
    """Leg bar: n >= min_n and derived confidence >= min_confidence."""
    stats = leg.get("stats") or {}
    n = int(stats.get("n") or 0)
    min_n = int(leg.get("min_n") or 1)
    if n < min_n:
        return False
    want = leg.get("min_confidence") or "low"
    derived = leg.get("confidence_derived") or derive_confidence(
        stats, map_type=leg.get("type")
    )
    return confidence_rank(derived) >= confidence_rank(str(want))


def sequence_prior_open(plan: dict[str, Any], leg_id: str) -> str | None:
    """If sequence mode and a prior leg is unsatisfied, return its id."""
    if plan.get("mode") != "sequence":
        return None
    idx = leg_index(plan, leg_id)
    if idx < 0:
        return f"(unknown leg {leg_id})"
    for i, lg in enumerate(plan.get("legs") or []):
        if i >= idx:
            break
        if not lg.get("satisfied"):
            return str(lg.get("id"))
    return None


def can_accept_run_on_leg(plan: dict[str, Any], leg_id: str) -> tuple[bool, str]:
    if get_leg(plan, leg_id) is None:
        return False, f"unknown leg {leg_id!r}"
    prior = sequence_prior_open(plan, leg_id)
    if prior is not None:
        return (
            False,
            f"sequence: prove leg {prior!r} first before {leg_id!r}",
        )
    return True, "ok"


def recompute_plan_node(
    record: dict[str, Any],
    *,
    project_root,
    run_dir_fn=None,
) -> dict[str, Any]:
    """Rebuild each leg's stats and overall plan progress."""
    run_dir_fn = run_dir_fn or run_dir
    record = dict(record)
    plan = dict(record.get("plan") or {})
    mode = plan.get("mode") or "all"
    legs_in = list(plan.get("legs") or [])
    legs_out: list[dict[str, Any]] = []
    all_run_ids: list[str] = []
    probe_ids: list[str] = list(record.get("probe_ids") or [])

    for lg in legs_in:
        if not isinstance(lg, dict):
            continue
        leg = dict(lg)
        # Fake a scalar node for recompute_typed_node
        scalar = {
            "type": leg.get("type") or "number",
            "quantity": leg.get("quantity"),
            "run_ids": list(leg.get("run_ids") or []),
            "stats": empty_stats(leg.get("type") or "number"),
            "confidence": "low",
            "confidence_derived": "low",
        }
        scalar = recompute_typed_node(
            scalar, project_root=project_root, run_dir_fn=run_dir_fn
        )
        leg["stats"] = scalar.get("stats") or empty_stats(leg.get("type") or "number")
        leg["confidence_derived"] = scalar.get("confidence_derived") or "low"
        leg["satisfied"] = is_leg_satisfied(leg)
        for rid in leg.get("run_ids") or []:
            if isinstance(rid, str) and rid not in all_run_ids:
                all_run_ids.append(rid)
        legs_out.append(leg)

    satisfied = sum(1 for lg in legs_out if lg.get("satisfied"))
    all_ok = bool(legs_out) and satisfied == len(legs_out)

    next_leg = None
    blocked_reason = None
    if mode == "sequence":
        for lg in legs_out:
            if not lg.get("satisfied"):
                next_leg = lg.get("id")
                break
        if not all_ok and next_leg:
            blocked_reason = f"waiting on leg {next_leg}"
    else:
        open_ids = [lg["id"] for lg in legs_out if not lg.get("satisfied")]
        if open_ids:
            blocked_reason = f"waiting on legs: {', '.join(open_ids)}"

    # Plan-level derived confidence: min of satisfied legs' derived, or low
    if not legs_out:
        derived = "low"
    elif all_ok:
        ranks = [confidence_rank(lg.get("confidence_derived") or "low") for lg in legs_out]
        inv = {0: "low", 1: "med", 2: "high"}
        derived = inv[min(ranks)]
    else:
        derived = "low"

    plan = {
        "mode": mode,
        "legs": legs_out,
        "satisfied_count": satisfied,
        "leg_count": len(legs_out),
        "all_satisfied": all_ok,
        "next_leg": next_leg,
        "blocked_reason": blocked_reason,
    }
    record["plan"] = plan
    record["run_ids"] = all_run_ids
    record["probe_ids"] = probe_ids
    record["stats"] = {
        "kind": "plan",
        "mode": mode,
        "satisfied_count": satisfied,
        "leg_count": len(legs_out),
        "all_satisfied": all_ok,
        "legs": [
            {
                "id": lg.get("id"),
                "satisfied": lg.get("satisfied"),
                "n": (lg.get("stats") or {}).get("n"),
                "confidence_derived": lg.get("confidence_derived"),
            }
            for lg in legs_out
        ],
    }
    record["confidence_derived"] = derived
    claimed = record.get("confidence") or "low"
    if claimed not in CONFIDENCE_SET:
        claimed = "low"
    if confidence_rank(claimed) > confidence_rank(derived):
        record["confidence"] = derived
    else:
        record["confidence"] = claimed
    return record


def can_claim_plan_confidence(
    record: dict[str, Any], want: str
) -> tuple[bool, str]:
    if want not in CONFIDENCE_SET:
        return False, f"confidence must be one of {sorted(CONFIDENCE_SET)}"
    # low is always allowed (provisional dossier)
    if want == "low":
        return True, "low"
    plan = record.get("plan") or {}
    if not plan.get("all_satisfied"):
        reason = plan.get("blocked_reason") or "not all legs satisfied"
        return False, f"cannot promote plan: {reason}"
    derived = record.get("confidence_derived") or "low"
    if confidence_rank(want) <= confidence_rank(derived):
        return True, derived
    return (
        False,
        f"cannot claim confidence={want!r} for plan "
        f"(derived max is {derived!r}; raise each leg's samples/conf first)",
    )


def validate_plan_record(data: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    plan = data.get("plan")
    if not isinstance(plan, dict):
        return ["type=plan requires plan object"]
    mode = plan.get("mode")
    if mode not in PLAN_MODES:
        blocks.append(f"plan.mode must be one of {sorted(PLAN_MODES)}, got {mode!r}")
    legs = plan.get("legs")
    if not isinstance(legs, list) or not legs:
        blocks.append("plan.legs must be a non-empty list")
        return blocks
    seen: set[str] = set()
    for i, lg in enumerate(legs):
        if not isinstance(lg, dict):
            blocks.append(f"plan.legs[{i}] must be an object")
            continue
        lid = lg.get("id")
        if not isinstance(lid, str) or not _SLUG_RE.match(lid):
            blocks.append(f"plan.legs[{i}].id must be a slug")
        elif lid in seen:
            blocks.append(f"duplicate plan leg id {lid!r}")
        else:
            seen.add(lid)
        if lg.get("type") not in LEG_SCALAR_TYPES:
            blocks.append(
                f"plan.legs[{i}].type must be one of {sorted(LEG_SCALAR_TYPES)}"
            )
        q = lg.get("quantity")
        if not isinstance(q, str) or not q.strip():
            blocks.append(f"plan.legs[{i}].quantity required")
        rids = lg.get("run_ids")
        if rids is not None and not isinstance(rids, list):
            blocks.append(f"plan.legs[{i}].run_ids must be a list")
    return blocks


def format_plan_summary(record: dict[str, Any]) -> list[str]:
    """Human lines for show/list."""
    plan = record.get("plan") or {}
    mode = plan.get("mode") or "?"
    lines = [
        f"plan mode={mode}  "
        f"{plan.get('satisfied_count', 0)}/{plan.get('leg_count', 0)} legs  "
        f"all_satisfied={plan.get('all_satisfied')}"
    ]
    if plan.get("next_leg"):
        lines.append(f"  next: {plan.get('next_leg')}")
    if plan.get("blocked_reason"):
        lines.append(f"  blocked: {plan.get('blocked_reason')}")
    for lg in plan.get("legs") or []:
        flag = "ok" if lg.get("satisfied") else "  "
        st = lg.get("stats") or {}
        if lg.get("type") == "boolean":
            body = f"n={st.get('n')} rate={st.get('rate')}"
        else:
            body = f"n={st.get('n')} mean={st.get('mean')}"
        lines.append(
            f"  [{flag}] {lg.get('id')}  {lg.get('type')}  "
            f"qty={lg.get('quantity')}  {body}  "
            f"derived={lg.get('confidence_derived')}  "
            f"need n>={lg.get('min_n')} conf>={lg.get('min_confidence')}  "
            f"runs={lg.get('run_ids') or []}"
        )
    return lines
