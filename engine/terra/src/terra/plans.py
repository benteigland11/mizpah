"""Evidence plans — *above* typed knowns.

Layering:

  probes (open instruments)
  runs (stamped readings)
  knowns / unknowns  — type = number | boolean  (scalar filters)
  **plans**          — multi / sequence dossiers composed of legs
  (product wiring)

A plan is not a type. Legs *use* types.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_plan import (
    build_plan,
    can_accept_run_on_leg,
    can_claim_plan_confidence,
    format_plan_summary,
    get_leg,
    parse_leg_spec,
    recompute_plan_node,
    validate_plan_record,
)
from .number_type import CONFIDENCE_SET, KNOWN_STATUSES
from .paths import ensure_plans_store, plan_path, plans_root, run_dir
from .probe_run import RUN_META_NAME

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PLAN_SCHEMA_VERSION = 1
PLAN_STATUSES = KNOWN_STATUSES  # provisional | active | contested | …


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def validate_plan_file_record(
    data: Any, *, expected_id: str | None = None
) -> list[str]:
    blocks: list[str] = []
    if not isinstance(data, dict):
        return ["plan must be a JSON object"]
    if data.get("schema_version") != PLAN_SCHEMA_VERSION:
        blocks.append(
            f"schema_version must be {PLAN_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )
    pid = data.get("id")
    if not isinstance(pid, str) or not pid.strip():
        blocks.append("id must be a non-empty string")
    elif expected_id is not None and pid != expected_id:
        blocks.append(f"id {pid!r} does not match filename {expected_id!r}")
    if data.get("role") != "plan":
        blocks.append("role must be 'plan'")
    claim = data.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        blocks.append("claim must be a non-empty string")
    status = data.get("status")
    if status not in PLAN_STATUSES:
        blocks.append(
            f"status must be one of {sorted(PLAN_STATUSES)}, got {status!r}"
        )
    conf = data.get("confidence")
    if conf not in CONFIDENCE_SET:
        blocks.append(
            f"confidence must be one of {sorted(CONFIDENCE_SET)}, got {conf!r}"
        )
    blocks.extend(validate_plan_record(data))
    if conf in CONFIDENCE_SET and conf != "low":
        ok, msg = can_claim_plan_confidence(data, conf)
        if not ok:
            blocks.append(msg)
    return blocks


def create_plan(
    project_root: Path,
    plan_id: str,
    *,
    claim: str,
    mode: str = "all",
    legs: list[str] | list[dict[str, Any]],
    status: str = "provisional",
    notes: str = "",
    force: bool = False,
) -> Path:
    if not _SLUG_RE.match(plan_id):
        raise ValueError(f"plan id {plan_id!r} must match {_SLUG_RE.pattern}")
    if not claim or not str(claim).strip():
        raise ValueError("claim is required")
    if not legs:
        raise ValueError(
            "plan requires at least one --leg "
            "(e.g. rcon:boolean:rcon_up hostiles:number:hostile_count:n=3)"
        )
    if status not in PLAN_STATUSES:
        raise ValueError(f"status must be one of {sorted(PLAN_STATUSES)}")

    ensure_plans_store(project_root)
    path = plan_path(project_root, plan_id)
    if path.exists() and not force:
        raise FileExistsError(f"plan already exists: {path}")

    parsed: list[dict[str, Any]] = []
    for item in legs:
        if isinstance(item, dict):
            parsed.append(item)
        else:
            parsed.append(parse_leg_spec(str(item)))
    plan_body = build_plan(mode=mode, legs=parsed)
    now = _now()
    record: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "id": plan_id,
        "role": "plan",
        "claim": claim.strip(),
        "status": status,
        "confidence": "low",
        "confidence_derived": "low",
        "run_ids": [],
        "probe_ids": [],
        "plan": plan_body,
        "stats": {
            "kind": "plan",
            "mode": mode,
            "satisfied_count": 0,
            "leg_count": len(parsed),
            "all_satisfied": False,
        },
        "notes": notes or "",
        "created_at": now,
        "updated_at": now,
    }
    record = recompute_plan_node(
        record, project_root=project_root, run_dir_fn=run_dir
    )
    blocks = validate_plan_file_record(record, expected_id=plan_id)
    if blocks:
        raise ValueError("invalid plan:\n  - " + "\n  - ".join(blocks))
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def load_plan(project_root: Path, plan_id: str) -> dict[str, Any]:
    path = plan_path(project_root, plan_id)
    if not path.is_file():
        raise FileNotFoundError(f"plan not found: {plan_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_plan(project_root: Path, record: dict[str, Any]) -> Path:
    pid = record["id"]
    record = dict(record)
    record["updated_at"] = _now()
    record = recompute_plan_node(
        record, project_root=project_root, run_dir_fn=run_dir
    )
    blocks = validate_plan_file_record(record, expected_id=pid)
    if blocks:
        raise ValueError("invalid plan:\n  - " + "\n  - ".join(blocks))
    ensure_plans_store(project_root)
    path = plan_path(project_root, pid)
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def link_run_plan(
    project_root: Path,
    plan_id: str,
    run_id: str,
    *,
    leg_id: str,
    primary: bool = False,
) -> dict[str, Any]:
    if not leg_id:
        raise ValueError("plan link-run requires --leg <leg_id>")
    if not (run_dir(project_root, run_id) / RUN_META_NAME).is_file():
        raise FileNotFoundError(f"run not found: {run_id}")
    meta = json.loads(
        (run_dir(project_root, run_id) / RUN_META_NAME).read_text(encoding="utf-8")
    )
    if meta.get("voided"):
        raise ValueError(
            f"run {run_id} is voided — cannot link "
            f"(terra run unvoid, or use another run)"
        )
    rec = load_plan(project_root, plan_id)
    rec = recompute_plan_node(
        rec, project_root=project_root, run_dir_fn=run_dir
    )
    plan = rec.get("plan") or {}
    ok, msg = can_accept_run_on_leg(plan, leg_id)
    if not ok:
        raise ValueError(msg)
    leg = get_leg(plan, leg_id)
    if leg is None:
        raise ValueError(f"unknown leg {leg_id!r}")
    rids = list(leg.get("run_ids") or [])
    if run_id not in rids:
        rids.append(run_id)
    for lg in plan.get("legs") or []:
        if lg.get("id") == leg_id:
            lg["run_ids"] = rids
            if primary or lg.get("primary_run_id") is None:
                lg["primary_run_id"] = run_id
            break
    rec["plan"] = plan
    pid = meta.get("probe_id")
    if isinstance(pid, str) and pid.strip():
        pids = list(rec.get("probe_ids") or [])
        if pid not in pids:
            pids.append(pid)
        rec["probe_ids"] = pids
    save_plan(project_root, rec)
    return load_plan(project_root, plan_id)


def unlink_run_plan(
    project_root: Path,
    plan_id: str,
    run_id: str,
    *,
    leg_id: str | None = None,
) -> dict[str, Any]:
    rec = load_plan(project_root, plan_id)
    plan = rec.get("plan") or {}
    legs = list(plan.get("legs") or [])
    found = False
    for lg in legs:
        if leg_id and lg.get("id") != leg_id:
            continue
        rids = list(lg.get("run_ids") or [])
        if run_id in rids:
            found = True
            rids = [x for x in rids if x != run_id]
            lg["run_ids"] = rids
            if lg.get("primary_run_id") == run_id:
                lg["primary_run_id"] = rids[-1] if rids else None
    if not found:
        raise ValueError(
            f"run {run_id} not linked"
            + (f" on leg {leg_id}" if leg_id else " on any leg")
        )
    plan["legs"] = legs
    rec["plan"] = plan
    save_plan(project_root, rec)
    return load_plan(project_root, plan_id)


def promote_plan(
    project_root: Path,
    plan_id: str,
    confidence: str,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    if confidence not in CONFIDENCE_SET:
        raise ValueError(f"confidence must be one of {sorted(CONFIDENCE_SET)}")
    rec = load_plan(project_root, plan_id)
    rec = recompute_plan_node(
        rec, project_root=project_root, run_dir_fn=run_dir
    )
    ok, msg = can_claim_plan_confidence(rec, confidence)
    if not ok:
        raise ValueError(msg)
    rec["confidence"] = confidence
    if status is not None:
        if status not in PLAN_STATUSES:
            raise ValueError(f"status must be one of {sorted(PLAN_STATUSES)}")
        rec["status"] = status
    elif confidence in ("med", "high") and rec.get("status") == "provisional":
        rec["status"] = "active"
    save_plan(project_root, rec)
    return load_plan(project_root, plan_id)


def delete_plan(project_root: Path, plan_id: str) -> Path:
    path = plan_path(project_root, plan_id)
    if not path.is_file():
        raise FileNotFoundError(f"plan not found: {plan_id}")
    path.unlink()
    return path


def list_plans(project_root: Path) -> list[dict[str, Any]]:
    root = plans_root(project_root)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data = recompute_plan_node(
                data, project_root=project_root, run_dir_fn=run_dir
            )
        except (json.JSONDecodeError, OSError) as e:
            out.append(
                {"id": path.stem, "ok": False, "blocks": [str(e)], "record": None}
            )
            continue
        blocks = validate_plan_file_record(data, expected_id=path.stem)
        out.append(
            {
                "id": path.stem,
                "ok": len(blocks) == 0,
                "blocks": blocks,
                "record": data,
            }
        )
    return out


def describe_plan(project_root: Path, plan_id: str) -> dict[str, Any]:
    rec = load_plan(project_root, plan_id)
    rec = recompute_plan_node(
        rec, project_root=project_root, run_dir_fn=run_dir
    )
    return {"record": rec, "plan_lines": format_plan_summary(rec)}


def find_plans_linking_run(
    project_root: Path, run_id: str
) -> list[str]:
    """Plan ids on the active map that still reference run_id on any leg."""
    ids: list[str] = []
    root = plans_root(project_root)
    if not root.is_dir():
        return ids
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        plan = data.get("plan") or {}
        for lg in plan.get("legs") or []:
            if run_id in (lg.get("run_ids") or []):
                ids.append(path.stem)
                break
    return ids
