"""Brief — compact SSOT design request for a Terra project.

Not an evidence plan (multi-leg beliefs). Not the route (task DAG).
The brief is what the program *is*; route walks it; map grounds it.

**Deliverables** — what the human receives (mission card, prints, mesh).
**Enablers** — internal means of production (drawing harness, CAD bridge,
sim wrapper). Not the customer pack, but required to produce it. May later
graduate to Cartograph widgets.

Change control: propose → accept (version++). Subagents should not
silent-edit the brief.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import brief_path, ensure_map_store, terra_root

BRIEF_SCHEMA_VERSION = 1
BRIEF_STATUSES = frozenset({"draft", "active", "frozen", "archived"})
ENABLER_STATUSES = frozenset(
    {"needed", "building", "ready", "graduated", "abandoned"}
)
# Effort budget: absolute project size (see route buckets low/medium/high = 3/8/21)
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def default_brief(
    *,
    title: str = "",
    mission: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "id": "brief",
        "title": (title or "Untitled project").strip(),
        "version": 1,
        "status": "draft",
        "mission": (mission or "").strip(),
        # Total effort budget for the program (same unit as route task points: 3/8/21 buckets)
        "budget_points": None,
        "budget_notes": "",
        "needs": [],
        "non_goals": [],
        "deliverables": [],
        # Internal tooling / harnesses required to produce deliverables
        "enablers": [],
        "phases": [],
        "change_control": (
            "Propose: terra brief propose --summary \"…\" "
            "[--need …] [--deliverable …] [--enabler id:title]. "
            "Apply: terra brief accept <proposal_id>. "
            "Subagents must not edit brief.json except via propose/accept. "
            "Enablers are internal means of production (not customer deliverables). "
            "budget_points = total effort; route tasks use buckets low=3 medium=8 high=21."
        ),
        "proposals": [],
        "created_at": _now(),
        "updated_at": _now(),
    }


def validate_brief(data: Any) -> list[str]:
    blocks: list[str] = []
    if not isinstance(data, dict):
        return ["brief must be a JSON object"]
    if data.get("schema_version") != BRIEF_SCHEMA_VERSION:
        blocks.append(
            f"schema_version must be {BRIEF_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        blocks.append("title required")
    if data.get("status") not in BRIEF_STATUSES:
        blocks.append(f"status must be one of {sorted(BRIEF_STATUSES)}")
    if not isinstance(data.get("version"), int) or data["version"] < 1:
        blocks.append("version must be int >= 1")
    bp = data.get("budget_points", None)
    if bp is not None and (not isinstance(bp, int) or isinstance(bp, bool) or bp < 0):
        blocks.append("budget_points must be null or int >= 0")
    if "budget_notes" in data and data["budget_notes"] is not None:
        if not isinstance(data["budget_notes"], str):
            blocks.append("budget_notes must be a string")
    for key in ("needs", "non_goals", "deliverables", "proposals", "enablers"):
        if key in data and data[key] is not None and not isinstance(data[key], list):
            blocks.append(f"{key} must be a list")
    if isinstance(data.get("enablers"), list):
        for i, en in enumerate(data["enablers"]):
            if isinstance(en, str):
                continue  # legacy string form
            if not isinstance(en, dict):
                blocks.append(f"enablers[{i}] must be object or string")
                continue
            if not en.get("id") or not en.get("title"):
                blocks.append(f"enablers[{i}] needs id and title")
            st = en.get("status", "needed")
            if st not in ENABLER_STATUSES:
                blocks.append(
                    f"enablers[{i}].status must be one of {sorted(ENABLER_STATUSES)}"
                )
    if "phases" in data:
        if not isinstance(data["phases"], list):
            blocks.append("phases must be a list")
        else:
            for i, ph in enumerate(data["phases"]):
                if not isinstance(ph, dict) or not ph.get("id"):
                    blocks.append(f"phases[{i}] must have id")
                elif ph.get("status") not in (None, "open", "closed"):
                    blocks.append(
                        f"phases[{i}].status must be 'open', 'closed', or null"
                    )
    return blocks


def _normalize_enabler(en: Any) -> dict[str, Any]:
    if isinstance(en, str):
        slug = re.sub(r"[^a-z0-9_]+", "_", en.strip().lower()).strip("_") or "enabler"
        return {
            "id": slug[:40],
            "title": en.strip(),
            "status": "needed",
            "path": "",
            "kind": "tooling",
            "graduates_to": None,
        }
    if not isinstance(en, dict):
        raise ValueError("enabler must be str or object")
    eid = str(en.get("id") or "").strip()
    title = str(en.get("title") or "").strip()
    if not eid or not _SLUG_RE.match(eid):
        raise ValueError(f"enabler id must be slug, got {eid!r}")
    if not title:
        raise ValueError(f"enabler {eid} needs title")
    st = en.get("status") or "needed"
    if st not in ENABLER_STATUSES:
        raise ValueError(f"enabler status must be one of {sorted(ENABLER_STATUSES)}")
    return {
        "id": eid,
        "title": title,
        "status": st,
        "path": str(en.get("path") or "").strip(),
        "kind": str(en.get("kind") or "tooling").strip() or "tooling",
        "graduates_to": en.get("graduates_to"),
        "notes": str(en.get("notes") or "").strip(),
    }


def parse_enabler_spec(spec: str) -> dict[str, Any]:
    """CLI form: id:title  or  id:title:path"""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("enabler spec empty")
    parts = [p.strip() for p in spec.split(":", 2)]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError("enabler must be id:title or id:title:path")
    out: dict[str, Any] = {
        "id": parts[0],
        "title": parts[1],
        "status": "needed",
        "kind": "tooling",
        "path": "",
        "graduates_to": None,
    }
    if len(parts) == 3:
        out["path"] = parts[2]
    return _normalize_enabler(out)


def load_brief(project_root: Path) -> dict[str, Any]:
    path = brief_path(project_root)
    if not path.is_file():
        raise FileNotFoundError(
            "no brief — terra brief init --title \"…\" --mission \"…\""
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    ens = []
    for en in data.get("enablers") or []:
        ens.append(_normalize_enabler(en))
    data["enablers"] = ens
    if "budget_points" not in data:
        data["budget_points"] = None
    if "budget_notes" not in data:
        data["budget_notes"] = ""
    blocks = validate_brief(data)
    if blocks:
        raise ValueError("invalid brief:\n  - " + "\n  - ".join(blocks))
    return data


def save_brief(project_root: Path, record: dict[str, Any]) -> Path:
    record = dict(record)
    record["updated_at"] = _now()
    blocks = validate_brief(record)
    if blocks:
        raise ValueError("invalid brief:\n  - " + "\n  - ".join(blocks))
    terra_root(project_root).mkdir(parents=True, exist_ok=True)
    path = brief_path(project_root)
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def init_brief(
    project_root: Path,
    *,
    title: str,
    mission: str = "",
    force: bool = False,
) -> Path:
    ensure_map_store(project_root)
    path = brief_path(project_root)
    if path.is_file() and not force:
        raise FileExistsError(f"brief already exists: {path}")
    rec = default_brief(title=title, mission=mission)
    if mission.strip():
        rec["status"] = "active"
    return save_brief(project_root, rec)


def set_brief_fields(
    project_root: Path,
    *,
    title: str | None = None,
    mission: str | None = None,
    status: str | None = None,
    budget_points: int | None = None,
    clear_budget_points: bool = False,
    budget_notes: str | None = None,
    needs: list[str] | None = None,
    non_goals: list[str] | None = None,
    deliverables: list[str] | None = None,
    enablers: list[str] | list[dict[str, Any]] | None = None,
    replace_lists: bool = False,
) -> dict[str, Any]:
    """Direct field updates (human / lead agent). Bumps version."""
    rec = load_brief(project_root)
    if title is not None:
        rec["title"] = title.strip()
    if mission is not None:
        rec["mission"] = mission.strip()
    if status is not None:
        if status not in BRIEF_STATUSES:
            raise ValueError(f"status must be one of {sorted(BRIEF_STATUSES)}")
        rec["status"] = status
    if clear_budget_points:
        rec["budget_points"] = None
    elif budget_points is not None:
        if not isinstance(budget_points, int) or isinstance(budget_points, bool):
            raise ValueError("budget_points must be an int >= 0")
        if budget_points < 0:
            raise ValueError("budget_points must be an int >= 0")
        rec["budget_points"] = budget_points
    if budget_notes is not None:
        rec["budget_notes"] = budget_notes.strip()
    for key, vals in (
        ("needs", needs),
        ("non_goals", non_goals),
        ("deliverables", deliverables),
    ):
        if vals is None:
            continue
        clean = [str(v).strip() for v in vals if str(v).strip()]
        if replace_lists:
            rec[key] = clean
        else:
            existing = list(rec.get(key) or [])
            for c in clean:
                if c not in existing:
                    existing.append(c)
            rec[key] = existing
    if enablers is not None:
        new_ens = []
        for e in enablers:
            if isinstance(e, str) and ":" in e:
                new_ens.append(parse_enabler_spec(e))
            else:
                new_ens.append(_normalize_enabler(e))
        if replace_lists:
            rec["enablers"] = new_ens
        else:
            existing = {_normalize_enabler(x)["id"]: _normalize_enabler(x) for x in (rec.get("enablers") or [])}
            for e in new_ens:
                existing[e["id"]] = e
            rec["enablers"] = list(existing.values())
    # Refuse budget that is already smaller than planned route points
    if budget_points is not None and not clear_budget_points:
        try:
            from .route import assert_planned_within_budget, load_route

            rt = load_route(project_root)
            assert_planned_within_budget(
                project_root,
                list(rt.get("tasks") or []),
                context="brief set --budget-points",
                budget_points=budget_points,
            )
        except FileNotFoundError:
            pass
    rec["version"] = int(rec.get("version") or 1) + 1
    save_brief(project_root, rec)
    return load_brief(project_root)


def set_enabler_status(
    project_root: Path,
    enabler_id: str,
    status: str,
    *,
    path: str | None = None,
    graduates_to: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Update an enabler's lifecycle (needed → building → ready → graduated)."""
    if status not in ENABLER_STATUSES:
        raise ValueError(f"status must be one of {sorted(ENABLER_STATUSES)}")
    rec = load_brief(project_root)
    found = False
    for en in rec.get("enablers") or []:
        if en.get("id") == enabler_id:
            en["status"] = status
            if path is not None:
                en["path"] = path.strip()
            if graduates_to is not None:
                en["graduates_to"] = graduates_to.strip() or None
            if notes is not None:
                en["notes"] = notes.strip()
            found = True
            break
    if not found:
        raise FileNotFoundError(f"enabler not found: {enabler_id}")
    rec["version"] = int(rec.get("version") or 1) + 1
    save_brief(project_root, rec)
    return load_brief(project_root)


def add_phase(
    project_root: Path,
    phase_id: str,
    *,
    title: str = "",
) -> dict[str, Any]:
    if not _SLUG_RE.match(phase_id):
        raise ValueError(f"phase id must match {_SLUG_RE.pattern}")
    rec = load_brief(project_root)
    phases = list(rec.get("phases") or [])
    if any(p.get("id") == phase_id for p in phases):
        raise FileExistsError(f"phase already exists: {phase_id}")
    phases.append(
        {
            "id": phase_id,
            "title": (title or phase_id).strip(),
            "status": "open",
        }
    )
    rec["phases"] = phases
    rec["version"] = int(rec.get("version") or 1) + 1
    save_brief(project_root, rec)
    return load_brief(project_root)


def close_phase(
    project_root: Path,
    phase_id: str,
    *,
    reason: str,
    reopen: bool = False,
) -> dict[str, Any]:
    """Declare a phase closed (or re-open it). An AUTHORITY act, not a count.

    Exit-readiness is COMPUTED (all tasks done/cancelled); closure is
    DECLARED. They are deliberately different: a gate passing is a
    precondition, never an authorization, and the converse also holds — a
    phase whose work was completed WITHOUT being tagged shows zero tasks and
    would otherwise read 'unplanned' forever, jamming `current` on an empty
    shell. That is exactly the state CG-01 was in: p2..p5 held no tasks
    because the work predated phase tagging.

    The reason is mandatory. A closure with no stated basis is the kind of
    unexplained authority act this program has been bitten by.
    """
    if not reason or not str(reason).strip():
        raise ValueError(
            "reason required — closing a phase is a decision and must say "
            "on what basis"
        )
    rec = load_brief(project_root)
    phases = list(rec.get("phases") or [])
    hit = next((p for p in phases if p.get("id") == phase_id), None)
    if hit is None:
        raise ValueError(
            f"unknown phase {phase_id!r} — declared: "
            f"{[p.get('id') for p in phases]}"
        )
    hit["status"] = "open" if reopen else "closed"
    hit["closed_reason"] = None if reopen else reason.strip()
    hit["closed_at"] = None if reopen else _now()
    rec["phases"] = phases
    rec["version"] = int(rec.get("version") or 1) + 1
    save_brief(project_root, rec)
    return load_brief(project_root)


def propose_change(
    project_root: Path,
    *,
    summary: str,
    need: str | None = None,
    non_goal: str | None = None,
    deliverable: str | None = None,
    enabler: str | None = None,
    mission: str | None = None,
) -> dict[str, Any]:
    """Queue a change; does not apply until accept."""
    if not summary or not str(summary).strip():
        raise ValueError("summary required")
    rec = load_brief(project_root)
    proposals = list(rec.get("proposals") or [])
    pid = f"p{len(proposals) + 1}_{int(datetime.now(timezone.utc).timestamp())}"
    prop: dict[str, Any] = {
        "id": pid,
        "summary": summary.strip(),
        "status": "open",
        "created_at": _now(),
        "patch": {},
    }
    patch = prop["patch"]
    if need:
        patch["add_need"] = need.strip()
    if non_goal:
        patch["add_non_goal"] = non_goal.strip()
    if deliverable:
        patch["add_deliverable"] = deliverable.strip()
    if enabler:
        patch["add_enabler"] = parse_enabler_spec(enabler)
    if mission:
        patch["mission"] = mission.strip()
    if not patch:
        patch["note"] = summary.strip()
    proposals.append(prop)
    rec["proposals"] = proposals
    # propose does not bump brief version until accept
    save_brief(project_root, rec)
    return prop


def accept_proposal(project_root: Path, proposal_id: str) -> dict[str, Any]:
    rec = load_brief(project_root)
    proposals = list(rec.get("proposals") or [])
    found = None
    for p in proposals:
        if p.get("id") == proposal_id:
            found = p
            break
    if found is None:
        raise FileNotFoundError(f"proposal not found: {proposal_id}")
    if found.get("status") != "open":
        raise ValueError(f"proposal {proposal_id} is {found.get('status')}")
    patch = found.get("patch") or {}
    if "add_need" in patch:
        needs = list(rec.get("needs") or [])
        if patch["add_need"] not in needs:
            needs.append(patch["add_need"])
        rec["needs"] = needs
    if "add_non_goal" in patch:
        ng = list(rec.get("non_goals") or [])
        if patch["add_non_goal"] not in ng:
            ng.append(patch["add_non_goal"])
        rec["non_goals"] = ng
    if "add_deliverable" in patch:
        d = list(rec.get("deliverables") or [])
        if patch["add_deliverable"] not in d:
            d.append(patch["add_deliverable"])
        rec["deliverables"] = d
    if "add_enabler" in patch:
        ens = list(rec.get("enablers") or [])
        new_e = _normalize_enabler(patch["add_enabler"])
        by_id = {e["id"]: e for e in ens}
        by_id[new_e["id"]] = new_e
        rec["enablers"] = list(by_id.values())
    if "mission" in patch:
        rec["mission"] = patch["mission"]
    found["status"] = "accepted"
    found["accepted_at"] = _now()
    rec["proposals"] = proposals
    rec["version"] = int(rec.get("version") or 1) + 1
    save_brief(project_root, rec)
    return load_brief(project_root)


def reject_proposal(project_root: Path, proposal_id: str) -> dict[str, Any]:
    rec = load_brief(project_root)
    proposals = list(rec.get("proposals") or [])
    for p in proposals:
        if p.get("id") == proposal_id:
            if p.get("status") != "open":
                raise ValueError(f"proposal {proposal_id} is {p.get('status')}")
            p["status"] = "rejected"
            p["rejected_at"] = _now()
            rec["proposals"] = proposals
            save_brief(project_root, rec)
            return load_brief(project_root)
    raise FileNotFoundError(f"proposal not found: {proposal_id}")


def brief_summary(rec: dict[str, Any]) -> dict[str, Any]:
    """Compact view for agents."""
    open_props = [
        p for p in (rec.get("proposals") or []) if p.get("status") == "open"
    ]
    ens = rec.get("enablers") or []
    return {
        "title": rec.get("title"),
        "version": rec.get("version"),
        "status": rec.get("status"),
        "mission": rec.get("mission"),
        "budget_points": rec.get("budget_points"),
        "budget_notes": rec.get("budget_notes") or "",
        "needs": rec.get("needs") or [],
        "non_goals": rec.get("non_goals") or [],
        "deliverables": rec.get("deliverables") or [],
        "enablers": ens,
        "enablers_needed": [
            e for e in ens if e.get("status") in ("needed", "building")
        ],
        "phases": rec.get("phases") or [],
        "open_proposals": len(open_props),
        "change_control": rec.get("change_control"),
    }
