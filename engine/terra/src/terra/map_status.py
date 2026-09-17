"""Map status board — one glance at beliefs + evidence on a map scope."""

from __future__ import annotations

import html
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .knowns import list_knowns
from .number_type import RETIRED_STATUSES
from .paths import (
    get_active_map_id,
    list_maps,
    map_root,
    probes_root,
    terra_root,
)
from .plans import list_plans
from .probe_run import list_runs
from .suites import list_suites
from .unknowns import list_unknowns


@contextmanager
def _scoped_map(map_id: str) -> Iterator[None]:
    from .paths import _active_map_id, _normalize_map_id

    token = _active_map_id.set(_normalize_map_id(map_id))
    try:
        yield
    finally:
        _active_map_id.reset(token)


def _probe_rows(project_root: Path) -> list[dict[str, Any]]:
    root = probes_root(project_root)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        if child.name.startswith("."):
            continue
        has_py = (child / "probe.py").is_file()
        has_meta = (child / "probe.json").is_file()
        if not has_py and not has_meta:
            continue
        kind = None
        purpose = ""
        if has_meta:
            try:
                meta = json.loads(
                    (child / "probe.json").read_text(encoding="utf-8")
                )
                kind = meta.get("kind")
                purpose = meta.get("purpose") or ""
            except (json.JSONDecodeError, OSError):
                pass
        rows.append(
            {
                "id": child.name,
                "kind": kind,
                "purpose": purpose,
                "has_script": has_py,
            }
        )
    return rows


def collect_map_status(
    project_root: Path,
    map_id: str | None = None,
) -> dict[str, Any]:
    """Snapshot one map scope (default: active). Probes always from global."""
    mid = map_id or get_active_map_id(project_root)
    probes = _probe_rows(project_root)

    with _scoped_map(mid):
        from .calculations import list_calculations

        unknowns = list_unknowns(project_root)
        knowns = list_knowns(project_root)
        plans = list_plans(project_root)
        runs = list_runs(project_root)
        suites = list_suites(project_root)
        calculations = list_calculations(project_root)
        belief = str(map_root(project_root))
        from .cohorts import cohort_violations, list_cohorts
        from .staleness import compute_staleness

        stale_map = compute_staleness(project_root)
        cohorts = list_cohorts(project_root)
        cohort_viols = cohort_violations(project_root)

    open_unk = []
    assumptions = []
    blocking = []
    unknown_rows = [
        u
        for u in unknowns
        if (u.get("record") or {}).get("role", "unknown") == "unknown"
    ]
    for u in unknowns:
        rec = u.get("record") or {}
        st = rec.get("status")
        if st in ("open", "probing", "blocked"):
            if rec.get("role", "unknown") == "assumption":
                assumptions.append(u)
            else:
                open_unk.append(u)
                blocking.append(u)

    voided_runs = [
        r
        for r in runs
        if (r.get("record") or {}).get("voided")
    ]
    recent_runs = runs[:8]

    plan_open = []
    plan_done = []
    for p in plans:
        rec = p.get("record") or {}
        pl = rec.get("plan") or {}
        if pl.get("all_satisfied"):
            plan_done.append(p)
        else:
            plan_open.append(p)

    known_by_conf: dict[str, list] = {"low": [], "med": [], "high": []}
    from .run_inputs import record_input_state

    known_input_states: dict[str, dict[str, Any]] = {}
    for k in knowns:
        rec = k.get("record") or {}
        known_input_states[str(rec.get("id") or k.get("id"))] = record_input_state(
            project_root, rec
        )
        conf = rec.get("confidence") or "low"
        if conf not in known_by_conf:
            conf = "low"
        known_by_conf[conf].append(k)

    return {
        "map_id": mid,
        "active": mid == get_active_map_id(project_root),
        "belief_path": belief,
        "probes_path": str(probes_root(project_root)),
        "counts": {
            "probes": len(probes),
            "unknowns": len(unknown_rows),
            "unknowns_open": len(open_unk),
            "unknowns_blocking": len(blocking),
            "assumptions": len(assumptions),
            "calculations": len(calculations),
            "calculations_stale": sum(
                1 for c in calculations if c["staleness"]["stale"]
            ),
            "calculations_conditional": sum(
                1
                for c in calculations
                if ((c.get("record") or {}).get("latest") or {}).get("conditional")
            ),
            "knowns": len(knowns),
            "knowns_low": len(known_by_conf["low"]),
            "knowns_med": len(known_by_conf["med"]),
            "knowns_high": len(known_by_conf["high"]),
            "knowns_stale": sum(1 for v in stale_map.values() if v.get("stale")),
            "knowns_input_stale": sum(
                1 for v in known_input_states.values() if v.get("stale")
            ),
            "knowns_conditional": sum(
                1 for v in known_input_states.values() if v.get("conditional")
            ),
            "plans": len(plans),
            "plans_open": len(plan_open),
            "plans_done": len(plan_done),
            "runs": len(runs),
            "runs_voided": len(voided_runs),
            "suites": len(suites),
            "cohorts": len(cohorts),
            "cohorts_inconsistent": len(cohort_viols),
        },
        "cohort_violations": cohort_viols,
        "probes": probes,
        "unknowns_open": [
            {
                "id": (u.get("record") or {}).get("id") or u.get("id"),
                "status": (u.get("record") or {}).get("status"),
                "blocks_build": bool((u.get("record") or {}).get("blocks_build")),
                "type": (u.get("record") or {}).get("type"),
                "claim": ((u.get("record") or {}).get("claim") or "")[:80],
                "ok": u.get("ok"),
            }
            for u in open_unk
        ],
        "assumptions": [
            {
                "id": (u.get("record") or {}).get("id") or u.get("id"),
                "status": (u.get("record") or {}).get("status"),
                "type": (u.get("record") or {}).get("type"),
                "value": (u.get("record") or {}).get("assumed_value"),
                "unit": (u.get("record") or {}).get("unit") or "",
                "reason": (u.get("record") or {}).get("assumption_reason"),
                "claim": ((u.get("record") or {}).get("claim") or "")[:80],
                "evidence_n": (((u.get("record") or {}).get("stats") or {}).get("n") or 0),
                "ok": u.get("ok"),
            }
            for u in assumptions
        ],
        "calculations": [
            {
                "id": c.get("id"),
                "ok": c.get("ok"),
                "blocks": c.get("blocks") or [],
                "stale": c["staleness"]["stale"],
                "stale_reasons": c["staleness"]["reasons"],
                "value": ((c.get("record") or {}).get("latest") or {}).get("value"),
                "quantity": ((c.get("record") or {}).get("output") or {}).get("quantity"),
                "profile": (c.get("record") or {}).get("profile") or "expression",
                "outputs": list(
                    (((c.get("record") or {}).get("latest") or {}).get("outputs") or {}).keys()
                ),
                "conditional": bool(
                    ((c.get("record") or {}).get("latest") or {}).get("conditional")
                ),
                "assumptions": (
                    ((c.get("record") or {}).get("latest") or {}).get("assumptions")
                    or []
                ),
            }
            for c in calculations
        ],
        "knowns": [
            {
                "id": (k.get("record") or {}).get("id") or k.get("id"),
                "type": (k.get("record") or {}).get("type"),
                "status": (k.get("record") or {}).get("status"),
                "confidence": (k.get("record") or {}).get("confidence"),
                "confidence_derived": (k.get("record") or {}).get(
                    "confidence_derived"
                ),
                "n": ((k.get("record") or {}).get("stats") or {}).get("n"),
                **(
                    {
                        "holds": ((k.get("record") or {}).get("stats") or {}).get(
                            "holds"
                        ),
                        "verdict": (
                            "pass"
                            if ((k.get("record") or {}).get("stats") or {}).get(
                                "holds"
                            )
                            is True
                            else "fail"
                            if ((k.get("record") or {}).get("stats") or {}).get(
                                "holds"
                            )
                            is False
                            else "unresolved"
                        ),
                    }
                    if (k.get("record") or {}).get("type") == "formula"
                    else {}
                ),
                "runs": len((k.get("record") or {}).get("run_ids") or []),
                "methods": (
                    (((k.get("record") or {}).get("stats") or {}).get(
                        "corroboration"
                    ) or {}).get("methods")
                ),
                "methods_agree": (
                    (((k.get("record") or {}).get("stats") or {}).get(
                        "corroboration"
                    ) or {}).get("agree")
                ),
                "spread_accepted": (
                    (((k.get("record") or {}).get("stats") or {}).get(
                        "corroboration"
                    ) or {}).get("accepted") is True
                ),
                "stale": bool(
                    (stale_map.get(
                        str((k.get("record") or {}).get("id") or k.get("id"))
                    ) or {}).get("stale")
                ),
                "stale_reasons": list(
                    (stale_map.get(
                        str((k.get("record") or {}).get("id") or k.get("id"))
                    ) or {}).get("reasons") or []
                ),
                "evidence_input_stale": bool(
                    known_input_states.get(
                        str((k.get("record") or {}).get("id") or k.get("id")), {}
                    ).get("stale")
                ),
                "conditional": bool(
                    known_input_states.get(
                        str((k.get("record") or {}).get("id") or k.get("id")), {}
                    ).get("conditional")
                ),
                "assumptions": known_input_states.get(
                    str((k.get("record") or {}).get("id") or k.get("id")), {}
                ).get("assumptions", []),
                "claim": ((k.get("record") or {}).get("claim") or "")[:80],
                "ok": k.get("ok"),
            }
            for k in knowns
        ],
        "plans": [
            {
                "id": (p.get("record") or {}).get("id") or p.get("id"),
                "mode": ((p.get("record") or {}).get("plan") or {}).get("mode"),
                "satisfied": (
                    ((p.get("record") or {}).get("plan") or {}).get(
                        "satisfied_count"
                    ),
                    ((p.get("record") or {}).get("plan") or {}).get("leg_count"),
                ),
                "all_satisfied": ((p.get("record") or {}).get("plan") or {}).get(
                    "all_satisfied"
                ),
                "next_leg": ((p.get("record") or {}).get("plan") or {}).get(
                    "next_leg"
                ),
                "confidence": (p.get("record") or {}).get("confidence"),
                "claim": ((p.get("record") or {}).get("claim") or "")[:80],
                "ok": p.get("ok"),
            }
            for p in plans
        ],
        "runs_recent": [
            {
                "id": r.get("id"),
                "source_type": (r.get("record") or {}).get("source_type")
                or "probe",
                "probe_id": (r.get("record") or {}).get("probe_id"),
                "calculation_id": (r.get("record") or {}).get("calculation_id"),
                "status": (r.get("record") or {}).get("status"),
                "voided": bool((r.get("record") or {}).get("voided")),
                "ok": r.get("ok"),
            }
            for r in recent_runs
        ],
        "suites": [
            {
                "id": (s.get("record") or {}).get("id") or s.get("id"),
                "probes": (s.get("record") or {}).get("probes"),
                "ok": s.get("ok"),
            }
            for s in suites
        ],
    }


def collect_status_board(
    project_root: Path,
    *,
    all_maps: bool = False,
    map_id: str | None = None,
) -> dict[str, Any]:
    """Build status board.

    Detail scopes follow active / --id / --all, but **attention and
    next_actions always scan every map** so a quiet global active map
    cannot hide session work (agent DX).
    """
    maps_meta = list_maps(project_root)
    from .paths import resolve_active_map

    active, active_source = resolve_active_map(project_root)
    all_ids = [m["id"] for m in maps_meta]
    if not all_ids:
        all_ids = [active]

    # Full scan for guidance (never lie about other maps)
    all_scopes = [collect_map_status(project_root, mid) for mid in all_ids]

    if all_maps:
        detail_ids = all_ids
    else:
        detail_ids = [map_id or active]
    detail_set = set(detail_ids)
    scopes = [s for s in all_scopes if s.get("map_id") in detail_set]
    if not scopes:
        scopes = [collect_map_status(project_root, detail_ids[0])]

    board = {
        "command": "map.status",
        "project_root": str(project_root),
        "active_map": active,
        "active_map_source": active_source,
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "maps": maps_meta,
        "scopes": scopes,
        # detail only — guidance uses all_scopes below
        "scopes_detail": "all" if all_maps else "selected",
    }
    guidance_board = {**board, "scopes": all_scopes}
    attention, next_actions = _derive_agent_guidance(guidance_board)
    if maps_meta and active not in {m["id"] for m in maps_meta}:
        attention.insert(
            0,
            {
                "kind": "active_map_missing",
                "map_id": active,
                "severity": "high",
                "detail": (
                    f"active map '{active}' (source: {active_source}) has no "
                    "map metadata — writes would land in a store no one reads. "
                    "Fix TERRA_MAP / .terra/active_map or terra map create."
                ),
            },
        )
    board["attention"] = attention
    board["next_actions"] = next_actions
    board["maps_with_attention"] = sorted(
        {
            str(a.get("map_id"))
            for a in attention
            if a.get("map_id")
            and a.get("kind")
            not in ("other_map_work", "other_map_summary")
        }
    )
    return board


def _derive_agent_guidance(
    board: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Agent-first: attention + runnable next_actions (not prose).

    Scopes in ``board`` must be the full multi-map scan for honesty.
    """
    from .agent_io import action, attention_item, sort_actions, sort_attention

    attention: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []
    active = board.get("active_map")

    for scope in board.get("scopes") or []:
        mid = str(scope.get("map_id") or "global")
        # Always pin --map when not active so argv works from any active map
        map_flag = ["--map", mid] if mid != active else []

        for u in scope.get("unknowns_open") or []:
            sev = "block"
            uid = str(u.get("id") or "")
            attention.append(
                attention_item(
                    "unknown_open",
                    id=uid,
                    severity=sev,
                    why=f"open unknown on map {mid}: {u.get('claim') or ''}",
                    extra={
                        "map_id": mid,
                        "status": u.get("status"),
                        "blocks_build": bool(u.get("blocks_build")),
                    },
                )
            )
            next_actions.append(
                action(
                    "unknown.show",
                    ["terra", *map_flag, "unknown", "show", uid, "--json"],
                    why="inspect open unknown before freehand domain work",
                    priority=10,
                    map_id=mid,
                )
            )

        for assumption in scope.get("assumptions") or []:
            aid = str(assumption.get("id") or "")
            attention.append(
                attention_item(
                    "assumption_active",
                    id=aid,
                    severity="med",
                    why=(
                        f"conditional input on map {mid}: {aid}="
                        f"{assumption.get('value')!r}"
                    ),
                    extra={"map_id": mid, "evidence_n": assumption.get("evidence_n")},
                )
            )

        for calculation in scope.get("calculations") or []:
            cid = str(calculation.get("id") or "")
            if not calculation.get("ok") or calculation.get("stale"):
                why = "; ".join(
                    calculation.get("blocks")
                    or calculation.get("stale_reasons")
                    or ["calculation needs attention"]
                )
                attention.append(
                    attention_item(
                        "calculation_invalid"
                        if not calculation.get("ok")
                        else "calculation_stale",
                        id=cid,
                        severity="block",
                        why=f"calculation {cid} on {mid}: {why}",
                        extra={"map_id": mid},
                    )
                )
                next_actions.append(
                    action(
                        "calculation.show",
                        ["terra", *map_flag, "calculation", "show", cid],
                        why="inspect and rerun the derived value",
                        priority=12,
                        map_id=mid,
                    )
                )
            elif calculation.get("conditional"):
                attention.append(
                    attention_item(
                        "calculation_conditional",
                        id=cid,
                        severity="med",
                        why=(
                            f"calculation {cid} on {mid} depends on assumptions: "
                            + ", ".join(calculation.get("assumptions") or [])
                        ),
                        extra={"map_id": mid},
                    )
                )
            next_actions.append(
                action(
                    "assumption.show",
                    ["terra", *map_flag, "assumption", "show", aid, "--json"],
                    why="inspect provisional basis and evidence needed",
                    priority=30,
                    map_id=mid,
                )
            )

        for p in scope.get("plans") or []:
            if p.get("all_satisfied"):
                continue
            pid = str(p.get("id") or "")
            sat = p.get("satisfied") or (0, 0)
            attention.append(
                attention_item(
                    "plan_incomplete",
                    id=pid,
                    severity="med",
                    why=(
                        f"plan {pid} on {mid}: {sat[0]}/{sat[1]} legs; "
                        f"next={p.get('next_leg') or '—'}"
                    ),
                    extra={
                        "map_id": mid,
                        "mode": p.get("mode"),
                        "next_leg": p.get("next_leg"),
                    },
                )
            )
            if p.get("next_leg"):
                next_actions.append(
                    action(
                        "plan.show",
                        ["terra", *map_flag, "plan", "show", pid, "--json"],
                        why=f"fill plan leg {p.get('next_leg')} before promote",
                        priority=30,
                        map_id=mid,
                    )
                )

        for k in scope.get("knowns") or []:
            kid = str(k.get("id") or "")
            if str(k.get("status") or "") in RETIRED_STATUSES:
                kid = str(k.get("id") or "")
                attention.append(
                    attention_item(
                        "known_retired",
                        id=kid,
                        severity="info",
                        why=(
                            f"known {kid} on map {mid} is {k.get('status')} "
                            "(retired belief, kept as history) - read path "
                            "refuses it as current"
                        ),
                        extra={"map_id": mid, "status": k.get("status")},
                    )
                )
                # Deliberately retired: don't also alarm as unbacked/stale/etc.
                continue
            if k.get("type") == "formula" and k.get("holds") is False:
                attention.append(
                    attention_item(
                        "known_formula_failed",
                        id=kid,
                        severity="block",
                        why=(
                            f"formula known {kid} on map {mid} has verdict=fail "
                            f"(claimed confidence={k.get('confidence')}, "
                            f"derived={k.get('confidence_derived')}, n={k.get('n')})"
                        ),
                        extra={
                            "map_id": mid,
                            "holds": False,
                            "verdict": "fail",
                            "confidence": k.get("confidence"),
                            "confidence_derived": k.get("confidence_derived"),
                        },
                    )
                )
                next_actions.append(
                    action(
                        "known.show",
                        ["terra", *map_flag, "known", "show", kid, "--json"],
                        why="inspect the evidence-backed failing formula",
                        priority=8,
                        map_id=mid,
                    )
                )
            if k.get("evidence_input_stale"):
                attention.append(
                    attention_item(
                        "evidence_input_stale",
                        id=kid,
                        severity="block",
                        why=(
                            f"known {kid} on map {mid} uses probe evidence "
                            "whose declared inputs moved"
                        ),
                        extra={"map_id": mid},
                    )
                )
                next_actions.append(
                    action(
                        "probe.rerun",
                        ["terra", *map_flag, "known", "show", kid],
                        why="identify and rerun stale assumption/known-conditioned evidence",
                        priority=9,
                        map_id=mid,
                    )
                )
            elif k.get("conditional"):
                attention.append(
                    attention_item(
                        "known_conditional",
                        id=kid,
                        severity="med",
                        why=(
                            f"known {kid} on map {mid} rests on assumptions: "
                            + ", ".join(k.get("assumptions") or [])
                        ),
                        extra={"map_id": mid},
                    )
                )
            if k.get("methods_agree") is False:
                kid = str(k.get("id") or "")
                if k.get("spread_accepted"):
                    attention.append(
                        attention_item(
                            "spread_accepted",
                            id=kid,
                            severity="info",
                            why=(
                                f"known {kid} on map {mid}: cross-method "
                                f"spread accepted as uncertainty (capped at "
                                f"med; consumers carry the band)"
                            ),
                            extra={"map_id": mid},
                        )
                    )
                    continue
                attention.append(
                    attention_item(
                        "methods_disagree",
                        id=kid,
                        severity="high",
                        why=(
                            f"known {kid} on map {mid}: independent probes "
                            f"disagree beyond tolerance — one instrument is wrong"
                        ),
                        extra={"map_id": mid},
                    )
                )
                next_actions.append(
                    action(
                        "known.show",
                        ["terra", *map_flag, "known", "show", kid],
                        why="compare per-probe stats; void bad evidence or fix the probe",
                        priority=11,
                        map_id=mid,
                    )
                )
            if k.get("stale"):
                kid = str(k.get("id") or "")
                attention.append(
                    attention_item(
                        "known_stale",
                        id=kid,
                        severity="high",
                        why=(
                            f"known {kid} on map {mid} is stale: "
                            + "; ".join(k.get("stale_reasons") or [])
                        ),
                        extra={"map_id": mid},
                    )
                )
                next_actions.append(
                    action(
                        "known.rederive",
                        ["terra", *map_flag, "known", "show", kid],
                        why="dependency moved — re-run probe + link-run, or reaffirm",
                        priority=12,
                        map_id=mid,
                    )
                )
            if (k.get("runs") or 0) > 0:
                continue
            kid = str(k.get("id") or "")
            attention.append(
                attention_item(
                    "known_unbacked",
                    id=kid,
                    severity="high",
                    why=(
                        f"known {kid} on map {mid} has no live linked runs "
                        "(evidence voided/unlinked) — belief is unbacked"
                    ),
                    extra={"map_id": mid, "confidence": k.get("confidence")},
                )
            )
            next_actions.append(
                action(
                    "known.link-run",
                    ["terra", *map_flag, "known", "link-run", kid, "<run_id>"],
                    why="re-back the known with a live run or delete it",
                    priority=15,
                    map_id=mid,
                )
            )

        for cv in scope.get("cohort_violations") or []:
            cid = str(cv.get("id") or "")
            attention.append(
                attention_item(
                    "cohort_inconsistent",
                    id=cid,
                    severity="high",
                    why=cv.get("why")
                    or f"cohort {cid} on map {mid}: members cite different solves",
                    extra={"map_id": mid, "members": cv.get("members")},
                )
            )
            next_actions.append(
                action(
                    "cohort.link-run",
                    ["terra", *map_flag, "cohort", "link-run", cid, "<run_id>"],
                    why="one re-solve refreshes the whole coupled set",
                    priority=12,
                    map_id=mid,
                )
            )

        for r in scope.get("runs_recent") or []:
            if not r.get("voided"):
                continue
            rid = str(r.get("id") or "")
            attention.append(
                attention_item(
                    "run_voided",
                    id=rid,
                    severity="info",
                    why=f"voided run still on map {mid} (excluded from stats)",
                    extra={"map_id": mid, "probe_id": r.get("probe_id")},
                )
            )

        c = scope.get("counts") or {}
        if c.get("probes", 0) == 0 and c.get("unknowns_open", 0) > 0:
            next_actions.append(
                action(
                    "probe.create",
                    [
                        "terra",
                        *map_flag,
                        "probe",
                        "create",
                        "<slug>",
                        "--purpose",
                        "…",
                    ],
                    why="open unknowns exist but no probes yet",
                    priority=15,
                    map_id=mid,
                )
            )

    # Surface "work is on another map" when detail is partial
    other_maps = sorted(
        {
            str(a.get("map_id"))
            for a in attention
            if a.get("map_id") and a.get("map_id") != active
        }
    )
    if other_maps:
        attention.append(
            attention_item(
                "other_map_work",
                severity="high",
                why=(
                    f"active map is {active!r} but work exists on: "
                    f"{', '.join(other_maps)} "
                    f"(use --map or terra map use; detail scopes may omit them "
                    f"unless --all)"
                ),
                extra={"map_id": active, "other_maps": other_maps},
            )
        )
        for om in other_maps:
            next_actions.append(
                action(
                    "map.use",
                    ["terra", "map", "use", om],
                    why=f"switch active map to {om} to work without --map flags",
                    priority=5,
                    map_id=om,
                )
            )

    if not attention and not next_actions:
        next_actions.append(
            action(
                "map.status",
                ["terra", "map", "status", "--all"],
                why="all maps quiet; re-check with --all after new runs/links",
                priority=90,
            )
        )

    return sort_attention(attention), sort_actions(next_actions)


def agent_status_response(board: dict[str, Any]) -> dict[str, Any]:
    """Cartograph AgentResponse envelope around the status board."""
    from .agent_io import success

    return success(
        board,
        meta={
            "surface": "terra.map.status",
            "attention_count": len(board.get("attention") or []),
            "next_action_count": len(board.get("next_actions") or []),
        },
    )


def format_status_text(board: dict[str, Any]) -> str:
    lines: list[str] = []
    src = board.get("active_map_source")
    src_tag = f" (via {src})" if src in ("env", "cli") else ""
    lines.append(f"Terra map status  active={board.get('active_map')}{src_tag}")
    lines.append(f"  project: {board.get('project_root')}")
    lines.append(f"  at: {board.get('generated_at')}")
    maps = board.get("maps") or []
    if len(maps) > 1:
        ids = "  ".join(
            ("*" if m.get("active") else " ") + str(m.get("id")) for m in maps
        )
        lines.append(f"  maps:{ids}")
    mwa = board.get("maps_with_attention") or []
    if mwa:
        lines.append(f"  attention on maps: {', '.join(mwa)}")
    # Cross-map attention (even when scopes are active-only)
    att = board.get("attention") or []
    if att:
        lines.append("")
        lines.append(f"ATTENTION ({len(att)}) — all maps")
        for a in att[:20]:
            lines.append(
                f"  • [{a.get('severity')}] {a.get('kind')}  "
                f"map={a.get('map_id')}  id={a.get('id') or '—'}  "
                f"{a.get('why') or ''}"
            )
        if len(att) > 20:
            lines.append(f"  … +{len(att) - 20} more")
    lines.append("")

    for scope in board.get("scopes") or []:
        mid = scope.get("map_id")
        star = " (active)" if scope.get("active") else ""
        c = scope.get("counts") or {}
        lines.append("=" * 60)
        lines.append(f"MAP  {mid}{star}")
        lines.append(f"  path: {scope.get('belief_path')}")
        lines.append(
            f"  probes={c.get('probes')} (global)  "
            f"unknowns={c.get('unknowns')} "
            f"(open={c.get('unknowns_open')} block={c.get('unknowns_blocking')})  "
            f"assumptions={c.get('assumptions')}  "
            f"calculations={c.get('calculations')} "
            f"(stale={c.get('calculations_stale')})  "
            f"knowns={c.get('knowns')} "
            f"(L{c.get('knowns_low')}/M{c.get('knowns_med')}/H{c.get('knowns_high')})  "
            f"plans={c.get('plans')} "
            f"(open={c.get('plans_open')} done={c.get('plans_done')})  "
            f"runs={c.get('runs')} voided={c.get('runs_voided')}  "
            f"suites={c.get('suites')}"
        )
        lines.append("")

        assumptions = scope.get("assumptions") or []
        lines.append(f"  ASSUMPTIONS ({len(assumptions)})")
        if not assumptions:
            lines.append("    (none)")
        else:
            for assumption in assumptions:
                lines.append(
                    f"    • {assumption.get('id')}={assumption.get('value')!r} "
                    f"CONDITIONAL n={assumption.get('evidence_n')}  "
                    f"{assumption.get('claim') or ''}"
                )
        lines.append("")

        calculations = scope.get("calculations") or []
        lines.append(f"  CALCULATIONS ({len(calculations)})")
        if not calculations:
            lines.append("    (none)")
        else:
            for calculation in calculations:
                state = "STALE" if calculation.get("stale") else "current"
                conditional = " CONDITIONAL" if calculation.get("conditional") else ""
                lines.append(
                    f"    • {calculation.get('id')}={calculation.get('value')!r} "
                    f"{state}{conditional}"
                )
        lines.append("")

        # Open unknowns first — attention surface
        unk = scope.get("unknowns_open") or []
        lines.append(f"  OPEN UNKNOWNS ({len(unk)})")
        if not unk:
            lines.append("    (none)")
        else:
            for u in unk:
                blk = " BLOCKS" if u.get("blocks_build") else ""
                typ = f" type={u.get('type')}" if u.get("type") else ""
                lines.append(
                    f"    • {u.get('id')}  {u.get('status')}{blk}{typ}  "
                    f"{u.get('claim') or ''}"
                )
        lines.append("")

        kn = scope.get("knowns") or []
        lines.append(f"  KNOWNS ({len(kn)})")
        if not kn:
            lines.append("    (none)")
        else:
            for k in kn:
                lines.append(
                    f"    • {k.get('id')}  {k.get('type')}  "
                    f"conf={k.get('confidence')}/{k.get('confidence_derived')}  "
                    f"n={k.get('n')}  {k.get('status')}  {k.get('claim') or ''}"
                )
        lines.append("")

        pl = scope.get("plans") or []
        lines.append(f"  PLANS ({len(pl)})")
        if not pl:
            lines.append("    (none)")
        else:
            for p in pl:
                sat = p.get("satisfied") or (0, 0)
                flag = "done" if p.get("all_satisfied") else "open"
                nxt = f" next={p.get('next_leg')}" if p.get("next_leg") else ""
                lines.append(
                    f"    • {p.get('id')}  [{flag}] mode={p.get('mode')}  "
                    f"{sat[0]}/{sat[1]}{nxt}  conf={p.get('confidence')}  "
                    f"{p.get('claim') or ''}"
                )
        lines.append("")

        rr = scope.get("runs_recent") or []
        lines.append(f"  RECENT RUNS (up to 8 of {c.get('runs', 0)})")
        if not rr:
            lines.append("    (none)")
        else:
            for r in rr:
                void = " VOID" if r.get("voided") else ""
                flag = "ok" if r.get("ok") else "BAD"
                lines.append(
                    f"    [{flag}]{void} {r.get('id')}  "
                    f"probe={r.get('probe_id')}  status={r.get('status')}"
                )
        lines.append("")

        pr = scope.get("probes") or []
        lines.append(f"  PROBES global ({len(pr)})")
        if not pr:
            lines.append("    (none)")
        else:
            for p in pr:
                lines.append(
                    f"    • {p.get('id')}  kind={p.get('kind') or '?'}  "
                    f"{(p.get('purpose') or '')[:50]}"
                )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_status_html(board: dict[str, Any]) -> str:
    """Self-contained HTML status page."""

    def esc(x: Any) -> str:
        return html.escape("" if x is None else str(x))

    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>Terra map status</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;margin:1.5rem;background:#0f1419;color:#e7ecf1}",
        "h1,h2,h3{color:#f0f3f6} a{color:#6cb6ff}",
        ".meta{color:#8b9bab;font-size:.9rem}",
        ".map{border:1px solid #2a3540;border-radius:8px;padding:1rem;margin:1rem 0;background:#151b22}",
        ".counts{display:flex;flex-wrap:wrap;gap:.5rem;margin:.75rem 0}",
        ".chip{background:#1e2832;border-radius:999px;padding:.25rem .7rem;font-size:.85rem}",
        ".chip.warn{background:#3d2a12;color:#ffb454}",
        ".chip.ok{background:#12301f;color:#7ee787}",
        "table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem;font-size:.9rem}",
        "th,td{border-bottom:1px solid #2a3540;padding:.35rem .5rem;text-align:left}",
        "th{color:#8b9bab;font-weight:600}",
        ".block{color:#ff7b72}.void{color:#ffa657}.done{color:#7ee787}",
        "code{font-family:ui-monospace,monospace;font-size:.85em}",
        "</style></head><body>",
        "<h1>Terra map status</h1>",
        f"<p class='meta'>active=<code>{esc(board.get('active_map'))}</code> · "
        f"{esc(board.get('generated_at'))}<br>"
        f"project: <code>{esc(board.get('project_root'))}</code></p>",
    ]

    for scope in board.get("scopes") or []:
        mid = scope.get("map_id")
        c = scope.get("counts") or {}
        star = " · active" if scope.get("active") else ""
        parts.append("<div class='map'>")
        parts.append(f"<h2>Map <code>{esc(mid)}</code>{esc(star)}</h2>")
        parts.append(
            f"<p class='meta'><code>{esc(scope.get('belief_path'))}</code></p>"
        )
        parts.append("<div class='counts'>")
        chips = [
            (f"probes {c.get('probes')}", ""),
            (
                f"open unknowns {c.get('unknowns_open')}",
                "warn" if c.get("unknowns_open") else "ok",
            ),
            (
                f"blocking {c.get('unknowns_blocking')}",
                "warn" if c.get("unknowns_blocking") else "",
            ),
            (f"knowns {c.get('knowns')} L/M/H "
             f"{c.get('knowns_low')}/{c.get('knowns_med')}/{c.get('knowns_high')}",
             ""),
            (
                f"plans open {c.get('plans_open')}",
                "warn" if c.get("plans_open") else "ok",
            ),
            (f"runs {c.get('runs')}", ""),
            (
                f"voided {c.get('runs_voided')}",
                "warn" if c.get("runs_voided") else "",
            ),
        ]
        for label, cls in chips:
            parts.append(f"<span class='chip {cls}'>{esc(label)}</span>")
        parts.append("</div>")

        # unknowns
        parts.append("<h3>Open unknowns</h3>")
        unk = scope.get("unknowns_open") or []
        if not unk:
            parts.append("<p class='meta'>(none)</p>")
        else:
            parts.append(
                "<table><tr><th>id</th><th>status</th><th></th><th>claim</th></tr>"
            )
            for u in unk:
                blk = (
                    "<span class='block'>BLOCKS</span>"
                    if u.get("blocks_build")
                    else ""
                )
                parts.append(
                    f"<tr><td><code>{esc(u.get('id'))}</code></td>"
                    f"<td>{esc(u.get('status'))}</td><td>{blk}</td>"
                    f"<td>{esc(u.get('claim'))}</td></tr>"
                )
            parts.append("</table>")

        parts.append("<h3>Knowns</h3>")
        kn = scope.get("knowns") or []
        if not kn:
            parts.append("<p class='meta'>(none)</p>")
        else:
            parts.append(
                "<table><tr><th>id</th><th>type</th><th>conf</th>"
                "<th>n</th><th>claim</th></tr>"
            )
            for k in kn:
                parts.append(
                    f"<tr><td><code>{esc(k.get('id'))}</code></td>"
                    f"<td>{esc(k.get('type'))}</td>"
                    f"<td>{esc(k.get('confidence'))}/"
                    f"{esc(k.get('confidence_derived'))}</td>"
                    f"<td>{esc(k.get('n'))}</td>"
                    f"<td>{esc(k.get('claim'))}</td></tr>"
                )
            parts.append("</table>")

        parts.append("<h3>Plans</h3>")
        pl = scope.get("plans") or []
        if not pl:
            parts.append("<p class='meta'>(none)</p>")
        else:
            parts.append(
                "<table><tr><th>id</th><th>mode</th><th>progress</th>"
                "<th>next</th><th>claim</th></tr>"
            )
            for p in pl:
                sat = p.get("satisfied") or (0, 0)
                done = (
                    "<span class='done'>done</span>"
                    if p.get("all_satisfied")
                    else "open"
                )
                parts.append(
                    f"<tr><td><code>{esc(p.get('id'))}</code></td>"
                    f"<td>{esc(p.get('mode'))} {done}</td>"
                    f"<td>{esc(sat[0])}/{esc(sat[1])}</td>"
                    f"<td>{esc(p.get('next_leg') or '—')}</td>"
                    f"<td>{esc(p.get('claim'))}</td></tr>"
                )
            parts.append("</table>")

        parts.append("<h3>Recent runs</h3>")
        rr = scope.get("runs_recent") or []
        if not rr:
            parts.append("<p class='meta'>(none)</p>")
        else:
            parts.append(
                "<table><tr><th>id</th><th>probe</th><th>status</th><th></th></tr>"
            )
            for r in rr:
                void = (
                    "<span class='void'>VOID</span>" if r.get("voided") else ""
                )
                parts.append(
                    f"<tr><td><code>{esc(r.get('id'))}</code></td>"
                    f"<td>{esc(r.get('probe_id'))}</td>"
                    f"<td>{esc(r.get('status'))}</td><td>{void}</td></tr>"
                )
            parts.append("</table>")

        parts.append("<h3>Probes (global)</h3>")
        pr = scope.get("probes") or []
        if not pr:
            parts.append("<p class='meta'>(none)</p>")
        else:
            parts.append("<table><tr><th>id</th><th>kind</th><th>purpose</th></tr>")
            for p in pr:
                parts.append(
                    f"<tr><td><code>{esc(p.get('id'))}</code></td>"
                    f"<td>{esc(p.get('kind'))}</td>"
                    f"<td>{esc(p.get('purpose'))}</td></tr>"
                )
            parts.append("</table>")

        parts.append("</div>")

    parts.append("</body></html>")
    return "\n".join(parts)


def write_status_html(
    project_root: Path, board: dict[str, Any], *, path: Path | None = None
) -> Path:
    out = path or (terra_root(project_root) / "map_status.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(format_status_html(board), encoding="utf-8")
    return out
