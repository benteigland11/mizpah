"""Cohorts — coupled knowns that are only valid as a set.

A converged solve (e.g. an aircraft sizing loop) emits several quantities
that are mutually consistent ONLY within one solve. Individually each
known can look healthy while jointly describing a design that never
existed (wing area from Tuesday's solve, empty weight from Thursday's).

A cohort declares that membership explicitly. Consistency is COMPUTED,
never stored (like staleness): members must carry identical live
evidence run sets. Mixed cohorts block gate and loud reads; the fix is
one action — re-solve once, ``terra cohort link-run`` fans the run out
to every member.

Membership is the only stored state: ``.terra/map/<scope>/cohorts/``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    cohort_path,
    cohorts_root,
    ensure_cohorts_store,
    known_path,
    run_dir,
)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_known(project_root: Path, known_id: str) -> dict[str, Any]:
    path = known_path(project_root, known_id)
    if not path.is_file():
        raise FileNotFoundError(f"known not found: {known_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_cohort(project_root: Path, cohort_id: str) -> dict[str, Any]:
    path = cohort_path(project_root, cohort_id)
    if not path.is_file():
        raise FileNotFoundError(f"cohort not found: {cohort_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_cohort(project_root: Path, record: dict[str, Any]) -> Path:
    record = dict(record)
    record["updated_at"] = _now()
    ensure_cohorts_store(project_root)
    path = cohort_path(project_root, record["id"])
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def list_cohorts(project_root: Path) -> list[dict[str, Any]]:
    root = cohorts_root(project_root)
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def find_cohort_for(
    project_root: Path, known_id: str
) -> dict[str, Any] | None:
    for c in list_cohorts(project_root):
        if known_id in (c.get("members") or []):
            return c
    return None


def create_cohort(
    project_root: Path,
    cohort_id: str,
    *,
    members: list[str],
    title: str = "",
) -> dict[str, Any]:
    if not _SLUG_RE.match(cohort_id):
        raise ValueError(f"cohort id must match {_SLUG_RE.pattern}")
    if cohort_path(project_root, cohort_id).is_file():
        raise FileExistsError(f"cohort already exists: {cohort_id}")
    seen: list[str] = []
    for kid in members or []:
        if kid in seen:
            continue
        _load_known(project_root, kid)  # must exist on this map
        other = find_cohort_for(project_root, kid)
        if other is not None:
            raise ValueError(
                f"known {kid} already belongs to cohort {other.get('id')!r} "
                f"— a known has one coupling context"
            )
        seen.append(kid)
    if not seen:
        raise ValueError("cohort needs at least one member known")
    rec = {
        "id": cohort_id,
        "title": (title or "").strip(),
        "members": seen,
        "created_at": _now(),
        "updated_at": _now(),
    }
    save_cohort(project_root, rec)
    return load_cohort(project_root, cohort_id)


def add_member(
    project_root: Path, cohort_id: str, known_id: str
) -> dict[str, Any]:
    rec = load_cohort(project_root, cohort_id)
    if known_id in (rec.get("members") or []):
        return rec
    _load_known(project_root, known_id)
    other = find_cohort_for(project_root, known_id)
    if other is not None and other.get("id") != cohort_id:
        raise ValueError(
            f"known {known_id} already belongs to cohort {other.get('id')!r}"
        )
    rec["members"] = list(rec.get("members") or []) + [known_id]
    save_cohort(project_root, rec)
    return load_cohort(project_root, cohort_id)


def set_cohort(
    project_root: Path,
    cohort_id: str,
    *,
    members: list[str] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Update cohort metadata or replace its coupled-known membership."""
    rec = load_cohort(project_root, cohort_id)
    if members is None and title is None:
        raise ValueError("cohort set needs --members and/or --title")
    if members is not None:
        seen: list[str] = []
        for kid in members:
            if kid in seen:
                continue
            _load_known(project_root, kid)
            other = find_cohort_for(project_root, kid)
            if other is not None and other.get("id") != cohort_id:
                raise ValueError(
                    f"known {kid} already belongs to cohort {other.get('id')!r}"
                )
            seen.append(kid)
        if not seen:
            raise ValueError("cohort needs at least one member known")
        rec["members"] = seen
    if title is not None:
        rec["title"] = title.strip()
    save_cohort(project_root, rec)
    return load_cohort(project_root, cohort_id)


def delete_cohort(project_root: Path, cohort_id: str) -> Path:
    """Delete the coupling declaration without deleting its member knowns."""
    path = cohort_path(project_root, cohort_id)
    if not path.is_file():
        raise FileNotFoundError(f"cohort not found: {cohort_id}")
    path.unlink()
    return path


def adopt_cohort(
    project_root: Path, cohort_id: str, *, from_map: str
) -> dict[str, Any]:
    """Adopt a whole cohort one hop up — coupled knowns move as a set."""
    from .knowns import adopt_known
    from .paths import map_parent, scoped_map

    to_map = map_parent(project_root, from_map)
    if to_map is None:
        raise ValueError("cannot adopt from 'global' — it has no parent")
    with scoped_map(from_map):
        rec = load_cohort(project_root, cohort_id)
        chk = check_cohort(project_root, rec)
        if not chk["consistent"]:
            raise ValueError(
                f"cohort {cohort_id!r} is inconsistent — re-solve before "
                "adopting:\n  - " + "\n  - ".join(chk["problems"])
            )
    members = list(rec.get("members") or [])
    with scoped_map(to_map):
        if cohort_path(project_root, cohort_id).is_file():
            raise FileExistsError(
                f"cohort {cohort_id} already exists on {to_map!r}"
            )
    for kid in members:
        adopt_known(project_root, kid, from_map=from_map, _cohort_ok=True)
    with scoped_map(to_map):
        create_cohort(
            project_root, cohort_id, members=members, title=rec.get("title") or ""
        )
        return load_cohort(project_root, cohort_id)


def _live_runs(project_root: Path, run_ids: list[str]) -> set[str]:
    from .probe_run import RUN_META_NAME

    live: set[str] = set()
    for rid in run_ids or []:
        meta_path = run_dir(project_root, rid) / RUN_META_NAME
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not meta.get("voided"):
            live.add(rid)
    return live


def check_cohort(
    project_root: Path, cohort: dict[str, Any] | str
) -> dict[str, Any]:
    """Computed consistency: members must share identical live run sets."""
    rec = (
        load_cohort(project_root, cohort)
        if isinstance(cohort, str)
        else cohort
    )
    members = list(rec.get("members") or [])
    per_member: dict[str, set[str]] = {}
    missing_knowns: list[str] = []
    for kid in members:
        try:
            k = _load_known(project_root, kid)
        except FileNotFoundError:
            missing_knowns.append(kid)
            continue
        per_member[kid] = _live_runs(project_root, list(k.get("run_ids") or []))
    union: set[str] = set().union(*per_member.values()) if per_member else set()
    common: set[str] = (
        set.intersection(*per_member.values()) if per_member else set()
    )
    diffs = {
        kid: {
            "missing": sorted(union - runs),
            "runs": sorted(runs),
        }
        for kid, runs in per_member.items()
        if runs != union
    }
    consistent = (
        not missing_knowns
        and bool(per_member)
        and bool(union)
        and not diffs
    )
    problems: list[str] = []
    for kid in missing_knowns:
        problems.append(f"member known missing on this map: {kid}")
    if per_member and not union:
        problems.append("no member has any live evidence run")
    for kid, d in diffs.items():
        problems.append(
            f"{kid} lacks runs {d['missing']} carried by other members"
        )
    return {
        "id": rec.get("id"),
        "members": members,
        "consistent": consistent,
        "common_runs": sorted(common),
        "problems": problems,
        "per_member": {k: sorted(v) for k, v in per_member.items()},
    }


def link_run_cohort(
    project_root: Path, cohort_id: str, run_id: str
) -> dict[str, Any]:
    """Fan one solve's run out to every member — the whole-family refresh."""
    from .knowns import link_run_known

    rec = load_cohort(project_root, cohort_id)
    linked: list[str] = []
    for kid in list(rec.get("members") or []):
        link_run_known(project_root, kid, run_id)
        linked.append(kid)
    return {
        "id": cohort_id,
        "run_id": run_id,
        "linked": linked,
        "check": check_cohort(project_root, cohort_id),
    }


def cohort_violations(project_root: Path) -> list[dict[str, Any]]:
    """Gate/attention feed for the active map scope."""
    out: list[dict[str, Any]] = []
    for c in list_cohorts(project_root):
        chk = check_cohort(project_root, c)
        if chk["consistent"]:
            continue
        out.append(
            {
                "kind": "cohort_inconsistent",
                "id": str(c.get("id")),
                "why": (
                    f"cohort {c.get('id')}: members are not backed by the "
                    f"same solve — {'; '.join(chk['problems'])} "
                    f"(re-solve once: terra cohort link-run {c.get('id')} "
                    f"<run_id>)"
                ),
                "members": chk["members"],
                "problems": chk["problems"],
            }
        )
    return out
