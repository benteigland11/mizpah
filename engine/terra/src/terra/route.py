"""Route — task DAG for the main agent (Gantt source of truth).

Walks the **brief**. Does not store world truth (that's the map).
Does not store multi-leg belief recipes (that's evidence plans).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import ensure_map_store, route_path, terra_root

ROUTE_SCHEMA_VERSION = 1
TASK_STATUSES = frozenset(
    {"ready", "in_progress", "blocked", "done", "cancelled"}
)
SKILLS = frozenset(
    {
        "terra-map",
        "terra-probe",
        "cg-plan",
        "cg-create",
        "cg-blueprint",
        "deliverable",  # produce brief.deliverables
        "tooling",  # build brief.enablers (harnesses, print pipelines)
        "any",
    }
)
# Optional task.role for clarity (enabler work vs customer deliverable)
TASK_ROLES = frozenset({"survey", "enabler", "deliverable", "orchestration", "any"})
# Effort buckets: how open the search space is (points fixed)
TASK_BUCKETS = frozenset({"low", "medium", "high"})
BUCKET_POINTS: dict[str, int] = {"low": 3, "medium": 8, "high": 21}
POINTS_BUCKET: dict[int, str] = {v: k for k, v in BUCKET_POINTS.items()}

# Priority: WHICH work, independent of HOW MUCH effort (bucket).
# Deliberately p0..p3 and NOT low/medium/high — those words already mean the
# effort bucket, and a `--bucket high` typed when `--priority p0` was meant is
# a silent wrong-field write that nothing downstream would catch.
TASK_PRIORITIES: tuple[str, ...] = ("p0", "p1", "p2", "p3")
DEFAULT_PRIORITY = "p2"
PRIORITY_RANK: dict[str, int] = {p: i for i, p in enumerate(TASK_PRIORITIES)}
PRIORITY_MEANING: dict[str, str] = {
    "p0": "spine — the program cannot finish without this",
    "p1": "required for the current phase",
    "p2": "normal backlog (default)",
    "p3": "deferred — kept as record, not scheduled",
}
# Computed on every load from deps+status — NEVER canonical, never persisted.
# See save_route for why writing these to disk was actively harmful.
_BASELINE_KEY = "_loaded_sha256"


class ConcurrentRouteWrite(RuntimeError):
    """Another agent wrote route.json between this load and this save."""


DERIVED_TASK_FIELDS = frozenset(
    {"pickable", "waiting_on", "waiting_on_cancelled", "unreachable_via"}
)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def declared_phases(project_root: Path) -> list[dict[str, Any]]:
    """Ordered phases from the brief. Empty when the project declares none.

    Order is the list order — a lifecycle implies sequence, and `brief phase`
    appends, so index IS the intended progression.
    """
    try:
        from .brief import load_brief

        rec = load_brief(project_root)
    except (FileNotFoundError, ValueError, OSError):
        return []
    out = []
    for p in rec.get("phases") or []:
        if isinstance(p, dict) and p.get("id"):
            out.append(
                {
                    "id": p["id"],
                    "title": p.get("title") or p["id"],
                    # legacy phases predate status; absent == open
                    "status": p.get("status") or "open",
                    "closed_reason": p.get("closed_reason"),
                }
            )
    return out


def phase_rollup(project_root: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-phase lifecycle state: can we exit this phase, and where are we?

    `task.phase` was a write-only string — stored, echoed in route_log, used
    nowhere else — while `brief.phases` was a separate list with its own verb.
    Two half-features that never met, which is why almost every task left it
    blank: it did nothing, so nobody filled it.

    Exit criterion: a phase is exit-ready when it holds at least one task and
    every task is done or cancelled. UNREACHABLE tasks (stranded on a cancelled
    dep) are counted separately and BLOCK exit-readiness — otherwise a phase
    full of dead routes reads as "almost done, just waiting" forever, which is
    exactly the failure that hid a dead spine for weeks.
    """
    declared = declared_phases(project_root)
    order = [p["id"] for p in declared]
    titles = {p["id"]: p["title"] for p in declared}
    closed = {p["id"]: p["status"] == "closed" for p in declared}
    close_reason = {p["id"]: p.get("closed_reason") for p in declared}

    rows: dict[str, dict[str, Any]] = {}

    def row(pid: str) -> dict[str, Any]:
        if pid not in rows:
            rows[pid] = {
                "id": pid,
                "title": titles.get(pid, pid),
                "declared": pid in titles,
                "closed": bool(closed.get(pid)),
                "closed_reason": close_reason.get(pid),
                "open": 0,
                "done": 0,
                "cancelled": 0,
                "blocked": 0,
                "in_progress": 0,
                "unreachable": 0,
                "points_open": 0,
                "by_priority_open": {p: 0 for p in TASK_PRIORITIES},
            }
        return rows[pid]

    for pid in order:
        row(pid)

    unphased = 0
    for t in tasks:
        pid = (t.get("phase") or "").strip()
        if not pid:
            if t.get("status") not in ("done", "cancelled"):
                unphased += 1
            continue
        r = row(pid)
        st = t.get("status")
        if st == "done":
            r["done"] += 1
        elif st == "cancelled":
            r["cancelled"] += 1
        else:
            r["open"] += 1
            r["points_open"] += _task_points(t)
            r["by_priority_open"][t.get("priority") or DEFAULT_PRIORITY] += 1
            if st == "blocked":
                r["blocked"] += 1
            if st == "in_progress":
                r["in_progress"] += 1
            if t.get("waiting_on_cancelled") or t.get("unreachable_via"):
                r["unreachable"] += 1

    for r in rows.values():
        total = r["open"] + r["done"] + r["cancelled"]
        r["total"] = total
        r["exit_ready"] = bool(total) and r["open"] == 0
        r["exit_blockers"] = r["open"]

    # Current phase = first DECLARED, NOT-CLOSED phase. Closure is a declared
    # decision; exit-readiness is a computed fact. We advance on the DECISION,
    # because a phase whose work predates tagging shows zero tasks and would
    # otherwise pin `current` to an empty shell forever. Undeclared phases
    # (free-text drift) can never become current — a typo must not silently
    # redefine where the program is.
    current = None
    for pid in order:
        if not rows[pid]["closed"]:
            current = pid
            break

    ordered = [rows[p] for p in order] + [
        r for pid, r in rows.items() if pid not in titles
    ]
    return {
        "declared": order,
        "current": current,
        "phases": ordered,
        "unphased_open": unphased,
        "undeclared_used": [pid for pid in rows if pid not in titles],
    }


def priority_rank(task: dict[str, Any]) -> int:
    """Sort key. Unset/garbage priority sorts as the default, never first."""
    return PRIORITY_RANK.get(task.get("priority") or DEFAULT_PRIORITY, PRIORITY_RANK[DEFAULT_PRIORITY])


def points_for_bucket(bucket: str) -> int:
    if bucket not in TASK_BUCKETS:
        raise ValueError(
            f"bucket must be one of {sorted(TASK_BUCKETS)} "
            f"(points: low=3 medium=8 high=21)"
        )
    return BUCKET_POINTS[bucket]


def resolve_bucket_points(
    *,
    bucket: str | None = None,
    points: int | None = None,
) -> tuple[str | None, int | None]:
    """Return (bucket, points). Either, both, or neither may be set."""
    if bucket is None and points is None:
        return None, None
    if bucket is not None and points is not None:
        expected = points_for_bucket(bucket)
        if int(points) != expected:
            raise ValueError(
                f"bucket {bucket!r} is {expected} points, got points={points}"
            )
        return bucket, expected
    if bucket is not None:
        return bucket, points_for_bucket(bucket)
    # points only
    p = int(points)  # type: ignore[arg-type]
    if p not in POINTS_BUCKET:
        raise ValueError(
            f"points must be one of {sorted(POINTS_BUCKET)} "
            f"(low=3 medium=8 high=21), got {p}"
        )
    return POINTS_BUCKET[p], p


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def default_route(*, brief_version: int | None = None) -> dict[str, Any]:
    return {
        "schema_version": ROUTE_SCHEMA_VERSION,
        "id": "route",
        "brief_version": brief_version,
        "plan_locked": False,
        "plan_locked_at": None,
        # Reserved effort pools to explode into tasks later (provisions)
        "sectors": [],
        "tasks": [],
        "created_at": _now(),
        "updated_at": _now(),
    }


def validate_route(data: Any) -> list[str]:
    blocks: list[str] = []
    if not isinstance(data, dict):
        return ["route must be a JSON object"]
    if data.get("schema_version") != ROUTE_SCHEMA_VERSION:
        blocks.append(
            f"schema_version must be {ROUTE_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )
    if "plan_locked" in data and data["plan_locked"] is not None:
        if not isinstance(data["plan_locked"], bool):
            blocks.append("plan_locked must be a bool")
    sector_ids: set[str] = set()
    if "sectors" in data and data["sectors"] is not None:
        if not isinstance(data["sectors"], list):
            blocks.append("sectors must be a list")
        else:
            for i, s in enumerate(data["sectors"]):
                if not isinstance(s, dict):
                    blocks.append(f"sectors[{i}] must be an object")
                    continue
                sid = s.get("id")
                if not isinstance(sid, str) or not _SLUG_RE.match(sid):
                    blocks.append(f"sectors[{i}].id must be a slug")
                elif sid in sector_ids:
                    blocks.append(f"duplicate sector id {sid!r}")
                else:
                    sector_ids.add(sid)
                rp = s.get("reserved_points")
                if not isinstance(rp, int) or isinstance(rp, bool) or rp < 0:
                    blocks.append(
                        f"sectors[{i}].reserved_points must be int >= 0"
                    )
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        blocks.append("tasks must be a list")
        return blocks
    ids: set[str] = set()
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            blocks.append(f"tasks[{i}] must be an object")
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not _SLUG_RE.match(tid):
            blocks.append(f"tasks[{i}].id must be a slug")
        elif tid in ids:
            blocks.append(f"duplicate task id {tid!r}")
        else:
            ids.add(tid)
        if t.get("status") not in TASK_STATUSES:
            blocks.append(
                f"tasks[{i}].status must be one of {sorted(TASK_STATUSES)}"
            )
        deps = t.get("deps") or []
        if not isinstance(deps, list):
            blocks.append(f"tasks[{i}].deps must be a list")
        prio = t.get("priority")
        if prio is not None and prio not in PRIORITY_RANK:
            blocks.append(
                f"tasks[{i}].priority must be one of {list(TASK_PRIORITIES)} or null"
            )
        bkt = t.get("bucket")
        pts = t.get("points")
        if bkt is not None and bkt not in TASK_BUCKETS:
            blocks.append(
                f"tasks[{i}].bucket must be one of {sorted(TASK_BUCKETS)} or null"
            )
        if pts is not None and (
            not isinstance(pts, int)
            or isinstance(pts, bool)
            or pts not in POINTS_BUCKET
        ):
            blocks.append(
                f"tasks[{i}].points must be one of {sorted(POINTS_BUCKET)} or null"
            )
        if bkt in TASK_BUCKETS and isinstance(pts, int) and not isinstance(pts, bool):
            if pts != BUCKET_POINTS[bkt]:
                blocks.append(
                    f"tasks[{i}]: bucket {bkt!r} expects points={BUCKET_POINTS[bkt]}, "
                    f"got {pts}"
                )
        pb = t.get("plan_bucket")
        pp = t.get("plan_points")
        if pb is not None and pb not in TASK_BUCKETS:
            blocks.append(
                f"tasks[{i}].plan_bucket must be one of {sorted(TASK_BUCKETS)} or null"
            )
        if pp is not None and (
            not isinstance(pp, int)
            or isinstance(pp, bool)
            or pp not in POINTS_BUCKET
        ):
            blocks.append(
                f"tasks[{i}].plan_points must be one of {sorted(POINTS_BUCKET)} or null"
            )
        if pb in TASK_BUCKETS and isinstance(pp, int) and not isinstance(pp, bool):
            if pp != BUCKET_POINTS[pb]:
                blocks.append(
                    f"tasks[{i}]: plan_bucket {pb!r} expects plan_points="
                    f"{BUCKET_POINTS[pb]}, got {pp}"
                )
        sec = t.get("sector_id")
        if sec is not None:
            if not isinstance(sec, str) or not _SLUG_RE.match(sec):
                blocks.append(f"tasks[{i}].sector_id must be a slug or null")
            elif "sectors" in data and sec not in sector_ids:
                blocks.append(
                    f"tasks[{i}].sector_id {sec!r} not in route.sectors"
                )
        for field in ("owner_agent", "started_at", "last_heartbeat_at"):
            val = t.get(field)
            if val is not None and not isinstance(val, str):
                blocks.append(f"tasks[{i}].{field} must be a string or null")
    # dep existence
    for t in tasks:
        if not isinstance(t, dict):
            continue
        for d in t.get("deps") or []:
            if d not in ids:
                blocks.append(f"task {t.get('id')}: unknown dep {d!r}")
    # dep acyclicity — a cycle silently makes tasks never-pickable
    dep_map = {
        t.get("id"): [d for d in (t.get("deps") or []) if d in ids]
        for t in tasks
        if isinstance(t, dict) and t.get("id") in ids
    }
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in dep_map}
    cycles: list[str] = []

    def visit(tid: str, path: list[str]) -> None:
        color[tid] = GRAY
        for d in dep_map.get(tid) or []:
            if color.get(d) == GRAY:
                cyc = path[path.index(d):] + [d] if d in path else [tid, d]
                cycles.append(" -> ".join(cyc + [cyc[0]] if cyc[-1] != cyc[0] else cyc))
            elif color.get(d) == WHITE:
                visit(d, path + [d])
        color[tid] = BLACK

    for tid in dep_map:
        if color[tid] == WHITE:
            visit(tid, [tid])
    for cyc in sorted(set(cycles)):
        blocks.append(f"dependency cycle: {cyc}")
    return blocks


def load_route(project_root: Path) -> dict[str, Any]:
    path = route_path(project_root)
    if not path.is_file():
        raise FileNotFoundError(
            "no route — terra route init  (after terra brief init)"
        )
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    # Optimistic-concurrency baseline. Several leads drive Terra CONCURRENTLY,
    # and load->mutate->save is a read-modify-write: two leads that both load,
    # both mutate and both save mean one edit vanishes with no error at all.
    # Stamping what we read lets save_route turn that SILENT lost update into
    # a loud refusal. Private key, stripped before write.
    data[_BASELINE_KEY] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if "plan_locked" not in data:
        data["plan_locked"] = False
    if "plan_locked_at" not in data:
        data["plan_locked_at"] = None
    if "sectors" not in data or data["sectors"] is None:
        data["sectors"] = []
    # Derived fields are no longer persisted (see save_route), so EVERY read
    # must recompute them. Without this, consumers that GUARD on them read
    # None instead of False and stop guarding — start_task's deps interlock
    # died exactly this way, letting a task with unmet deps be claimed.
    # Stripping computed state from disk is only safe if the read path always
    # rebuilds it.
    data["tasks"] = _recompute_ready(list(data.get("tasks") or []))
    for t in data.get("tasks") or []:
        if not isinstance(t, dict):
            continue
        # legacy tasks predate priority — backfill the DEFAULT, never p0.
        # Silently promoting an unranked backlog to urgent would make the
        # first sorted `next` a lie. No schema bump; optional field.
        if t.get("priority") not in PRIORITY_RANK:
            t["priority"] = DEFAULT_PRIORITY
        # legacy: copy working → plan if plan missing
        if "plan_points" not in t and t.get("points") is not None:
            t["plan_points"] = t.get("points")
        if "plan_bucket" not in t and t.get("bucket") is not None:
            t["plan_bucket"] = t.get("bucket")
        # legacy: liveness fields default to unclaimed / no heartbeat
        t.setdefault("owner_agent", None)
        t.setdefault("started_at", None)
        t.setdefault("last_heartbeat_at", None)
    blocks = validate_route(data)
    if blocks:
        raise ValueError("invalid route:\n  - " + "\n  - ".join(blocks))
    return data


def save_route(project_root: Path, record: dict[str, Any]) -> Path:
    record = dict(record)
    record["updated_at"] = _now()
    # refresh ready flags from deps
    record["tasks"] = _recompute_ready(list(record.get("tasks") or []))
    blocks = validate_route(record)
    if blocks:
        raise ValueError("invalid route:\n  - " + "\n  - ".join(blocks))
    terra_root(project_root).mkdir(parents=True, exist_ok=True)
    path = route_path(project_root)
    # Persist ONLY canonical state. pickable/waiting_on/waiting_on_cancelled/
    # unreachable_via are DERIVED from deps+status on every load — writing them
    # to disk made them look like levers. A lead repairing the DAG edited
    # `waiting_on`, read the file back, saw its edit, and had it silently
    # clobbered by the next terra command; its follow-up audit still reported
    # the unrepaired count. Had it trusted its own read-back it would have
    # reported a clean spine over a dead DAG. A computed field must not sit in
    # the file inviting an edit — `deps` is the only lever.
    on_disk = dict(record)
    on_disk["tasks"] = [
        {k: v for k, v in t.items() if k not in DERIVED_TASK_FIELDS}
        for t in on_disk.get("tasks") or []
    ]
    baseline = on_disk.pop(_BASELINE_KEY, None)
    if baseline is not None and path.is_file():
        current = hashlib.sha256(
            path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        if current != baseline:
            raise ConcurrentRouteWrite(
                "route.json changed on disk since it was loaded — another "
                "agent wrote it while you were editing. Your change was NOT "
                "saved (saving would have silently discarded theirs). "
                "Re-read the route and re-apply your edit."
            )
    payload = json.dumps(on_disk, indent=2, sort_keys=True) + "\n"
    # ATOMIC publish. A bare write_text leaves the file truncated-then-partial
    # for a moment, and a concurrent reader lands mid-write and gets a JSON
    # parse error on the program's central record — observed live on CG-01
    # ("raw JSON parse errors mid-batch" while a peer lead wrote). Same-dir
    # temp + fsync + os.replace makes readers see only whole files.
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".route.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def _recompute_ready(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate pickable/waiting_on; leave terminal statuses alone."""
    by_id = {t["id"]: dict(t) for t in tasks if isinstance(t, dict) and t.get("id")}
    result: list[dict[str, Any]] = []
    for _tid, t in by_id.items():
        st = t.get("status")
        if st in ("done", "cancelled", "blocked", "in_progress"):
            t.pop("waiting_on", None)
            t["pickable"] = False
            result.append(t)
            continue
        deps = list(t.get("deps") or [])
        waiting = [
            d
            for d in deps
            if d not in by_id or by_id[d].get("status") != "done"
        ]
        # A CANCELLED dep never becomes done, so the dependent waits forever
        # while reading status=ready/waiting_on=[…] — indistinguishable from
        # work that is merely queued. This silently stranded 22 of 118 open
        # routes on a live program, including the only CFD route and the root
        # of the whole flight-demo chain, and NOTHING alarmed.
        # We deliberately do NOT auto-unblock: a cancelled basis often means
        # the dependent's premise died too, and silently making it pickable
        # would be the worse lie. Instead the death is made VISIBLE and a
        # human/lead decides: re-point the dep, or cancel the dependent.
        dead = [
            d
            for d in deps
            if d in by_id and by_id[d].get("status") == "cancelled"
        ]
        t["status"] = "ready"
        t.pop("unreachable_via", None)
        if dead:
            t["waiting_on_cancelled"] = dead
        else:
            t.pop("waiting_on_cancelled", None)
        if waiting:
            t["waiting_on"] = waiting
            t["pickable"] = False
        else:
            t.pop("waiting_on", None)
            t["pickable"] = True
        result.append(t)
    by_new = {t["id"]: t for t in result}
    # Unreachability is TRANSITIVE: a task waiting on a stranded task is
    # equally dead. Reporting only the direct hop understated a live program
    # by 2.4x (9 reported vs 22 actually unreachable), and an undercount on
    # a "what can never be worked" instrument is the failure mode this
    # whole feature exists to kill. Fixpoint to closure.
    unreachable: dict[str, str] = {
        tid: tid for tid, t in by_new.items() if t.get("waiting_on_cancelled")
    }
    changed = True
    while changed:
        changed = False
        for tid, t in by_new.items():
            if tid in unreachable or t.get("status") in ("done", "cancelled"):
                continue
            for d in t.get("deps") or []:
                if d in unreachable:
                    unreachable[tid] = unreachable[d]
                    changed = True
                    break
    for tid, t in by_new.items():
        root = unreachable.get(tid)
        # the root names ITSELF; only downstream tasks carry unreachable_via
        if root is not None and root != tid:
            t["unreachable_via"] = root
    order = [t["id"] for t in tasks if isinstance(t, dict) and t.get("id")]
    return [by_new[i] for i in order if i in by_new]


def init_route(project_root: Path, *, force: bool = False) -> Path:
    ensure_map_store(project_root)
    path = route_path(project_root)
    if path.is_file() and not force:
        raise FileExistsError(f"route already exists: {path}")
    brief_version = None
    try:
        from .brief import load_brief

        brief_version = load_brief(project_root).get("version")
    except (FileNotFoundError, ValueError, OSError):
        pass
    rec = default_route(brief_version=brief_version)
    return save_route(project_root, rec)


def add_task(
    project_root: Path,
    task_id: str,
    *,
    title: str,
    phase: str = "",
    deps: list[str] | None = None,
    skill: str = "any",
    role: str = "any",
    enabler_id: str | None = None,
    acceptance: list[str] | None = None,
    map_id: str | None = None,
    bucket: str | None = None,
    points: int | None = None,
    sector_id: str | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    if not _SLUG_RE.match(task_id):
        raise ValueError(f"task id must match {_SLUG_RE.pattern}")
    # Validate phase against the brief ONLY when phases are declared. A
    # project that declares none keeps free-text (don't break it); one that
    # declared a lifecycle gets a typo caught at the write, not discovered
    # later as a phase that quietly holds one orphan task.
    ph = (phase or "").strip()
    if ph:
        _declared = [p["id"] for p in declared_phases(project_root)]
        if _declared and ph not in _declared:
            raise ValueError(
                f"phase {ph!r} is not declared in the brief — valid: "
                f"{_declared}. Add it first (terra brief phase <id> "
                f"--title \"…\") or use one of those."
            )
    prio = priority or DEFAULT_PRIORITY
    if prio not in PRIORITY_RANK:
        raise ValueError(
            f"priority must be one of {list(TASK_PRIORITIES)} — "
            + "; ".join(f"{k}={v}" for k, v in PRIORITY_MEANING.items())
        )
    if skill not in SKILLS:
        raise ValueError(f"skill must be one of {sorted(SKILLS)}")
    if role not in TASK_ROLES:
        raise ValueError(f"role must be one of {sorted(TASK_ROLES)}")
    if not title or not str(title).strip():
        raise ValueError("title required")
    bkt, pts = resolve_bucket_points(bucket=bucket, points=points)
    budget = brief_budget_points(project_root)
    if budget is not None and bkt is None and pts is None:
        raise ValueError(
            "brief has budget_points set — assign effort with "
            "--bucket low|medium|high (3/8/21) or --points 3|8|21"
        )
    # Default role from skill when not set
    if role == "any" and skill == "tooling":
        role = "enabler"
    if role == "any" and skill == "deliverable":
        role = "deliverable"
    rec = load_route(project_root)
    sectors = list(rec.get("sectors") or [])
    sector_ids = {s.get("id") for s in sectors if isinstance(s, dict)}
    if sector_id is not None:
        if not _SLUG_RE.match(sector_id):
            raise ValueError(f"sector_id must match {_SLUG_RE.pattern}")
        if sector_id not in sector_ids:
            raise ValueError(
                f"unknown sector {sector_id!r} — "
                f"terra route sector add {sector_id} --title \"…\" --points N"
            )
    if rec.get("plan_locked"):
        # Locked: only explode provisions into finer tasks inside a sector
        if not sector_id:
            raise ValueError(
                "route plan is locked — cannot add unsectored tasks to the baseline. "
                "Either: terra route add … --sector <id> --bucket …  "
                "(draw from a sector provision), or unlock: "
                "terra route unlock-plan then terra route unlock-plan --confirm"
            )
    tasks = list(rec.get("tasks") or [])
    if any(t.get("id") == task_id for t in tasks):
        raise FileExistsError(f"task already exists: {task_id}")
    tasks.append(
        {
            "id": task_id,
            "title": title.strip(),
            "phase": (phase or "").strip(),
            "deps": list(deps or []),
            "status": "ready",
            "skill": skill,
            "role": role,
            "enabler_id": enabler_id,
            "acceptance": list(acceptance or []),
            "map_id": map_id,
            "sector_id": sector_id,
            "priority": prio,
            "bucket": bkt,
            "points": pts,
            "plan_bucket": bkt,
            "plan_points": pts,
            "blocked_reason": None,
            # Liveness: owner + heartbeat distinguish "working" from "died".
            # status is NOT a liveness signal — see route_attention.
            "owner_agent": None,
            "started_at": None,
            "last_heartbeat_at": None,
            "evidence": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
    )
    assert_planned_within_budget(
        project_root,
        tasks,
        context=f"route add {task_id}",
        sectors=sectors,
    )
    rec["tasks"] = tasks
    save_route(project_root, rec)
    return _get_task(load_route(project_root), task_id)


def add_sector(
    project_root: Path,
    sector_id: str,
    *,
    title: str,
    reserved_points: int,
    notes: str = "",
) -> dict[str, Any]:
    """Reserve a point provision to explode into tasks later."""
    if not _SLUG_RE.match(sector_id):
        raise ValueError(f"sector id must match {_SLUG_RE.pattern}")
    if not title or not str(title).strip():
        raise ValueError("title required")
    if not isinstance(reserved_points, int) or isinstance(reserved_points, bool):
        raise ValueError("reserved_points must be an int >= 0")
    if reserved_points < 0:
        raise ValueError("reserved_points must be an int >= 0")
    rec = load_route(project_root)
    if rec.get("plan_locked"):
        raise ValueError(
            "route plan is locked — cannot add sector reserves. "
            "Unlock: terra route unlock-plan --confirm"
        )
    sectors = list(rec.get("sectors") or [])
    if any(s.get("id") == sector_id for s in sectors):
        raise FileExistsError(f"sector already exists: {sector_id}")
    sectors.append(
        {
            "id": sector_id,
            "title": title.strip(),
            "reserved_points": reserved_points,
            "notes": (notes or "").strip(),
            "created_at": _now(),
        }
    )
    assert_planned_within_budget(
        project_root,
        list(rec.get("tasks") or []),
        context=f"sector add {sector_id}",
        sectors=sectors,
    )
    rec["sectors"] = sectors
    save_route(project_root, rec)
    return next(s for s in load_route(project_root)["sectors"] if s["id"] == sector_id)


def set_sector_reserve(
    project_root: Path,
    sector_id: str,
    *,
    reserved_points: int | None = None,
    title: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Adjust sector reserve (plan unlocked only for reserve changes)."""
    rec = load_route(project_root)
    if rec.get("plan_locked") and reserved_points is not None:
        raise ValueError(
            "route plan is locked — cannot change sector reserves. "
            "Unlock: terra route unlock-plan --confirm"
        )
    found = None
    for s in rec.get("sectors") or []:
        if s.get("id") == sector_id:
            found = s
            if reserved_points is not None:
                if reserved_points < 0:
                    raise ValueError("reserved_points must be >= 0")
                s["reserved_points"] = int(reserved_points)
            if title is not None:
                s["title"] = title.strip()
            if notes is not None:
                s["notes"] = notes.strip()
            break
    if not found:
        raise FileNotFoundError(f"sector not found: {sector_id}")
    assert_planned_within_budget(
        project_root,
        list(rec.get("tasks") or []),
        context=f"sector set {sector_id}",
        sectors=list(rec.get("sectors") or []),
    )
    save_route(project_root, rec)
    return next(s for s in load_route(project_root)["sectors"] if s["id"] == sector_id)


def _get_task(rec: dict[str, Any], task_id: str) -> dict[str, Any]:
    for t in rec.get("tasks") or []:
        if t.get("id") == task_id:
            return t
    raise FileNotFoundError(f"task not found: {task_id}")


def start_task(
    project_root: Path, task_id: str, *, agent: str | None = None
) -> dict[str, Any]:
    rec = load_route(project_root)
    t = _get_task(rec, task_id)

    # RECLAIM path. An in_progress task carries pickable=False, so without
    # this it fell through to the deps branch and reported "waiting on None"
    # — a meaningless message for the exact case `--agent` is documented to
    # serve ("attributes a stranded lead"). Reclaiming a DEAD owner must
    # work; stealing a LIVE one must not — two writers on one task is how the
    # master model got corrupted.
    if t.get("status") == "in_progress":
        from datetime import datetime, timezone

        owner = t.get("owner_agent")
        hb = _parse_iso(t.get("last_heartbeat_at"))
        hours = (
            None
            if hb is None
            else (datetime.now(timezone.utc) - hb).total_seconds() / 3600.0
        )
        claimant = (agent or "").strip()
        same = bool(claimant) and claimant == str(owner or "").strip()
        stranded = owner is None or hours is None or hours >= HEARTBEAT_STALE_HOURS
        if not (same or stranded):
            raise ValueError(
                f"task {task_id} is in_progress and ALIVE — owner {owner!r} "
                f"heartbeated {hours:.1f}h ago (stale at "
                f"{HEARTBEAT_STALE_HOURS}h). Refusing to reassign: two "
                "writers on one task is how the master model got corrupted. "
                "Verify the owner is really gone, then retry."
            )
        now = _now()
        for task in rec["tasks"]:
            if task.get("id") == task_id:
                task["owner_agent"] = claimant or owner
                task["last_heartbeat_at"] = now
                task.setdefault("started_at", now)
                if not same:
                    task["reclaimed_at"] = now
                    task["reclaimed_from"] = owner
        save_route(project_root, rec)
        return _get_task(load_route(project_root), task_id)

    if t.get("pickable") is False or t.get("waiting_on"):
        raise ValueError(
            f"task {task_id} waiting on {t.get('waiting_on')}"
        )
    if t.get("status") in ("done", "cancelled"):
        raise ValueError(f"task {task_id} is {t.get('status')}")
    if t.get("status") == "blocked":
        raise ValueError(f"task {task_id} is blocked: {t.get('blocked_reason')}")
    now = _now()
    for task in rec["tasks"]:
        if task.get("id") == task_id:
            task["status"] = "in_progress"
            task["updated_at"] = now
            # Claim ownership + open the heartbeat. A lead that dies mid-task
            # stops refreshing last_heartbeat_at — route_attention sees it.
            task["owner_agent"] = (agent or "").strip() or None
            task["started_at"] = now
            task["last_heartbeat_at"] = now
            task.pop("waiting_on", None)
            task["pickable"] = False
    save_route(project_root, rec)
    return _get_task(load_route(project_root), task_id)


def heartbeat_task(
    project_root: Path, task_id: str, *, agent: str | None = None
) -> dict[str, Any]:
    """Refresh an in_progress task's liveness stamp.

    The explicit "I'm still alive" ping. Only meaningful while in_progress —
    a dead lead simply stops calling this, and the gap becomes visible in
    route_attention. Does NOT touch status or updated_at (status is not a
    liveness signal; updated_at tracks real status/effort changes).
    """
    rec = load_route(project_root)
    t = _get_task(rec, task_id)
    if t.get("status") != "in_progress":
        raise ValueError(
            f"task {task_id} is {t.get('status')!r}, not in_progress — "
            "heartbeat only applies to a task being actively worked "
            "(terra route start it first)"
        )
    now = _now()
    for task in rec["tasks"]:
        if task.get("id") == task_id:
            task["last_heartbeat_at"] = now
            claim = (agent or "").strip()
            if claim:
                task["owner_agent"] = claim
    save_route(project_root, rec)
    return _get_task(load_route(project_root), task_id)


# Claim-shaped skills: completing these must cite map evidence, not prose
SURVEY_SKILLS = frozenset({"terra-map", "terra-probe"})


def _all_map_ids(project_root: Path) -> list[str]:
    from .paths import list_maps

    return [m["id"] for m in list_maps(project_root)] or ["global"]


def _find_run(project_root: Path, run_id: str) -> dict[str, Any] | None:
    """Search every map for a run id (run ids are globally unique)."""
    from .paths import run_dir, scoped_map
    from .probe_run import RUN_META_NAME

    for mid in _all_map_ids(project_root):
        with scoped_map(mid):
            meta_path = run_dir(project_root, run_id) / RUN_META_NAME
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                meta["_map_id"] = mid
                return meta
    return None


def _find_known(project_root: Path, known_id: str) -> dict[str, Any] | None:
    """Search active map first, then the rest."""
    from .paths import get_active_map_id, known_path, scoped_map

    active = get_active_map_id(project_root)
    order = [active] + [m for m in _all_map_ids(project_root) if m != active]
    for mid in order:
        with scoped_map(mid):
            path = known_path(project_root, known_id)
            if path.is_file():
                try:
                    rec = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                rec["_map_id"] = mid
                return rec
    return None


def validate_evidence_refs(
    project_root: Path,
    *,
    run_ids: list[str] | None = None,
    known_ids: list[str] | None = None,
) -> list[str]:
    """Hard checks: cited evidence must exist and be trustworthy."""
    problems: list[str] = []
    for rid in run_ids or []:
        meta = _find_run(project_root, rid)
        if meta is None:
            problems.append(f"run not found on any map: {rid}")
        elif meta.get("voided"):
            problems.append(
                f"run {rid} is voided ({meta.get('void_reason')!r}) — "
                f"voided evidence cannot complete a task"
            )
    for kid in known_ids or []:
        rec = _find_known(project_root, kid)
        if rec is None:
            problems.append(f"known not found on any map: {kid}")
            continue
        stats = rec.get("stats") or {}
        if not (rec.get("run_ids") or []) or not (stats.get("n") or 0):
            problems.append(f"known {kid} is unbacked (n=0) — not evidence")
        if (stats.get("corroboration") or {}).get("agree") is False:
            problems.append(
                f"known {kid}: methods disagree — resolve before citing it"
            )
    return problems


def complete_task(
    project_root: Path,
    task_id: str,
    *,
    evidence: str | None = None,
    run_ids: list[str] | None = None,
    known_ids: list[str] | None = None,
    freehand: str | None = None,
) -> dict[str, Any]:
    rec = load_route(project_root)
    task = _get_task(rec, task_id)
    # A CANCELLED task marked done launders a dead premise into a completion,
    # and route_log then renders it as a genuine completion event. `done` is
    # guarded too: re-completing appends a second evidence block as if the
    # work happened twice.
    _refuse_if_terminal(task, "complete")

    problems = validate_evidence_refs(
        project_root, run_ids=run_ids, known_ids=known_ids
    )
    if problems:
        raise ValueError(
            "evidence refs rejected:\n  - " + "\n  - ".join(problems)
        )

    claim_shaped = (
        task.get("skill") in SURVEY_SKILLS or task.get("role") == "survey"
    )
    if claim_shaped and not run_ids and not known_ids:
        if not freehand:
            raise ValueError(
                f"task {task_id} is claim-shaped (skill="
                f"{task.get('skill')!r}, role={task.get('role')!r}) — "
                "completing it needs map evidence, not prose:\n"
                "    terra route complete "
                f"{task_id} --run <run_id> | --known <known_id>\n"
                "  or record why none applies:\n"
                f"    terra route complete {task_id} --freehand '<reason>'"
            )

    for t in rec["tasks"]:
        if t.get("id") == task_id:
            t["status"] = "done"
            t["updated_at"] = _now()
            t["blocked_reason"] = None
            # No longer being worked — drop the live heartbeat (keep
            # started_at as history). A done task must never read as "alive."
            t["owner_agent"] = None
            t["last_heartbeat_at"] = None
            t.pop("waiting_on", None)
            entry: dict[str, Any] = {"at": _now()}
            if evidence:
                entry["note"] = evidence.strip()
            if run_ids:
                entry["runs"] = list(run_ids)
            if known_ids:
                entry["knowns"] = list(known_ids)
            if freehand:
                entry["freehand"] = freehand.strip()
            if len(entry) > 1:
                ev = list(t.get("evidence") or [])
                ev.append(entry)
                t["evidence"] = ev
    save_route(project_root, rec)
    return _get_task(load_route(project_root), task_id)


# `done` and `cancelled` are TERMINAL: each asserts a settled outcome that
# later records (evidence, superseded beliefs, cancelled_reason) hang off.
# Re-opening one silently rewrites history — a cancelled dead premise could be
# marked `done` and would then render in `route log` as a genuine completion.
TERMINAL_STATUSES = frozenset({"done", "cancelled"})

_TERMINAL_ESCAPE = {
    "done": (
        "supersede the belief it produced (terra known supersede), or add a "
        "NEW task for the follow-on work — do not re-open the completion"
    ),
    "cancelled": (
        "add a NEW task if the premise came back to life; a cancelled route "
        "records that this work was never valid, and that record is evidence"
    ),
}


def _refuse_if_terminal(task: dict[str, Any], verb: str) -> None:
    """Guard every state-changing verb against terminal-state resurrection.

    This rule already existed in exactly ONE place (cancel refusing on done)
    and was never applied systematically, so `cancelled --complete--> done`,
    `cancelled --unblock--> ready` (dead work back in the pickable queue),
    `cancelled --block-->` and `done --block-->` all silently succeeded.
    """
    st = task.get("status")
    if st in TERMINAL_STATUSES:
        raise ValueError(
            f"task {task.get('id')!r} is {st} — {verb} would re-open a "
            f"terminal state and rewrite what the record says happened. "
            f"Instead: {_TERMINAL_ESCAPE[st]}."
        )


def block_task(
    project_root: Path,
    task_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    if not reason or not str(reason).strip():
        raise ValueError("reason required")
    rec = load_route(project_root)
    _refuse_if_terminal(_get_task(rec, task_id), "block")
    for task in rec["tasks"]:
        if task.get("id") == task_id:
            task["status"] = "blocked"
            task["blocked_reason"] = reason.strip()
            task["updated_at"] = _now()
            # Not being actively worked while blocked — stop the heartbeat.
            task["owner_agent"] = None
            task["last_heartbeat_at"] = None
    save_route(project_root, rec)
    return _get_task(load_route(project_root), task_id)


def cancel_task(
    project_root: Path,
    task_id: str,
    *,
    reason: str,
    force: bool = False,
) -> dict[str, Any]:
    """Retire a task that should never be worked. NOT the same as done.

    ``cancelled`` was in TASK_STATUSES from the start but had no CLI verb, so
    the only disposals available were `block` (leaves it as standing debt that
    reads like pending work) or `complete` (asserts an outcome that never
    happened). Dead-premise routes therefore accumulated as permanent blocked
    debt — 7 of them by 2026-07-27, several citing "PM authorization needed".

    The reason is mandatory and preserved: a cancelled route must say WHY it
    was never valid, or the next planner re-derives it.
    """
    if not reason or not str(reason).strip():
        raise ValueError("reason required — a cancelled route must say why")
    rec = load_route(project_root)
    t = _get_task(rec, task_id)
    _refuse_if_terminal(t, "cancel")
    # Cancelling live claimed work destroys it with no interlock, while
    # `start_task` REFUSES to touch a task whose owner is alive. Same
    # double-writer hazard, so the same guard: force an explicit override.
    if t.get("status") == "in_progress" and not force:
        hb = _parse_iso(t.get("last_heartbeat_at"))
        from datetime import datetime, timezone

        age_h = (
            (datetime.now(timezone.utc) - hb).total_seconds() / 3600.0
            if hb is not None
            else None
        )
        if age_h is None or age_h < HEARTBEAT_STALE_HOURS:
            owner = t.get("owner_agent")
            raise ValueError(
                f"task {task_id} is in_progress under {owner!r} with a "
                f"{'fresh' if age_h is not None else 'never-stamped'} "
                f"heartbeat — cancelling would destroy work that may be "
                f"running right now. VERIFY the owner is dead, then "
                f"--force. (start_task enforces the same interlock; cancel "
                f"used to bypass it.)"
            )
    for task in rec["tasks"]:
        if task.get("id") == task_id:
            task["status"] = "cancelled"
            task["cancelled_reason"] = reason.strip()
            task["cancelled_at"] = _now()
            task["updated_at"] = _now()
            task.pop("blocked_reason", None)
            task["owner_agent"] = None
            task["last_heartbeat_at"] = None
            task["pickable"] = False
    save_route(project_root, rec)
    return _get_task(load_route(project_root), task_id)


def dependents_of(
    project_root: Path, task_id: str, *, live_only: bool = True
) -> list[dict[str, Any]]:
    """Tasks that declare task_id as a dep.

    Cancelling a task STRANDS these permanently — a cancelled dep never turns
    done, so the dependent waits forever while reading status=ready. Callers
    surface them so the stranding is a DECISION, not a side effect discovered
    weeks later.
    """
    rec = load_route(project_root)
    out = []
    for t in rec.get("tasks") or []:
        if not isinstance(t, dict) or task_id not in (t.get("deps") or []):
            continue
        if live_only and t.get("status") in ("done", "cancelled"):
            continue
        out.append(t)
    return out


def unblock_task(project_root: Path, task_id: str) -> dict[str, Any]:
    rec = load_route(project_root)
    _refuse_if_terminal(_get_task(rec, task_id), "unblock")
    for task in rec["tasks"]:
        if task.get("id") == task_id:
            task["status"] = "ready"
            task["blocked_reason"] = None
            task["updated_at"] = _now()
    save_route(project_root, rec)
    return _get_task(load_route(project_root), task_id)


def set_task_priority(
    project_root: Path,
    task_ids: list[str],
    *,
    priority: str,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    """Re-rank tasks. Priority is orthogonal to effort and to the plan
    baseline — it never touches points/plan_points, so re-ranking cannot
    move the budget and needs no plan unlock."""
    if priority not in PRIORITY_RANK:
        raise ValueError(
            f"priority must be one of {list(TASK_PRIORITIES)} — "
            + "; ".join(f"{k}={v}" for k, v in PRIORITY_MEANING.items())
        )
    rec = load_route(project_root)
    tasks = list(rec.get("tasks") or [])
    by_id = {t.get("id"): t for t in tasks if isinstance(t, dict)}
    missing = [tid for tid in task_ids if tid not in by_id]
    if missing:
        raise ValueError(f"unknown task id(s): {', '.join(sorted(missing))}")
    changed: list[dict[str, Any]] = []
    for tid in task_ids:
        t = by_id[tid]
        prev = t.get("priority") or DEFAULT_PRIORITY
        t["priority"] = priority
        t["updated_at"] = _now()
        if reason:
            ev = list(t.get("evidence") or [])
            ev.append(
                {
                    "at": _now(),
                    "kind": "priority",
                    "note": f"priority {prev} → {priority}: {reason}",
                }
            )
            t["evidence"] = ev
        changed.append(t)
    rec["tasks"] = tasks
    save_route(project_root, rec)
    fresh = load_route(project_root)
    return [_get_task(fresh, t["id"]) for t in changed]


def set_task_phase(
    project_root: Path,
    task_ids: list[str],
    *,
    phase: str,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    """Re-tag tasks into a phase.

    Needed because `--phase` was write-once at creation: work could never
    move between lifecycle stages, which makes a lifecycle unmanageable.
    Like priority, this touches no points and no plan baseline, so it needs
    no plan unlock. Terminal tasks CAN be re-phased — that is history
    re-filing, not resurrection; it changes no outcome.
    """
    ph = (phase or "").strip()
    if not ph:
        raise ValueError("phase required (use --phase <id>)")
    declared = [p["id"] for p in declared_phases(project_root)]
    if declared and ph not in declared:
        raise ValueError(
            f"phase {ph!r} is not declared in the brief — valid: {declared}"
        )
    rec = load_route(project_root)
    tasks = list(rec.get("tasks") or [])
    by_id = {t.get("id"): t for t in tasks if isinstance(t, dict)}
    missing = [tid for tid in task_ids if tid not in by_id]
    if missing:
        raise ValueError(f"unknown task id(s): {', '.join(sorted(missing))}")
    changed = []
    for tid in task_ids:
        t = by_id[tid]
        prev = t.get("phase") or "(none)"
        t["phase"] = ph
        t["updated_at"] = _now()
        if reason:
            ev = list(t.get("evidence") or [])
            ev.append({"at": _now(), "kind": "phase",
                       "note": f"phase {prev} -> {ph}: {reason}"})
            t["evidence"] = ev
        changed.append(t)
    rec["tasks"] = tasks
    save_route(project_root, rec)
    fresh = load_route(project_root)
    return [_get_task(fresh, t["id"]) for t in changed]


def next_tasks(
    project_root: Path,
    *,
    limit: int = 5,
    priority: str | None = None,
    phase: str | None = None,
) -> list[dict[str, Any]]:
    rec = load_route(project_root)
    rec["tasks"] = _recompute_ready(list(rec.get("tasks") or []))
    pickable = [
        t
        for t in rec["tasks"]
        if t.get("status") == "ready" and t.get("pickable") is True
    ]
    if phase is not None:
        pickable = [t for t in pickable if (t.get("phase") or "") == phase]
    if priority is not None:
        if priority not in PRIORITY_RANK:
            raise ValueError(f"priority must be one of {list(TASK_PRIORITIES)}")
        pickable = [t for t in pickable if (t.get("priority") or DEFAULT_PRIORITY) == priority]
    # Stable sort by priority only: within a rank, insertion order (the
    # historical behaviour) is preserved, so this is purely additive.
    pickable.sort(key=priority_rank)
    in_prog = [t for t in rec["tasks"] if t.get("status") == "in_progress"]
    if phase is not None:
        in_prog = [t for t in in_prog if (t.get("phase") or "") == phase]
    out = in_prog + pickable
    return out[: max(1, limit)]


def _task_points(t: dict[str, Any]) -> int:
    pts = t.get("points")
    if isinstance(pts, int) and not isinstance(pts, bool) and pts in POINTS_BUCKET:
        return pts
    bkt = t.get("bucket")
    if bkt in BUCKET_POINTS:
        return BUCKET_POINTS[bkt]
    return 0


def planned_points(tasks: list[dict[str, Any]]) -> int:
    return sum(
        _task_points(t)
        for t in tasks
        if t.get("status") != "cancelled"
    )


def brief_budget_points(project_root: Path) -> int | None:
    try:
        from .brief import load_brief

        bp = load_brief(project_root).get("budget_points")
        if isinstance(bp, int) and not isinstance(bp, bool) and bp >= 0:
            return bp
    except (FileNotFoundError, ValueError, OSError):
        pass
    return None


def assert_planned_within_budget(
    project_root: Path,
    tasks: list[dict[str, Any]],
    *,
    context: str = "route",
    budget_points: int | None = None,
    sectors: list[dict[str, Any]] | None = None,
) -> None:
    """Hard fail when plan allocation exceeds budget or sector reserves.

    Uses **plan_points** (baseline) when set, else working points.
    ``budget_points`` / ``sectors`` override disk when not saved yet.
    """
    budget = budget_points if budget_points is not None else brief_budget_points(
        project_root
    )
    if sectors is None:
        try:
            sectors = list(load_route(project_root).get("sectors") or [])
        except (FileNotFoundError, ValueError, OSError):
            sectors = []

    reserved = 0
    by_sec_reserve: dict[str, int] = {}
    for s in sectors:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        rp = s.get("reserved_points")
        if isinstance(sid, str) and isinstance(rp, int) and not isinstance(rp, bool):
            by_sec_reserve[sid] = rp
            reserved += rp

    if budget is not None and reserved > budget:
        raise ValueError(
            f"{context}: sum of sector reserves {reserved} exceeds "
            f"budget_points {budget}"
        )

    plan_by_sector: dict[str, int] = {sid: 0 for sid in by_sec_reserve}
    unsectored = 0
    total_plan = 0
    for t in tasks:
        if t.get("status") == "cancelled":
            continue
        p = _plan_task_points(t) or _task_points(t)
        total_plan += p
        sid = t.get("sector_id")
        if sid:
            if sid not in by_sec_reserve:
                raise ValueError(
                    f"{context}: task {t.get('id')!r} sector_id {sid!r} "
                    f"has no matching route sector"
                )
            plan_by_sector[sid] = plan_by_sector.get(sid, 0) + p
        else:
            unsectored += p

    for sid, used in plan_by_sector.items():
        cap = by_sec_reserve.get(sid, 0)
        if used > cap:
            raise ValueError(
                f"{context}: sector {sid!r} plan allocation {used} exceeds "
                f"reserved_points {cap}. Add less, raise sector reserve, or "
                f"use a different sector."
            )

    if budget is not None:
        free = budget - reserved
        if unsectored > free:
            raise ValueError(
                f"{context}: unsectored plan points {unsectored} exceed free "
                f"pool {free} (budget {budget} − sector reserves {reserved}). "
                f"Put work in a sector, lower buckets, or raise budget."
            )
        if total_plan > budget:
            raise ValueError(
                f"{context}: plan points {total_plan} exceed budget_points {budget} "
                f"(unallocated would be {budget - total_plan}). "
                f"Lower task buckets, cancel tasks, or raise "
                f"`terra brief set --budget-points …`."
            )


def set_task_effort(
    project_root: Path,
    task_id: str,
    *,
    bucket: str | None = None,
    points: int | None = None,
) -> dict[str, Any]:
    """Re-bucket a task (e.g. low→medium when work got harder).

    If plan is **locked**: only working ``bucket``/``points`` change (plan_* frozen).
    Working may exceed budget_points.

    If plan is **unlocked**: updates both plan and working; still enforces
    planned (working) ≤ budget like initial setting.
    """
    if bucket is None and points is None:
        raise ValueError("provide --bucket and/or --points")
    bkt, pts = resolve_bucket_points(bucket=bucket, points=points)
    rec = load_route(project_root)
    t = _get_task(rec, task_id)
    if t.get("status") == "cancelled":
        raise ValueError(f"task {task_id} is cancelled")
    locked = bool(rec.get("plan_locked"))
    for task in rec["tasks"]:
        if task.get("id") == task_id:
            task["bucket"] = bkt
            task["points"] = pts
            if not locked:
                task["plan_bucket"] = bkt
                task["plan_points"] = pts
            task["updated_at"] = _now()
            break
    if not locked:
        assert_planned_within_budget(
            project_root,
            list(rec.get("tasks") or []),
            context=f"set-effort {task_id} (plan unlocked — treated as replan)",
        )
    save_route(project_root, rec)
    return _get_task(load_route(project_root), task_id)


def lock_plan(project_root: Path) -> dict[str, Any]:
    """Freeze plan_* ledger from current working points; stop baseline drift."""
    rec = load_route(project_root)
    if rec.get("plan_locked"):
        return {
            "plan_locked": True,
            "plan_locked_at": rec.get("plan_locked_at"),
            "message": "plan already locked",
            "budget": budget_rollup(project_root, list(rec.get("tasks") or [])),
        }
    # Ensure every weighted task has plan_* = working before lock
    for task in rec.get("tasks") or []:
        if task.get("status") == "cancelled":
            continue
        if task.get("points") is not None or task.get("bucket") is not None:
            task["plan_bucket"] = task.get("bucket")
            task["plan_points"] = task.get("points")
            if task.get("plan_points") is None and task.get("bucket") in BUCKET_POINTS:
                task["plan_points"] = BUCKET_POINTS[task["bucket"]]
                task["plan_bucket"] = task["bucket"]
    assert_planned_within_budget(
        project_root,
        list(rec.get("tasks") or []),
        context="lock-plan",
    )
    rec["plan_locked"] = True
    rec["plan_locked_at"] = _now()
    save_route(project_root, rec)
    rec2 = load_route(project_root)
    return {
        "plan_locked": True,
        "plan_locked_at": rec2.get("plan_locked_at"),
        "message": "plan locked — set-effort only changes working points; "
        "route add only with --sector <provision> (explode reserves); "
        "full replan: unlock-plan --confirm",
        "budget": budget_rollup(project_root, list(rec2.get("tasks") or [])),
    }


def unlock_plan(project_root: Path, *, confirm: bool = False) -> dict[str, Any]:
    """Unlock plan ledger. Requires confirm=True (--confirm on CLI)."""
    rec = load_route(project_root)
    if not rec.get("plan_locked"):
        return {
            "plan_locked": False,
            "plan_locked_at": rec.get("plan_locked_at"),
            "message": "plan already unlocked",
            "budget": budget_rollup(project_root, list(rec.get("tasks") or [])),
        }
    if not confirm:
        raise ValueError(
            "WARNING: unlocking the plan allows rewriting plan_bucket/plan_points "
            "(baseline commitment) and adding tasks to the plan again. "
            "Working effort history is not deleted, but the locked baseline can drift. "
            "If you intend this, re-run: terra route unlock-plan --confirm"
        )
    rec["plan_locked"] = False
    # keep plan_locked_at as last lock time for audit
    save_route(project_root, rec)
    rec2 = load_route(project_root)
    return {
        "plan_locked": False,
        "plan_locked_at": rec2.get("plan_locked_at"),
        "message": "plan unlocked — set-effort updates plan+working; route add allowed",
        "budget": budget_rollup(project_root, list(rec2.get("tasks") or [])),
    }


def _plan_task_points(t: dict[str, Any]) -> int:
    pp = t.get("plan_points")
    if isinstance(pp, int) and not isinstance(pp, bool) and pp in POINTS_BUCKET:
        return pp
    pb = t.get("plan_bucket")
    if pb in BUCKET_POINTS:
        return BUCKET_POINTS[pb]
    return 0


def budget_rollup(project_root: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Project budget vs plan/actual task points (3/8/21 buckets)."""
    budget = None
    notes = ""
    plan_locked = False
    plan_locked_at = None
    try:
        from .brief import load_brief

        br = load_brief(project_root)
        budget = br.get("budget_points")
        notes = br.get("budget_notes") or ""
    except (FileNotFoundError, ValueError, OSError):
        pass
    try:
        rt = load_route(project_root)
        plan_locked = bool(rt.get("plan_locked"))
        plan_locked_at = rt.get("plan_locked_at")
    except (FileNotFoundError, ValueError, OSError):
        pass

    actual = 0
    plan = 0
    done = 0
    in_flight = 0
    by_bucket: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    by_plan_bucket: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    unset_tasks = 0
    for t in tasks:
        if t.get("status") == "cancelled":
            continue
        p = _task_points(t)
        pp = _plan_task_points(t)
        bkt = t.get("bucket") if t.get("bucket") in TASK_BUCKETS else None
        pb = t.get("plan_bucket") if t.get("plan_bucket") in TASK_BUCKETS else None
        if bkt:
            by_bucket[bkt] += p
        elif p == 0:
            unset_tasks += 1
        if pb:
            by_plan_bucket[pb] += pp
        actual += p
        plan += pp
        st = t.get("status")
        if st == "done":
            done += p
        elif st in ("in_progress", "ready", "blocked"):
            in_flight += p

    # Sector provisions
    sectors_out: list[dict[str, Any]] = []
    reserved_total = 0
    try:
        for s in load_route(project_root).get("sectors") or []:
            if not isinstance(s, dict) or not s.get("id"):
                continue
            sid = s["id"]
            reserved = int(s.get("reserved_points") or 0)
            reserved_total += reserved
            plan_used = sum(
                _plan_task_points(t) or _task_points(t)
                for t in tasks
                if t.get("status") != "cancelled" and t.get("sector_id") == sid
            )
            actual_used = sum(
                _task_points(t)
                for t in tasks
                if t.get("status") != "cancelled" and t.get("sector_id") == sid
            )
            sectors_out.append(
                {
                    "id": sid,
                    "title": s.get("title"),
                    "reserved_points": reserved,
                    "points_plan": plan_used,
                    "points_actual": actual_used,
                    "remaining_reserve_plan": reserved - plan_used,
                    "over_reserve": actual_used > reserved,
                    "notes": s.get("notes") or "",
                }
            )
    except (FileNotFoundError, ValueError, OSError):
        pass

    remaining = None
    unallocated_plan = None
    unallocated_actual = None
    free_pool = None
    over_budget = False
    over_plan = False
    if isinstance(budget, int) and not isinstance(budget, bool):
        remaining = budget - done
        unallocated_plan = budget - plan
        unallocated_actual = budget - actual
        free_pool = budget - reserved_total
        over_budget = actual > budget
        over_plan = actual > plan

    return {
        "budget_points": budget,
        "budget_notes": notes,
        "plan_locked": plan_locked,
        "plan_locked_at": plan_locked_at,
        "bucket_scale": dict(BUCKET_POINTS),
        "sectors": sectors_out,
        "sector_reserved_total": reserved_total,
        "free_pool": free_pool,  # budget − sector reserves (for unsectored tasks)
        # baseline commitment
        "points_plan": plan,
        "points_by_bucket_plan": by_plan_bucket,
        # working effort (may diverge after lock + set-effort)
        "points_actual": actual,
        "points_planned": actual,  # alias for older clients
        "points_done": done,
        "points_remaining_work": in_flight,
        "points_remaining_budget": remaining,
        "points_unallocated_plan": unallocated_plan,
        "points_unallocated_actual": unallocated_actual,
        "points_unallocated": unallocated_actual,  # alias
        "over_budget": over_budget,
        "over_plan": over_plan,
        "variance_actual_minus_plan": actual - plan,
        "points_by_bucket_actual": by_bucket,
        "points_by_bucket_planned": by_bucket,  # alias
        "tasks_without_points": unset_tasks,
    }


STALL_DAYS = 7
# Liveness window: an in_progress task whose owner has not sent a heartbeat
# within this many hours is presumed possibly-dead. Deliberately short — the
# whole point is to catch a stranded lead FAST, before someone infers
# liveness from status and re-dispatches into a double-writer collision.
HEARTBEAT_STALE_HOURS = 6


def _parse_iso(raw: Any) -> "datetime | None":
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def route_attention(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aging debt on the route: dead/stalled in-progress work, standing blocks.

    Liveness is NOT the status field. An in_progress task with a fresh
    heartbeat is alive; one whose heartbeat has gone quiet may be a stranded
    or dead lead — surfaced distinctly so it is never mistaken for active work.
    """
    from datetime import datetime, timezone

    items: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    by_status = {
        t.get("id"): t.get("status")
        for t in tasks
        if isinstance(t, dict) and t.get("id")
    }
    for t in tasks:
        st = t.get("status")
        # A dep that was CANCELLED can never complete, so this task is dead,
        # not queued — but it reads status=ready/pickable=false, which looks
        # exactly like ordinary waiting. Blocking severity: ranking such a
        # task p0 accomplishes nothing, `route next` will never surface it.
        # A done task whose dep never finished means the DAG asserts an order
        # the work did not follow: either the dep was wrong, or the completion
        # was premature. We do NOT refuse the completion — deps are often soft
        # ordering and a hard refusal would push agents to freehand around the
        # route — but the discrepancy must not be silent.
        if st == "done":
            unmet = [
                d
                for d in (t.get("deps") or [])
                if d in by_status and by_status[d] not in ("done", "cancelled")
            ]
            if unmet:
                items.append(
                    {
                        "kind": "task_done_before_deps",
                        "id": t.get("id"),
                        "severity": "med",
                        "unmet_deps": unmet,
                        "why": (
                            f"completed while dep(s) {', '.join(unmet)} are "
                            "still open — either the dep is wrong (remove it) "
                            "or this completion is premature. The DAG and the "
                            "record disagree."
                        ),
                    }
                )
        via = t.get("unreachable_via")
        if via and st not in ("done", "cancelled"):
            items.append(
                {
                    "kind": "task_unreachable",
                    "id": t.get("id"),
                    "severity": "high",
                    "priority": t.get("priority"),
                    "root": via,
                    "why": (
                        f"transitively unreachable — its dep chain reaches "
                        f"{via!r}, which is stranded on a CANCELLED dep. "
                        f"Fix the root; this clears automatically."
                    ),
                }
            )
        dead = list(t.get("waiting_on_cancelled") or [])
        if dead and st not in ("done", "cancelled"):
            items.append(
                {
                    "kind": "task_dep_cancelled",
                    "id": t.get("id"),
                    "severity": "block",
                    "priority": t.get("priority"),
                    "cancelled_deps": dead,
                    "why": (
                        f"dep(s) {', '.join(dead)} are CANCELLED and can never "
                        "complete — this task is UNREACHABLE, not queued. "
                        "`route next` will never surface it at any priority. "
                        "Decide: re-point the dep onto the live successor "
                        "(route add … --dep <successor>), or cancel this task "
                        "too if its premise died with the dep."
                    ),
                }
            )
        if st == "blocked":
            items.append(
                {
                    "kind": "task_blocked",
                    "id": t.get("id"),
                    "severity": "med",
                    "why": f"blocked: {t.get('blocked_reason') or '(no reason)'}",
                }
            )
        elif st == "in_progress":
            owner = t.get("owner_agent")
            # Heartbeat liveness — only for tasks claimed under the new
            # machinery (last_heartbeat_at present). Legacy tasks have none
            # and fall back to the day-scale stall check below.
            hb = _parse_iso(t.get("last_heartbeat_at"))
            if hb is not None:
                hb_age_h = (now - hb).total_seconds() / 3600.0
                if hb_age_h >= HEARTBEAT_STALE_HOURS:
                    items.append(
                        {
                            "kind": "task_no_heartbeat",
                            "id": t.get("id"),
                            "owner": owner,
                            "severity": "high",
                            "hours_since_heartbeat": round(hb_age_h, 1),
                            "why": (
                                f"owner {owner!r} last heartbeat "
                                f"{int(hb_age_h)}h ago — lead may have died "
                                "mid-task. VERIFY it is not still running "
                                "before touching its work; do NOT infer "
                                "liveness from in_progress status and "
                                "re-dispatch (that is how the double-writer "
                                "corruption happened)."
                            ),
                        }
                    )
            touched = _parse_iso(t.get("updated_at"))
            if touched is not None and (now - touched).days >= STALL_DAYS:
                items.append(
                    {
                        "kind": "task_stalled",
                        "id": t.get("id"),
                        "severity": "high",
                        "why": (
                            f"in_progress untouched for {(now - touched).days}d "
                            "— complete/block it or split the work"
                        ),
                    }
                )
    return items


def route_log(
    project_root: Path,
    *,
    limit: int = 0,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Chronological history of what happened, with evidence.

    Pure view over route.json — no new state. One event per evidence
    entry (completion records carry note/runs/knowns/freehand), plus
    bare events for done tasks with no evidence and currently blocked
    tasks. This is the timeline agents were faking with shadow md logs.
    """
    rec = load_route(project_root)
    events: list[dict[str, Any]] = []
    # One route's history. Without this a lead wanting the story of a single
    # task had to parse .terra/route.json by hand — which is exactly the
    # shadow-tracker behaviour route_log exists to remove.
    if task_id:
        known_ids = {t.get("id") for t in (rec.get("tasks") or [])}
        if task_id not in known_ids:
            raise ValueError(f"task not found: {task_id}")
    for t in [
        t
        for t in (rec.get("tasks") or [])
        if not task_id or t.get("id") == task_id
    ]:
        base = {
            "task": t.get("id"),
            "title": t.get("title"),
            "skill": t.get("skill"),
            "phase": t.get("phase") or None,
        }
        entries = list(t.get("evidence") or [])
        for entry in entries:
            # Honour the entry's own kind. This defaulted to "complete" for
            # every evidence row, so a non-completion event (priority
            # re-rank) would have been rendered in the timeline as a
            # COMPLETION. Legacy entries carry no kind and still read
            # "complete", so this is backward-compatible.
            ev: dict[str, Any] = {
                "at": entry.get("at"),
                "kind": entry.get("kind") or "complete",
                **base,
            }
            for key in ("note", "runs", "knowns", "freehand"):
                if entry.get(key):
                    ev[key] = entry[key]
            events.append(ev)
        if t.get("status") == "done" and not entries:
            events.append(
                {"at": t.get("updated_at"), "kind": "complete", **base}
            )
        if t.get("status") == "blocked":
            events.append(
                {
                    "at": t.get("updated_at"),
                    "kind": "blocked",
                    **base,
                    "reason": t.get("blocked_reason"),
                }
            )
    events.sort(key=lambda e: str(e.get("at") or ""))
    total = len(events)
    if limit and limit > 0:
        events = events[-limit:]
    return {
        "command": "route.log",
        "counts": {"events": total, "shown": len(events)},
        "events": events,
    }


def route_status(project_root: Path) -> dict[str, Any]:
    rec = load_route(project_root)
    rec["tasks"] = _recompute_ready(list(rec.get("tasks") or []))
    tasks = rec["tasks"]
    by_status: dict[str, int] = {}
    for t in tasks:
        st = str(t.get("status") or "?")
        by_status[st] = by_status.get(st, 0) + 1
    pickable = [t for t in tasks if t.get("pickable") is True]
    blocked = [t for t in tasks if t.get("status") == "blocked"]
    # `next` is now priority-SORTED under a limit, so low-priority work can sit
    # unseen behind a full page of p0. Rollup-before-sample (sitrep §19.2):
    # counts are never truncated, so nothing hides behind the window.
    open_tasks = [
        t for t in tasks if t.get("status") in ("ready", "blocked", "in_progress")
    ]
    by_priority = {p: 0 for p in TASK_PRIORITIES}
    pickable_by_priority = {p: 0 for p in TASK_PRIORITIES}
    for t in open_tasks:
        by_priority[t.get("priority") or DEFAULT_PRIORITY] += 1
    for t in pickable:
        pickable_by_priority[t.get("priority") or DEFAULT_PRIORITY] += 1
    return {
        "command": "route.status",
        "brief_version": rec.get("brief_version"),
        "plan_locked": bool(rec.get("plan_locked")),
        "plan_locked_at": rec.get("plan_locked_at"),
        "counts": {
            "tasks": len(tasks),
            "by_status": by_status,
            "by_priority_open": by_priority,
            "by_priority_pickable": pickable_by_priority,
            "pickable": len(pickable),
            "blocked": len(blocked),
            "in_progress": by_status.get("in_progress", 0),
            "done": by_status.get("done", 0),
        },
        "budget": budget_rollup(project_root, tasks),
        "phases": phase_rollup(project_root, tasks),
        "next": next_tasks(project_root, limit=5),
        "blocked": blocked,
        "attention": route_attention(tasks) + _phase_attention(project_root, tasks),
        "tasks": tasks,
    }


def _phase_attention(
    project_root: Path, tasks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Phase drift + exit readiness.

    Only speaks when the project actually declares phases — a project not
    using them must not be nagged into it.
    """
    roll = phase_rollup(project_root, tasks)
    if not roll["declared"]:
        return []
    items: list[dict[str, Any]] = []
    for pid in roll["undeclared_used"]:
        items.append(
            {
                "kind": "task_phase_undeclared",
                "id": pid,
                "severity": "med",
                "why": (
                    f"tasks carry phase {pid!r} which is NOT declared in the "
                    "brief — free-text drift. It will never be reported as "
                    "the current phase. Declare it (terra brief phase) or "
                    "re-tag those tasks."
                ),
            }
        )
    cur = roll["current"]
    for r in roll["phases"]:
        # Declaring a phase closed does NOT retire its tasks. If open work
        # remains, the lifecycle has moved on while the work is still live —
        # either the closure was premature or that work belongs elsewhere.
        # Blocking severity: this is the one way phase closure can lie.
        if r["closed"] and r["open"]:
            items.append(
                {
                    "kind": "phase_closed_with_open_work",
                    "id": r["id"],
                    "severity": "block",
                    "open": r["open"],
                    "why": (
                        f"phase {r['id']!r} is declared CLOSED but still holds "
                        f"{r['open']} open task(s). Closure does not retire "
                        "tasks. Either re-tag that work to a live phase, "
                        "cancel it, or re-open the phase "
                        "(terra brief phase-close <id> --reopen --reason …)."
                    ),
                }
            )
        if r["id"] == cur and r["unreachable"]:
            items.append(
                {
                    "kind": "phase_exit_blocked_by_unreachable",
                    "id": r["id"],
                    "severity": "high",
                    "why": (
                        f"current phase {r['id']!r} has {r['unreachable']} "
                        f"UNREACHABLE task(s) of {r['open']} open — this "
                        "phase can never exit on its own. Fix the cancelled "
                        "deps (kind=task_dep_cancelled) or cancel those tasks."
                    ),
                }
            )
    if roll["unphased_open"]:
        items.append(
            {
                "kind": "tasks_unphased",
                "severity": "info",
                "count": roll["unphased_open"],
                "why": (
                    f"{roll['unphased_open']} open task(s) carry no phase — "
                    "invisible to phase exit criteria. They will never block "
                    "a phase exit, and never be counted as its work."
                ),
            }
        )
    return items
