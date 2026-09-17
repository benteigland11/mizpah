"""Terra CLI — brief, route, and map state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .agent_io import emit, error, success
from .assumptions import (
    create_assumption,
    describe_assumption,
    link_probe_assumption,
    link_run_assumption,
    list_assumptions,
    read_assumption,
    set_assumption,
    unlink_run_assumption,
)
from .calculations import (
    create_calculation,
    delete_calculation,
    get_calculation,
    list_calculations,
    load_calculation,
    parse_bindings,
    parse_output_specs,
    run_calculation,
    validate_calculation,
)
from .paths import (
    GLOBAL_MAP_ID,
    create_session_map,
    ensure_map_store,
    ensure_project_root,
    ensure_probes_store,
    get_active_map_id,
    list_maps,
    map_root,
    probe_dir,
    probes_root,
    require_project_root,
    set_active_map_id,
    unknown_path,
    write_active_map,
)
from .probe_init import init_probe
from .probe_run import (
    DEFAULT_RUN_TIMEOUT_S,
    delete_run,
    list_runs,
    load_run,
    parse_to_arg,
    run_probe,
    unvoid_run,
    void_run,
)
from .probe_validate import (
    validate_all_probes,
    validate_probe_dir,
    validate_probe_script,
)
from .run_validate import validate_all_runs, validate_run_id
from .knowns import (
    create_known,
    delete_known,
    describe_known,
    link_run_known,
    list_knowns,
    load_known,
    promote_known,
    set_known,
    set_known_status,
    unlink_run_known,
    validate_known_file,
)
from .number_type import CONFIDENCE_SET, KNOWN_STATUSES
from .suites import (
    create_suite,
    list_suites,
    load_suite,
    parse_probe_list,
    run_suite,
    validate_all_suites,
    validate_suite,
)
from .unknown_contract import UNKNOWN_STATUSES
from .unknowns import (
    create_unknown,
    delete_unknown,
    describe_unknown,
    link_probe,
    link_run,
    list_unknowns,
    load_unknown,
    set_status,
    unlink_run,
    validate_all_unknowns,
    validate_unknown_file,
)


def _print_io_steps(exercise: dict | None, *, indent: str = "  ") -> None:
    """Always print INPUT/OUTPUT (and EXECUTE) status — never silent on failure."""
    if not exercise or "steps" not in exercise:
        return
    steps = exercise["steps"]
    for name in ("input", "execute", "output"):
        step = steps.get(name) or {}
        ok = step.get("ok")
        if ok is True:
            label = "ok"
        elif ok is False:
            label = "FAIL"
        else:
            label = "—"
        print(f"{indent}{name.upper():8} {label}")
        for b in step.get("blocks") or []:
            print(f"{indent}  error: {b}")


def _print_probe_result(result: dict, *, json_out: bool) -> int:
    if json_out:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    level = result.get("level", 1)
    if "probes" in result:
        print(f"validation level {level} (input to → output to/status/artifacts)")
        for b in result.get("blocks", []):
            print(f"block: {b}")
        for p in result.get("probes", []):
            status = "ok" if p["ok"] else "FAIL"
            print(f"[{status}] {p.get('id')}")
            _print_io_steps(p.get("exercise"), indent="  ")
            for b in p.get("blocks", []):
                # avoid duplicating step errors already printed under INPUT/OUTPUT
                if "/input:" in b or "/output:" in b or "/execute:" in b:
                    continue
                print(f"  block: {b}")
            for w in p.get("warnings", []):
                print(f"  warn:  {w}")
        print("PASS" if result.get("ok") else "FAIL")
        return 0 if result.get("ok") else 1

    status = "ok" if result.get("ok") else "FAIL"
    print(f"[{status}] {result.get('id')}  (level {level})")
    if result.get("path"):
        print(f"  path:  {result['path']}")
    _print_io_steps(result.get("exercise"), indent="  ")
    for b in result.get("blocks", []):
        if "/input:" in b or "/output:" in b or "/execute:" in b:
            continue
        print(f"  block: {b}")
    for w in result.get("warnings", []):
        print(f"  warn:  {w}")
    ex = result.get("exercise")
    if ex and result.get("ok"):
        print(
            f"  exercise: status={ex.get('status')!r} "
            f"artifacts={ex.get('artifact_count')}"
        )
    print("PASS" if result.get("ok") else "FAIL")
    return 0 if result.get("ok") else 1


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve() if args.path else Path.cwd().resolve()
    try:
        ensure_map_store(root)
    except (ValueError, OSError) as e:
        return emit(error(str(e), code="init"))
    return emit(
        success(
            {
                "project_root": str(root),
                "map_id": GLOBAL_MAP_ID,
                "active_map": get_active_map_id(root),
                "belief_path": str(map_root(root)),
                "probes_path": str(probes_root(root)),
                "next_actions": [
                    {
                        "op": "map.create",
                        "argv": [
                            "terra", "map", "create", "<experiment>",
                            "--purpose", "<purpose>", "--use",
                        ],
                        "why": "isolate experimental beliefs from global",
                    }
                ],
            },
            meta={"surface": "terra.init"},
        )
    )


def cmd_map_create(args: argparse.Namespace) -> int:
    try:
        root, created = ensure_project_root()
        path = create_session_map(
            root,
            args.id,
            purpose=args.purpose or "",
            use=bool(args.use),
            force=bool(args.force),
            parent=getattr(args, "parent", None),
        )
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if created:
        print(f"initialized {root / '.terra' / 'map'}")
    parent = getattr(args, "parent", None) or "global"
    print(f"created map {args.id}  (session, parent={parent})")
    if parent != "global":
        print(
            f"  reads fall through {args.id} -> {parent} -> … -> global; "
            f"promote up with: terra known adopt <id> --from {args.id}"
        )
    print(f"  {path}")
    print("  probes/lib stay global; unknowns/knowns/runs/suites are scoped here")
    if args.use:
        print(f"  active map → {args.id}")
        print(
            "  NOTE: unknowns/knowns/runs you create now land on "
            f"'{args.id}', not global — back: terra map use global"
        )
    else:
        print(f"  use: terra map use {args.id}   or  --map {args.id}")
    return 0


def cmd_map_list(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rows = list_maps(root)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    children: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("kind") == "session":
            children.setdefault(r.get("parent") or "global", []).append(r)

    visited: set[str] = set()

    def _emit(r: dict, depth: int) -> None:
        visited.add(r.get("id"))
        star = "*" if r.get("active") else " "
        indent = "  " * depth
        print(
            f"{star} {indent}[{r.get('kind')}] {r.get('id')}  "
            f"{r.get('purpose') or ''}"
        )
        for c in children.get(r.get("id"), []):
            _emit(c, depth + 1)

    _emit(rows[0], 0)
    # orphans (parent map deleted by hand, or a cycle) still listed, loudly
    for r in rows[1:]:
        if r.get("id") not in visited:
            print(f"! [orphan] {r.get('id')}  parent {r.get('parent')!r} unreachable")
    from .paths import resolve_active_map

    mid, source = resolve_active_map(root)
    tag = f"  (via {source})" if source in ("env", "cli") else ""
    print(f"active: {mid}{tag}")
    return 0


def cmd_map_use(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        write_active_map(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    active = get_active_map_id(root)
    env_map = os.environ.get("TERRA_MAP", "").strip()
    if env_map and env_map != args.id:
        print(f"active map → {env_map}  (TERRA_MAP overrides the pointer)")
        print(
            f"  NOTE: .terra/active_map now says '{args.id}', but TERRA_MAP={env_map} "
            f"wins for every later command in this shell — unset TERRA_MAP or "
            f"use terra --map {args.id} …"
        )
        return 0
    print(f"active map → {active}")
    if env_map:
        print("  (pinned by TERRA_MAP in this shell)")
    print(f"  belief path: {map_root(root)}")
    if active != "global":
        print(
            "  NOTE: beliefs/runs now land on this session map — durable "
            "results belong on global (terra map use global)"
        )
    return 0


def cmd_map_show(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    mid = get_active_map_id(root)
    info = {
        "active": mid,
        "belief_path": str(map_root(root)),
        "probes_path": str(probes_root(root)),
        "maps": list_maps(root),
    }
    if args.json:
        print(json.dumps(info, indent=2, default=str))
        return 0
    print(f"active map: {mid}")
    print(f"  beliefs (unknowns/knowns/runs/suites): {map_root(root)}")
    print(f"  probes+lib (always global): {probes_root(root)}")
    print("  board: terra map status   (or --html)")
    return 0


def cmd_brief_init(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .brief import brief_summary, init_brief, load_brief

    try:
        root, _ = ensure_project_root()
        init_brief(
            root,
            title=args.title,
            mission=args.mission or "",
            force=bool(args.force),
        )
        rec = load_brief(root)
    except (ValueError, FileExistsError, OSError) as e:
        return emit(error(str(e), code="brief_init"))
    return emit(success(brief_summary(rec), meta={"surface": "terra.brief.init"}))


def cmd_brief_show(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .brief import brief_summary, load_brief

    try:
        root = require_project_root()
        rec = load_brief(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="brief_show"))
    if args.human:
        print(f"brief v{rec.get('version')}  status={rec.get('status')}")
        print(f"title: {rec.get('title')}")
        print(f"mission: {rec.get('mission') or '—'}")
        bp = rec.get("budget_points")
        bn = rec.get("budget_notes") or ""
        print(f"budget_points: {bp if bp is not None else '—'}  (task buckets: low=3 medium=8 high=21)")
        if bn:
            print(f"budget_notes: {bn}")
        print(f"needs: {rec.get('needs') or []}")
        print(f"non_goals: {rec.get('non_goals') or []}")
        print(f"deliverables: {rec.get('deliverables') or []}")
        ens = rec.get("enablers") or []
        if ens:
            print("enablers (internal tooling — not customer pack):")
            for e in ens:
                print(
                    f"  • [{e.get('status')}] {e.get('id')}  {e.get('title')}  "
                    f"path={e.get('path') or '—'}"
                )
        else:
            print("enablers: []")
        print(f"phases: {[p.get('id') for p in (rec.get('phases') or [])]}")
        print(f"change_control: {rec.get('change_control')}")
        return 0
    data = rec if args.full else brief_summary(rec)
    return emit(success(data, meta={"surface": "terra.brief.show"}))


def cmd_brief_set(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .brief import brief_summary, set_brief_fields

    try:
        root = require_project_root()
        rec = set_brief_fields(
            root,
            title=args.title,
            mission=args.mission,
            status=args.status,
            budget_points=getattr(args, "budget_points", None),
            clear_budget_points=bool(getattr(args, "clear_budget_points", False)),
            budget_notes=getattr(args, "budget_notes", None),
            needs=args.needs,
            non_goals=args.non_goals,
            deliverables=args.deliverables,
            enablers=getattr(args, "enablers", None),
            replace_lists=bool(args.replace_lists),
        )
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="brief_set"))
    return emit(success(brief_summary(rec), meta={"surface": "terra.brief.set"}))


def cmd_brief_phase(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .brief import add_phase, brief_summary

    try:
        root = require_project_root()
        rec = add_phase(root, args.id, title=args.title or "")
    except (FileNotFoundError, ValueError, FileExistsError, OSError) as e:
        return emit(error(str(e), code="brief_phase"))
    return emit(success(brief_summary(rec), meta={"surface": "terra.brief.phase"}))


def cmd_brief_propose(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .brief import propose_change

    try:
        root = require_project_root()
        prop = propose_change(
            root,
            summary=args.summary,
            need=args.need,
            non_goal=args.non_goal,
            deliverable=args.deliverable,
            enabler=getattr(args, "enabler", None),
            mission=args.mission,
        )
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="brief_propose"))
    return emit(success(prop, meta={"surface": "terra.brief.propose"}))


def cmd_brief_accept(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .brief import accept_proposal, brief_summary

    try:
        root = require_project_root()
        rec = accept_proposal(root, args.id)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="brief_accept"))
    return emit(success(brief_summary(rec), meta={"surface": "terra.brief.accept"}))


def cmd_brief_reject(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .brief import brief_summary, reject_proposal

    try:
        root = require_project_root()
        rec = reject_proposal(root, args.id)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="brief_reject"))
    return emit(success(brief_summary(rec), meta={"surface": "terra.brief.reject"}))


def cmd_brief_enabler(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .brief import brief_summary, set_enabler_status

    try:
        root = require_project_root()
        rec = set_enabler_status(
            root,
            args.id,
            args.status,
            path=args.path,
            graduates_to=args.graduates_to,
            notes=args.notes,
        )
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="brief_enabler"))
    ens = [e for e in (rec.get("enablers") or []) if e.get("id") == args.id]
    return emit(
        success(
            {"enabler": ens[0] if ens else None, "brief": brief_summary(rec)},
            meta={"surface": "terra.brief.enabler"},
        )
    )


def cmd_route_init(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import init_route, route_status

    try:
        root, _ = ensure_project_root()
        init_route(root, force=bool(args.force))
        st = route_status(root)
    except (ValueError, FileExistsError, OSError) as e:
        return emit(error(str(e), code="route_init"))
    return emit(success(st, meta={"surface": "terra.route.init"}))


def cmd_route_status(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import route_status

    try:
        root = require_project_root()
        st = route_status(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_status"))
    if args.human:
        c = st.get("counts") or {}
        print(f"route  tasks={c.get('tasks')}  pickable={c.get('pickable')}  "
              f"in_progress={c.get('in_progress')}  blocked={c.get('blocked')}  "
              f"done={c.get('done')}")
        b = st.get("budget") or {}
        _print_budget_human(b)
        for a in st.get("attention") or []:
            print(f"  ! [{a.get('kind')}] {a.get('id')}: {a.get('why')}")
        print("next:")
        for t in st.get("next") or []:
            pts = t.get("points")
            bkt = t.get("bucket")
            weight = f"  {bkt}/{pts}pt" if bkt or pts else ""
            print(f"  • {t.get('id')}  [{t.get('status')}]  skill={t.get('skill')}{weight}  "
                  f"{t.get('title')}")
        return 0
    return emit(success(st, meta={"surface": "terra.route.status"}))


def _print_budget_human(b: dict) -> None:
    if (
        b.get("budget_points") is None
        and not b.get("points_actual")
        and not b.get("points_plan")
        and not b.get("points_planned")
        and not b.get("sectors")
    ):
        return
    flags = []
    if b.get("plan_locked"):
        flags.append("PLAN LOCKED")
    if b.get("over_budget"):
        flags.append("OVER BUDGET")
    if b.get("over_plan"):
        flags.append("OVER PLAN")
    flag_s = ("  " + " ".join(flags)) if flags else ""
    plan = b.get("points_plan")
    actual = b.get("points_actual", b.get("points_planned"))
    print(
        f"budget  total={b.get('budget_points') if b.get('budget_points') is not None else '—'}  "
        f"plan={plan}  actual={actual}  done={b.get('points_done')}  "
        f"free_pool={b.get('free_pool') if b.get('free_pool') is not None else '—'}  "
        f"sector_reserved={b.get('sector_reserved_total')}  "
        f"var={b.get('variance_actual_minus_plan')}  "
        f"(low=3 medium=8 high=21){flag_s}"
    )
    if b.get("plan_locked_at"):
        print(f"  plan_locked_at={b.get('plan_locked_at')}")
    for s in b.get("sectors") or []:
        over = " OVER_RESERVE" if s.get("over_reserve") else ""
        print(
            f"  sector {s.get('id')}: reserved={s.get('reserved_points')}  "
            f"plan={s.get('points_plan')}  actual={s.get('points_actual')}  "
            f"remain_plan={s.get('remaining_reserve_plan')}{over}  "
            f"— {s.get('title')}"
        )
    if b.get("tasks_without_points"):
        print(f"  tasks_without_points={b.get('tasks_without_points')}")


def cmd_route_log(args: argparse.Namespace) -> int:
    """Chronological what-happened view — routes are the record, not md files."""
    from .agent_io import emit, error, success
    from .route import route_log

    try:
        root = require_project_root()
        out = route_log(
            root,
            limit=int(args.limit or 0),
            task_id=getattr(args, "task", None),
        )
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_log"))
    if args.human:
        c = out.get("counts") or {}
        print(f"route log  events={c.get('events')}  shown={c.get('shown')}")
        for ev in out.get("events") or []:
            mark = "✗" if ev.get("kind") == "blocked" else "✓"
            print(
                f"{ev.get('at')}  {mark} {ev.get('task')}  "
                f"[{ev.get('skill')}]  {ev.get('title')}"
            )
            if ev.get("note"):
                print(f"    note: {ev['note']}")
            if ev.get("freehand"):
                print(f"    freehand: {ev['freehand']}")
            if ev.get("runs"):
                print(f"    runs: {', '.join(ev['runs'])}")
            if ev.get("knowns"):
                print(f"    knowns: {', '.join(ev['knowns'])}")
            if ev.get("reason"):
                print(f"    blocked: {ev['reason']}")
        return 0
    return emit(success(out, meta={"surface": "terra.route.log"}))


def cmd_route_budget(args: argparse.Namespace) -> int:
    """Agent-friendly budget-only view (same numbers as route status)."""
    from .agent_io import emit, error, success
    from .route import route_status

    try:
        root = require_project_root()
        st = route_status(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_budget"))
    b = st.get("budget") or {}
    # include compact task weights for allocation visibility
    tasks = []
    for t in st.get("tasks") or []:
        if t.get("status") == "cancelled":
            continue
        tasks.append(
            {
                "id": t.get("id"),
                "status": t.get("status"),
                "sector_id": t.get("sector_id"),
                "bucket": t.get("bucket"),
                "points": t.get("points"),
                "plan_bucket": t.get("plan_bucket"),
                "plan_points": t.get("plan_points"),
                "title": t.get("title"),
            }
        )
    data = {**b, "tasks": tasks}
    if args.human:
        _print_budget_human(b)
        for t in tasks:
            sec = f" sector={t.get('sector_id')}" if t.get("sector_id") else ""
            w = (
                f"plan={t.get('plan_bucket')}/{t.get('plan_points')}pt "
                f"actual={t.get('bucket')}/{t.get('points')}pt"
                if t.get("points") is not None or t.get("plan_points") is not None
                else "unset"
            )
            print(
                f"  • {t.get('id')}  [{t.get('status')}]{sec}  {w}  {t.get('title')}"
            )
        return 0
    return emit(success(data, meta={"surface": "terra.route.budget"}))


def cmd_route_set_effort(args: argparse.Namespace) -> int:
    """Re-bucket a task; may exceed budget when plan is locked."""
    from .agent_io import emit, error, success
    from .route import route_status, set_task_effort

    try:
        root = require_project_root()
        t = set_task_effort(
            root,
            args.id,
            bucket=getattr(args, "bucket", None),
            points=getattr(args, "points", None),
        )
        st = route_status(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_set_effort"))
    payload = {"task": t, "budget": st.get("budget")}
    if args.human:
        print(
            f"set {t.get('id')} → actual={t.get('bucket')}/{t.get('points')}pt "
            f"plan={t.get('plan_bucket')}/{t.get('plan_points')}pt "
            f"(plan_locked={st.get('plan_locked')})"
        )
        _print_budget_human(st.get("budget") or {})
        return 0
    return emit(success(payload, meta={"surface": "terra.route.set_effort"}))


def cmd_route_prioritize(args: argparse.Namespace) -> int:
    """Re-rank tasks. Orthogonal to effort — never moves the budget."""
    from .agent_io import emit, error, success
    from .route import PRIORITY_MEANING, route_status, set_task_priority

    try:
        root = require_project_root()
        changed = set_task_priority(
            root,
            list(args.ids or []),
            priority=args.priority,
            reason=getattr(args, "reason", None),
        )
        st = route_status(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_prioritize"))
    counts = (st.get("counts") or {}).get("by_priority_open") or {}
    payload = {
        "changed": changed,
        "priority": args.priority,
        "meaning": PRIORITY_MEANING.get(args.priority),
        "by_priority_open": counts,
    }
    if args.human:
        for t in changed:
            print(f"{t.get('id')} → {t.get('priority')}  {t.get('title')}")
        print(f"open by priority: {counts}")
        return 0
    return emit(success(payload, meta={"surface": "terra.route.prioritize"}))


def cmd_brief_phase_close(args: argparse.Namespace) -> int:
    """Declare a phase closed/re-opened. Authority act, not a count."""
    from .agent_io import emit, error, success
    from .brief import brief_summary, close_phase

    try:
        root = require_project_root()
        rec = close_phase(
            root, args.id, reason=args.reason, reopen=bool(args.reopen)
        )
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="brief_phase_close"))
    from .route import route_status

    try:
        st = route_status(root)
        roll = st.get("phases") or {}
        warn = [a for a in (st.get("attention") or [])
                if a.get("kind") == "phase_closed_with_open_work"
                and a.get("id") == args.id]
    except Exception:  # noqa: BLE001
        roll, warn = {}, []
    if warn:
        print(f"NOTE: {warn[0]['why']}", file=sys.stderr)
    payload = {
        "phases": brief_summary(rec).get("phases"),
        "current": roll.get("current"),
        "warning": warn[0] if warn else None,
    }
    if args.human:
        state = "re-opened" if args.reopen else "CLOSED"
        print(f"phase {args.id} {state} — current phase now: {roll.get('current')}")
        return 0
    return emit(success(payload, meta={"surface": "terra.brief.phase_close"}))


def cmd_route_rephase(args: argparse.Namespace) -> int:
    """Move tasks between lifecycle phases."""
    from .agent_io import emit, error, success
    from .route import route_status, set_task_phase

    try:
        root = require_project_root()
        changed = set_task_phase(
            root, list(args.ids or []), phase=args.phase,
            reason=getattr(args, "reason", None),
        )
        st = route_status(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_rephase"))
    payload = {"changed": [t.get("id") for t in changed],
               "phase": args.phase, "phases": st.get("phases")}
    if args.human:
        print(f"re-phased {len(changed)} task(s) -> {args.phase}")
        return 0
    return emit(success(payload, meta={"surface": "terra.route.rephase"}))


def cmd_route_phases(args: argparse.Namespace) -> int:
    """Lifecycle view: where are we, and can we exit the current phase?"""
    from .agent_io import emit, error, success
    from .route import route_status

    try:
        root = require_project_root()
        st = route_status(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_phases"))
    roll = st.get("phases") or {}
    if args.human:
        if not roll.get("declared"):
            print("(no phases declared — terra brief phase <id> --title \"…\")")
        print(f"current phase: {roll.get('current') or '(all declared phases exited)'}")
        hdr = (
            f"{'phase':<24}{'open':>6}{'done':>6}{'canc':>6}"
            f"{'blkd':>6}{'dead':>6}{'pts':>7}  exit"
        )
        print(hdr)
        print("-" * len(hdr))
        for r in roll.get("phases") or []:
            if r["closed"]:
                mark = "CLOSED" if not r["open"] else f"CLOSED (!{r['open']} OPEN)"
            else:
                mark = "ready" if r["exit_ready"] else f"{r['exit_blockers']} left"
            if r["unreachable"]:
                mark += f" (!{r['unreachable']} unreachable)"
            flag = "" if r["declared"] else "  [UNDECLARED]"
            print(
                f"{r['id']:<24}{r['open']:>6}{r['done']:>6}{r['cancelled']:>6}"
                f"{r['blocked']:>6}{r['unreachable']:>6}{r['points_open']:>7}"
                f"  {mark}{flag}"
            )
        if roll.get("unphased_open"):
            print(f"\n{roll['unphased_open']} open task(s) carry NO phase "
                  f"— invisible to every phase exit criterion")
        return 0
    return emit(success(roll, meta={"surface": "terra.route.phases"}))


def cmd_route_sector_add(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import add_sector, route_status

    try:
        root = require_project_root()
        sec = add_sector(
            root,
            args.id,
            title=args.title,
            reserved_points=int(args.points),
            notes=args.notes or "",
        )
        st = route_status(root)
    except (FileNotFoundError, ValueError, FileExistsError, OSError) as e:
        return emit(error(str(e), code="route_sector_add"))
    if args.human:
        print(
            f"sector {sec.get('id')} reserved={sec.get('reserved_points')}  "
            f"{sec.get('title')}"
        )
        _print_budget_human(st.get("budget") or {})
        return 0
    return emit(
        success({"sector": sec, "budget": st.get("budget")}, meta={"surface": "terra.route.sector_add"})
    )


def cmd_route_sector_set(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import route_status, set_sector_reserve

    try:
        root = require_project_root()
        sec = set_sector_reserve(
            root,
            args.id,
            reserved_points=args.points,
            title=args.title,
            notes=args.notes,
        )
        st = route_status(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_sector_set"))
    if args.human:
        print(
            f"sector {sec.get('id')} reserved={sec.get('reserved_points')}  "
            f"{sec.get('title')}"
        )
        _print_budget_human(st.get("budget") or {})
        return 0
    return emit(
        success({"sector": sec, "budget": st.get("budget")}, meta={"surface": "terra.route.sector_set"})
    )


def cmd_route_lock_plan(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import lock_plan

    try:
        root = require_project_root()
        out = lock_plan(root)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_lock_plan"))
    if args.human:
        print(out.get("message"))
        _print_budget_human(out.get("budget") or {})
        return 0
    return emit(success(out, meta={"surface": "terra.route.lock_plan"}))


def cmd_route_unlock_plan(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import unlock_plan

    try:
        root = require_project_root()
        out = unlock_plan(root, confirm=bool(args.confirm))
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_unlock_plan"))
    if args.human:
        print(out.get("message"))
        _print_budget_human(out.get("budget") or {})
        return 0
    return emit(success(out, meta={"surface": "terra.route.unlock_plan"}))


def cmd_route_next(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import next_tasks

    try:
        root = require_project_root()
        tasks = next_tasks(
            root,
            limit=int(args.limit or 5),
            priority=getattr(args, "priority", None),
            phase=getattr(args, "phase", None),
        )
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_next"))
    if args.human:
        if not tasks:
            print("(no pickable tasks)")
            return 0
        for t in tasks:
            pts = t.get("points")
            bkt = t.get("bucket")
            weight = f"  {bkt}/{pts}pt" if bkt or pts else ""
            print(
                f"{t.get('id')}  [{t.get('status')}]  {t.get('priority')}  "
                f"skill={t.get('skill')}{weight}  {t.get('title')}"
            )
        return 0
    return emit(
        success(
            {"command": "route.next", "tasks": tasks},
            meta={"surface": "terra.route.next"},
        )
    )


def cmd_route_add(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import add_task

    try:
        root = require_project_root()
        t = add_task(
            root,
            args.id,
            title=args.title,
            phase=args.phase or "",
            deps=args.deps or [],
            skill=args.skill or "any",
            role=getattr(args, "role", None) or "any",
            enabler_id=getattr(args, "enabler_id", None),
            acceptance=args.acceptance or [],
            map_id=args.task_map,
            bucket=getattr(args, "bucket", None),
            points=getattr(args, "points", None),
            sector_id=getattr(args, "sector_id", None),
            priority=getattr(args, "priority", None),
        )
    except (FileNotFoundError, ValueError, FileExistsError, OSError) as e:
        return emit(error(str(e), code="route_add"))
    return emit(success(t, meta={"surface": "terra.route.add"}))


def cmd_route_start(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import start_task

    try:
        root = require_project_root()
        t = start_task(root, args.id, agent=getattr(args, "agent", None))
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_start"))
    meta: dict[str, Any] = {"surface": "terra.route.start"}
    from .route import HEARTBEAT_STALE_HOURS, load_route

    notes: list[str] = []
    if not t.get("owner_agent"):
        notes.append(
            "no --agent given: task is claimed anonymously — pass "
            "--agent <id> so a stranded lead is attributable"
        )
    notes.append(
        f"send `terra route heartbeat {args.id}` during long silent work; "
        f"route status flags a missing heartbeat after {HEARTBEAT_STALE_HOURS}h"
    )
    others = [
        x.get("id")
        for x in load_route(root).get("tasks") or []
        if x.get("status") == "in_progress" and x.get("id") != args.id
    ]
    if others:
        notes.append(
            f"also in_progress: {others} — complete/block before they go "
            f"stale (route status flags >=7d)"
        )
    if notes:
        meta["note"] = " | ".join(notes)
    return emit(success(t, meta=meta))


def cmd_route_heartbeat(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import heartbeat_task

    try:
        root = require_project_root()
        t = heartbeat_task(root, args.id, agent=getattr(args, "agent", None))
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_heartbeat"))
    return emit(success(t, meta={"surface": "terra.route.heartbeat"}))


def cmd_route_complete(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import _get_task, complete_task, load_route

    try:
        root = require_project_root()
        task = _get_task(load_route(root), args.id)
        gate_meta: dict[str, Any] = {}
        if "deliverable" in (task.get("skill"), task.get("role")):
            from .gate import check_gate

            verdict = check_gate(root)
            if not verdict["ok"]:
                override = getattr(args, "skip_gate", None)
                if not override:
                    lines = [
                        f"[{v['kind']}] {v['map_id']}/{v['id']}: {v['why']}"
                        for v in verdict["violations"]
                    ]
                    return emit(
                        error(
                            "deliverable task blocked by gate — resolve the "
                            "debt or record an override with --skip-gate "
                            "'<reason>':\n  " + "\n  ".join(lines),
                            code="route_gate",
                            meta={"violations": verdict["violations"]},
                        )
                    )
                gate_meta = {
                    "gate_overridden": override,
                    "gate_violations": len(verdict["violations"]),
                }
        ev = args.evidence
        if gate_meta:
            note = (
                f"GATE OVERRIDE ({gate_meta['gate_violations']} violations): "
                f"{gate_meta['gate_overridden']}"
            )
            ev = f"{ev} | {note}" if ev else note
        t = complete_task(
            root,
            args.id,
            evidence=ev,
            run_ids=getattr(args, "run_refs", None),
            known_ids=getattr(args, "known_refs", None),
            freehand=getattr(args, "freehand", None),
        )
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_complete"))
    return emit(success(t, meta={"surface": "terra.route.complete", **gate_meta}))


def cmd_route_block(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import block_task

    try:
        root = require_project_root()
        t = block_task(root, args.id, reason=args.reason)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_block"))
    return emit(success(t, meta={"surface": "terra.route.block"}))


def cmd_route_cancel(args: argparse.Namespace) -> int:
    """Retire a task that should never be worked. Not `done` — never valid."""
    from .agent_io import emit, error, success
    from .route import cancel_task, dependents_of

    try:
        root = require_project_root()
        # Look BEFORE the write: cancelling strands every live dependent
        # permanently (a cancelled dep never turns done), and until this
        # note existed that happened in total silence.
        stranded = dependents_of(root, args.id, live_only=True)
        t = cancel_task(root, args.id, reason=args.reason,
                        force=getattr(args, "force", False))
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_cancel"))
    if stranded:
        ids = ", ".join(str(s.get("id")) for s in stranded)
        print(
            f"NOTE: cancelling {args.id} STRANDS {len(stranded)} live "
            f"task(s): {ids}\n"
            f"      They can never become pickable — a cancelled dep never "
            f"turns done.\n"
            f"      Re-point each onto the live successor, or cancel it too:\n"
            f"        terra route status   # see kind=task_dep_cancelled",
            file=sys.stderr,
        )
    payload = dict(t)
    payload["stranded_dependents"] = [s.get("id") for s in stranded]
    return emit(success(payload, meta={"surface": "terra.route.cancel"}))


def cmd_route_unblock(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .route import unblock_task

    try:
        root = require_project_root()
        t = unblock_task(root, args.id)
    except (FileNotFoundError, ValueError, OSError) as e:
        return emit(error(str(e), code="route_unblock"))
    return emit(success(t, meta={"surface": "terra.route.unblock"}))


def cmd_map_status(args: argparse.Namespace) -> int:
    """Map board — agent-first (JSON default, like cartograph status).

    Human pretty-print: --human. Browser: --html / --open.
    """
    from .agent_io import emit, error
    from .map_status import (
        agent_status_response,
        collect_status_board,
        format_status_text,
        write_status_html,
    )

    try:
        root = require_project_root()
        board = collect_status_board(
            root,
            all_maps=bool(args.all),
            map_id=getattr(args, "map_scope", None),
        )
    except FileNotFoundError as e:
        return emit(error(str(e), code="no_project"))

    want_html = bool(args.html) or bool(args.open)
    if want_html:
        out_path = Path(args.output) if args.output else None
        path = write_status_html(root, board, path=out_path)
        # Still agent-envelope for path so agents can open it if needed
        if not getattr(args, "human", False):
            return emit(
                agent_status_response(
                    {
                        **board,
                        "html_path": str(path),
                    }
                )
            )
        print(f"wrote {path}")
        if args.open:
            import webbrowser

            webbrowser.open(path.resolve().as_uri())
            print("opened in browser")
        return 0

    if getattr(args, "human", False):
        print(format_status_text(board), end="")
        return 0

    # Default: machine-first JSON (Cartograph status precedent)
    return emit(agent_status_response(board))


def cmd_probe_create(args: argparse.Namespace) -> int:
    """Scaffold a new probe package (create is the base command; init is an alias).

    Auto-creates the map store in cwd when missing.
    """
    try:
        root, created_store = ensure_project_root()
        from .map_inputs import parse_map_bindings

        pdir = init_probe(
            root,
            args.id,
            purpose=args.purpose,
            kind=args.kind,
            duration_s=args.duration,
            force=args.force,
            inputs=parse_map_bindings(args.input or []),
        )
    except (ValueError, FileExistsError, OSError) as e:
        return emit(error(str(e), code="probe_create"))
    rec = json.loads((pdir / "probe.json").read_text(encoding="utf-8"))
    return emit(
        success(
            {
                **rec,
                "next_actions": [
                    {
                        "op": "probe.validate",
                        "argv": ["terra", "probe", "validate", args.id],
                        "why": "validate the implemented probe contract",
                    }
                ],
            },
            meta={
                "surface": "terra.probe.create",
                "path": str(pdir),
                "initialized_store": bool(created_store),
            },
        )
    )


def cmd_unknown_create(args: argparse.Namespace) -> int:
    try:
        if args.no_blocks_build:
            raise ValueError(
                "unknowns are hard composability gates; use `terra assumption "
                "create` for a provisional consumable value"
            )
        root, created_store = ensure_project_root()
        path = create_unknown(
            root,
            args.id,
            claim=args.claim,
            evidence_needed=args.evidence or "",
            blocks_build=not args.no_blocks_build,
            probe_id=args.probe,
            notes=args.notes or "",
            force=args.force,
            map_type=getattr(args, "type", None),
            quantity=getattr(args, "quantity", None),
            unit=getattr(args, "unit", "") or "",
            expression=getattr(args, "expression", None),
            vars=getattr(args, "vars", None),
            tolerance=getattr(args, "within", None),
            x_quantity=getattr(args, "x_quantity", None),
            x_unit=getattr(args, "x_unit", "") or "",
        )
    except (ValueError, FileExistsError, OSError) as e:
        return emit(error(str(e), code="unknown_create"))
    rec = load_unknown(root, args.id)
    _announce_write_target(root, "unknown create")
    next_actions = (
        [
            {
                "op": "probe.create",
                "argv": [
                    "terra", "probe", "create", "<id>",
                    "--purpose", "<purpose>",
                ],
                "why": "build an instrument for this unknown",
            },
            {
                "op": "unknown.link_probe",
                "argv": [
                    "terra", "unknown", "link-probe", args.id,
                    "<probe_id>",
                ],
                "why": "attach an existing instrument",
            },
        ]
        if not args.probe
        else []
    )
    return emit(
        success(
            {**rec, "next_actions": next_actions},
            meta={
                "surface": "terra.unknown.create",
                "path": str(path),
                "initialized_store": bool(created_store),
            },
        )
    )


def cmd_unknown_list(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rows = [
        row
        for row in list_unknowns(root)
        if (row.get("record") or {}).get("role", "unknown") == "unknown"
    ]
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("(no unknowns — if stuck, create one: terra unknown create …)")
        return 0
    for r in rows:
        flag = "ok" if r["ok"] else "BAD"
        rec = r.get("record") or {}
        status = rec.get("status", "?")
        # blocks_build only meaningful while still active (open/probing/blocked)
        active = status in ("open", "probing", "blocked")
        block = (
            " blocks_build"
            if active and rec.get("blocks_build")
            else ""
        )
        claim = rec.get("claim") or ""
        print(f"[{flag}] {r['id']}  {status}{block}  {claim}")
        for b in r.get("blocks") or []:
            print(f"  block: {b}")
    return 0


def _parse_assumption_value(raw: str, map_type: str) -> object:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"assumption value must be JSON: {e}") from e
    if map_type == "boolean" and not isinstance(value, bool):
        raise ValueError("boolean assumption value must be true or false")
    if map_type == "number" and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        raise ValueError("number assumption value must be numeric")
    return value


def cmd_assumption_create(args: argparse.Namespace) -> int:
    try:
        root, created_store = ensure_project_root()
        value = _parse_assumption_value(args.value, args.type)
        path = create_assumption(
            root,
            args.id,
            claim=args.claim,
            map_type=args.type,
            quantity=args.quantity,
            value=value,
            reason=args.reason,
            evidence_needed=args.evidence,
            unit=args.unit or "",
            probe_id=args.probe,
            notes=args.notes or "",
            force=args.force,
        )
        _announce_write_target(root, "assumption create")
    except (ValueError, FileExistsError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if created_store:
        print(f"initialized {root / '.terra' / 'map'}")
    return emit(success(read_assumption(root, args.id), meta={"surface": "terra.assumption.create", "path": str(path)}))


def cmd_assumption_list(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rows = list_assumptions(root)
    except (FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_list"))
    return emit(success(rows, meta={"surface": "terra.assumption.list"}))


def cmd_assumption_show(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        data = describe_assumption(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_show"))
    return emit(success(data, meta={"surface": "terra.assumption.show"}))


def cmd_assumption_get(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        reading = read_assumption(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_get"))
    if args.raw:
        print(json.dumps(reading.get("value")))
        return 0
    return emit(success(reading, meta={"surface": "terra.assumption.get"}))


def cmd_assumption_set(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        current = read_assumption(root, args.id)
        value = _parse_assumption_value(args.value, str(current.get("type")))
        _announce_write_target(root, "assumption set")
        set_assumption(root, args.id, value=value, reason=args.reason)
        reading = read_assumption(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_set"))
    return emit(success(reading, meta={"surface": "terra.assumption.set"}))


def cmd_assumption_link_probe(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        _announce_write_target(root, "assumption link-probe")
        rec = link_probe_assumption(root, args.id, args.probe_id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_link_probe"))
    return emit(success(rec, meta={"surface": "terra.assumption.link_probe"}))


def cmd_assumption_link_run(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        _announce_write_target(root, "assumption link-run")
        rec = link_run_assumption(root, args.id, args.run_id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_link_run"))
    return emit(success(rec, meta={"surface": "terra.assumption.link_run"}))


def cmd_assumption_unlink_run(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        _announce_write_target(root, "assumption unlink-run")
        rec = unlink_run_assumption(root, args.id, args.run_id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_unlink_run"))
    return emit(success(rec, meta={"surface": "terra.assumption.unlink_run"}))


def cmd_assumption_graduate(args: argparse.Namespace) -> int:
    from .knowns import graduate_unknown

    try:
        root = require_project_root()
        _announce_write_target(root, "assumption graduate")
        rec = graduate_unknown(root, args.id, known_id=args.as_id)
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_graduate"))
    return emit(success(rec, meta={"surface": "terra.assumption.graduate"}))


def cmd_assumption_delete(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        from .assumptions import load_assumption

        load_assumption(root, args.id)
        _announce_write_target(root, "assumption delete")
        path = delete_unknown(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="assumption_delete"))
    return emit(
        success(
            {"id": args.id, "deleted": True},
            meta={"surface": "terra.assumption.delete", "path": str(path)},
        )
    )


def cmd_calculation_create(args: argparse.Namespace) -> int:
    try:
        root, _ = ensure_project_root()
        inputs = parse_bindings(args.input or [])
        outputs = parse_output_specs(args.output or [])
        if args.profile == "expression" and outputs:
            raise ValueError("--output is for profile=model; use --type/--quantity")
        if args.profile == "model" and (args.type or args.quantity or args.decimals is not None):
            raise ValueError(
                "profile=model uses repeatable --output; --type/--quantity/--decimals are expression options"
            )
        _announce_write_target(root, "calculation create")
        path = create_calculation(
            root,
            args.id,
            inputs=inputs,
            output_type=args.type,
            quantity=args.quantity,
            unit=args.unit or "",
            purpose=args.purpose or "",
            force=args.force,
            decimal_places=args.decimals,
            profile=args.profile,
            outputs=outputs,
        )
        rec = load_calculation(root, args.id)
    except (ValueError, FileExistsError, OSError) as exc:
        return emit(error(str(exc), code="calculation_create"))
    return emit(success(rec, meta={"surface": "terra.calculation.create", "path": str(path)}))


def cmd_calculation_validate(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        if args.id:
            result = validate_calculation(root, args.id)
        else:
            rows = list_calculations(root)
            result = {"ok": all(r["ok"] for r in rows), "calculations": rows}
    except (ValueError, FileNotFoundError, OSError) as exc:
        return emit(error(str(exc), code="calculation_validate"))
    emit(success(result, meta={"surface": "terra.calculation.validate"}))
    return 0 if result["ok"] else 1


def cmd_calculation_run(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        _announce_write_target(root, "calculation run")
        result = run_calculation(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return emit(error(str(exc), code="calculation_run"))
    return emit(
        success(
            result,
            meta={
                "surface": "terra.calculation.run",
                "note": (
                    "calculation stamped an evidence run; link it with "
                    "terra unknown link-calculation <unknown> " + args.id
                ),
            },
        )
    )


def cmd_calculation_get(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        result = get_calculation(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return emit(error(str(exc), code="calculation_get"))
    if args.raw:
        print(json.dumps(result["value"]))
        return 0
    return emit(success(result, meta={"surface": "terra.calculation.get"}))


def cmd_calculation_show(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = load_calculation(root, args.id)
        validation = validate_calculation(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return emit(error(str(exc), code="calculation_show"))
    return emit(success({"record": rec, "validation": validation}, meta={"surface": "terra.calculation.show"}))


def cmd_calculation_list(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rows = list_calculations(root)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return emit(error(str(exc), code="calculation_list"))
    return emit(success(rows, meta={"surface": "terra.calculation.list"}))


def cmd_calculation_delete(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        _announce_write_target(root, "calculation delete")
        path = delete_calculation(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return emit(error(str(exc), code="calculation_delete"))
    return emit(success({"id": args.id, "deleted": True}, meta={"surface": "terra.calculation.delete", "path": str(path)}))


def cmd_unknown_show(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        desc = describe_unknown(root, args.id)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        # A formula node whose var binds a SUPERSEDED/stale known used to
        # raise straight out of here as an uncaught traceback. The read
        # refusal is correct — a retired belief must not be consumed — but
        # escaping as a traceback makes the node UNREADABLE, and an
        # unreadable node is invisible to every CLI-based audit. So the very
        # act of retiring a belief could silently hide the nodes that still
        # depend on it, which is the opposite of what supersession is for.
        # Report it as a describable defect instead.
        print(
            f"error: unknown {args.id!r} cannot be described — one of its "
            f"formula vars binds a belief that refuses to be read:\n"
            f"  {e}\n"
            f"  This node is NOT broken data; its binding is stale. Re-point "
            f"the var at the live successor, or read the historical value "
            f"with --allow-superseded on the bound known.",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(json.dumps(desc, indent=2, sort_keys=True, default=str))
        return 0
    rec = desc["record"]
    status = rec.get("status", "?")
    active = status in ("open", "probing", "blocked")
    block = "  blocks_build" if active and rec.get("blocks_build") else ""
    print(f"unknown {rec.get('id')}  status={status}{block}")
    print(f"claim: {rec.get('claim')}")
    print(f"evidence_needed: {rec.get('evidence_needed')}")
    pids = rec.get("probe_ids") or []
    if rec.get("probe_id") and rec.get("probe_id") not in pids:
        pids = [rec["probe_id"], *pids]
    print(f"probes: {', '.join(pids) if pids else '(none)'}")
    if rec.get("type") in ("number", "boolean", "formula"):
        st = rec.get("stats") or {}
        if rec.get("type") == "formula":
            print(
                f"type: formula  expr={rec.get('expression')!r}  "
                f"holds={st.get('holds')}  holds_rate={st.get('holds_rate')}  "
                f"n={st.get('n')}  conf_derived={rec.get('confidence_derived')}"
            )
            print(f"vars: {rec.get('vars')}")
        elif rec.get("type") == "boolean":
            print(
                f"type: boolean  quantity={rec.get('quantity')}  "
                f"n={st.get('n')}  rate={st.get('rate')}  "
                f"k_true={st.get('k_true')}  k_false={st.get('k_false')}  "
                f"confidence_derived={rec.get('confidence_derived')}"
            )
        else:
            print(
                f"type: number  quantity={rec.get('quantity')}  "
                f"n={st.get('n')}  mean={st.get('mean')}  std={st.get('std')}  "
                f"confidence_derived={rec.get('confidence_derived')}"
            )
    if rec.get("resolved_by"):
        print(f"resolved_by: {rec.get('resolved_by')}")
    runs = desc.get("linked_runs") or []
    if not runs:
        print("runs: (none — terra unknown link-run <id> <run_id>)")
    else:
        print("runs:")
        for r in runs:
            flag = "ok" if r.get("exists") else "MISSING"
            prim = " primary" if r.get("primary") else ""
            print(
                f"  [{flag}] {r.get('id')}{prim}  "
                f"probe={r.get('probe_id')}  status={r.get('status')}  "
                f"{r.get('captured_at') or ''}"
            )
    return 0


def _announce_write_target(root: Path, what: str) -> None:
    """Print (to stderr) which map a state-changing command is writing to.

    The map is invisible until you're about to hit the wrong copy (a delete
    landing on a session map you forgot was active). This makes the write
    target explicit on every mutation. Louder when the target is NOT global,
    since a silently-active session/experiment map is the actual footgun.
    stderr so it never pollutes the JSON envelope on stdout.
    """
    from .paths import GLOBAL_MAP_ID, resolve_active_map

    mid, source = resolve_active_map(root)
    via = f" (via {source})" if source in ("env", "cli") else ""
    if mid == GLOBAL_MAP_ID:
        print(f"  → {what} writes to map '{mid}'{via}", file=sys.stderr)
    else:
        print(
            f"  ⚠ {what} writes to map '{mid}'{via} - NOT global. If that is "
            "not where you meant to write, pin the map: "
            "terra --map <id> … or export TERRA_MAP=<id>",
            file=sys.stderr,
        )


def _print_known_summary(prefix: str, rec: dict, path: Path | None = None) -> None:
    st = rec.get("stats") or {}
    print(
        f"{prefix} known {rec.get('id')}  type={rec.get('type')}  "
        f"status={rec.get('status')}"
    )
    print(
        f"  confidence={rec.get('confidence')}  "
        f"derived={rec.get('confidence_derived')}"
    )
    if rec.get("type") == "formula":
        print(f"  expression: {rec.get('expression')}")
        print(f"  vars: {rec.get('vars')}")
        print(
            f"  holds={st.get('holds')}  holds_rate={st.get('holds_rate')}  "
            f"n={st.get('n')}"
        )
    elif rec.get("type") == "relation":
        print(
            f"  F({rec.get('x_quantity')}) → {rec.get('quantity')}  "
            f"sweeps={st.get('n')}  stations={st.get('station_count')}  "
            f"x_range={st.get('x_range')}"
        )
    elif rec.get("type") == "boolean":
        print(
            f"  n={st.get('n')}  rate={st.get('rate')}  "
            f"k_true={st.get('k_true')}  k_false={st.get('k_false')}"
        )
    else:
        print(f"  n={st.get('n')}  mean={st.get('mean')}  std={st.get('std')}")
    if path is not None:
        print(f"  {path}")


_KNOWN_BIRTH_MSG = (
    "`terra known create` is retired — knowns are born by graduating an "
    "evidence-bearing unknown.\n"
    "  terra unknown create <slug> --type number|boolean|formula "
    "--quantity <q> --claim \"…?\" --evidence \"…\"\n"
    "  terra unknown link-probe <slug> <probe_id>\n"
    "  terra probe run <probe_id> --to '…' --json\n"
    "  terra unknown link-run <slug> <run_id>\n"
    "  terra unknown graduate <slug> [--as <known_slug>]"
)


def cmd_known_create(args: argparse.Namespace) -> int:
    print(f"error: {_KNOWN_BIRTH_MSG}", file=sys.stderr)
    return 2


def cmd_known_set(args: argparse.Namespace) -> int:
    """Upsert known — agent muscle-memory verb. Never silent no-op."""
    # Merge --note alias into notes
    notes = args.notes
    if getattr(args, "note", None) is not None:
        notes = args.note if notes is None else notes

    value = getattr(args, "value", None)
    try:
        root, created = ensure_project_root()
        mtype = getattr(args, "type", None)
        rec, action = set_known(
            root,
            args.id,
            claim=args.claim,
            notes=notes,
            map_type=mtype,
            quantity=args.quantity,
            unit=args.unit,
            confidence=args.confidence,
            status=args.status,
            run_id=args.from_run,
            expression=getattr(args, "expression", None),
            vars=getattr(args, "vars", None),
            value=value,
        )
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if created:
        print(f"initialized {root / '.terra' / 'map'}")
    from .paths import known_path as _known_path

    _print_known_summary(action, rec, _known_path(root, args.id))
    _announce_write_target(root, "known set")
    return 0


def cmd_known_list(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rows = list_knowns(root)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("(no knowns)")
        return 0
    for r in rows:
        flag = "ok" if r["ok"] else "BAD"
        rec = r.get("record") or {}
        st = rec.get("stats") or {}
        if rec.get("type") == "formula":
            stat_s = f"holds={st.get('holds')}  rate={st.get('holds_rate')}  n={st.get('n')}"
        elif rec.get("type") == "boolean":
            stat_s = f"n={st.get('n')}  rate={st.get('rate')}"
        else:
            stat_s = f"n={st.get('n')}  mean={st.get('mean')}"
        print(
            f"[{flag}] {r['id']}  {rec.get('type')}  {rec.get('status')}  "
            f"conf={rec.get('confidence')}/{rec.get('confidence_derived')}  "
            f"{stat_s}  {rec.get('claim', '')[:50]}"
        )
    return 0


def cmd_known_show(args: argparse.Namespace) -> int:
    from .knowns import find_known_map, shadowed_ancestor
    from .paths import get_active_map_id, scoped_map

    try:
        root = require_project_root()
        owner = find_known_map(root, args.id)
        if owner is None:
            raise FileNotFoundError(
                f"known not found: {args.id} (searched the map parent chain)"
            )
        with scoped_map(owner):
            desc = describe_known(root, args.id)
        active = get_active_map_id(root)
        shadow = shadowed_ancestor(root, args.id) if owner == active else None
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rec = desc["record"]
    if args.json:
        print(json.dumps({**rec, "map": owner}, indent=2, sort_keys=True, default=str))
        return 0
    st = rec.get("stats") or {}
    print(f"known {rec.get('id')}  type={rec.get('type')}  status={rec.get('status')}")
    if owner != active:
        print(f"map: {owner}  (inherited — active map is {active})")
    if shadow:
        print(
            f"NOTE: shadows {shadow}'s known {rec.get('id')!r} for this map "
            f"subtree — inherited value hidden here"
        )
    print(f"claim: {rec.get('claim')}")
    print(
        f"confidence: claimed={rec.get('confidence')}  "
        f"derived={rec.get('confidence_derived')}"
    )
    if rec.get("type") == "formula":
        print(f"expression: {rec.get('expression')}")
        print(f"vars: {rec.get('vars')}")
        print(
            f"stats: holds={st.get('holds')}  holds_rate={st.get('holds_rate')}  "
            f"n={st.get('n')}  k_hold={st.get('k_hold')}  k_fail={st.get('k_fail')}  "
            f"bindings={st.get('bindings')}"
        )
        if st.get("error"):
            print(f"error: {st.get('error')}")
    elif rec.get("type") == "relation":
        print(
            f"F({rec.get('x_quantity')}{rec.get('x_unit') and ' ' + rec.get('x_unit') or ''}) "
            f"→ {rec.get('quantity')}{rec.get('unit') and ' ' + rec.get('unit') or ''}"
        )
        print(
            f"stats: sweeps={st.get('n')}  points={st.get('points')}  "
            f"stations={st.get('station_count')}  x_range={st.get('x_range')}"
        )
        for stn in st.get("stations") or []:
            print(
                f"  x={stn.get('x')}  n={stn.get('n')}  "
                f"mean={stn.get('mean')}  std={stn.get('std')}"
            )
    else:
        print(f"quantity: {rec.get('quantity')}  unit={rec.get('unit') or '—'}")
        if rec.get("type") == "boolean":
            print(
                f"stats: n={st.get('n')}  rate={st.get('rate')}  "
                f"k_true={st.get('k_true')}  k_false={st.get('k_false')}"
            )
        else:
            print(
                f"stats: n={st.get('n')}  mean={st.get('mean')}  std={st.get('std')}  "
                f"min={st.get('min')}  max={st.get('max')}"
            )
    by_probe = st.get("by_probe") or {}
    if by_probe:
        for pid, g in sorted(by_probe.items()):
            if rec.get("type") == "boolean":
                print(f"  method {pid}: n={g.get('n')} rate={g.get('rate')}")
            else:
                print(f"  method {pid}: n={g.get('n')} mean={g.get('mean')} std={g.get('std')}")
    corr = st.get("corroboration") or {}
    if corr:
        line = (
            f"corroboration: methods={corr.get('methods')} "
            f"agree={corr.get('agree')}"
        )
        if corr.get("spread") is not None:
            line += f"  spread={corr.get('spread')}"
        if corr.get("tolerance") is not None:
            line += f"  tolerance={corr.get('tolerance')}"
        print(line)
        if corr.get("agree") is False:
            if corr.get("accepted") is True:
                print(
                    f"spread ACCEPTED as uncertainty: {corr.get('accepted_reason')}"
                    f"  band={corr.get('band')}"
                )
            else:
                print("METHODS DISAGREE — one instrument is wrong; void bad evidence or fix the probe")
    print(f"run_ids: {rec.get('run_ids') or []}")
    deps = rec.get("deps") or {}
    if deps.get("knowns") or deps.get("files"):
        dep_bits = [f"known:{d.get('id')}" for d in deps.get("knowns") or []]
        dep_bits += [f"file:{d.get('path')}" for d in deps.get("files") or []]
        print(f"deps: {dep_bits}")
    from .readings import list_consumers
    from .staleness import compute_staleness

    with scoped_map(owner):
        info = compute_staleness(root).get(args.id) or {}
        consumers = list_consumers(root, args.id)
    if info.get("stale"):
        print(f"STALE: {'; '.join(info.get('reasons') or [])}")
    if consumers:
        print(
            "consumers: "
            + ", ".join(str(c.get("consumer")) for c in consumers)
        )
    return 0


def cmd_known_get(args: argparse.Namespace) -> int:
    """Read path — the single place a number lives. Loud on debt."""
    from .readings import read_known

    try:
        root = require_project_root()
        reading = read_known(
            root,
            args.id,
            min_conf=args.min_conf,
            allow_stale=bool(args.allow_stale),
            allow_disagree=bool(getattr(args, "allow_disagree", False)),
            allow_cohort_mismatch=bool(
                getattr(args, "allow_cohort_mismatch", False)
            ),
            allow_superseded=bool(getattr(args, "allow_superseded", False)),
            consumer=args.consumer,
            at=getattr(args, "at", None),
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    # Scream when the value is not a clean current local read: retired
    # (superseded/refuted), inherited (map-chain read-through), or stale
    # (allowed with --allow-stale) — all were tucked in quiet fields, so
    # surface them rather than let a tombstoned/inherited/aged number be
    # consumed as if it were fresh, local, and current.
    if reading.get("superseded"):
        info = reading.get("superseded_info") or {}
        print(
            f"  NOTE: known {args.id} is RETIRED "
            f"({info.get('reason') or 'no reason'}) - returning a historical "
            "value only because --allow-superseded was given; do NOT treat "
            "it as current.",
            file=sys.stderr,
        )
    if reading.get("inherited"):
        print(
            f"  NOTE: known {args.id} is INHERITED from map "
            f"'{reading.get('map')}' (not the active map) via the parent "
            "chain - you are reading an ancestor's belief, not a local one.",
            file=sys.stderr,
        )
    if reading.get("stale"):
        reasons = "; ".join(reading.get("stale_reasons") or [])
        print(
            f"  NOTE: known {args.id} is STALE ({reasons}) - returned only "
            "because --allow-stale was given; a dependency moved. Re-derive "
            "or reaffirm before trusting it.",
            file=sys.stderr,
        )
    if args.raw:
        print(reading["value"])
        return 0
    print(json.dumps(reading, indent=2, sort_keys=True, default=str))
    return 0


def cmd_known_supersede(args: argparse.Namespace) -> int:
    from .agent_io import emit, error, success
    from .knowns import supersede_known

    try:
        root = require_project_root()
        rec = supersede_known(
            root,
            args.id,
            reason=args.reason,
            superseded_by=getattr(args, "by", None),
            refuted=bool(getattr(args, "refuted", False)),
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="known_supersede"))
    meta = {
        "surface": "terra.known.supersede",
        "note": (
            f"known {args.id} is now {rec.get('status')} - read path refuses "
            "it as current (--allow-superseded to read the historical value). "
            "History preserved; consumers/design params break loudly on next "
            "read, which is the point."
        ),
    }
    return emit(success(rec, meta=meta))


def cmd_cohort_create(args: argparse.Namespace) -> int:
    from .cohorts import create_cohort

    try:
        root = require_project_root()
        members = [m.strip() for m in (args.members or "").split(",") if m.strip()]
        _announce_write_target(root, "cohort create")
        rec = create_cohort(root, args.id, members=members, title=args.title or "")
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec, indent=2, sort_keys=True))
        return 0
    print(f"cohort {rec['id']}  members={', '.join(rec['members'])}")
    print(
        f"  members must share identical live evidence runs — refresh with:\n"
        f"    terra cohort link-run {rec['id']} <run_id>"
    )
    return 0


def cmd_cohort_add(args: argparse.Namespace) -> int:
    from .cohorts import add_member

    try:
        root = require_project_root()
        _announce_write_target(root, "cohort add")
        rec = add_member(root, args.id, args.known_id)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"cohort {rec['id']}  members={', '.join(rec['members'])}")
    return 0


def cmd_cohort_set(args: argparse.Namespace) -> int:
    from .cohorts import set_cohort

    try:
        root = require_project_root()
        members = None
        if args.members is not None:
            members = [m.strip() for m in args.members.split(",") if m.strip()]
        _announce_write_target(root, "cohort set")
        rec = set_cohort(root, args.id, members=members, title=args.title)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rec, indent=2, sort_keys=True))
        return 0
    print(f"cohort {rec['id']}  members={', '.join(rec['members'])}")
    return 0


def cmd_cohort_delete(args: argparse.Namespace) -> int:
    from .cohorts import delete_cohort

    try:
        root = require_project_root()
        _announce_write_target(root, "cohort delete")
        path = delete_cohort(root, args.id)
    except (FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"id": args.id, "deleted": True, "path": str(path)}, indent=2, sort_keys=True))
        return 0
    print(f"deleted cohort {args.id}  ({path})")
    print("  member knowns and their evidence were preserved")
    return 0


def cmd_cohort_list(args: argparse.Namespace) -> int:
    from .cohorts import check_cohort, list_cohorts

    try:
        root = require_project_root()
        rows = [
            {**c, "check": check_cohort(root, c)} for c in list_cohorts(root)
        ]
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    if not rows:
        print("cohorts: (none — terra cohort create <id> --members k1,k2)")
        return 0
    for c in rows:
        chk = c["check"]
        mark = "ok" if chk["consistent"] else "INCONSISTENT"
        print(f"[{mark}] {c['id']}  members={', '.join(c.get('members') or [])}")
        for pr in chk.get("problems") or []:
            print(f"    ! {pr}")
    return 0


def cmd_cohort_check(args: argparse.Namespace) -> int:
    from .cohorts import check_cohort

    try:
        root = require_project_root()
        chk = check_cohort(root, args.id)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(chk, indent=2, sort_keys=True))
        return 0 if chk["consistent"] else 1
    mark = "ok" if chk["consistent"] else "INCONSISTENT"
    print(f"[{mark}] cohort {chk['id']}  common_runs={chk['common_runs']}")
    for pr in chk.get("problems") or []:
        print(f"  ! {pr}")
    if not chk["consistent"]:
        print(
            f"  fix with ONE re-solve, not per-known refreshes:\n"
            f"    terra probe run <solver> --json && "
            f"terra cohort link-run {chk['id']} <run_id>"
        )
    return 0 if chk["consistent"] else 1


def cmd_cohort_link_run(args: argparse.Namespace) -> int:
    from .cohorts import link_run_cohort

    try:
        root = require_project_root()
        _announce_write_target(root, "cohort link-run")
        out = link_run_cohort(root, args.id, args.run_id)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    chk = out["check"]
    mark = "ok" if chk["consistent"] else "INCONSISTENT"
    print(
        f"cohort {out['id']}  run {out['run_id']} linked to: "
        f"{', '.join(out['linked'])}  [{mark}]"
    )
    for pr in chk.get("problems") or []:
        print(f"  ! {pr}")
    return 0


def cmd_known_depend(args: argparse.Namespace) -> int:
    from .knowns import add_dependency

    try:
        root = require_project_root()
        rec = add_dependency(root, args.id, args.on or [])
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    deps = rec.get("deps") or {}
    print(
        f"known {rec['id']}  deps: "
        f"knowns={[d.get('id') for d in deps.get('knowns') or []]}  "
        f"files={[d.get('path') for d in deps.get('files') or []]}"
    )
    return 0


def cmd_known_reaffirm(args: argparse.Namespace) -> int:
    from .knowns import reaffirm_known

    try:
        root = require_project_root()
        rec = reaffirm_known(root, args.id, reason=args.reason)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"reaffirmed known {rec['id']} "
        f"(deps re-stamped; trail in record.reaffirmed)"
    )
    return 0


def cmd_known_accept_spread(args: argparse.Namespace) -> int:
    from .knowns import accept_spread

    try:
        root = require_project_root()
        rec = accept_spread(root, args.id, reason=args.reason)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    corr = (rec.get("stats") or {}).get("corroboration") or {}
    print(
        f"known {rec['id']}: spread accepted as uncertainty  "
        f"spread={corr.get('spread')}  band={corr.get('band')}\n"
        f"  confidence caps at med; new runs that widen the spread re-trip "
        f"the alarm; agreement clears it"
    )
    return 0


def cmd_known_tolerance(args: argparse.Namespace) -> int:
    from .knowns import set_tolerance

    try:
        root = require_project_root()
        rec = set_tolerance(root, args.id, within=args.within)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    corr = (rec.get("stats") or {}).get("corroboration") or {}
    print(
        f"known {rec['id']}  tolerance={rec.get('tolerance')}  "
        f"methods={corr.get('methods')}  agree={corr.get('agree')}"
    )
    return 0


def cmd_known_graph(args: argparse.Namespace) -> int:
    """Whole-map dependency graph — which upstream moved, in one look."""
    from .agent_io import emit, error, success
    from .known_graph import build_graph, render_graph_text

    try:
        root = require_project_root()
        graph = build_graph(root)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="known_graph"))
    if getattr(args, "human", False):
        print(render_graph_text(graph))
        return 0
    return emit(success(graph, meta={"surface": "terra.known.graph"}))


def cmd_known_tree(args: argparse.Namespace) -> int:
    """One known: upstream chain + downstream fan + consumers."""
    from .agent_io import emit, error, success
    from .known_graph import build_graph, build_tree, render_tree_text

    try:
        root = require_project_root()
        tree = build_tree(build_graph(root), args.id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="known_tree"))
    if getattr(args, "human", False):
        print(render_tree_text(tree))
        return 0
    return emit(success(tree, meta={"surface": "terra.known.tree"}))


def cmd_design_add(args: argparse.Namespace) -> int:
    from .design import add_param

    try:
        root = require_project_root()
        entry = add_param(
            root, args.known_id, name=args.as_name, force=args.force
        )
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"design param {entry['name']}  known={entry['known_id']}  "
        f"value={entry['value_at_admission']}{entry.get('unit') or ''}  "
        f"conf={entry['confidence_at_admission']}"
    )
    return 0


def cmd_design_refresh(args: argparse.Namespace) -> int:
    from .design import refresh_param

    try:
        root = require_project_root()
        entry = refresh_param(root, args.name)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"design param {entry['name']} re-pinned  "
        f"value={entry['value_at_admission']}  "
        f"conf={entry['confidence_at_admission']}"
    )
    from .design import load_design

    stale_artifacts = [
        a.get("path")
        for a in load_design(root).get("artifacts") or []
        if entry["name"] in (a.get("uses") or [])
    ]
    if stale_artifacts:
        print(
            f"  NOTE: artifacts built from {entry['name']} are now stale: "
            f"{stale_artifacts} — REGENERATE each, then\n"
            f"    terra design attach <path> --uses …"
        )
    return 0


def cmd_design_remove(args: argparse.Namespace) -> int:
    from .design import remove_param

    try:
        root = require_project_root()
        remove_param(root, args.name)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"design param {args.name} removed")
    return 0


def cmd_design_get(args: argparse.Namespace) -> int:
    from .design import get_param

    try:
        root = require_project_root()
        reading = get_param(root, args.name, consumer=args.consumer)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.raw:
        print(reading["value"])
        return 0
    print(json.dumps(reading, indent=2, sort_keys=True, default=str))
    return 0


def cmd_design_attach(args: argparse.Namespace) -> int:
    from .design import attach_artifact

    try:
        root = require_project_root()
        uses = [u.strip() for u in (args.uses or "").split(",") if u.strip()]
        entry = attach_artifact(root, args.path, uses=uses)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"attached {entry['path']}  uses={entry['uses']}  "
        f"sha={str(entry.get('sha256'))[:12]}…"
    )
    return 0


def cmd_design_detach(args: argparse.Namespace) -> int:
    from .design import detach_artifact

    try:
        root = require_project_root()
        detach_artifact(root, args.path)
    except (FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"detached {args.path}")
    return 0


def cmd_design_check(args: argparse.Namespace) -> int:
    """Design health: exit 0 iff every param + artifact is green."""
    from .agent_io import emit, error, success
    from .design import check_design

    try:
        root = require_project_root()
        result = check_design(root)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="design_check"))
    if getattr(args, "human", False):
        c = result["counts"]
        flag = "OK" if result["ok"] else "RED"
        print(
            f"DESIGN {flag}  params={c['params']} ({c['params_red']} red)  "
            f"artifacts={c['artifacts']} ({c['artifacts_red']} red)"
        )
        for p in result["params"]:
            mark = "✓" if p["ok"] else "✗"
            print(
                f"  {mark} {p['name']} = {p.get('current_value')}"
                f"{p.get('unit') or ''}  [{p.get('current_confidence')}]"
            )
            for r in p.get("reasons") or []:
                print(f"      ! {r}")
        for a in result["artifacts"]:
            mark = "✓" if a["ok"] else "✗"
            print(f"  {mark} {a['path']}  uses={a.get('uses')}")
            for r in a.get("reasons") or []:
                print(f"      ! {r}")
        for n in result.get("notices") or []:
            print(f"  note [{n['kind']}] {n['id']}: {n['why']}")
        return 0 if result["ok"] else 1
    emit(success(result, meta={"surface": "terra.design.check"}))
    return 0 if result["ok"] else 1


def cmd_gate(args: argparse.Namespace) -> int:
    """Mechanical gate: exit 0 clean, exit 1 with violations. CI-able."""
    from .agent_io import emit, error, success
    from .gate import check_gate

    try:
        root = require_project_root()
        verdict = check_gate(root, map_id=args.map_scope)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="gate"))
    if getattr(args, "human", False):
        notices = verdict.get("notices") or []
        if verdict["ok"]:
            print(f"GATE PASS  maps={verdict['maps_checked']}")
        else:
            print(f"GATE FAIL  maps={verdict['maps_checked']}")
            for v in verdict["violations"]:
                print(f"  [{v['kind']}] {v['map_id']}/{v['id']}: {v['why']}")
        for n in notices:
            print(f"  note [{n['kind']}] {n['map_id']}/{n['id']}: {n['why']}")
        return 0 if verdict["ok"] else 1
    emit(success(verdict, meta={"surface": "terra.gate"}))
    return 0 if verdict["ok"] else 1


def cmd_sitrep(args: argparse.Namespace) -> int:
    """One-call orientation digest. ALWAYS exits 0 on success.

    Deliberately does NOT adopt `gate`'s exit-1-on-violation semantics: a
    nonzero exit silently aborts `&&` chains, so a batched shell call would
    lose every command after a failing gate and look clean. The verdict is
    data (`data.gate.ok`), not an exit code.
    """
    from .agent_io import emit, error, success
    from .sitrep import collect_sitrep, format_sitrep_text

    try:
        root = require_project_root()
        rep = collect_sitrep(
            root,
            full=bool(getattr(args, "full", False)),
            map_id=getattr(args, "map_scope", None),
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="sitrep"))
    if getattr(args, "human", False):
        print(format_sitrep_text(rep))
        return 0
    emit(success(rep, meta={"surface": "terra.sitrep"}))
    return 0


def cmd_known_link_run(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = link_run_known(
            root, args.id, args.run_id, primary=bool(args.primary)
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    st = rec.get("stats") or {}
    if rec.get("type") == "formula":
        print(
            f"known {rec['id']}  holds={st.get('holds')}  "
            f"holds_rate={st.get('holds_rate')}  n={st.get('n')}  "
            f"conf={rec.get('confidence')}/{rec.get('confidence_derived')}"
        )
    elif rec.get("type") == "boolean":
        print(
            f"known {rec['id']}  n={st.get('n')}  rate={st.get('rate')}  "
            f"k_true={st.get('k_true')}  conf={rec.get('confidence')}/"
            f"{rec.get('confidence_derived')}"
        )
    else:
        print(
            f"known {rec['id']}  n={st.get('n')}  mean={st.get('mean')}  "
            f"std={st.get('std')}  conf={rec.get('confidence')}/"
            f"{rec.get('confidence_derived')}"
        )
    _note_cohort_and_convergence(root, rec, args.run_id)
    _announce_write_target(root, "known link-run")
    return 0


def _note_cohort_and_convergence(root, rec, run_id: str) -> None:
    """Agent NOTEs at the two coupled-solve footguns (best-effort)."""
    from .cohorts import find_cohort_for
    from .paths import run_dir
    from .probe_run import RUN_META_NAME

    try:
        cohort = find_cohort_for(root, rec["id"])
        if cohort is not None:
            print(
                f"  NOTE: {rec['id']} is a member of cohort "
                f"{cohort.get('id')!r} — linking runs per-known can leave "
                f"siblings citing a different solve. Prefer:\n"
                f"    terra cohort link-run {cohort.get('id')} {run_id}"
            )
        meta_path = run_dir(root, run_id) / RUN_META_NAME
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        conv = meta.get("convergence")
        if isinstance(conv, dict) and conv.get("converged"):
            same_start = 0
            for rid in rec.get("run_ids") or []:
                if rid == run_id:
                    continue
                other_path = run_dir(root, rid) / RUN_META_NAME
                if not other_path.is_file():
                    continue
                other = json.loads(other_path.read_text(encoding="utf-8"))
                if (
                    other.get("probe_id") == meta.get("probe_id")
                    and isinstance(other.get("convergence"), dict)
                    and other.get("to") == meta.get("to")
                ):
                    same_start += 1
            if same_start:
                print(
                    f"  NOTE: {same_start} earlier linked solve(s) used the "
                    f"same probe AND the same start (to) — identical "
                    f"re-solves don't add independent evidence. Vary the "
                    f"initial guess/damping (different --to) for real n."
                )
    except (json.JSONDecodeError, OSError, FileNotFoundError, ValueError):
        pass


def cmd_known_promote(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = promote_known(
            root,
            args.id,
            args.confidence,
            status=args.status,
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"known {rec['id']}  confidence={rec.get('confidence')}  "
        f"status={rec.get('status')}  derived={rec.get('confidence_derived')}"
    )
    return 0


def cmd_known_adopt(args: argparse.Namespace) -> int:
    from .knowns import adopt_known
    from .paths import map_parent

    try:
        root = require_project_root()
        rec = adopt_known(root, args.id, from_map=args.from_map)
        to_map = map_parent(root, args.from_map)
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"adopted known {rec['id']}  {args.from_map} -> {to_map}  "
        f"(n={rec.get('stats', {}).get('n')}, "
        f"confidence={rec.get('confidence')})"
    )
    print(f"  runs copied with it; provenance stamped (adopted_from)")
    if to_map != "global":
        print(
            f"  NOTE: {to_map} is not the top — climb further with: "
            f"terra known adopt {rec['id']} --from {to_map}"
        )
    return 0


def cmd_cohort_adopt(args: argparse.Namespace) -> int:
    from .cohorts import adopt_cohort
    from .paths import map_parent

    try:
        root = require_project_root()
        rec = adopt_cohort(root, args.id, from_map=args.from_map)
        to_map = map_parent(root, args.from_map)
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"adopted cohort {rec['id']}  {args.from_map} -> {to_map}  "
        f"members={', '.join(rec.get('members') or [])}"
    )
    return 0


def cmd_known_status(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = set_known_status(root, args.id, args.status, notes=args.notes)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"known {rec['id']}  status={rec['status']}")
    return 0


def cmd_known_validate(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.id:
        result = validate_known_file(root, args.id)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0 if result["ok"] else 1
        status = "ok" if result["ok"] else "FAIL"
        print(f"[{status}] {result.get('id')}")
        for b in result.get("blocks") or []:
            print(f"  block: {b}")
        print("PASS" if result["ok"] else "FAIL")
        return 0 if result["ok"] else 1
    rows = list_knowns(root)
    ok = all(r["ok"] for r in rows) if rows else True
    for r in rows:
        status = "ok" if r["ok"] else "FAIL"
        print(f"[{status}] {r['id']}")
        for b in r.get("blocks") or []:
            print(f"  block: {b}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_unknown_status(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = set_status(
            root,
            args.id,
            args.status,
            resolved_by=args.resolved_by,
            notes=args.notes,
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="unknown_status"))
    note = None
    if (
        args.status == "resolved"
        and rec.get("type")
        and (rec.get("run_ids") or [])
        and not str(rec.get("resolved_by") or "").startswith("known:")
    ):
        note = (
            f"this unknown is typed with linked runs — a prose "
            f"resolve keeps NO durable anchor. If the answer should live "
            f"on:\n    terra unknown graduate {rec['id']}"
        )
    meta = {"surface": "terra.unknown.status"}
    if note:
        meta["note"] = note
    return emit(success(rec, meta=meta))


def cmd_unknown_graduate(args: argparse.Namespace) -> int:
    """Graduate an evidence-bearing unknown into a known (only birth path)."""
    from .knowns import graduate_unknown

    try:
        root = require_project_root()
        with_ids = [
            u.strip()
            for u in (getattr(args, "with_ids", None) or "").split(",")
            if u.strip()
        ]
        rec = graduate_unknown(
            root,
            args.id,
            known_id=getattr(args, "as_id", None),
            with_ids=with_ids or None,
            into=getattr(args, "into", None),
            notes=args.notes,
            force=args.force,
            cohort_id=getattr(args, "cohort_id", None),
        )
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="unknown_graduate"))
    _announce_write_target(root, "unknown graduate")
    resolved = [args.id] + (with_ids or [])
    from .knowns import shadowed_ancestor

    try:
        shadow = shadowed_ancestor(root, rec["id"])
    except ValueError:
        shadow = None
    note = None
    if shadow:
        note = (
            f"this known SHADOWS {shadow}'s {rec['id']!r} — reads on "
            f"this map subtree now see the local value, not the inherited "
            f"one. Rename if that's unintended, or adopt up when it proves "
            f"out: terra known adopt {rec['id']} --from "
            f"{get_active_map_id(root)}"
        )
    data = {
        **rec,
        "resolved_unknown_ids": resolved,
        "next_actions": [
            {
                "op": "known.link_run",
                "argv": [
                    "terra", "known", "link-run", rec["id"], "<run_id>",
                ],
                "why": "add an independent reading before promotion",
            },
            {
                "op": "known.promote",
                "argv": ["terra", "known", "promote", rec["id"], "med"],
                "why": "promote after the evidence ladder is satisfied",
            },
        ],
    }
    meta = {
        "surface": "terra.unknown.graduate",
        "action": "merged" if getattr(args, "into", None) else "graduated",
    }
    if note:
        meta["note"] = note
    return emit(success(data, meta=meta))


def cmd_unknown_link_probe(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = link_probe(root, args.id, args.probe_id)
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="unknown_link_probe"))
    return emit(
        success(rec, meta={"surface": "terra.unknown.link_probe"})
    )


def cmd_unknown_link_run(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = link_run(
            root, args.id, args.run_id, primary=bool(args.primary),
            allow_no_sample=bool(getattr(args, "allow_no_sample", False)),
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        return emit(error(str(e), code="unknown_link_run"))
    note = None
    if rec.get("status") in ("resolved", "wont_care"):
        resolved_by = str(rec.get("resolved_by") or "")
        hint = (
            f"    terra known link-run {resolved_by.removeprefix('known:')} "
            f"{args.run_id}"
            if resolved_by.startswith("known:")
            else "    (runs on a closed unknown feed nothing downstream)"
        )
        note = (
            f"this unknown is {rec['status']}"
            f"{' by ' + resolved_by if resolved_by else ''} — linked runs "
            f"here feed NO known's stats. Did you mean:\n{hint}"
        )
    meta = {"surface": "terra.unknown.link_run"}
    if note:
        meta["note"] = note
    return emit(success(rec, meta=meta))


def cmd_unknown_link_calculation(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        from .unknowns import link_calculation

        rec = link_calculation(
            root,
            args.id,
            args.calculation_id,
            output=args.output,
            primary=bool(args.primary),
        )
    except (ValueError, FileNotFoundError, OSError) as exc:
        return emit(error(str(exc), code="unknown_link_calculation"))
    return emit(
        success(
            rec,
            meta={
                "surface": "terra.unknown.link_calculation",
                "note": (
                    f"linked calculation evidence; graduate when the claim is "
                    f"answered: terra unknown graduate {args.id}"
                ),
            },
        )
    )


def cmd_unknown_unlink_run(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = unlink_run(root, args.id, args.run_id)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    st = rec.get("stats") or {}
    print(
        f"unknown {rec['id']}  run_ids={rec.get('run_ids')}  "
        f"primary_run={rec.get('primary_run_id')}"
    )
    if rec.get("type") in ("number", "boolean"):
        print(
            f"  recomputed n={st.get('n')}  "
            f"derived={rec.get('confidence_derived')}"
        )
    return 0


def cmd_unknown_delete(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        path = delete_unknown(root, args.id)
    except (FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"deleted unknown {args.id}  ({path})")
    _announce_write_target(root, "unknown delete")
    return 0


def cmd_known_unlink_run(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = unlink_run_known(root, args.id, args.run_id)
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    st = rec.get("stats") or {}
    print(
        f"known {rec['id']}  run_ids={rec.get('run_ids')}  "
        f"conf={rec.get('confidence')}/{rec.get('confidence_derived')}"
    )
    if rec.get("type") == "boolean":
        print(f"  n={st.get('n')}  rate={st.get('rate')}")
    else:
        print(f"  n={st.get('n')}  mean={st.get('mean')}  std={st.get('std')}")
    if not (st.get("n") or 0):
        print(
            f"  NOTE: known {rec['id']} is now unbacked (n=0) — reads and "
            f"the gate will refuse it. Re-back or delete:\n"
            f"    terra known link-run {rec['id']} <run_id>\n"
            f"    terra known delete {rec['id']}"
        )
    return 0


def cmd_plan_create(args: argparse.Namespace) -> int:
    from .plans import create_plan, load_plan
    from .evidence_plan import format_plan_summary

    try:
        root, created = ensure_project_root()
        path = create_plan(
            root,
            args.id,
            claim=args.claim,
            mode=args.mode or "all",
            legs=args.legs or [],
            status=args.status,
            notes=args.notes or "",
            force=args.force,
        )
    except (ValueError, FileExistsError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if created:
        print(f"initialized {root / '.terra' / 'map'}")
    rec = load_plan(root, args.id)
    print(f"created plan {args.id}  (above types; legs use number|boolean)")
    print(f"  status={rec.get('status')}  conf={rec.get('confidence')}")
    for line in format_plan_summary(rec):
        print(f"  {line}")
    print(f"  {path}")
    print("  next: terra plan link-run <id> <run> --leg <leg_id>")
    return 0


def cmd_plan_list(args: argparse.Namespace) -> int:
    from .plans import list_plans

    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rows = list_plans(root)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("(no plans — terra plan create …)")
        return 0
    for r in rows:
        flag = "ok" if r["ok"] else "BAD"
        rec = r.get("record") or {}
        pl = rec.get("plan") or {}
        print(
            f"[{flag}] {r['id']}  mode={pl.get('mode')}  "
            f"{pl.get('satisfied_count', 0)}/{pl.get('leg_count', 0)}  "
            f"conf={rec.get('confidence')}/{rec.get('confidence_derived')}  "
            f"{(rec.get('claim') or '')[:50]}"
        )
    return 0


def cmd_plan_show(args: argparse.Namespace) -> int:
    from .plans import describe_plan
    from .evidence_plan import format_plan_summary

    try:
        root = require_project_root()
        desc = describe_plan(root, args.id)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rec = desc["record"]
    if args.json:
        print(json.dumps(rec, indent=2, sort_keys=True, default=str))
        return 0
    print(f"plan {rec.get('id')}  status={rec.get('status')}")
    print(f"claim: {rec.get('claim')}")
    print(
        f"confidence: claimed={rec.get('confidence')}  "
        f"derived={rec.get('confidence_derived')}"
    )
    for line in format_plan_summary(rec):
        print(line)
    return 0


def cmd_plan_link_run(args: argparse.Namespace) -> int:
    from .plans import link_run_plan
    from .evidence_plan import format_plan_summary

    try:
        root = require_project_root()
        rec = link_run_plan(
            root,
            args.id,
            args.run_id,
            leg_id=args.leg,
            primary=bool(args.primary),
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"plan {rec['id']}  linked run → leg {args.leg}")
    for line in format_plan_summary(rec):
        print(f"  {line}")
    return 0


def cmd_plan_unlink_run(args: argparse.Namespace) -> int:
    from .plans import unlink_run_plan
    from .evidence_plan import format_plan_summary

    try:
        root = require_project_root()
        rec = unlink_run_plan(
            root, args.id, args.run_id, leg_id=getattr(args, "leg", None)
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"plan {rec['id']}  unlinked {args.run_id}")
    for line in format_plan_summary(rec):
        print(f"  {line}")
    return 0


def cmd_plan_promote(args: argparse.Namespace) -> int:
    from .plans import promote_plan

    try:
        root = require_project_root()
        rec = promote_plan(
            root, args.id, args.confidence, status=args.status
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"plan {rec['id']}  confidence={rec.get('confidence')}  "
        f"status={rec.get('status')}  derived={rec.get('confidence_derived')}"
    )
    return 0


def cmd_plan_delete(args: argparse.Namespace) -> int:
    from .plans import delete_plan

    try:
        root = require_project_root()
        path = delete_plan(root, args.id)
    except (FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"deleted plan {args.id}  ({path})")
    return 0


def cmd_known_delete(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        from .design import load_design
        from .readings import list_consumers

        params = [
            p.get("name")
            for p in load_design(root).get("params") or []
            if p.get("known_id") == args.id
        ]
        consumers = [
            c.get("consumer") for c in list_consumers(root, args.id)
        ]
        # Name the write target BEFORE the destructive act - deleting the
        # wrong map's copy (a silently-active session map) is the footgun.
        _announce_write_target(root, "known delete")
        path = delete_known(root, args.id)
    except (FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"deleted known {args.id}  ({path})")
    if params or consumers:
        print(
            "  NOTE: to retire a WRONG belief without destroying its history "
            "(and without dangling refs), prefer: terra known supersede "
            f"{args.id} --reason '…' [--by <replacement>]"
        )
    if params:
        print(
            f"  NOTE: design param(s) {params} still reference this known — "
            f"design check will go red; terra design remove <param> or "
            f"re-survey and terra design add"
        )
    if consumers:
        print(
            f"  NOTE: consumers read this known: {consumers} — they now "
            f"break loudly on next read (that is the point)"
        )
    return 0


def cmd_suite_create(args: argparse.Namespace) -> int:
    try:
        root, created = ensure_project_root()
        probes = parse_probe_list(args.probes)
        default_to = None
        if args.to_file:
            default_to = json.loads(Path(args.to_file).read_text(encoding="utf-8"))
        elif args.to is not None:
            default_to = parse_to_arg(args.to)
        path = create_suite(
            root,
            args.id,
            probes=probes,
            default_to=default_to,
            purpose=args.purpose or "",
            force=args.force,
        )
    except (ValueError, FileExistsError, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if created:
        print(f"initialized {root / '.terra' / 'map'}")
    print(f"created suite {args.id}")
    print(f"  probes: {', '.join(parse_probe_list(args.probes))}")
    print(f"  {path}")
    print(f"  next: terra suite run {args.id} --to '{{…}}'")
    return 0


def cmd_suite_list(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rows = list_suites(root)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("(no suites)")
        return 0
    for r in rows:
        flag = "ok" if r["ok"] else "BAD"
        rec = r.get("record") or {}
        probes = rec.get("probes") or []
        print(f"[{flag}] {r['id']}  ({len(probes)}) {', '.join(probes)}")
    return 0


def cmd_suite_show(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = load_suite(root, args.id)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(rec, indent=2, sort_keys=True, default=str))
    return 0


def cmd_suite_run(args: argparse.Namespace) -> int:
    try:
        root, created = ensure_project_root()
        to = None
        if args.to_file:
            to = json.loads(Path(args.to_file).read_text(encoding="utf-8"))
        elif args.to is not None:
            to = parse_to_arg(args.to)
        timeout = args.timeout if args.timeout is not None else DEFAULT_RUN_TIMEOUT_S
        summary = run_suite(
            root,
            args.id,
            to=to,
            timeout_s=float(timeout),
            dry_run=bool(args.dry_run),
            stop_on_error=not bool(args.continue_on_error),
            strict_to=bool(getattr(args, "strict_to", False)),
            strict_status=bool(getattr(args, "strict_status", False)),
        )
    except (ValueError, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if created:
        print(f"initialized {root / '.terra' / 'map'}")
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return 0 if summary.get("ok") else 1
    status = "ok" if summary.get("ok") else "FAIL"
    print(f"[{status}] suite {summary.get('suite_id')}")
    print(f"  to: {json.dumps(summary.get('to'), default=str)}")
    for r in summary.get("results") or []:
        if r.get("ok"):
            print(
                f"  [ok] {r.get('probe_id')}  run={r.get('run_id')}  "
                f"status={r.get('status')}"
            )
            for w in r.get("warnings") or []:
                print(f"    warn: {w}")
        else:
            print(f"  [FAIL] {r.get('probe_id')}  {r.get('error')}")
    print(f"  run_ids: {summary.get('run_ids')}")
    print("PASS" if summary.get("ok") else "FAIL")
    return 0 if summary.get("ok") else 1


def cmd_suite_validate(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.id:
        result = validate_suite(root, args.id)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0 if result["ok"] else 1
        status = "ok" if result["ok"] else "FAIL"
        print(f"[{status}] suite {result.get('id')}")
        for b in result.get("blocks") or []:
            print(f"  block: {b}")
        for p in result.get("probes") or []:
            ps = "ok" if p.get("ok") else "FAIL"
            print(f"  probe [{ps}] {p.get('id')}")
        print("PASS" if result["ok"] else "FAIL")
        return 0 if result["ok"] else 1

    result = validate_all_suites(root)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["ok"] else 1
    for s in result.get("suites") or []:
        status = "ok" if s["ok"] else "FAIL"
        print(f"[{status}] {s.get('id')}")
        for b in s.get("blocks") or []:
            print(f"  block: {b}")
    print(f"count={result.get('count', 0)}")
    print("PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 1


def cmd_probe_run(args: argparse.Namespace) -> int:
    """Execute a probe and stamp a run under .terra/map/runs/."""
    try:
        root = require_project_root()
        if getattr(args, "to_file", None):
            to_path = Path(args.to_file)
            to = json.loads(to_path.read_text(encoding="utf-8"))
        else:
            to = parse_to_arg(args.to)
        timeout = args.timeout if args.timeout is not None else DEFAULT_RUN_TIMEOUT_S

        # --spec-file: the probe's spec travels as an ARGUMENT, not env.
        # Probes were selecting their subject through PROBE_SPEC_JSON, which
        # dies silently at a fresh-shell boundary (each agent tool call is a
        # new shell; only cwd survives). A dropped override then let a probe
        # measure the DEFAULT body and still report status=ok — that is how a
        # gate certified canon while claiming to test a different artifact.
        extra: dict[str, Any] = {}
        if getattr(args, "spec_file", None):
            spec_path = Path(args.spec_file)
            if not spec_path.is_file():
                raise FileNotFoundError(f"--spec-file not found: {spec_path}")
            extra["spec"] = json.loads(spec_path.read_text(encoding="utf-8"))

        stamp = run_probe(
            root,
            args.id,
            to=to,
            timeout_s=float(timeout),
            dry_run=bool(args.dry_run),
            extra_ctx=extra or None,
            strict_to=bool(getattr(args, "strict_to", False)),
            strict_status=bool(getattr(args, "strict_status", False)),
        )

        # --assert-measure: a post-run gate that FAILS LOUDLY. The run is still
        # stamped (audit trail), but the command exits nonzero and names the
        # mismatch, so "the override silently didn't take" becomes impossible
        # to mistake for a clean pass.
        asserts = getattr(args, "assert_measure", None) or []
        if asserts:
            got = {
                m.get("quantity"): m.get("value")
                for m in (stamp.get("measures") or [])
            }
            problems: list[str] = []
            for spec in asserts:
                name, _, want = str(spec).partition("=")
                name = name.strip()
                if name not in got:
                    problems.append(
                        f"{name}: NOT EMITTED by this run "
                        f"(the probe never measured it)"
                    )
                    continue
                if want.strip():
                    try:
                        ok = float(got[name]) == float(want)
                    except (TypeError, ValueError):
                        ok = str(got[name]) == want.strip()
                    if not ok:
                        problems.append(
                            f"{name}: expected {want.strip()}, got {got[name]!r}"
                        )
            if problems:
                return emit(
                    error(
                        "run stamped as "
                        f"{stamp.get('id')} but --assert-measure FAILED:\n  - "
                        + "\n  - ".join(problems)
                        + "\n  the reading exists; it does NOT satisfy the "
                        "assertion you required.",
                        code="probe_run_assert",
                        meta={"run_id": stamp.get("id"), "failed": problems},
                    )
                )
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, TimeoutError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not args.dry_run:
        _announce_write_target(root, "probe run")

    val = stamp.get("validation") or {}
    if val.get("revalidated"):
        print(
            f"NOTE: probe {args.id} was re-validated before this run "
            f"({val.get('reason') or 'stamp missing/stale'}) — validation "
            "stamp refreshed",
            file=sys.stderr,
        )

    if args.json:
        out = {k: v for k, v in stamp.items() if not str(k).startswith("_")}
        out["path"] = stamp.get("_path")
        out["run_dir"] = stamp.get("_run_dir")
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"run {stamp['id']}")
    print(f"  probe:   {stamp.get('probe_id')}")
    print(f"  status:  {stamp.get('status')}")
    print(f"  path:    {stamp.get('_path')}")
    arts = stamp.get("artifacts") or []
    print(f"  artifacts: {len(arts)}")
    for w in stamp.get("warnings") or []:
        print(f"  warn:  {w}")
    time = stamp.get("time") or {}
    if time.get("duration_s") is not None:
        print(f"  duration_s: {time.get('duration_s')}")
    if not args.dry_run:
        try:
            from .knowns import list_knowns
            from .unknowns import list_unknowns

            open_unk = [
                str((u.get("record") or {}).get("id") or u.get("id"))
                for u in list_unknowns(root)
                if (u.get("record") or {}).get("status")
                in ("open", "probing", "blocked")
                and args.id in ((u.get("record") or {}).get("probe_ids") or [])
            ]
            linked_kn = [
                str((k.get("record") or {}).get("id") or k.get("id"))
                for k in list_knowns(root)
                if args.id in ((k.get("record") or {}).get("probe_ids") or [])
            ]
        except (OSError, ValueError):
            open_unk, linked_kn = [], []
        for uid in open_unk:
            print(f"  next: terra unknown link-run {uid} {stamp['id']}")
        for kid in linked_kn:
            print(f"  next: terra known link-run {kid} {stamp['id']}")
        if (open_unk or linked_kn) and not stamp.get("measures"):
            print(
                "  NOTE: this run emitted no measures[] — linked typed "
                "unknowns/knowns will not gain samples from it"
            )
    return 0


def cmd_run_validate(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.id:
        result = validate_run_id(root, args.id)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0 if result["ok"] else 1
        status = "ok" if result["ok"] else "FAIL"
        print(f"[{status}] {result.get('id')}")
        for b in result.get("blocks") or []:
            print(f"  block: {b}")
        for w in result.get("warnings") or []:
            print(f"  warn:  {w}")
        print("PASS" if result["ok"] else "FAIL")
        return 0 if result["ok"] else 1

    result = validate_all_runs(root)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["ok"] else 1
    for r in result.get("runs") or []:
        status = "ok" if r["ok"] else "FAIL"
        rec = r.get("record") or {}
        print(
            f"[{status}] {r.get('id')}  probe={rec.get('probe_id')}  "
            f"status={rec.get('status')}"
        )
        for b in r.get("blocks") or []:
            print(f"  block: {b}")
        for w in r.get("warnings") or []:
            print(f"  warn:  {w}")
    print(f"count={result.get('count', 0)}")
    print("PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 1


def cmd_run_list(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rows = list_runs(
        root,
        probe_id=getattr(args, "probe", None),
        status=getattr(args, "status", None),
    )
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("(no runs — terra probe run <id>)")
        return 0
    for r in rows:
        flag = "ok" if r["ok"] else "BAD"
        rec = r.get("record") or {}
        void = " VOID" if rec.get("voided") else ""
        print(
            f"[{flag}] {r['id']}{void}  probe={rec.get('probe_id')}  "
            f"status={rec.get('status')}"
        )
    return 0


def cmd_run_show(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = load_run(root, args.id)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(rec, indent=2, sort_keys=True, default=str))
    return 0


def cmd_run_void(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        result = void_run(
            root,
            args.id,
            reason=args.reason or "",
            cascade=not bool(args.no_cascade),
        )
    except (ValueError, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _announce_write_target(root, "run void")
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    rec = result["run"]
    print(f"voided run {rec.get('id')}  reason={rec.get('void_reason')!r}")
    u = result.get("unlinked") or {}
    if result.get("cascade"):
        print(
            f"  unlinked knowns={u.get('knowns') or []}  "
            f"unknowns={u.get('unknowns') or []}  "
            f"plans={u.get('plans') or []}"
        )
    else:
        print(
            f"  recomputed (still linked) knowns={u.get('knowns') or []}  "
            f"unknowns={u.get('unknowns') or []}  "
            f"plans={u.get('plans') or []}"
        )
        still = (u.get("knowns") or []) + (u.get("unknowns") or [])
        if still:
            print(
                f"  NOTE: {still} still reference this voided run "
                f"(--no-cascade) — stats exclude it, but unlink or re-void "
                f"with cascade if the link itself was the mistake"
            )
    if result.get("cascade") and (u.get("knowns") or []):
        print(
            "  NOTE: unlinked knowns may now be unbacked (n=0) — "
            "terra map status will flag known_unbacked; re-run + link or "
            "delete them"
        )
    print("  next agent will not use this sample in stats")
    return 0


def cmd_run_unvoid(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        rec = unvoid_run(root, args.id)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"unvoided run {rec.get('id')}  (re-link if it should count again)")
    return 0


def cmd_run_delete(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
        result = delete_run(
            root, args.id, cascade=not bool(args.no_cascade)
        )
    except (ValueError, FileNotFoundError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    print(f"deleted run {result.get('deleted')}  ({result.get('path')})")
    u = result.get("unlinked") or {}
    print(
        f"  unlinked knowns={u.get('knowns') or []}  "
        f"unknowns={u.get('unknowns') or []}"
    )
    return 0


def cmd_unknown_validate(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.id:
        result = validate_unknown_file(unknown_path(root, args.id))
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            status = "ok" if result["ok"] else "FAIL"
            print(f"[{status}] {result['id']}")
            for b in result.get("blocks") or []:
                print(f"  block: {b}")
            print("PASS" if result["ok"] else "FAIL")
        return 0 if result["ok"] else 1

    result = validate_all_unknowns(root)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["ok"] else 1
    for u in result.get("unknowns") or []:
        status = "ok" if u["ok"] else "FAIL"
        rec = u.get("record") or {}
        print(f"[{status}] {u['id']}  {rec.get('status', '?')}")
        for b in u.get("blocks") or []:
            print(f"  block: {b}")
    print(
        f"active={result.get('active_count', 0)}  "
        f"blocking={result.get('blocking_count', 0)}"
    )
    print("PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 1


def cmd_probe_validate(args: argparse.Namespace) -> int:
    """Validate one probe package, all packages, or a bare .py script."""
    target = args.target

    # Bare script path
    if target and (target.endswith(".py") or Path(target).is_file()):
        path = Path(target)
        if path.is_file():
            result = validate_probe_script(
                path,
                purpose=args.purpose,
                probe_id=args.id,
            )
            return _print_probe_result(result, json_out=args.json)

    try:
        root = require_project_root()
    except FileNotFoundError as e:
        # Allow bare script without project; otherwise error
        if target and Path(target).is_file():
            result = validate_probe_script(
                Path(target), purpose=args.purpose, probe_id=args.id
            )
            return _print_probe_result(result, json_out=args.json)
        print(f"error: {e}", file=sys.stderr)
        return 1

    ensure_probes_store(root)

    if not target or target in (".", "all", "--all"):
        result = validate_all_probes(probes_root(root))
        return _print_probe_result(result, json_out=args.json)

    # Package id or path to package dir
    cand = Path(target)
    if cand.is_dir() and (cand / "probe.json").is_file():
        pdir = cand
        result = validate_probe_dir(cand)
    else:
        pdir = probe_dir(root, target)
        if not pdir.is_dir():
            print(
                f"error: unknown probe {target!r} (no {pdir})",
                file=sys.stderr,
            )
            return 1
        result = validate_probe_dir(pdir)
    rc = _print_probe_result(result, json_out=args.json)
    if rc == 0 and not args.json:
        st = result.get("stamp") or {}
        if st.get("source_sha256"):
            print(
                f"  stamped: validated {st.get('validated_at')} "
                f"source {str(st['source_sha256'])[:12]}… "
                "(probe run refuses an unvalidated/edited probe)"
            )
        try:
            src = (pdir / "probe.py").read_text(encoding="utf-8")
        except OSError:
            src = ""
        if "TODO: implement" in src or "scaffold stub" in src:
            print(
                "  NOTE: this is still the scaffold stub — it validates but "
                "measures nothing real; implement run() before trusting runs"
            )
        elif "measures" not in src:
            print(
                "  NOTE: probe.py emits no measures[] — typed unknowns/"
                "knowns (number/boolean/relation) need "
                "{'quantity': …, 'value': …} rows to gain samples"
            )
        print(
            "  NOTE: validate PASS ≠ surveyed — the reading is "
            "`terra probe run … --json` + link-run"
        )
    return rc


def cmd_probe_list(args: argparse.Namespace) -> int:
    try:
        root = require_project_root()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    proot = probes_root(root)
    if not proot.is_dir():
        print("(no probes directory — run terra init)")
        return 0
    rows = []
    for child in sorted(p for p in proot.iterdir() if p.is_dir()):
        if child.name.startswith("."):
            continue
        if not (child / "probe.json").is_file() and not (child / "probe.py").is_file():
            continue
        r = validate_probe_dir(child)
        rows.append(r)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("(no probes)")
        return 0
    for r in rows:
        flag = "ok" if r["ok"] else "BAD"
        meta = r.get("meta") or {}
        purpose = meta.get("purpose") or ""
        kind = meta.get("kind") or "?"
        extra = ""
        if kind == "watch":
            dur = meta.get("duration_s", 0)
            mode = "snapshot" if float(dur or 0) <= 0 else f"{dur}s"
            extra = f"  watch/{mode}"
        elif kind == "run":
            extra = "  run"
        print(f"[{flag}] {r.get('id')}  {kind}{extra}  {purpose}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    from .route import TASK_PRIORITIES

    p = argparse.ArgumentParser(
        prog="terra",
        description=(
            "Terra — above Cartograph: brief (SSOT) + route (tasks) + map (survey). "
            "Typed knowns/unknowns, assumptions, calculations, probes, and sessions."
        ),
    )
    p.add_argument("--version", action="version", version=f"terra {__version__}")
    p.add_argument(
        "--map",
        default=None,
        dest="map_id",
        help=(
            "Map scope for this command: 'global' (default) or session id. "
            "Overrides .terra/active_map and TERRA_MAP. "
            "Probes/lib always global; beliefs/runs use this map."
        ),
    )
    sub = p.add_subparsers(dest="group", required=True)

    p_map = sub.add_parser(
        "map",
        help="Map scopes: global durable map vs experiment sessions",
    )
    map_sub = p_map.add_subparsers(dest="map_cmd", required=True)

    p_mc = map_sub.add_parser(
        "create",
        help="Create experiment-scoped map (isolated knowns/unknowns/runs)",
    )
    p_mc.add_argument("id", help="Session map slug")
    p_mc.add_argument("--purpose", default="", help="What this experiment is for")
    p_mc.add_argument(
        "--parent",
        default=None,
        help="Parent map (default global) — reads fall through child -> "
        "parent -> global; promote up with `terra known adopt`",
    )
    p_mc.add_argument(
        "--use",
        action="store_true",
        help="Set as active map after create",
    )
    p_mc.add_argument("--force", action="store_true")
    p_mc.set_defaults(func=cmd_map_create)

    p_ml = map_sub.add_parser("list", help="List global + session maps")
    p_ml.add_argument("--json", action="store_true")
    p_ml.set_defaults(func=cmd_map_list)

    p_mu = map_sub.add_parser("use", help="Set default active map (writes .terra/active_map)")
    p_mu.add_argument("id", help="'global' or session slug")
    p_mu.set_defaults(func=cmd_map_use)

    p_ms = map_sub.add_parser("show", help="Show active map paths")
    p_ms.add_argument("--json", action="store_true")
    p_ms.set_defaults(func=cmd_map_show)

    p_mst = map_sub.add_parser(
        "status",
        help="Map board (JSON by default — agent-first; use --human for text)",
    )
    p_mst.add_argument(
        "--all",
        action="store_true",
        help="Include every session map + global",
    )
    p_mst.add_argument(
        "--id",
        dest="map_scope",
        default=None,
        help="Board for this map id only (default: active)",
    )
    p_mst.add_argument(
        "--human",
        action="store_true",
        help="Pretty-print for humans (secondary view)",
    )
    p_mst.add_argument(
        "--json",
        action="store_true",
        help="Alias for default agent JSON (always on unless --human/--html)",
    )
    p_mst.add_argument(
        "--html",
        action="store_true",
        help="Write HTML board (human view); JSON path still emitted unless --human",
    )
    p_mst.add_argument(
        "-o",
        "--output",
        default=None,
        help="HTML output path (with --html)",
    )
    p_mst.add_argument(
        "--open",
        action="store_true",
        help="Open HTML in browser (implies --html)",
    )
    p_mst.set_defaults(func=cmd_map_status)

    p_init = sub.add_parser(
        "init", help="Create Terra map stores in this project"
    )
    p_init.add_argument(
        "path", nargs="?", default=None, help="Project directory (default: cwd)"
    )
    p_init.set_defaults(func=cmd_init)

    p_probe = sub.add_parser("probe", help="Map probes (instruments)")
    probe_sub = p_probe.add_subparsers(dest="probe_cmd", required=True)

    def _add_probe_scaffold_parser(name: str, help_text: str) -> None:
        sp = probe_sub.add_parser(name, help=help_text)
        sp.add_argument("id", help="Probe slug (e.g. env_fingerprint)")
        sp.add_argument(
            "--purpose",
            required=True,
            help="One sentence: what mystery this probe reduces",
        )
        sp.add_argument(
            "--kind",
            choices=("run", "watch"),
            default="watch",
            help="run = drive/simulate; watch = observe (duration_s=0 → snapshot)",
        )
        sp.add_argument(
            "--duration",
            type=float,
            default=None,
            dest="duration",
            help="For kind=watch only: seconds (0=snapshot, default 0; >0=stream window)",
        )
        sp.add_argument(
            "--force",
            action="store_true",
            help="Overwrite probe.json / probe.py if present",
        )
        sp.add_argument(
            "--input",
            action="append",
            default=None,
            metavar="NAME=known:ID|assumption:ID",
            help="Declared map input injected as ctx['inputs'][NAME] (repeatable)",
        )
        sp.set_defaults(func=cmd_probe_create)

    _add_probe_scaffold_parser(
        "create",
        "Scaffold a new Python probe package (base create)",
    )
    _add_probe_scaffold_parser(
        "init",
        "Alias for create — scaffold a new Python probe package",
    )

    p_val = probe_sub.add_parser(
        "validate",
        help="Pseudo-validate a probe package, all probes, or a bare .py script",
    )
    p_val.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Probe id, package dir, path to .py, or omit for all",
    )
    p_val.add_argument(
        "--purpose",
        default=None,
        help="Purpose hint when validating a bare script",
    )
    p_val.add_argument(
        "--id",
        default=None,
        help="Probe id hint when validating a bare script",
    )
    p_val.add_argument("--json", action="store_true")
    p_val.set_defaults(func=cmd_probe_validate)

    p_list = probe_sub.add_parser("list", help="List probes and validation status")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_probe_list)

    p_run = probe_sub.add_parser(
        "run",
        help="Execute probe and stamp a run (time/from/to/status/artifacts)",
    )
    p_run.add_argument("id", help="Probe id")
    p_run.add_argument(
        "--to",
        default=None,
        help='Target: JSON, key=value pairs, or literal (default {"kind":"default"})',
    )
    p_run.add_argument(
        "--to-file",
        default=None,
        help="Path to JSON file for `to`",
    )
    p_run.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=f"Seconds (default {DEFAULT_RUN_TIMEOUT_S:g})",
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass dry_run=True (probe should avoid live side effects)",
    )
    p_run.add_argument(
        "--strict-to",
        action="store_true",
        help="CI: fail if recommended to envelope warns (e.g. missing kind)",
    )
    p_run.add_argument(
        "--strict-status",
        action="store_true",
        help="CI: fail if status is not in recommended vocab",
    )
    p_run.add_argument(
        "--spec-file",
        default=None,
        help="JSON file whose contents become ctx['spec'] — survives the "
        "shell boundary that silently drops env-based overrides",
    )
    p_run.add_argument(
        "--assert-measure",
        action="append",
        default=None,
        metavar="NAME[=VALUE]",
        help="Fail loudly unless the run emitted NAME (optionally == VALUE). "
        "Repeatable. Turns a silently-ignored override into a hard error.",
    )
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_probe_run)

    p_runs = sub.add_parser("run", help="Stamped map readings")
    runs_sub = p_runs.add_subparsers(dest="run_cmd", required=True)
    p_rl = runs_sub.add_parser("list", help="List stamped runs")
    p_rl.add_argument("--probe", default=None, help="Filter by probe id")
    p_rl.add_argument(
        "--status",
        default=None,
        help=(
            "Filter by status string (recommended: ok|degraded|unavailable|"
            "empty|error — freeform values still match exactly)"
        ),
    )
    p_rl.add_argument("--json", action="store_true")
    p_rl.set_defaults(func=cmd_run_list)

    p_rv = runs_sub.add_parser("validate", help="Validate stamped run(s)")
    p_rv.add_argument("id", nargs="?", default=None, help="Run id or omit for all")
    p_rv.add_argument("--json", action="store_true")
    p_rv.set_defaults(func=cmd_run_validate)

    p_rs = runs_sub.add_parser("show", help="Show one run meta.json")
    p_rs.add_argument("id", help="Run id")
    p_rs.set_defaults(func=cmd_run_show)

    p_rvoid = runs_sub.add_parser(
        "void",
        help="Mark run bad (excluded from stats); cascade-unlinks by default",
    )
    p_rvoid.add_argument("id", help="Run id")
    p_rvoid.add_argument(
        "--reason",
        default="",
        help="Why this run must not feed the map",
    )
    p_rvoid.add_argument(
        "--no-cascade",
        action="store_true",
        help="Keep run_ids on knowns/unknowns (still skipped in stats)",
    )
    p_rvoid.add_argument("--json", action="store_true")
    p_rvoid.set_defaults(func=cmd_run_void)

    p_runvoid = runs_sub.add_parser(
        "unvoid",
        help="Clear voided flag (does not re-link; link-run again if needed)",
    )
    p_runvoid.add_argument("id", help="Run id")
    p_runvoid.set_defaults(func=cmd_run_unvoid)

    p_rdel = runs_sub.add_parser(
        "delete",
        help="Hard-delete run dir (prefer void). Cascade-unlinks by default",
    )
    p_rdel.add_argument("id", help="Run id")
    p_rdel.add_argument(
        "--no-cascade",
        action="store_true",
        help="Do not unlink from knowns/unknowns first",
    )
    p_rdel.add_argument("--json", action="store_true")
    p_rdel.set_defaults(func=cmd_run_delete)

    # --- unknowns ---
    p_unk = sub.add_parser(
        "unknown",
        help="Named gaps in understanding (stuck → open an unknown)",
    )
    unk_sub = p_unk.add_subparsers(dest="unknown_cmd", required=True)

    p_uc = unk_sub.add_parser("create", help="Open a new unknown (auto-inits store)")
    p_uc.add_argument("id", help="Slug id (e.g. mob_query_api)")
    p_uc.add_argument(
        "--claim",
        required=True,
        help="What we do not know (one clear sentence)",
    )
    p_uc.add_argument(
        "--evidence",
        default="",
        help="What reading would resolve this (required content for open status)",
    )
    p_uc.add_argument(
        "--type",
        default=None,
        choices=["number", "boolean", "formula", "relation"],
        dest="type",
        help="number | boolean | formula (expr+vars) | relation (F(x) curve)",
    )
    p_uc.add_argument(
        "--x-quantity",
        dest="x_quantity",
        default=None,
        help="For relation: what x means (e.g. alpha_deg)",
    )
    p_uc.add_argument(
        "--x-unit", dest="x_unit", default="", help="For relation: x unit"
    )
    p_uc.add_argument(
        "--quantity",
        default=None,
        help="For number/boolean: stable measure name (e.g. hostile_count)",
    )
    p_uc.add_argument(
        "--expression",
        default=None,
        help="For formula: e.g. 'mean(h) <= 10 and n(h) >= 3'",
    )
    p_uc.add_argument(
        "--var",
        action="append",
        dest="vars",
        default=None,
        metavar="NAME=QTY[:kind]|NAME=known:ID",
        help="For formula: bind a run quantity or live known (repeatable)",
    )
    p_uc.add_argument("--unit", default="", help="Optional unit for number type")
    p_uc.add_argument(
        "--no-blocks-build",
        action="store_true",
        help="Do not mark as blocking product build (default: blocks_build=true)",
    )
    p_uc.add_argument("--probe", default=None, help="Optional linked probe id")
    p_uc.add_argument("--notes", default="", help="Freeform notes")
    p_uc.add_argument("--force", action="store_true")
    p_uc.add_argument(
        "--within",
        default=None,
        help="Method-agreement tolerance for corroboration (e.g. 5%% or 0.5); "
        "carried through graduate",
    )
    p_uc.set_defaults(func=cmd_unknown_create)

    p_ul = unk_sub.add_parser("list", help="List unknowns")
    p_ul.add_argument("--json", action="store_true")
    p_ul.set_defaults(func=cmd_unknown_list)

    p_us = unk_sub.add_parser(
        "show", help="Show unknown + linked probes/runs (use --json for raw)"
    )
    p_us.add_argument("id")
    p_us.add_argument("--json", action="store_true", help="Machine-readable describe")
    p_us.set_defaults(func=cmd_unknown_show)

    # `known get` is THE read verb, so agents reach for `unknown get` and hit
    # a phantom command; the nearest-looking verb, `unknown status`, is a
    # SETTER that errors without its positional. Alias the reader so the verb
    # is symmetric across both node types.
    p_ug = unk_sub.add_parser(
        "get",
        help="Alias of `unknown show` — symmetric with `known get`",
    )
    p_ug.add_argument("id")
    p_ug.add_argument("--json", action="store_true", help="Machine-readable describe")
    p_ug.set_defaults(func=cmd_unknown_show)

    p_ust = unk_sub.add_parser("status", help="Set status (open|probing|blocked|resolved|wont_care)")
    p_ust.add_argument("id")
    p_ust.add_argument("status", choices=sorted(UNKNOWN_STATUSES))
    p_ust.add_argument(
        "--resolved-by",
        default=None,
        help="How it was closed (required trail when status=resolved)",
    )
    p_ust.add_argument("--notes", default=None)
    p_ust.set_defaults(func=cmd_unknown_status)

    p_ugr = unk_sub.add_parser(
        "graduate",
        help="Graduate typed unknown with linked runs into a known "
        "(the only way knowns are born)",
    )
    p_ugr.add_argument("id", help="Unknown id")
    p_ugr.add_argument(
        "--as",
        dest="as_id",
        default=None,
        help="Known slug (default: same as unknown id)",
    )
    p_ugr.add_argument(
        "--with",
        dest="with_ids",
        default=None,
        metavar="U1,U2",
        help="Merge sibling unknowns (same type+quantity) into the same "
        "known: evidence unions, all resolve",
    )
    p_ugr.add_argument(
        "--into",
        default=None,
        metavar="KNOWN_ID",
        help="Merge into an EXISTING known instead of minting a new one",
    )
    p_ugr.add_argument("--notes", default=None)
    p_ugr.add_argument(
        "--cohort",
        dest="cohort_id",
        default=None,
        metavar="COHORT_ID",
        help="Join this coupled-known cohort at birth (created on first "
        "member) — for quantities that settle together in one solve",
    )
    p_ugr.add_argument(
        "--force", action="store_true", help="Overwrite existing known"
    )
    p_ugr.add_argument("--json", action="store_true")
    p_ugr.set_defaults(func=cmd_unknown_graduate)

    p_ulp = unk_sub.add_parser(
        "link-probe",
        help="Link a probe id (multi ok via probe_ids); sets probing if was open",
    )
    p_ulp.add_argument("id", help="Unknown id")
    p_ulp.add_argument("probe_id", help="Probe id")
    p_ulp.set_defaults(func=cmd_unknown_link_probe)

    p_ulr = unk_sub.add_parser(
        "link-run",
        help="Link a stamped run as structured evidence; sets probing if was open",
    )
    p_ulr.add_argument("id", help="Unknown id")
    p_ulr.add_argument("run_id", help="Run id under .terra/map/runs/")
    p_ulr.add_argument(
        "--primary",
        action="store_true",
        help="Set as primary_run_id (default: first link becomes primary)",
    )
    p_ulr.add_argument(
        "--allow-no-sample",
        action="store_true",
        help="Attach a run that contributes ZERO samples (provenance only). "
        "Refused by default: a link that adds no sample looks like success "
        "while the evidence carries no weight.",
    )
    p_ulr.set_defaults(func=cmd_unknown_link_run)

    p_ulc = unk_sub.add_parser(
        "link-calculation",
        help="Link the latest fresh calculation result as evidence",
    )
    p_ulc.add_argument("id", help="Unknown id")
    p_ulc.add_argument("calculation_id", help="Calculation id")
    p_ulc.add_argument(
        "--output",
        default=None,
        help="Model output name (optional when type+quantity identify one output)",
    )
    p_ulc.add_argument("--primary", action="store_true")
    p_ulc.set_defaults(func=cmd_unknown_link_calculation)

    p_uur = unk_sub.add_parser(
        "unlink-run",
        help="Remove a run id from an unknown; recompute typed stats",
    )
    p_uur.add_argument("id", help="Unknown id")
    p_uur.add_argument("run_id", help="Run id to detach")
    p_uur.set_defaults(func=cmd_unknown_unlink_run)

    p_udel = unk_sub.add_parser(
        "delete", help="Delete unknown record from active map"
    )
    p_udel.add_argument("id", help="Unknown id")
    p_udel.set_defaults(func=cmd_unknown_delete)

    p_uv = unk_sub.add_parser("validate", help="Validate unknown record(s)")
    p_uv.add_argument("id", nargs="?", default=None)
    p_uv.add_argument("--json", action="store_true")
    p_uv.set_defaults(func=cmd_unknown_validate)

    # --- assumptions (unresolved but consumable; always conditional) ---
    p_asm = sub.add_parser(
        "assumption",
        help="Provisional consumable values; progress continues but stays conditional",
    )
    asm_sub = p_asm.add_subparsers(dest="assumption_cmd", required=True)

    p_ac = asm_sub.add_parser("create", help="Create a provisional typed value")
    p_ac.add_argument("id", help="Assumption slug")
    p_ac.add_argument("--claim", required=True)
    p_ac.add_argument("--type", required=True, choices=["number", "boolean"])
    p_ac.add_argument("--quantity", required=True)
    p_ac.add_argument("--value", required=True, help="JSON scalar, e.g. 0.85 or true")
    p_ac.add_argument("--unit", default="")
    p_ac.add_argument("--reason", required=True, help="Why this provisional basis is acceptable")
    p_ac.add_argument("--evidence", required=True, help="What would replace it with a known")
    p_ac.add_argument("--probe", default=None)
    p_ac.add_argument("--notes", default="")
    p_ac.add_argument("--force", action="store_true")
    p_ac.set_defaults(func=cmd_assumption_create)

    p_al = asm_sub.add_parser("list", help="List assumptions")
    p_al.set_defaults(func=cmd_assumption_list)

    p_ash = asm_sub.add_parser("show", help="Show assumption, evidence, and reading")
    p_ash.add_argument("id")
    p_ash.add_argument("--json", action="store_true")
    p_ash.set_defaults(func=cmd_assumption_show)

    p_ag = asm_sub.add_parser("get", help="Read conditional value for composition")
    p_ag.add_argument("id")
    p_ag.add_argument("--raw", action="store_true")
    p_ag.set_defaults(func=cmd_assumption_get)

    p_aset = asm_sub.add_parser("set", help="Revise provisional value with history")
    p_aset.add_argument("id")
    p_aset.add_argument("--value", required=True, help="JSON scalar")
    p_aset.add_argument("--reason", required=True)
    p_aset.set_defaults(func=cmd_assumption_set)

    p_alp = asm_sub.add_parser("link-probe", help="Attach an evidence instrument")
    p_alp.add_argument("id")
    p_alp.add_argument("probe_id")
    p_alp.set_defaults(func=cmd_assumption_link_probe)

    p_alr = asm_sub.add_parser("link-run", help="Attach evidence without replacing the assumption")
    p_alr.add_argument("id")
    p_alr.add_argument("run_id")
    p_alr.set_defaults(func=cmd_assumption_link_run)

    p_aur = asm_sub.add_parser("unlink-run", help="Detach evidence run")
    p_aur.add_argument("id")
    p_aur.add_argument("run_id")
    p_aur.set_defaults(func=cmd_assumption_unlink_run)

    p_agr = asm_sub.add_parser("graduate", help="Replace assumption with an evidence-backed known")
    p_agr.add_argument("id")
    p_agr.add_argument("--as", dest="as_id", default=None)
    p_agr.set_defaults(func=cmd_assumption_graduate)

    p_ad = asm_sub.add_parser("delete", help="Delete assumption record")
    p_ad.add_argument("id")
    p_ad.set_defaults(func=cmd_assumption_delete)

    # --- calculations (inside-map composition; declared map values only) ---
    p_calc = sub.add_parser(
        "calculation",
        aliases=["calc"],
        help="Compose declared knowns and assumptions with validated Python",
    )
    calc_sub = p_calc.add_subparsers(dest="calculation_cmd", required=True)

    p_cc = calc_sub.add_parser("create", help="Scaffold a map-native calculation")
    p_cc.add_argument("id")
    p_cc.add_argument(
        "--profile",
        choices=["expression", "model"],
        default="expression",
    )
    p_cc.add_argument(
        "--input",
        action="append",
        required=True,
        metavar="NAME=known:ID|assumption:ID",
    )
    p_cc.add_argument("--type", choices=["number", "boolean"])
    p_cc.add_argument("--quantity")
    p_cc.add_argument(
        "--output",
        action="append",
        default=None,
        metavar="NAME=TYPE:QUANTITY[:…]",
        help=(
            "Declared model output (repeatable): number|boolean:Q[:UNIT] or "
            "relation:Y_Q:X_Q[:Y_UNIT[:X_UNIT]]"
        ),
    )
    p_cc.add_argument("--unit", default="")
    p_cc.add_argument(
        "--decimals",
        type=int,
        default=None,
        help="Presentation decimal places (raw value remains full precision)",
    )
    p_cc.add_argument("--purpose", default="")
    p_cc.add_argument("--force", action="store_true")
    p_cc.set_defaults(func=cmd_calculation_create)

    p_cv = calc_sub.add_parser("validate", help="Validate calculation package(s)")
    p_cv.add_argument("id", nargs="?", default=None)
    p_cv.set_defaults(func=cmd_calculation_validate)

    p_cr = calc_sub.add_parser("run", help="Resolve inputs and stamp a result")
    p_cr.add_argument("id")
    p_cr.set_defaults(func=cmd_calculation_run)

    p_cg = calc_sub.add_parser("get", help="Read latest result; refuses stale")
    p_cg.add_argument("id")
    p_cg.add_argument("--raw", action="store_true")
    p_cg.set_defaults(func=cmd_calculation_get)

    p_cs = calc_sub.add_parser("show", help="Show definition, history, and validation")
    p_cs.add_argument("id")
    p_cs.set_defaults(func=cmd_calculation_show)

    p_cl = calc_sub.add_parser("list", help="List calculations and freshness")
    p_cl.set_defaults(func=cmd_calculation_list)

    p_cd = calc_sub.add_parser("delete", help="Delete calculation package")
    p_cd.add_argument("id")
    p_cd.set_defaults(func=cmd_calculation_delete)

    # --- suites (composition recipes) ---
    p_suite = sub.add_parser(
        "suite",
        help="Ordered probe recipes (shared to; no domain plugins)",
    )
    suite_sub = p_suite.add_subparsers(dest="suite_cmd", required=True)

    p_sc = suite_sub.add_parser("create", help="Create a suite of ordered probes")
    p_sc.add_argument("id", help="Suite slug")
    p_sc.add_argument(
        "--probes",
        required=True,
        help="Comma-separated probe ids in order, e.g. a,b,c",
    )
    p_sc.add_argument(
        "--purpose",
        default="",
        help="Optional one-line purpose",
    )
    p_sc.add_argument(
        "--to",
        default=None,
        help="Optional default to (JSON / key=val) baked into the suite",
    )
    p_sc.add_argument("--to-file", default=None, help="Default to from JSON file")
    p_sc.add_argument("--force", action="store_true")
    p_sc.set_defaults(func=cmd_suite_create)

    p_sl = suite_sub.add_parser("list", help="List suites")
    p_sl.add_argument("--json", action="store_true")
    p_sl.set_defaults(func=cmd_suite_list)

    p_ss = suite_sub.add_parser("show", help="Show suite JSON")
    p_ss.add_argument("id")
    p_ss.set_defaults(func=cmd_suite_show)

    p_sr = suite_sub.add_parser(
        "run",
        help="Run each probe in order with shared --to (or suite default_to)",
    )
    p_sr.add_argument("id", help="Suite id")
    p_sr.add_argument(
        "--to",
        default=None,
        help="Shared target (overrides suite default_to)",
    )
    p_sr.add_argument("--to-file", default=None)
    p_sr.add_argument("--timeout", type=float, default=None)
    p_sr.add_argument("--dry-run", action="store_true")
    p_sr.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Do not stop suite when one probe fails",
    )
    p_sr.add_argument(
        "--strict-to",
        action="store_true",
        help="CI: fail leaf runs on to-schema warnings",
    )
    p_sr.add_argument(
        "--strict-status",
        action="store_true",
        help="CI: fail leaf runs on status-vocab warnings",
    )
    p_sr.add_argument("--json", action="store_true")
    p_sr.set_defaults(func=cmd_suite_run)

    p_sv = suite_sub.add_parser(
        "validate",
        help="Validate suite meta + level-1 each probe (no live survey)",
    )
    p_sv.add_argument("id", nargs="?", default=None, help="Suite id or omit for all")
    p_sv.add_argument("--json", action="store_true")
    p_sv.set_defaults(func=cmd_suite_validate)

    # --- knowns (typed anchors; first type: number) ---
    p_kn = sub.add_parser(
        "known",
        help="Typed anchors (number: mean±std from samples; n=1 cannot be high)",
    )
    kn_sub = p_kn.add_subparsers(dest="known_cmd", required=True)

    # Retired birth path — kept so agents get guidance, not argparse noise.
    # Accepts (and ignores) legacy flags; always errors → unknown graduate.
    p_kc = kn_sub.add_parser(
        "create",
        help="Retired — knowns are born via `terra unknown graduate`",
    )
    p_kc.add_argument("id", nargs="?", default=None)
    p_kc.add_argument("legacy_args", nargs=argparse.REMAINDER)
    p_kc.set_defaults(func=cmd_known_create)

    p_kset = kn_sub.add_parser(
        "set",
        help="Edit known metadata (claim/notes/unit/status …) — never values; "
        "evidence only enters via runs",
    )
    p_kset.add_argument("id", help="Known id")
    p_kset.add_argument("--claim", default=None, help="Rewrite the claim string")
    p_kset.add_argument("--notes", default=None)
    p_kset.add_argument("--note", default=None, help=argparse.SUPPRESS)
    p_kset.add_argument("--unit", default=None)
    p_kset.add_argument(
        "--quantity", default=None, help="Rename measured quantity (scalar types)"
    )
    p_kset.add_argument(
        "--status", default=None, help="Known status (same set as known status)"
    )
    p_kset.add_argument(
        "--confidence",
        default=None,
        help="Claim a confidence (blocked unless the sample ladder allows)",
    )
    p_kset.add_argument(
        "--from-run",
        dest="from_run",
        default=None,
        metavar="RUN_ID",
        help="Also link this run (same as known link-run)",
    )
    p_kset.add_argument(
        "--expression", default=None, help="Formula knowns: replace expression"
    )
    p_kset.add_argument(
        "--var",
        dest="vars",
        action="append",
        default=None,
        help=argparse.SUPPRESS,
    )
    # --value exists only so the freehand footgun gets the loud refusal
    p_kset.add_argument("--value", default=None, help=argparse.SUPPRESS)
    p_kset.add_argument("--type", dest="type", default=None, help=argparse.SUPPRESS)
    p_kset.set_defaults(func=cmd_known_set)

    p_kl = kn_sub.add_parser("list", help="List knowns")
    p_kl.add_argument("--json", action="store_true")
    p_kl.set_defaults(func=cmd_known_list)

    p_ks = kn_sub.add_parser("show", help="Show known + stats")
    p_ks.add_argument("id")
    p_ks.add_argument("--json", action="store_true")
    p_ks.set_defaults(func=cmd_known_show)

    p_kg = kn_sub.add_parser(
        "get",
        help="Read a known for consumption (loud if unbacked/stale/low-conf; "
        "stamps a consumer edge)",
    )
    p_kg.add_argument("id")
    p_kg.add_argument(
        "--raw", action="store_true", help="Print bare value only (shell-able)"
    )
    p_kg.add_argument(
        "--min-conf",
        dest="min_conf",
        default="low",
        choices=sorted(CONFIDENCE_SET),
        help="Refuse below this confidence (default low)",
    )
    p_kg.add_argument(
        "--allow-stale",
        dest="allow_stale",
        action="store_true",
        help="Read even if a dependency moved (recorded, not recommended)",
    )
    p_kg.add_argument(
        "--at",
        type=float,
        default=None,
        help="For relation knowns: evaluate F(x) at this x (linear interp; "
        "no extrapolation)",
    )
    p_kg.add_argument(
        "--allow-disagree",
        dest="allow_disagree",
        action="store_true",
        help="Read even while methods disagree (recorded, not recommended)",
    )
    p_kg.add_argument(
        "--allow-cohort-mismatch",
        dest="allow_cohort_mismatch",
        action="store_true",
        help="Read even while the known's cohort cites mixed solves "
        "(recorded, not recommended)",
    )
    p_kg.add_argument(
        "--allow-superseded",
        dest="allow_superseded",
        action="store_true",
        help="Read the historical value of a retired/superseded known "
        "(loudly flagged; never current truth)",
    )
    p_kg.add_argument(
        "--consumer",
        default=None,
        help="Reader identity (default: probe ctx / $TERRA_CONSUMER / tool:argv0)",
    )
    p_kg.set_defaults(func=cmd_known_get)

    p_kd = kn_sub.add_parser(
        "depend",
        help="Declare deps: --on known:<id> --on file:<relpath> "
        "(dep moves → this known goes stale)",
    )
    p_kd.add_argument("id")
    p_kd.add_argument(
        "--on",
        action="append",
        required=True,
        metavar="known:<id>|file:<relpath>",
        help="Dependency spec (repeatable)",
    )
    p_kd.set_defaults(func=cmd_known_depend)

    p_kgr = kn_sub.add_parser(
        "graph",
        help="Dependency graph for this map: files → knowns → knowns, "
        "stale + consumers inline",
    )
    p_kgr.add_argument("--human", action="store_true", help="Tree text output")
    p_kgr.set_defaults(func=cmd_known_graph)

    p_ktr = kn_sub.add_parser(
        "tree",
        help="One known: upstream chain, downstream dependents, consumers",
    )
    p_ktr.add_argument("id")
    p_ktr.add_argument("--human", action="store_true", help="Text output")
    p_ktr.set_defaults(func=cmd_known_tree)

    p_kas = kn_sub.add_parser(
        "accept-spread",
        help="Accept an irreducible cross-method spread as honest "
        "uncertainty (recorded; caps at med; band carried to consumers)",
    )
    p_kas.add_argument("id")
    p_kas.add_argument("--reason", required=True)
    p_kas.set_defaults(func=cmd_known_accept_spread)

    p_kt = kn_sub.add_parser(
        "tolerance",
        help="Declare method-agreement tolerance: --within 5%% (relative) "
        "or --within 0.5 (absolute)",
    )
    p_kt.add_argument("id")
    p_kt.add_argument("--within", required=True)
    p_kt.set_defaults(func=cmd_known_tolerance)

    p_kra = kn_sub.add_parser(
        "reaffirm",
        help="Verified-unchanged: re-stamp dep freshness with a recorded reason",
    )
    p_kra.add_argument("id")
    p_kra.add_argument("--reason", required=True)
    p_kra.set_defaults(func=cmd_known_reaffirm)

    p_klr = kn_sub.add_parser("link-run", help="Add a sample run; recompute n/mean/std")
    p_klr.add_argument("id")
    p_klr.add_argument("run_id")
    p_klr.add_argument("--primary", action="store_true")
    p_klr.set_defaults(func=cmd_known_link_run)

    p_kur = kn_sub.add_parser(
        "unlink-run",
        help="Remove a sample run; recompute stats / demote confidence if needed",
    )
    p_kur.add_argument("id")
    p_kur.add_argument("run_id")
    p_kur.set_defaults(func=cmd_known_unlink_run)

    # --- plans (above types: multi/sequence evidence dossiers) ---
    p_plan = sub.add_parser(
        "plan",
        help="Evidence plans above types (multi all | sequential prove A then B)",
    )
    plan_sub = p_plan.add_subparsers(dest="plan_cmd", required=True)

    p_pc = plan_sub.add_parser("create", help="Create multi or sequence evidence plan")
    p_pc.add_argument("id", help="Slug id")
    p_pc.add_argument("--claim", required=True, help="What the dossier asserts")
    p_pc.add_argument(
        "--mode",
        choices=["all", "sequence"],
        default="all",
        help="all = multi any-order; sequence = prove A then B",
    )
    p_pc.add_argument(
        "--leg",
        action="append",
        dest="legs",
        required=True,
        metavar="SPEC",
        help=(
            "Repeatable leg: id:type:quantity[:n=N][:conf=low|med|high]  "
            "e.g. --leg rcon:boolean:rcon_up --leg hostiles:number:hostile_count:n=3"
        ),
    )
    p_pc.add_argument(
        "--status",
        default="provisional",
        choices=sorted(KNOWN_STATUSES),
    )
    p_pc.add_argument("--notes", default="")
    p_pc.add_argument("--force", action="store_true")
    p_pc.set_defaults(func=cmd_plan_create)

    p_pl = plan_sub.add_parser("list", help="List plans on active map")
    p_pl.add_argument("--json", action="store_true")
    p_pl.set_defaults(func=cmd_plan_list)

    p_ps = plan_sub.add_parser("show", help="Show plan progress + legs")
    p_ps.add_argument("id")
    p_ps.add_argument("--json", action="store_true")
    p_ps.set_defaults(func=cmd_plan_show)

    p_plr = plan_sub.add_parser(
        "link-run", help="Attach a run to one leg (sequence enforces order)"
    )
    p_plr.add_argument("id")
    p_plr.add_argument("run_id")
    p_plr.add_argument("--leg", required=True, help="Leg id to fill")
    p_plr.add_argument("--primary", action="store_true")
    p_plr.set_defaults(func=cmd_plan_link_run)

    p_pur = plan_sub.add_parser("unlink-run", help="Detach a run from plan leg(s)")
    p_pur.add_argument("id")
    p_pur.add_argument("run_id")
    p_pur.add_argument("--leg", default=None, help="Only this leg")
    p_pur.set_defaults(func=cmd_plan_unlink_run)

    p_pp = plan_sub.add_parser(
        "promote",
        help="Raise confidence only when all legs satisfied",
    )
    p_pp.add_argument("id")
    p_pp.add_argument("confidence", choices=sorted(CONFIDENCE_SET))
    p_pp.add_argument(
        "--status",
        default=None,
        choices=sorted(KNOWN_STATUSES),
    )
    p_pp.set_defaults(func=cmd_plan_promote)

    p_pd = plan_sub.add_parser("delete", help="Delete plan from active map")
    p_pd.add_argument("id")
    p_pd.set_defaults(func=cmd_plan_delete)

    p_kdel = kn_sub.add_parser(
        "delete", help="Delete known record from active map"
    )
    p_kdel.add_argument("id")
    p_kdel.set_defaults(func=cmd_known_delete)

    p_ksup = kn_sub.add_parser(
        "supersede",
        help="Retire a wrong belief: keep as history, refuse as current "
        "(soft tombstone between set and delete)",
    )
    p_ksup.add_argument("id")
    p_ksup.add_argument(
        "--reason", required=True, help="Why it's retired (recorded)"
    )
    p_ksup.add_argument(
        "--by",
        default=None,
        help="Known id that replaces this one (must exist; shown in refusals)",
    )
    p_ksup.add_argument(
        "--refuted",
        action="store_true",
        help="Mark refuted (belief was wrong) rather than superseded "
        "(replaced by a better belief)",
    )
    p_ksup.set_defaults(func=cmd_known_supersede)

    p_kp = kn_sub.add_parser(
        "promote",
        help="Raise confidence only if sample ladder allows (blocks n=1 high)",
    )
    p_kp.add_argument("id")
    p_kp.add_argument(
        "confidence",
        choices=sorted(CONFIDENCE_SET),
        help="Target confidence",
    )
    p_kp.add_argument(
        "--status",
        default=None,
        choices=sorted(KNOWN_STATUSES),
        help="Optional status (default: active when med/high from provisional)",
    )
    p_kp.set_defaults(func=cmd_known_promote)

    p_kad = kn_sub.add_parser(
        "adopt",
        help="Promote a known one hop up the map tree (child -> parent map); "
        "copies live runs + stamps provenance; admission bar re-checked",
    )
    p_kad.add_argument("id")
    p_kad.add_argument(
        "--from",
        dest="from_map",
        required=True,
        help="Source map (destination is its parent)",
    )
    p_kad.set_defaults(func=cmd_known_adopt)

    p_kst = kn_sub.add_parser("status", help="Set known status")
    p_kst.add_argument("id")
    p_kst.add_argument("status", choices=sorted(KNOWN_STATUSES))
    p_kst.add_argument("--notes", default=None)
    p_kst.set_defaults(func=cmd_known_status)

    p_kv = kn_sub.add_parser("validate", help="Validate known(s); recompute stats")
    p_kv.add_argument("id", nargs="?", default=None)
    p_kv.add_argument("--json", action="store_true")
    p_kv.set_defaults(func=cmd_known_validate)

    # --- brief (SSOT design request) ---
    p_br = sub.add_parser(
        "brief",
        help="SSOT design request (needs, deliverables, change control)",
    )
    br_sub = p_br.add_subparsers(dest="brief_cmd", required=True)

    p_bi = br_sub.add_parser("init", help="Create project brief")
    p_bi.add_argument("--title", required=True)
    p_bi.add_argument("--mission", default="")
    p_bi.add_argument("--force", action="store_true")
    p_bi.set_defaults(func=cmd_brief_init)

    p_bs = br_sub.add_parser("show", help="Show brief (JSON default)")
    p_bs.add_argument("--human", action="store_true")
    p_bs.add_argument("--full", action="store_true", help="Include proposals")
    # Accepted no-op: JSON is already this verb's default. Refusing the
    # flag made argparse exit 2 with EMPTY stdout, which downstream reads
    # as "corrupt JSON", not "bad flag" — it cost a lead several turns on
    # `sitrep` before the same trap was found on five sibling verbs.
    p_bs.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_bs.set_defaults(func=cmd_brief_show)

    p_bset = br_sub.add_parser("set", help="Update brief fields (bumps version)")
    p_bset.add_argument("--title", default=None)
    p_bset.add_argument("--mission", default=None)
    p_bset.add_argument(
        "--status",
        default=None,
        choices=["draft", "active", "frozen", "archived"],
    )
    p_bset.add_argument("--need", action="append", dest="needs", default=None)
    p_bset.add_argument(
        "--non-goal", action="append", dest="non_goals", default=None
    )
    p_bset.add_argument(
        "--deliverable", action="append", dest="deliverables", default=None
    )
    p_bset.add_argument(
        "--enabler",
        action="append",
        dest="enablers",
        default=None,
        metavar="ID:TITLE[:PATH]",
        help="Internal tooling/harness (not a customer deliverable)",
    )
    p_bset.add_argument(
        "--replace-lists",
        action="store_true",
        help="Replace needs/non_goals/deliverables/enablers instead of append",
    )
    p_bset.add_argument(
        "--budget-points",
        type=int,
        default=None,
        dest="budget_points",
        help="Total project effort budget (task points: low=3 medium=8 high=21)",
    )
    p_bset.add_argument(
        "--clear-budget-points",
        action="store_true",
        dest="clear_budget_points",
        help="Clear budget_points (set to null)",
    )
    p_bset.add_argument(
        "--budget-notes",
        default=None,
        dest="budget_notes",
        help="Human note for budget (horizon, team size, …)",
    )
    p_bset.set_defaults(func=cmd_brief_set)

    p_bph = br_sub.add_parser("phase", help="Add a phase name to the brief")
    p_bph.add_argument("id", help="Phase slug")
    p_bph.add_argument("--title", default="")
    p_bph.set_defaults(func=cmd_brief_phase)

    p_bpc = br_sub.add_parser(
        "phase-close",
        help="Declare a phase CLOSED (or --reopen). Authority act — closure "
        "is declared, exit-readiness is computed.",
    )
    p_bpc.add_argument("id", help="Declared phase id")
    p_bpc.add_argument(
        "--reason",
        required=True,
        help="On what basis — a closure with no stated basis is an "
        "unexplained authority act",
    )
    p_bpc.add_argument("--reopen", action="store_true", help="Re-open instead")
    p_bpc.add_argument("--human", action="store_true")
    p_bpc.set_defaults(func=cmd_brief_phase_close)

    p_bp = br_sub.add_parser("propose", help="Queue a change (does not apply)")
    p_bp.add_argument("--summary", required=True)
    p_bp.add_argument("--need", default=None)
    p_bp.add_argument("--non-goal", default=None, dest="non_goal")
    p_bp.add_argument("--deliverable", default=None)
    p_bp.add_argument(
        "--enabler",
        default=None,
        metavar="ID:TITLE[:PATH]",
        help="Propose adding an enabler (tooling/harness)",
    )
    p_bp.add_argument("--mission", default=None)
    p_bp.set_defaults(func=cmd_brief_propose)

    p_ba = br_sub.add_parser("accept", help="Accept a proposal (bumps version)")
    p_ba.add_argument("id", help="Proposal id")
    p_ba.set_defaults(func=cmd_brief_accept)

    p_brj = br_sub.add_parser("reject", help="Reject a proposal")
    p_brj.add_argument("id", help="Proposal id")
    p_brj.set_defaults(func=cmd_brief_reject)

    p_ben = br_sub.add_parser(
        "enabler",
        help="Update enabler status (needed|building|ready|graduated|abandoned)",
    )
    p_ben.add_argument("id", help="Enabler id")
    p_ben.add_argument(
        "status",
        choices=["needed", "building", "ready", "graduated", "abandoned"],
    )
    p_ben.add_argument("--path", default=None, help="Path to tooling in repo")
    p_ben.add_argument(
        "--graduates-to",
        default=None,
        dest="graduates_to",
        help="Cartograph widget/blueprint id if extracted",
    )
    p_ben.add_argument("--notes", default=None)
    p_ben.set_defaults(func=cmd_brief_enabler)

    # --- design (stable baseline: graduated knowns + attached artifacts) ---
    p_dsn = sub.add_parser(
        "design",
        help="Stable design baseline: admit knowns as params, attach "
        "deliverable files, check goes red when anything moves",
    )
    dsn_sub = p_dsn.add_subparsers(dest="design_cmd", required=True)

    p_da = dsn_sub.add_parser(
        "add",
        help="Admit a GLOBAL-map known (>=med, backed, agreeing, fresh)",
    )
    p_da.add_argument("known_id")
    p_da.add_argument(
        "--as", dest="as_name", default=None, help="Param name (default: known id)"
    )
    p_da.add_argument("--force", action="store_true", help="Replace existing param")
    p_da.set_defaults(func=cmd_design_add)

    p_drf = dsn_sub.add_parser(
        "refresh", help="Re-pin a param after its known legitimately moved"
    )
    p_drf.add_argument("name")
    p_drf.set_defaults(func=cmd_design_refresh)

    p_drm = dsn_sub.add_parser("remove", help="Remove a param (refuses if artifacts use it)")
    p_drm.add_argument("name")
    p_drm.set_defaults(func=cmd_design_remove)

    p_dg = dsn_sub.add_parser(
        "get", help="Read a design value (live known, admission bar enforced)"
    )
    p_dg.add_argument("name")
    p_dg.add_argument("--raw", action="store_true")
    p_dg.add_argument("--consumer", default=None)
    p_dg.set_defaults(func=cmd_design_get)

    p_dat = dsn_sub.add_parser(
        "attach",
        help="Stamp a deliverable file against the design params it was built from",
    )
    p_dat.add_argument("path")
    p_dat.add_argument(
        "--uses", required=True, help="Comma-separated design param names"
    )
    p_dat.set_defaults(func=cmd_design_attach)

    p_ddt = dsn_sub.add_parser("detach", help="Remove an artifact stamp")
    p_ddt.add_argument("path")
    p_ddt.set_defaults(func=cmd_design_detach)

    p_dc = dsn_sub.add_parser(
        "check",
        help="Design health (exit 1 if any param/artifact is red); "
        "`show` is an alias",
    )
    p_dc.add_argument("--human", action="store_true")
    p_dc.set_defaults(func=cmd_design_check)

    p_ds = dsn_sub.add_parser("show", help="Alias of check (the design card)")
    p_ds.add_argument("--human", action="store_true")
    p_ds.set_defaults(func=cmd_design_check)

    # --- gate (mechanical debt collector) ---
    p_coh = sub.add_parser(
        "cohort",
        help="Coupled knowns from one solve — valid only as a set "
        "(consistency computed, gate-enforced)",
    )
    coh_sub = p_coh.add_subparsers(dest="cohort_cmd", required=True)

    p_cc = coh_sub.add_parser(
        "create", help="Declare a coupled set of existing knowns"
    )
    p_cc.add_argument("id", help="Cohort slug")
    p_cc.add_argument(
        "--members",
        required=True,
        metavar="K1,K2",
        help="Member known ids (must exist on this map)",
    )
    p_cc.add_argument("--title", default="")
    p_cc.add_argument("--json", action="store_true")
    p_cc.set_defaults(func=cmd_cohort_create)

    p_ca = coh_sub.add_parser("add", help="Add a known to a cohort")
    p_ca.add_argument("id", help="Cohort slug")
    p_ca.add_argument("known_id", help="Known id to add")
    p_ca.set_defaults(func=cmd_cohort_add)

    p_cs = coh_sub.add_parser(
        "set", help="Edit title and/or replace the complete member set"
    )
    p_cs.add_argument("id", help="Cohort slug")
    p_cs.add_argument(
        "--members",
        metavar="K1,K2",
        help="Replacement member known ids (must exist on this map)",
    )
    p_cs.add_argument("--title", default=None)
    p_cs.add_argument("--json", action="store_true")
    p_cs.set_defaults(func=cmd_cohort_set)

    p_cd = coh_sub.add_parser(
        "delete", help="Delete a cohort declaration; member knowns are preserved"
    )
    p_cd.add_argument("id", help="Cohort slug")
    p_cd.add_argument("--json", action="store_true")
    p_cd.set_defaults(func=cmd_cohort_delete)

    p_cl = coh_sub.add_parser("list", help="List cohorts with consistency")
    p_cl.add_argument("--json", action="store_true")
    p_cl.set_defaults(func=cmd_cohort_list)

    p_ck = coh_sub.add_parser(
        "check",
        help="Computed consistency: members must share identical live runs "
        "(exit 1 when mixed)",
    )
    p_ck.add_argument("id", help="Cohort slug")
    p_ck.add_argument("--json", action="store_true")
    p_ck.set_defaults(func=cmd_cohort_check)

    p_clr = coh_sub.add_parser(
        "link-run",
        help="Fan one solve's run out to every member (the whole-family refresh)",
    )
    p_clr.add_argument("id", help="Cohort slug")
    p_clr.add_argument("run_id", help="Run id from the converged solve")
    p_clr.add_argument("--json", action="store_true")
    p_clr.set_defaults(func=cmd_cohort_link_run)

    p_cad = coh_sub.add_parser(
        "adopt",
        help="Adopt a whole cohort one hop up the map tree — coupled knowns "
        "move as a set (refused while inconsistent)",
    )
    p_cad.add_argument("id", help="Cohort slug")
    p_cad.add_argument(
        "--from",
        dest="from_map",
        required=True,
        help="Source map (destination is its parent)",
    )
    p_cad.set_defaults(func=cmd_cohort_adopt)

    p_gate = sub.add_parser(
        "gate",
        help="Mechanical gate: exit 0 iff no blocking unknowns, stale/unbacked "
        "knowns, or incomplete plans (all maps)",
    )
    p_gate.add_argument(
        "--map",
        dest="map_scope",
        default=None,
        help="Check one map only (default: every map — debt cannot hide)",
    )
    p_gate.add_argument("--human", action="store_true", help="Prose output")
    # Accepted no-op: JSON is already this verb's default. Refusing the
    # flag made argparse exit 2 with EMPTY stdout, which downstream reads
    # as "corrupt JSON", not "bad flag" — it cost a lead several turns on
    # `sitrep` before the same trap was found on five sibling verbs.
    p_gate.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_gate.set_defaults(func=cmd_gate)

    p_sit = sub.add_parser(
        "sitrep",
        help="ONE-CALL orientation: brief + route + budget + map + gate "
        "digest. Start every session here instead of 5 separate reads.",
        description=(
            "Composite read that replaces the `route status` + `route budget` "
            "+ `map status` + `known list` + `gate` opening sequence with a "
            "single command. Returns a DIGEST (counts, merged attention, "
            "next actions, gate verdict) — not the full task/known tables. "
            "ALWAYS exits 0 on success, even when the gate fails, so it is "
            "safe in a chained shell call; read data.gate.ok for the verdict."
        ),
    )
    p_sit.add_argument(
        "--full",
        action="store_true",
        help="Remove list caps (default truncates and reports what it dropped)",
    )
    p_sit.add_argument(
        "--map",
        dest="map_scope",
        default=None,
        help="Detail scope for map counts (attention always scans all maps)",
    )
    # Accepted no-op. JSON is already the default here, but EVERY other read
    # verb takes --json, so an agent reaching for it got argparse rc=2 and an
    # EMPTY stdout — which reads downstream as "the JSON is corrupt", not "bad
    # flag". Cost a lead several turns on 2026-07-27. Consistency beats purity.
    p_sit.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_sit.add_argument("--human", action="store_true", help="Prose output")
    p_sit.set_defaults(func=cmd_sitrep)

    # --- route (task DAG) ---
    p_rt = sub.add_parser(
        "route",
        help="Task DAG for the main agent (walks the brief)",
    )
    rt_sub = p_rt.add_subparsers(dest="route_cmd", required=True)

    p_ri = rt_sub.add_parser("init", help="Create empty route")
    p_ri.add_argument("--force", action="store_true")
    p_ri.set_defaults(func=cmd_route_init)

    p_rst = rt_sub.add_parser("status", help="Counts + next + blocked (JSON)")
    p_rst.add_argument("--human", action="store_true")
    # Accepted no-op: JSON is already this verb's default. Refusing the
    # flag made argparse exit 2 with EMPTY stdout, which downstream reads
    # as "corrupt JSON", not "bad flag" — it cost a lead several turns on
    # `sitrep` before the same trap was found on five sibling verbs.
    p_rst.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_rst.set_defaults(func=cmd_route_status)

    p_rlog = rt_sub.add_parser(
        "log",
        help="Chronological history: completions with evidence + blocks (JSON)",
    )
    p_rlog.add_argument(
        "--limit", type=int, default=0, help="Show only the last N events"
    )
    p_rlog.add_argument(
        "--task",
        default=None,
        help="One task's history (else every task). Without this a lead had "
        "to hand-parse route.json — the shadow-tracker habit this verb "
        "exists to remove.",
    )
    p_rlog.add_argument("--human", action="store_true")
    # Accepted no-op: JSON is already this verb's default. Refusing the
    # flag made argparse exit 2 with EMPTY stdout, which downstream reads
    # as "corrupt JSON", not "bad flag" — it cost a lead several turns on
    # `sitrep` before the same trap was found on five sibling verbs.
    p_rlog.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_rlog.set_defaults(func=cmd_route_log)

    p_rbgt = rt_sub.add_parser(
        "budget",
        help="Budget rollup only (total/planned/done/unallocated + task weights)",
    )
    p_rbgt.add_argument("--human", action="store_true")
    # Accepted no-op: JSON is already this verb's default. Refusing the
    # flag made argparse exit 2 with EMPTY stdout, which downstream reads
    # as "corrupt JSON", not "bad flag" — it cost a lead several turns on
    # `sitrep` before the same trap was found on five sibling verbs.
    p_rbgt.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_rbgt.set_defaults(func=cmd_route_budget)

    p_rse = rt_sub.add_parser(
        "set-effort",
        help="Re-bucket a task (low|medium|high). May exceed budget_points.",
    )
    p_rse.add_argument("id", help="Task slug")
    p_rse.add_argument(
        "--bucket",
        default=None,
        choices=["low", "medium", "high"],
        help="low=3 implement, medium=8 validate, high=21 explore",
    )
    p_rse.add_argument(
        "--points",
        type=int,
        default=None,
        help="3, 8, or 21 (sets bucket if --bucket omitted)",
    )
    p_rse.add_argument("--human", action="store_true")
    p_rse.set_defaults(func=cmd_route_set_effort)

    p_rpr = rt_sub.add_parser(
        "prioritize",
        help=(
            "Re-rank tasks p0..p3 (WHICH work). Orthogonal to --bucket "
            "(HOW MUCH effort); never moves the budget, no unlock needed."
        ),
    )
    p_rpr.add_argument("ids", nargs="+", help="Task slug(s)")
    p_rpr.add_argument(
        "--priority",
        required=True,
        choices=list(TASK_PRIORITIES),
        help=(
            "p0=spine, cannot finish without it; p1=required this phase; "
            "p2=normal backlog (default); p3=deferred, kept as record"
        ),
    )
    p_rpr.add_argument(
        "--reason",
        default=None,
        help="Recorded on the task as a priority event (route log)",
    )
    p_rpr.add_argument("--human", action="store_true")
    p_rpr.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_rpr.set_defaults(func=cmd_route_prioritize)

    p_rph = rt_sub.add_parser(
        "phases",
        help=(
            "Lifecycle view: per-phase open/done/blocked/unreachable, "
            "current phase, and exit readiness"
        ),
    )
    p_rph.add_argument("--human", action="store_true")
    p_rph.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_rph.set_defaults(func=cmd_route_phases)

    p_rrp = rt_sub.add_parser(
        "rephase", help="Move task(s) into a lifecycle phase"
    )
    p_rrp.add_argument("ids", nargs="+")
    p_rrp.add_argument("--phase", required=True)
    p_rrp.add_argument("--reason", default=None)
    p_rrp.add_argument("--human", action="store_true")
    p_rrp.set_defaults(func=cmd_route_rephase)

    p_rsa = rt_sub.add_parser(
        "sector-add",
        help="Reserve a point provision (sector) to explode into tasks later",
    )
    p_rsa.add_argument("id", help="Sector slug")
    p_rsa.add_argument("--title", required=True)
    p_rsa.add_argument(
        "--points",
        type=int,
        required=True,
        help="Reserved points from project budget (any int >= 0)",
    )
    p_rsa.add_argument("--notes", default="")
    p_rsa.add_argument("--human", action="store_true")
    p_rsa.set_defaults(func=cmd_route_sector_add)

    p_rss = rt_sub.add_parser(
        "sector-set",
        help="Update sector reserve/title (plan must be unlocked to change points)",
    )
    p_rss.add_argument("id", help="Sector slug")
    p_rss.add_argument("--points", type=int, default=None, help="New reserved_points")
    p_rss.add_argument("--title", default=None)
    p_rss.add_argument("--notes", default=None)
    p_rss.add_argument("--human", action="store_true")
    p_rss.set_defaults(func=cmd_route_sector_set)

    p_rlp = rt_sub.add_parser(
        "lock-plan",
        help="Lock plan_* baseline (set-effort only changes working effort)",
    )
    p_rlp.add_argument("--human", action="store_true")
    p_rlp.set_defaults(func=cmd_route_lock_plan)

    p_rul = rt_sub.add_parser(
        "unlock-plan",
        help="Unlock plan baseline (requires --confirm after warning)",
    )
    p_rul.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually unlock (without it, prints warning and fails)",
    )
    p_rul.add_argument("--human", action="store_true")
    p_rul.set_defaults(func=cmd_route_unlock_plan)

    p_rn = rt_sub.add_parser("next", help="Next pickable / in-progress tasks")
    p_rn.add_argument("--limit", type=int, default=5)
    p_rn.add_argument(
        "--priority",
        default=None,
        choices=list(TASK_PRIORITIES),
        help="Only tasks at this priority (default: all, sorted p0 first)",
    )
    p_rn.add_argument(
        "--phase",
        default=None,
        help="Only tasks in this phase (default: all phases)",
    )
    p_rn.add_argument("--human", action="store_true")
    # Accepted no-op: JSON is already this verb's default. Refusing the
    # flag made argparse exit 2 with EMPTY stdout, which downstream reads
    # as "corrupt JSON", not "bad flag" — it cost a lead several turns on
    # `sitrep` before the same trap was found on five sibling verbs.
    p_rn.add_argument(
        "--json",
        action="store_true",
        help="Accepted no-op — JSON is already the default output",
    )
    p_rn.set_defaults(func=cmd_route_next)

    p_ra = rt_sub.add_parser("add", help="Add a task")
    p_ra.add_argument("id", help="Task slug")
    p_ra.add_argument("--title", required=True)
    p_ra.add_argument("--phase", default="")
    p_ra.add_argument(
        "--dep",
        action="append",
        dest="deps",
        default=None,
        help="Dependency task id (repeatable)",
    )
    p_ra.add_argument(
        "--skill",
        default="any",
        choices=sorted(
            {
                "terra-map",
                "terra-probe",
                "cg-plan",
                "cg-create",
                "cg-blueprint",
                "deliverable",
                "tooling",
                "any",
            }
        ),
        help="tooling=build enablers/harnesses; deliverable=customer pack",
    )
    p_ra.add_argument(
        "--role",
        default="any",
        choices=sorted(
            {"survey", "enabler", "deliverable", "orchestration", "any"}
        ),
        help="enabler = internal means of production; deliverable = brief output",
    )
    p_ra.add_argument(
        "--enabler",
        dest="enabler_id",
        default=None,
        help="Link task to brief.enablers[].id",
    )
    p_ra.add_argument(
        "--bucket",
        default=None,
        choices=["low", "medium", "high"],
        help="Effort bucket: low=3 (implement), medium=8 (validate), high=21 (explore)",
    )
    p_ra.add_argument(
        "--points",
        type=int,
        default=None,
        help="Task points (must be 3, 8, or 21; sets bucket if --bucket omitted)",
    )
    p_ra.add_argument(
        "--sector",
        dest="sector_id",
        default=None,
        help="Draw from a sector provision (required to add tasks when plan is locked)",
    )
    p_ra.add_argument(
        "--accept",
        action="append",
        dest="acceptance",
        default=None,
        help="Acceptance criterion (repeatable)",
    )
    p_ra.add_argument(
        "--priority",
        default=None,
        choices=list(TASK_PRIORITIES),
        help=(
            "WHICH work (default p2). NOT effort — that is --bucket. "
            "p0=spine; p1=this phase; p2=backlog; p3=deferred"
        ),
    )
    p_ra.add_argument("--map", dest="task_map", default=None)
    p_ra.set_defaults(func=cmd_route_add)

    p_rstt = rt_sub.add_parser("start", help="Mark task in_progress")
    p_rstt.add_argument("id")
    p_rstt.add_argument(
        "--agent",
        default=None,
        help="Owner id claiming this task (attributes a stranded lead)",
    )
    p_rstt.set_defaults(func=cmd_route_start)

    p_rhb = rt_sub.add_parser(
        "heartbeat",
        help="Refresh an in_progress task's liveness stamp (I'm still alive)",
    )
    p_rhb.add_argument("id")
    p_rhb.add_argument(
        "--agent",
        default=None,
        help="Owner id (re-asserts ownership on the ping)",
    )
    p_rhb.set_defaults(func=cmd_route_heartbeat)

    p_rc = rt_sub.add_parser(
        "complete",
        help="Mark task done (claim-shaped tasks need --run/--known refs)",
    )
    p_rc.add_argument("id")
    p_rc.add_argument("--evidence", default=None, help="Prose note (optional)")
    p_rc.add_argument(
        "--run",
        action="append",
        dest="run_refs",
        default=None,
        metavar="RUN_ID",
        help="Cite a stamped run (validated: exists, not voided; repeatable)",
    )
    p_rc.add_argument(
        "--known",
        action="append",
        dest="known_refs",
        default=None,
        metavar="KNOWN_ID",
        help="Cite a known (validated: backed, methods not disagreeing; repeatable)",
    )
    p_rc.add_argument(
        "--freehand",
        default=None,
        help="Complete a claim-shaped task without map evidence (reason recorded)",
    )
    p_rc.add_argument(
        "--skip-gate",
        dest="skip_gate",
        default=None,
        help="Override a failing gate on a deliverable task (reason recorded)",
    )
    p_rc.set_defaults(func=cmd_route_complete)

    p_rb = rt_sub.add_parser("block", help="Block a task")
    p_rb.add_argument("id")
    p_rb.add_argument("--reason", required=True)
    p_rb.set_defaults(func=cmd_route_block)

    p_rcan = rt_sub.add_parser(
        "cancel",
        help="Retire a task that should never be worked (dead premise, wrong "
        "object, superseded). NOT `done` — asserts no outcome.",
    )
    p_rcan.add_argument("id")
    p_rcan.add_argument(
        "--reason",
        required=True,
        help="Why it was never valid — preserved, so the next planner does "
        "not re-derive it",
    )
    p_rcan.add_argument(
        "--force",
        action="store_true",
        help="Cancel even while in_progress with a live heartbeat. VERIFY the "
        "owner is actually dead first — this destroys work that may be running.",
    )
    p_rcan.set_defaults(func=cmd_route_cancel)

    p_rub = rt_sub.add_parser("unblock", help="Unblock a task")
    p_rub.add_argument("id")
    p_rub.set_defaults(func=cmd_route_unblock)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    # Apply --map before any path resolution in the command
    if getattr(args, "map_id", None):
        try:
            set_active_map_id(args.map_id)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
