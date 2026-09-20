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


def _removed_indices(old: list[str], new: list[str]) -> list[int]:
    """1-based indices dropped when ``new`` is ``old`` with some entries removed and the rest kept in order
    (an edit that also rewrites text is not a removal; nothing is renumbered for it)."""
    if len(new) >= len(old):
        return []
    removed, j = [], 0
    for i, entry in enumerate(old, 1):
        if j < len(new) and new[j] == entry:
            j += 1
        else:
            removed.append(i)
    return removed if j == len(new) else []

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
    signed_by: str = "",
    signature: str = "",
) -> dict[str, Any]:
    """Direct field updates (human / lead agent). Bumps version.

    ``signed_by`` is the person issuing the brief; it is recorded on the brief itself
    (``issued_by`` / ``issued_at``) the moment the status becomes active, so the
    document carries its own signature rather than whoever happens to be configured
    when it is read later."""
    rec = load_brief(project_root)
    if title is not None:
        rec["title"] = title.strip()
    if mission is not None:
        rec["mission"] = mission.strip()
    if status is not None:
        if status not in BRIEF_STATUSES:
            raise ValueError(f"status must be one of {sorted(BRIEF_STATUSES)}")
        if status == "active" and rec.get("status") != "active":
            rec["issued_at"] = _now()
            if signed_by.strip():
                rec["issued_by"] = signed_by.strip()
            if signature.strip():
                rec["issued_signature"] = signature.strip()
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
            # A replacement that only drops entries is a removal, and entries are cited by position: renumber
            # the phases and the map's cites exactly as an accepted remove proposal would (highest index first,
            # so earlier indices stay valid while later ones shift).
            old_entries = list(rec.get(key) or [])
            kind = {"needs": "need", "non_goals": "non_goal", "deliverables": "deliverable"}[key]
            removed = _removed_indices(old_entries, clean)
            rec[key] = clean
            for index in sorted(removed, reverse=True):
                _renumber_after_removal(project_root, rec, kind, index)
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


def parse_index_spec(spec: str) -> list[int]:
    """'1-7', '1,3,5' or '1-3,9' → sorted 1-based indices; '' → []."""
    out: set[int] = set()
    for part in (spec or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            if not (lo.isdigit() and hi.isdigit()) or int(lo) > int(hi):
                raise ValueError(f"bad index range {part!r}")
            out.update(range(int(lo), int(hi) + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ValueError(f"bad index {part!r}")
    if any(i < 1 for i in out):
        raise ValueError("indices are 1-based")
    return sorted(out)


def add_phase(
    project_root: Path,
    phase_id: str,
    *,
    title: str = "",
    description: str = "",
    needs: list[int] | None = None,
    deliverables: list[int] | None = None,
) -> dict[str, Any]:
    if not _SLUG_RE.match(phase_id):
        raise ValueError(f"phase id must match {_SLUG_RE.pattern}")
    rec = load_brief(project_root)
    phases = list(rec.get("phases") or [])
    if any(p.get("id") == phase_id for p in phases):
        raise FileExistsError(f"phase already exists: {phase_id}")
    # A phase owns brief entries: the needs and deliverables that must resolve before it can close.
    # Without them a phase is a label on tasks; with them it is a scope the controller routes within.
    for key, idx in (("needs", needs or []), ("deliverables", deliverables or [])):
        n = len(rec.get(key) or [])
        bad = [i for i in idx if i > n]
        if bad:
            raise ValueError(f"phase {key} {bad} exceed the brief's {n} {key}")
        taken = {i for p in phases for i in (p.get(key) or [])} & set(idx)
        if taken:
            raise ValueError(f"{key} {sorted(taken)} already belong to another phase")
    phases.append(
        {
            "id": phase_id,
            "title": (title or phase_id).strip(),
            # What the phase delivers, for the humans reading the brief.
            "description": (description or "").strip(),
            "needs": list(needs or []),
            "deliverables": list(deliverables or []),
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


def _next_proposal_id(proposals: list[dict[str, Any]]) -> str:
    """CR-001, CR-002, … — a change-request number a human can say aloud.

    Counts every proposal ever queued (rejected ones included) so numbers
    are never reused, and steps past any id already present.
    """
    taken = {p.get("id") for p in proposals}
    n = len(proposals) + 1
    while f"CR-{n:03d}" in taken:
        n += 1
    return f"CR-{n:03d}"


def propose_change(
    project_root: Path,
    *,
    summary: str,
    need: str | None = None,
    non_goal: str | None = None,
    deliverable: str | None = None,
    enabler: str | None = None,
    mission: str | None = None,
    edit_need: str | None = None,
    edit_deliverable: str | None = None,
    edit_non_goal: str | None = None,
    remove_need: int | None = None,
    remove_deliverable: int | None = None,
    remove_non_goal: int | None = None,
    budget_points: int | None = None,
) -> dict[str, Any]:
    """Queue a change; does not apply until accept.

    A proposal can do anything a person can do to the brief: add an entry, rewrite one in place
    (``edit_*`` as ``"<index>: <new text>"``, 1-based) or remove one (``remove_*`` by index). The person
    decides; nothing here applies.
    """
    if not summary or not str(summary).strip():
        raise ValueError("summary required")
    rec = load_brief(project_root)
    proposals = list(rec.get("proposals") or [])
    pid = _next_proposal_id(proposals)
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
    for key, spec in (("edit_need", edit_need), ("edit_deliverable", edit_deliverable), ("edit_non_goal", edit_non_goal)):
        if spec:
            patch[key] = parse_edit_spec(rec, key[5:], spec)
    for key, index in (("remove_need", remove_need), ("remove_deliverable", remove_deliverable), ("remove_non_goal", remove_non_goal)):
        if index is not None:
            patch[key] = _check_index(rec, key[7:], int(index))
    if budget_points is not None:
        # A budget change is its own patch: the ask for more (or less) effort, with nothing smuggled into a need.
        if isinstance(budget_points, bool) or int(budget_points) < 0:
            raise ValueError("budget_points must be an int >= 0")
        patch["budget_points"] = int(budget_points)
        patch["was_budget_points"] = rec.get("budget_points")
    if not patch:
        patch["note"] = summary.strip()
    proposals.append(prop)
    rec["proposals"] = proposals
    # propose does not bump brief version until accept
    save_brief(project_root, rec)
    return prop


_SECTION = {"need": "needs", "deliverable": "deliverables", "non_goal": "non_goals"}


def _check_index(rec: dict[str, Any], kind: str, index: int) -> int:
    entries = list(rec.get(_SECTION[kind]) or [])
    if not 1 <= index <= len(entries):
        raise ValueError(f"{kind} {index} does not exist; the brief has {len(entries)}")
    return index


def parse_edit_spec(rec: dict[str, Any], kind: str, spec: str) -> dict[str, Any]:
    """``"<index>: <new text>"`` → {"index": n, "text": ...}; the index is 1-based as the brief is read."""
    m = re.match(r"\s*(\d+)\s*:\s*(.+)\s*$", str(spec), re.S)
    if not m:
        raise ValueError(f"edit spec must be '<index>: <new text>', got {spec!r}")
    return {"index": _check_index(rec, kind, int(m.group(1))), "text": m.group(2).strip()}


def _renumber_after_removal(project_root: Path, rec: dict[str, Any], kind: str, removed: int) -> None:
    """Entries are cited by position (need:9), so removing one shifts everything after it. Keep the record
    honest: phases that own entries, and map unknowns whose notes cite them, move down by one; a citation of
    the removed entry becomes ``<kind>:removed`` so it is visibly orphaned rather than silently pointing at
    its neighbour."""
    section = _SECTION[kind]
    for phase in rec.get("phases") or []:
        owned = [int(i) for i in phase.get(section) or []]
        phase[section] = [i - 1 if i > removed else i for i in owned if i != removed]
    map_root = terra_root(project_root) / "map"
    stores = [map_root / "unknowns"] + sorted((map_root / "sessions").glob("*/unknowns")) if map_root.is_dir() else []
    pattern = re.compile(r"\b" + kind + r":(\d+)")

    def shift(m: re.Match[str]) -> str:
        n = int(m.group(1))
        if n == removed:
            return kind + ":removed"
        return kind + ":" + str(n - 1 if n > removed else n)

    for path in (q for store in stores if store.is_dir() for q in store.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except ValueError:
            continue
        notes = doc.get("notes")
        if isinstance(notes, str) and pattern.search(notes):
            doc["notes"] = pattern.sub(shift, notes)
            path.write_text(json.dumps(doc, indent=2) + "\n")


def accept_proposal(
    project_root: Path, proposal_id: str, *, reason: str = "", signed_by: str = "", signature: str = ""
) -> dict[str, Any]:
    """A decision carries its reason: what the person saw, so the next reader of the brief (a controller
    deciding whether to propose the same thing) knows why it went the way it did."""
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
    if patch.get("budget_points") is not None:
        rec["budget_points"] = int(patch["budget_points"])
    for kind in ("need", "deliverable", "non_goal"):
        edit = patch.get("edit_" + kind)
        if isinstance(edit, dict):
            entries = list(rec.get(_SECTION[kind]) or [])
            at = _check_index(rec, kind, int(edit["index"])) - 1
            edit["was"] = entries[at]  # the record keeps what the entry said before, for anyone reading the decision later
            entries[at] = str(edit["text"]).strip()
            rec[_SECTION[kind]] = entries
    for kind in ("need", "deliverable", "non_goal"):
        index = patch.get("remove_" + kind)
        if index is not None:
            entries = list(rec.get(_SECTION[kind]) or [])
            patch["removed_" + kind] = entries[_check_index(rec, kind, int(index)) - 1]
            del entries[int(index) - 1]
            rec[_SECTION[kind]] = entries
            _renumber_after_removal(project_root, rec, kind, int(index))
    found["status"] = "accepted"
    found["accepted_at"] = _now()
    found["decided_at"] = found["accepted_at"]
    if reason.strip():
        found["decision_reason"] = reason.strip()
    if signed_by.strip():
        found["signed_by"] = signed_by.strip()
    if signature.strip():
        found["signature"] = signature.strip()
    rec["proposals"] = proposals
    rec["version"] = int(rec.get("version") or 1) + 1
    save_brief(project_root, rec)
    return load_brief(project_root)


def reject_proposal(
    project_root: Path, proposal_id: str, *, reason: str = "", signed_by: str = "", signature: str = ""
) -> dict[str, Any]:
    rec = load_brief(project_root)
    proposals = list(rec.get("proposals") or [])
    for p in proposals:
        if p.get("id") == proposal_id:
            if p.get("status") != "open":
                raise ValueError(f"proposal {proposal_id} is {p.get('status')}")
            p["status"] = "rejected"
            p["rejected_at"] = _now()
            p["decided_at"] = p["rejected_at"]
            if reason.strip():
                p["decision_reason"] = reason.strip()
            if signed_by.strip():
                p["signed_by"] = signed_by.strip()
            if signature.strip():
                p["signature"] = signature.strip()
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
