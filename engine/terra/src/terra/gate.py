"""Mechanical release gate — debts fail the gate, no judgment calls.

``terra gate`` exits 0 only when every map is clean:
  - no active unknowns (assumptions are non-blocking but loud notices)
  - no unbacked knowns (evidence voided/unlinked away)
  - no stale knowns (dependency moved without re-derivation)
  - no knowns whose independent methods disagree beyond tolerance
  - no cohorts whose members cite different solves (mixed coupled sets)
  - no unsatisfied evidence plans
  - no red design params/artifacts (moved knowns, unregenerated files)

Accepted spreads (``known accept-spread``) do not fail the gate but are
surfaced as non-blocking ``notices`` — a release built on an accepted band
should say so out loud.

Route integration: completing a ``deliverable`` task requires the gate to
pass (or an explicit recorded override).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import (
    GLOBAL_MAP_ID,
    get_active_map_id,
    list_maps,
    scoped_map,
)


def _collect_map_violations(
    project_root: Path, map_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from .cohorts import cohort_violations
    from .knowns import list_knowns
    from .plans import list_plans
    from .staleness import compute_staleness
    from .unknowns import list_unknowns
    from .calculations import list_calculations

    violations: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    with scoped_map(map_id):
        unknowns = list_unknowns(project_root)
        knowns = list_knowns(project_root)
        plans = list_plans(project_root)
        stale = compute_staleness(project_root)
        calculations = list_calculations(project_root)
        for v in cohort_violations(project_root):
            violations.append({**v, "map_id": map_id})

    for u in unknowns:
        rec = u.get("record") or {}
        if rec.get("status") not in ("open", "probing", "blocked"):
            continue
        if rec.get("role", "unknown") == "assumption":
            notices.append(
                {
                    "kind": "assumption_active",
                    "id": rec.get("id") or u.get("id"),
                    "map_id": map_id,
                    "why": (
                        f"active assumption: {(rec.get('claim') or '')[:80]} "
                        f"(value={rec.get('assumed_value')!r})"
                    ),
                }
            )
        else:
            violations.append(
                {
                    "kind": "unknown_blocking",
                    "id": rec.get("id") or u.get("id"),
                    "map_id": map_id,
                    "why": (
                        f"unknown still {rec.get('status')}: "
                        f"{(rec.get('claim') or '')[:80]}"
                    ),
                }
            )

    for k in knowns:
        rec = k.get("record") or {}
        kid = rec.get("id") or k.get("id")
        n = (rec.get("stats") or {}).get("n") or 0

        # A RETIRED belief is not debt — it is a belief you deliberately took
        # out of service, and its violations were the reason you retired it.
        # Without this, superseding a mis-wired gate could not clear its own
        # `known_formula_failed`; only DELETING it could, which destroys the
        # history that makes the retirement auditable. That is a perverse
        # incentive: the gate would push agents to erase evidence of a mistake
        # instead of recording it. `map_status` already short-circuits these
        # (RETIRED_STATUSES → `known_retired`, info); gate must agree, or the
        # two instruments disagree about the same record.
        from .number_type import RETIRED_STATUSES

        if rec.get("status") in RETIRED_STATUSES:
            notices.append(
                {
                    "kind": "known_retired",
                    "id": kid,
                    "map_id": map_id,
                    "why": (
                        f"known {kid} is {rec.get('status')} — retired belief "
                        "kept as history; excluded from gate debt"
                    ),
                }
            )
            continue

        from .run_inputs import record_input_state

        evidence_inputs = record_input_state(project_root, rec)
        if evidence_inputs["stale"]:
            violations.append(
                {
                    "kind": "evidence_input_stale",
                    "id": kid,
                    "map_id": map_id,
                    "why": f"known {kid} uses runs with stale declared inputs",
                }
            )
        if evidence_inputs["conditional"]:
            notices.append(
                {
                    "kind": "known_conditional",
                    "id": kid,
                    "map_id": map_id,
                    "why": (
                        f"known {kid} depends on assumption-conditioned evidence: "
                        + ", ".join(evidence_inputs["assumptions"])
                    ),
                }
            )
        if not (rec.get("run_ids") or []) or n == 0:
            violations.append(
                {
                    "kind": "known_unbacked",
                    "id": kid,
                    "map_id": map_id,
                    "why": f"known {kid} has no live evidence (n={n})",
                }
            )
        if rec.get("type") == "formula" and (rec.get("stats") or {}).get(
            "holds"
        ) is False:
            violations.append(
                {
                    "kind": "known_formula_failed",
                    "id": kid,
                    "map_id": map_id,
                    "why": (
                        f"formula known {kid} does not hold "
                        f"(claimed confidence={rec.get('confidence') or 'low'}, "
                        f"derived={rec.get('confidence_derived') or 'low'}, n={n})"
                    ),
                }
            )
        corr = ((rec.get("stats") or {}).get("corroboration")) or {}
        if corr.get("agree") is False and corr.get("accepted") is not True:
            violations.append(
                {
                    "kind": "methods_disagree",
                    "id": kid,
                    "map_id": map_id,
                    "why": (
                        f"known {kid}: methods disagree "
                        f"(spread={corr.get('spread')!r} vs tolerance="
                        f"{corr.get('tolerance')!r})"
                    ),
                }
            )
        from .corroboration import methods_unjudged

        if methods_unjudged(rec.get("stats") or {}):
            notices.append(
                {
                    "kind": "methods_unjudged",
                    "id": kid,
                    "map_id": map_id,
                    "why": (
                        f"known {kid}: {corr.get('methods')} methods, NO "
                        f"tolerance declared, and they are "
                        f"{(corr.get('spread_rel') or 0) * 100:.0f}% apart "
                        f"(spread={corr.get('spread')!r}). Agreement cannot "
                        f"be judged, and the reported value AVERAGES across "
                        f"them. Either declare a tolerance "
                        f"(terra known tolerance {kid} --within X%) or check "
                        f"the probes measure the SAME proposition — Terra "
                        f"matches on the quantity NAME alone, so two probes "
                        f"over different populations look like corroboration."
                    ),
                }
            )
        if corr.get("agree") is False and corr.get("accepted") is True:
            acc = rec.get("accepted_spread") or {}
            notices.append(
                {
                    "kind": "spread_accepted",
                    "id": kid,
                    "map_id": map_id,
                    "why": (
                        f"known {kid}: cross-method spread "
                        f"{corr.get('spread')!r} accepted as uncertainty "
                        f"(band={corr.get('band')!r}; reason: "
                        f"{acc.get('reason')})"
                    ),
                }
            )
        info = stale.get(str(kid)) or {}
        if info.get("stale"):
            violations.append(
                {
                    "kind": "known_stale",
                    "id": kid,
                    "map_id": map_id,
                    "why": (
                        f"known {kid} is stale: "
                        + "; ".join(info.get("reasons") or [])
                    ),
                }
            )

    for p in plans:
        rec = p.get("record") or {}
        pl = rec.get("plan") or {}
        if not pl.get("all_satisfied"):
            violations.append(
                {
                    "kind": "plan_incomplete",
                    "id": rec.get("id") or p.get("id"),
                    "map_id": map_id,
                    "why": (
                        f"plan {rec.get('id')}: "
                        f"{pl.get('satisfied_count')}/{pl.get('leg_count')} "
                        f"legs satisfied"
                    ),
                }
            )

    for calculation in calculations:
        cid = calculation.get("id")
        if not calculation.get("ok"):
            violations.append(
                {
                    "kind": "calculation_invalid",
                    "id": cid,
                    "map_id": map_id,
                    "why": "; ".join(calculation.get("blocks") or []),
                }
            )
            continue
        staleness = calculation.get("staleness") or {}
        if staleness.get("stale"):
            violations.append(
                {
                    "kind": "calculation_stale",
                    "id": cid,
                    "map_id": map_id,
                    "why": "; ".join(staleness.get("reasons") or []),
                }
            )
            continue
        latest = ((calculation.get("record") or {}).get("latest") or {})
        if latest.get("conditional"):
            notices.append(
                {
                    "kind": "calculation_conditional",
                    "id": cid,
                    "map_id": map_id,
                    "why": (
                        f"calculation {cid} depends on assumptions: "
                        + ", ".join(latest.get("assumptions") or [])
                    ),
                }
            )

    return violations, notices


def check_gate(
    project_root: Path,
    *,
    map_id: str | None = None,
) -> dict[str, Any]:
    """Gate verdict. Default scans ALL maps — session debt cannot hide."""
    if map_id:
        map_ids = [map_id]
    else:
        map_ids = [m["id"] for m in list_maps(project_root)] or [GLOBAL_MAP_ID]
    violations: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    for mid in map_ids:
        vs, ns = _collect_map_violations(project_root, mid)
        violations.extend(vs)
        notices.extend(ns)
    # Design layer is project-wide: red params/artifacts fail release
    from .design import check_design

    design_verdict = check_design(project_root)
    for v in design_verdict["violations"]:
        violations.append({**v, "map_id": "design"})
    # Non-blocking: a gate stricter than the accepted DoR baseline is a
    # self-referential warning, not release debt.
    for n in design_verdict.get("notices") or []:
        notices.append({**n, "map_id": "design"})
    return {
        "ok": not violations,
        "maps_checked": map_ids,
        "active_map": get_active_map_id(project_root),
        "violations": violations,
        "notices": notices,
        "counts": {
            kind: sum(1 for v in violations if v["kind"] == kind)
            for kind in sorted({v["kind"] for v in violations})
        },
    }
