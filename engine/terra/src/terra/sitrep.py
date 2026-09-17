"""Session orientation in ONE call — the agent's first command of a session.

Why this exists (agent economics, not convenience):

An agent turn costs the re-read of its entire context (~20k tokens for a
program lead). The Terra CLI is one-command-per-invocation, so the standard
orientation sequence — ``route status`` + ``route budget`` + ``map status`` +
``known list`` + ``gate`` — burns FIVE turns to answer one question ("where
does the program stand?"). Measured on a real program, orientation calls were
~2.3k invocations and Terra-bearing turns were ~26% of total token spend,
while the commands' own output was under 4% of that cost. The expense is the
round trip, not the bytes.

``sitrep`` is therefore a DIGEST, not a concatenation: it composes the same
pure read functions and returns counts, merged attention, budget rollup, gate
verdict and next actions — deliberately WITHOUT the full task table, known
table, or violation list that make the individual commands large. It is
designed to be smaller than any one of the calls it replaces, and to be the
only command an agent needs to run to decide what to do next.

Design invariants (do not break these):

1. **Pure view.** Composes ``route_status`` / ``collect_status_board`` /
   ``check_gate`` / ``brief_summary``. Writes nothing, stores nothing,
   computes nothing those functions don't already compute. No new state.
2. **Always exits 0** on success, even when the gate FAILS. ``terra gate``
   exits 1 by design, which silently aborts ``&&`` chains mid-sequence — the
   exact footgun that makes batched shell calls lie. sitrep is an orientation
   read; the gate verdict travels in the payload (``data.gate.ok``), never in
   the exit code. Use ``terra gate`` when you want CI semantics.
3. **Capped by default.** Lists are truncated with an explicit
   ``truncated`` block so an agent can never mistake a clipped list for a
   complete one. ``--full`` removes the caps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default caps — chosen so a sitrep stays smaller than a single `route status`
MAX_ATTENTION = 12
MAX_ACTIONS = 6
MAX_NEXT_TASKS = 5
MAX_VIOLATIONS = 8
MAX_BLOCKED = 8


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _slim_task(t: dict[str, Any]) -> dict[str, Any]:
    """Task row reduced to what a dispatch decision actually needs."""
    row = {
        "id": t.get("id"),
        "title": t.get("title"),
        # priority (WHICH work) rides beside bucket (HOW MUCH effort).
        # sitrep is the first command of every session — a rank the
        # orientation digest drops is a rank nobody acts on.
        "priority": t.get("priority"),
        "bucket": t.get("bucket"),
        "points": t.get("points"),
    }
    if t.get("sector_id"):
        row["sector_id"] = t.get("sector_id")
    if t.get("skill"):
        row["skill"] = t.get("skill")
    if t.get("owner_agent"):
        row["owner_agent"] = t.get("owner_agent")
    return row


def _slim_violation(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": v.get("kind"),
        "map_id": v.get("map_id"),
        "id": v.get("id"),
        "why": v.get("why"),
    }


def _cap(rows: list[Any], limit: int | None) -> tuple[list[Any], int]:
    """Return (kept, dropped)."""
    if limit is None or len(rows) <= limit:
        return list(rows), 0
    return list(rows[:limit]), len(rows) - limit


def _slim_budget(bud: dict[str, Any] | None) -> dict[str, Any] | None:
    """Budget headline only — the full rollup carries every sector row."""
    if not bud:
        return None
    sectors = [
        {
            "id": s.get("id"),
            "reserved": s.get("reserved_points"),
            "actual": s.get("points_actual", s.get("actual_points")),
        }
        for s in (bud.get("sectors") or [])
    ]
    return {
        "budget_points": bud.get("budget_points"),
        "points_plan": bud.get("points_plan"),
        "points_actual": bud.get("points_actual"),
        "points_done": bud.get("points_done"),
        "points_remaining_budget": bud.get("points_remaining_budget"),
        "points_remaining_work": bud.get("points_remaining_work"),
        "over_budget": bud.get("over_budget"),
        "over_plan": bud.get("over_plan"),
        "free_pool": bud.get("free_pool"),
        "sectors": sectors,
    }


def _summarize_attention(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Roll attention up by (plane, kind, severity) with counts.

    A program with 300 open unknowns produces 300 near-identical attention
    rows; truncating that to 12 shows an agent twelve versions of the same
    fact and hides every other KIND of debt behind them. The summary is the
    honest digest — it is never truncated, because it is bounded by the
    number of distinct kinds, not the size of the program.
    """
    from .agent_io import severity_rank

    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for a in rows:
        key = (
            str(a.get("plane") or "?"),
            str(a.get("kind") or "?"),
            str(a.get("severity") or "info"),
        )
        b = buckets.get(key)
        if b is None:
            buckets[key] = {
                "plane": key[0],
                "kind": key[1],
                "severity": key[2],
                "count": 1,
                "example_id": a.get("id"),
            }
        else:
            b["count"] += 1
    return sorted(
        buckets.values(),
        key=lambda b: (severity_rank(str(b["severity"])), -int(b["count"])),
    )


def _diverse_sample(
    rows: list[dict[str, Any]], limit: int | None
) -> tuple[list[dict[str, Any]], int]:
    """Sample attention so every KIND is represented before any repeats.

    Round-robins across kinds instead of taking the first N, so a flood of
    one kind cannot bury the single instance of another.
    """
    if limit is None or len(rows) <= limit:
        return list(rows), 0
    by_kind: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for a in rows:
        k = f"{a.get('plane')}/{a.get('kind')}"
        if k not in by_kind:
            by_kind[k] = []
            order.append(k)
        by_kind[k].append(a)
    out: list[dict[str, Any]] = []
    while len(out) < limit:
        progressed = False
        for k in order:
            if by_kind[k]:
                out.append(by_kind[k].pop(0))
                progressed = True
                if len(out) >= limit:
                    break
        if not progressed:
            break
    return out, len(rows) - len(out)


def collect_sitrep(
    project_root: Path,
    *,
    full: bool = False,
    map_id: str | None = None,
) -> dict[str, Any]:
    """One-call program orientation. Pure read; never mutates."""
    from .agent_io import sort_attention, sort_actions
    from .brief import brief_summary, load_brief
    from .gate import check_gate
    from .map_status import collect_status_board
    from .paths import resolve_active_map
    from .route import route_status

    _BRIEF_ERR: list[str] = []
    _ROUTE_ERR: list[str] = []

    cap_att = None if full else MAX_ATTENTION
    cap_act = None if full else MAX_ACTIONS
    cap_next = None if full else MAX_NEXT_TASKS
    cap_viol = None if full else MAX_VIOLATIONS
    cap_blk = None if full else MAX_BLOCKED

    active, active_source = resolve_active_map(project_root)

    # --- brief (may be absent on a map-only project) -----------------
    brief_block: dict[str, Any] | None = None
    try:
        bs = brief_summary(load_brief(project_root))
        brief_block = {
            "title": bs.get("title"),
            "status": bs.get("status"),
            "version": bs.get("version"),
            "budget_points": bs.get("budget_points"),
            "needs": len(bs.get("needs") or []),
            "deliverables": len(bs.get("deliverables") or []),
            "enablers_needed": [
                e.get("id") or e.get("name")
                for e in (bs.get("enablers_needed") or [])
            ],
            "open_proposals": bs.get("open_proposals", 0),
        }
    except Exception as e:  # noqa: BLE001
        brief_block = None
        _BRIEF_ERR.append(f"{type(e).__name__}: {e}")

    # --- route (may be absent before `route init`) -------------------
    route_block: dict[str, Any] | None = None
    route_attention: list[dict[str, Any]] = []
    try:
        rs = route_status(project_root)
        route_attention = list(rs.get("attention") or [])
        nxt, nxt_dropped = _cap(
            [_slim_task(t) for t in (rs.get("next") or [])], cap_next
        )
        blk, blk_dropped = _cap(
            [_slim_task(t) for t in (rs.get("blocked") or [])], cap_blk
        )
        # Phase lifecycle: current phase + its exit blockers. Slimmed to the
        # current row — the full per-phase table is `route phases`.
        ph = rs.get("phases") or {}
        cur_id = ph.get("current")
        cur_row = next(
            (r for r in (ph.get("phases") or []) if r.get("id") == cur_id), None
        )
        phase_block = {
            "current": cur_id,
            "declared": ph.get("declared") or [],
            "unphased_open": ph.get("unphased_open", 0),
            "undeclared_used": ph.get("undeclared_used") or [],
            "current_row": cur_row,
        } if (ph.get("declared") or ph.get("undeclared_used")) else None
        route_block = {
            "counts": rs.get("counts"),
            "phases": phase_block,
            "plan_locked": rs.get("plan_locked"),
            "budget": _slim_budget(rs.get("budget")),
            "next": nxt,
            "blocked": blk,
            "dropped": {"next": nxt_dropped, "blocked": blk_dropped},
        }
    except Exception as e:  # noqa: BLE001
        route_block = None
        _ROUTE_ERR.append(f"{type(e).__name__}: {e}")

    # Orientation must DEGRADE, never die. A single stale belief anywhere in
    # the tree used to raise out of collect_status_board / check_gate and take
    # the whole digest with it — on 2026-07-28 one stale known on a session map
    # made `terra sitrep` unusable program-wide, which is the opposite of what
    # a first-command-of-the-session verb is for. Each section now fails
    # independently into `degraded`, so the parts that still work still answer.
    degraded: list[dict[str, Any]] = []

    def _try(section: str, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — orientation outranks purity
            degraded.append(
                {"section": section, "error": f"{type(e).__name__}: {e}"}
            )
            return None

    # --- map board ---------------------------------------------------
    board = _try(
        "maps",
        lambda: collect_status_board(project_root, all_maps=True, map_id=map_id),
    ) or {}
    map_attention = list(board.get("attention") or [])
    board_actions = list(board.get("next_actions") or [])

    scopes = board.get("scopes") or []
    map_rows = []
    for s in scopes:
        counts = s.get("counts") or {}
        map_rows.append(
            {
                "map_id": s.get("map_id"),
                "counts": counts,
            }
        )

    # --- gate --------------------------------------------------------
    gate = _try("gate", lambda: check_gate(project_root)) or {
        "ok": None,
        "violations": [],
        "notices": [],
        "counts": {},
        "maps_checked": [],
    }
    violations = [_slim_violation(v) for v in (gate.get("violations") or [])]
    viol_kept, viol_dropped = _cap(violations, cap_viol)

    # --- merged attention -------------------------------------------
    # Route attention has no map_id; tag it so an agent can tell the two
    # planes apart (task debt vs belief debt) without a second call.
    merged: list[dict[str, Any]] = []
    for a in route_attention:
        merged.append({**a, "plane": "route"})
    for a in map_attention:
        merged.append({**a, "plane": "map"})
    merged = sort_attention(merged)
    attention_summary = _summarize_attention(merged)
    att_kept, att_dropped = _diverse_sample(merged, cap_att)

    actions = sort_actions(board_actions)
    act_kept, act_dropped = _cap(actions, cap_act)

    return {
        "command": "terra.sitrep",
        "generated_at": _now(),
        "project_root": str(project_root),
        "active_map": active,
        "active_map_source": active_source,
        "brief": brief_block,
        "route": route_block,
        "maps": map_rows,
        "maps_with_attention": board.get("maps_with_attention") or [],
        "degraded": (
            degraded
            + [{"section": "brief", "error": e} for e in _BRIEF_ERR]
            + [{"section": "route", "error": e} for e in _ROUTE_ERR]
        ),
        "gate": {
            "ok": gate.get("ok") if gate.get("ok") is None else bool(gate.get("ok")),
            "maps_checked": gate.get("maps_checked"),
            "counts": gate.get("counts") or {},
            "violations": viol_kept,
            "notices": len(gate.get("notices") or []),
        },
        "attention_summary": attention_summary,
        "attention_total": len(merged),
        "attention": att_kept,
        "next_actions": act_kept,
        "truncated": {
            "attention": att_dropped,
            "next_actions": act_dropped,
            "violations": viol_dropped,
            "route_next": (route_block or {}).get("dropped", {}).get("next", 0),
            "route_blocked": (route_block or {})
            .get("dropped", {})
            .get("blocked", 0),
            "full": bool(full),
        },
    }


def format_sitrep_text(rep: dict[str, Any]) -> str:
    """Human rendering. Agents should use the default JSON envelope."""
    out: list[str] = []
    b = rep.get("brief") or {}
    title = b.get("title") or "(no brief)"
    out.append(f"SITREP  {title}   map={rep.get('active_map')} "
               f"(via {rep.get('active_map_source')})")

    r = rep.get("route")
    if r:
        c = r.get("counts") or {}
        bud = r.get("budget") or {}
        out.append(
            f"  route: {c.get('done', 0)}/{c.get('tasks', 0)} done  "
            f"pickable={c.get('pickable', 0)}  in_progress={c.get('in_progress', 0)}  "
            f"blocked={c.get('blocked', 0)}  "
            f"locked={'yes' if r.get('plan_locked') else 'no'}"
        )
        if bud:
            out.append(
                f"  budget: actual={bud.get('points_actual')} "
                f"plan={bud.get('points_plan')} "
                f"of {bud.get('budget_points')}  "
                f"remaining={bud.get('points_remaining_budget')}  "
                f"in_flight={bud.get('points_remaining_work')}"
                + ("  OVER BUDGET" if bud.get("over_budget") else "")
            )
        ph = r.get("phases")
        if ph:
            row = ph.get("current_row") or {}
            bits = [f"  phase: {ph.get('current') or '(all exited)'}"]
            if row:
                bits.append(
                    f"{row.get('open', 0)} open/{row.get('done', 0)} done"
                    + (
                        f"  !{row['unreachable']} UNREACHABLE"
                        if row.get("unreachable")
                        else ""
                    )
                )
            if ph.get("undeclared_used"):
                bits.append(
                    f"  ⚠ undeclared phase tags: {ph['undeclared_used']}"
                )
            if ph.get("unphased_open"):
                bits.append(f"  {ph['unphased_open']} open task(s) unphased")
            out.append("  ".join(bits))
        prio = (r.get("counts") or {}).get("by_priority_open") or {}
        if prio:
            out.append(
                "  priority(open): "
                + "  ".join(f"{k}={v}" for k, v in prio.items())
            )
        for t in r.get("next") or []:
            out.append(
                f"    next  {t.get('id')}  [{t.get('priority')}/{t.get('bucket')}] "
                f"{t.get('title')}"
            )
    else:
        out.append("  route: (not initialized)")

    g = rep.get("gate") or {}
    counts = g.get("counts") or {}
    by_kind = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    # A degraded gate is UNKNOWN, not FAIL — rendering "FAIL" for a verdict we
    # could not compute is the same lying-instrument class sitrep exists to
    # avoid, and rendering "PASS" would be worse.
    _verdict = "UNKNOWN" if g.get("ok") is None else ("PASS" if g.get("ok") else "FAIL")
    out.append(
        f"  gate: {_verdict}  "
        f"violations={sum(counts.values())}  "
        f"notices={g.get('notices', 0)}"
        + (f"\n    by kind: {by_kind}" if by_kind else "")
    )

    summary = rep.get("attention_summary") or []
    if summary:
        out.append(f"  attention rollup ({rep.get('attention_total', 0)} items):")
        for s in summary:
            out.append(
                f"    {s.get('count'):>5}x [{s.get('severity')}] "
                f"{s.get('plane')}/{s.get('kind')}"
                + (f"   e.g. {s.get('example_id')}" if s.get("example_id") else "")
            )
    att = rep.get("attention") or []
    if att:
        out.append(f"  sample ({len(att)} of {rep.get('attention_total', 0)}, one per kind first):")
        for a in att:
            why = str(a.get("why") or a.get("detail") or "")
            if len(why) > 100:
                why = why[:100] + "…"
            out.append(
                f"    [{a.get('severity')}] {a.get('plane')}/{a.get('kind')} "
                f"{a.get('id') or ''} {why}".rstrip()
            )
    for a in rep.get("next_actions") or []:
        out.append(f"    do  {' '.join(a.get('argv') or [])}   # {a.get('why')}")

    for d in rep.get("degraded") or []:
        out.append(f"  ⚠ DEGRADED [{d.get('section')}]: {str(d.get('error'))[:150]}")
    tr = rep.get("truncated") or {}
    dropped = {k: v for k, v in tr.items() if k != "full" and v}
    if dropped:
        out.append(f"  (truncated: {dropped} — rerun with --full)")
    return "\n".join(out)
