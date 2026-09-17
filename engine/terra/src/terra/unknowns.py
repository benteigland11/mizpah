"""Create / update / list / validate map unknowns."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .formula_type import (
    empty_formula_stats,
    parse_vars_arg,
    validate_formula_fields,
)
from .number_type import (
    MAP_TYPES,
    SCALAR_TYPES,
    empty_stats,
    recompute_typed_node,
)
from .paths import (
    ensure_unknowns_store,
    run_dir,
    unknown_path,
    unknowns_root,
)
from .probe_run import RUN_META_NAME
from .unknown_contract import (
    ACTIVE_STATUSES,
    UNKNOWN_SCHEMA_VERSION,
    UNKNOWN_STATUSES,
    validate_unknown_record,
)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def create_unknown(
    project_root: Path,
    unknown_id: str,
    *,
    claim: str,
    evidence_needed: str = "",
    blocks_build: bool = True,
    probe_id: str | None = None,
    notes: str = "",
    force: bool = False,
    map_type: str | None = None,
    quantity: str | None = None,
    unit: str = "",
    expression: str | None = None,
    vars: dict[str, Any] | list[str] | str | None = None,
    tolerance: Any = None,
    x_quantity: str | None = None,
    x_unit: str = "",
    role: str = "unknown",
    assumed_value: Any = None,
    assumption_reason: str = "",
) -> Path:
    if not _SLUG_RE.match(unknown_id):
        raise ValueError(
            f"unknown id {unknown_id!r} must match {_SLUG_RE.pattern}"
        )
    if not claim or not str(claim).strip():
        raise ValueError("claim is required (what we do not know)")

    ensure_unknowns_store(project_root)
    path = unknown_path(project_root, unknown_id)
    if path.exists() and not force:
        raise FileExistsError(f"unknown already exists: {path}")

    if map_type is not None and map_type not in MAP_TYPES:
        raise ValueError(
            f"type must be one of {sorted(MAP_TYPES)} or omitted, got {map_type!r}"
        )
    if map_type in SCALAR_TYPES and (
        not quantity or not str(quantity).strip()
    ):
        raise ValueError(f"type={map_type} requires --quantity")
    if map_type == "relation":
        if not quantity or not str(quantity).strip():
            raise ValueError("type=relation requires --quantity (the y measure)")
        if not x_quantity or not str(x_quantity).strip():
            raise ValueError(
                "type=relation requires --x-quantity (what x means, "
                "e.g. alpha_deg)"
            )
    if map_type == "formula":
        vars_spec = parse_vars_arg(vars)
        expr = (expression or "").strip()
        ferr = validate_formula_fields(expr, vars_spec)
        if ferr:
            raise ValueError("invalid formula:\n  - " + "\n  - ".join(ferr))
    else:
        vars_spec = {}
        expr = ""

    if role not in ("unknown", "assumption"):
        raise ValueError("role must be unknown or assumption")
    if role == "assumption":
        if map_type not in ("number", "boolean"):
            raise ValueError("assumptions currently support type=number|boolean")
        if not str(assumption_reason or "").strip():
            raise ValueError("assumption requires a reason")
        if map_type == "number" and (
            not isinstance(assumed_value, (int, float))
            or isinstance(assumed_value, bool)
        ):
            raise ValueError("number assumption value must be numeric")
        if map_type == "boolean" and not isinstance(assumed_value, bool):
            raise ValueError("boolean assumption value must be true or false")
        blocks_build = False

    now = _now()
    # create with --probe behaves like link-probe (probing, not open)
    status = "probing" if probe_id else "open"
    record: dict[str, Any] = {
        "schema_version": UNKNOWN_SCHEMA_VERSION,
        "id": unknown_id,
        "role": role,
        "claim": claim.strip(),
        "status": status,
        "blocks_build": bool(blocks_build),
        "evidence_needed": (evidence_needed or "").strip()
        or "a validated probe reading that answers the claim",
        "probe_id": probe_id,
        "probe_ids": [probe_id] if probe_id else [],
        "run_ids": [],
        "primary_run_id": None,
        "resolved_by": None,
        "notes": notes or "",
        "created_at": now,
        "updated_at": now,
    }
    if role == "assumption":
        record["assumed_value"] = assumed_value
        record["assumption_reason"] = assumption_reason.strip()
        record["assumption_revisions"] = [
            {"at": now, "value": assumed_value, "reason": assumption_reason.strip()}
        ]
    if map_type in SCALAR_TYPES:
        record["type"] = map_type
        record["quantity"] = quantity.strip()
        record["unit"] = (unit or "").strip()
        record["stats"] = empty_stats(map_type)
        record["confidence_derived"] = "low"
    elif map_type == "relation":
        record["type"] = "relation"
        record["quantity"] = quantity.strip()
        record["unit"] = (unit or "").strip()
        record["x_quantity"] = str(x_quantity).strip()
        record["x_unit"] = (x_unit or "").strip()
        record["stats"] = empty_stats("relation")
        record["confidence_derived"] = "low"
    elif map_type == "formula":
        record["type"] = "formula"
        record["expression"] = expr
        record["vars"] = vars_spec
        record["stats"] = empty_formula_stats()
        record["confidence_derived"] = "low"
    if map_type in SCALAR_TYPES or map_type == "relation":
        if tolerance is not None:
            from .corroboration import parse_tolerance

            parse_tolerance(tolerance)  # loud on junk
            record["tolerance"] = tolerance
    blocks = validate_unknown_record(record, expected_id=unknown_id)
    if blocks:
        raise ValueError("invalid unknown:\n  - " + "\n  - ".join(blocks))

    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_unknown(project_root: Path, unknown_id: str) -> dict[str, Any]:
    path = unknown_path(project_root, unknown_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown not found: {unknown_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_unknown(project_root: Path, record: dict[str, Any]) -> Path:
    uid = record["id"]
    record = dict(record)
    record["updated_at"] = _now()
    if record.get("type") in MAP_TYPES:
        record = recompute_typed_node(
            record, project_root=project_root, run_dir_fn=run_dir
        )
    blocks = validate_unknown_record(record, expected_id=uid)
    if blocks:
        raise ValueError("invalid unknown:\n  - " + "\n  - ".join(blocks))
    ensure_unknowns_store(project_root)
    path = unknown_path(project_root, uid)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def set_status(
    project_root: Path,
    unknown_id: str,
    status: str,
    *,
    resolved_by: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    if status not in UNKNOWN_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(UNKNOWN_STATUSES)}, got {status!r}"
        )
    rec = load_unknown(project_root, unknown_id)
    rec["status"] = status
    if resolved_by is not None:
        rec["resolved_by"] = resolved_by
    if notes is not None:
        rec["notes"] = notes
    save_unknown(project_root, rec)
    return rec


def link_probe(
    project_root: Path, unknown_id: str, probe_id: str
) -> dict[str, Any]:
    """Attach a probe. Supports multiple probes via probe_ids; probe_id = primary."""
    rec = load_unknown(project_root, unknown_id)
    ids = list(rec.get("probe_ids") or [])
    # Seed from legacy primary so re-link does not drop the original probe
    legacy = rec.get("probe_id")
    if isinstance(legacy, str) and legacy.strip() and legacy not in ids:
        ids.append(legacy)
    if probe_id not in ids:
        ids.append(probe_id)
    rec["probe_ids"] = ids
    rec["probe_id"] = probe_id  # primary = most recently linked
    if rec.get("status") == "open":
        rec["status"] = "probing"
    save_unknown(project_root, rec)
    return rec


def _run_meta_path(project_root: Path, run_id: str) -> Path:
    return run_dir(project_root, run_id) / RUN_META_NAME



def _maps_holding_run(project_root, run_id: str) -> list[str]:
    """Which OTHER maps have this run on disk. Diagnostic only."""
    from .paths import list_maps, get_active_map_id, runs_root, scoped_map

    here = get_active_map_id(project_root)
    found: list[str] = []
    for m in list_maps(project_root):
        mid = m.get("id")
        if not mid or mid == here:
            continue
        with scoped_map(mid):
            if (runs_root(project_root) / run_id / RUN_META_NAME).is_file():
                found.append(str(mid))
    return found


class LinkAddedNoSample(ValueError):
    """A linked run contributed zero samples — the evidence is inert."""


def link_run(
    project_root: Path,
    unknown_id: str,
    run_id: str,
    *,
    primary: bool = False,
    allow_no_sample: bool = False,
) -> dict[str, Any]:
    """Attach a stamped run as structured evidence. Idempotent on run_id."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    run_id = run_id.strip()
    rec = load_unknown(project_root, unknown_id)
    meta_path = _run_meta_path(project_root, run_id)
    if not meta_path.is_file() and rec.get("type") == "formula":
        from .paths import find_run_dir

        visible = find_run_dir(project_root, run_id)
        if visible is not None:
            meta_path = visible[0] / RUN_META_NAME
    if not meta_path.is_file():
        # Runs are map-LOCAL by design (maps stay self-contained; adopt COPIES
        # run dirs rather than referencing across). But a bare "run not found"
        # sent agents hunting for a typo when the run existed on a SIBLING map
        # — it cost a lead a whole extra dispatch to re-stamp a sweep it
        # already had. Name the map that holds it, and the way across.
        hint = ""
        try:
            elsewhere = _maps_holding_run(project_root, run_id)
            if elsewhere:
                hint = (
                    f"\n  it EXISTS on: {', '.join(elsewhere)} — runs are "
                    "map-local by design, so you cannot link across maps.\n"
                    "  re-run the probe on THIS map, or promote the belief "
                    "with `terra known adopt` (which copies the run dirs)."
                )
        except Exception:  # noqa: BLE001 — a hint must never mask the error
            hint = ""
        raise FileNotFoundError(
            f"run not found: {run_id} (expected {meta_path}){hint}"
        )
    try:
        _meta_chk = json.loads(meta_path.read_text(encoding="utf-8"))
        if _meta_chk.get("voided"):
            raise ValueError(
                f"run {run_id} is voided — cannot link "
                f"(terra run unvoid, or use another run)"
            )
        _conv_chk = _meta_chk.get("convergence")
        if isinstance(_conv_chk, dict) and not _conv_chk.get("converged"):
            raise ValueError(
                f"run {run_id} did not converge (residual="
                f"{_conv_chk.get('residual')!r} vs tol="
                f"{_conv_chk.get('tol')!r}) — an unsettled iterate is not "
                f"evidence; fix the solve and re-run the probe"
            )
    except ValueError:
        raise
    except (json.JSONDecodeError, OSError):
        pass

    rids = list(rec.get("run_ids") or [])
    if run_id not in rids:
        rids.append(run_id)
    rec["run_ids"] = rids
    if primary or rec.get("primary_run_id") is None:
        rec["primary_run_id"] = run_id
    if rec.get("status") == "open":
        rec["status"] = "probing"
    # Best-effort: attach probe from run meta if present
    try:
        run_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        pid = run_meta.get("probe_id")
        if isinstance(pid, str) and pid.strip():
            pids = list(rec.get("probe_ids") or [])
            if pid not in pids:
                pids.append(pid)
            rec["probe_ids"] = pids
            if not rec.get("probe_id"):
                rec["probe_id"] = pid
    except (json.JSONDecodeError, OSError):
        pass
    if rec.get("type") in MAP_TYPES:
        before_n = ((rec.get("stats") or {}).get("n")) or 0
        rec = recompute_typed_node(
            rec, project_root=project_root, run_dir_fn=run_dir
        )
        after_n = ((rec.get("stats") or {}).get("n")) or 0
        # A link that adds NO sample is the worst kind of success: the run is
        # attached, the command reports OK, and n stays 0 — so the evidence
        # carries zero weight and nobody finds out until a downstream `known
        # get` refuses it. Five real FlightGear sessions were silently worth
        # nothing this way (2026-07-28) because the probe emitted
        # `elev_bias_refusal_fires_at_runtime` while the unknown declared
        # `elev_bias_refusal_observed_at_runtime`. Same proposition, different
        # spelling, zero evidence. Say so at link time, loudly.
        if after_n == before_n and not allow_no_sample:
            # This was a stderr NOTE from 2026-07-28. It kept being missed:
            # by 2026-08-08 the SAME defect had silently voided evidence at
            # least three more times in one day (canon empennage presence,
            # the D9 washout trade verdict, the tmp-path-literals sweep) —
            # each time the link "succeeded", n stayed 0, and the finding
            # could not graduate despite good evidence behind it. A warning
            # that has failed this often is not a warning, it is decoration.
            # REFUSE, and make the caller say `--allow-no-sample` on purpose.
            _warn_link_added_no_sample(project_root, rec, run_id)
            raise LinkAddedNoSample(
                _no_sample_message(project_root, rec, run_id)
            )
    save_unknown(project_root, rec)
    return rec


def _no_sample_message(
    project_root: Path, rec: dict[str, Any], run_id: str
) -> str:
    want = rec.get("quantity")
    emitted: list[str] = []
    try:
        meta_path = _run_meta_path(project_root, run_id)
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            emitted = sorted(
                {
                    str(m.get("quantity"))
                    for m in (meta.get("measures") or [])
                    if isinstance(m, dict) and m.get("quantity")
                }
            )
    except Exception:  # noqa: BLE001
        pass
    lines = [
        f"run {run_id} added NO sample to {rec.get('id')!r} — the link would "
        f"attach evidence worth nothing (n stays "
        f"{((rec.get('stats') or {}).get('n')) or 0}).",
        f"  this node declares quantity: {want!r}",
    ]
    if emitted:
        lines.append(
            "  the run emitted: " + ", ".join(repr(q) for q in emitted[:8])
            + (" …" if len(emitted) > 8 else "")
        )
        if want and want not in emitted:
            lines.append(
                "  NAME MISMATCH — same proposition, different spelling reads "
                "as no evidence at all. Name the PROPOSITION, not the "
                "instrument, and agree the quantity before the run."
            )
    else:
        lines.append(
            "  the run emitted no measures at all (status=error runs carry "
            "none and are structurally uncountable)."
        )
    lines.append(
        "  Fix the quantity name (or the probe), then re-link. To attach it "
        "anyway as provenance-only, pass --allow-no-sample."
    )
    return "\n".join(lines)


def _warn_link_added_no_sample(
    project_root: Path, rec: dict[str, Any], run_id: str
) -> None:
    """stderr NOTE when a linked run contributes no sample. Never raises."""
    import sys as _sys

    try:
        want = rec.get("quantity")
        emitted: list[str] = []
        meta_path = _run_meta_path(project_root, run_id)
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            emitted = sorted(
                {
                    str(m.get("quantity"))
                    for m in (meta.get("measures") or [])
                    if isinstance(m, dict) and m.get("quantity")
                }
            )
        print(
            f"NOTE: run {run_id} linked to {rec.get('id')} but added NO sample "
            f"(n is still {((rec.get('stats') or {}).get('n')) or 0}). This "
            "evidence currently carries ZERO weight.",
            file=_sys.stderr,
        )
        if want:
            print(f"    this node declares quantity: {want!r}", file=_sys.stderr)
        if emitted:
            print(
                f"    the run emitted: {', '.join(repr(q) for q in emitted[:8])}"
                + (" …" if len(emitted) > 8 else ""),
                file=_sys.stderr,
            )
            if want and want not in emitted:
                print(
                    "    NAME MISMATCH — same proposition, different spelling "
                    "reads as no evidence at all.",
                    file=_sys.stderr,
                )
        else:
            print(
                "    the run emitted no measures at all (status=error runs "
                "carry none and are structurally uncountable).",
                file=_sys.stderr,
            )
    except Exception:  # noqa: BLE001 — a warning must never fail the link
        pass


def link_calculation(
    project_root: Path,
    unknown_id: str,
    calculation_id: str,
    *,
    output: str | None = None,
    primary: bool = False,
) -> dict[str, Any]:
    """Link the latest fresh calculation result as typed evidence."""
    from .calculations import get_calculation

    unknown = load_unknown(project_root, unknown_id)
    result = get_calculation(project_root, calculation_id)
    profile = result.get("profile") or "expression"
    selected: dict[str, Any] | None = None
    if profile == "expression":
        if output:
            raise ValueError("--output applies only to profile=model calculations")
        selected = result
    elif output:
        selected = (result.get("outputs") or {}).get(output)
        if selected is None:
            raise ValueError(
                f"calculation {calculation_id!r} has no output {output!r}; "
                f"choose one of {sorted((result.get('outputs') or {}).keys())}"
            )
    elif unknown.get("type") in (*SCALAR_TYPES, "relation"):
        matches = [
            row
            for row in (result.get("outputs") or {}).values()
            if row.get("type") == unknown.get("type")
            and row.get("quantity") == unknown.get("quantity")
            and (
                unknown.get("type") != "relation"
                or row.get("x_quantity") == unknown.get("x_quantity")
            )
        ]
        if len(matches) != 1:
            raise ValueError(
                "model calculation output is ambiguous for this unknown; "
                "pass --output <name>"
            )
        selected = matches[0]

    if unknown.get("type") in (*SCALAR_TYPES, "relation") and selected is not None:
        if selected.get("type") != unknown.get("type"):
            raise ValueError(
                f"calculation output type={selected.get('type')!r} does not "
                f"match unknown type={unknown.get('type')!r}"
            )
        if selected.get("quantity") != unknown.get("quantity"):
            raise ValueError(
                f"calculation output quantity={selected.get('quantity')!r} does "
                f"not match unknown quantity={unknown.get('quantity')!r}"
            )
        if unknown.get("type") == "relation" and selected.get(
            "x_quantity"
        ) != unknown.get("x_quantity"):
            raise ValueError(
                f"calculation output x_quantity={selected.get('x_quantity')!r} "
                f"does not match unknown x_quantity={unknown.get('x_quantity')!r}"
            )
    run_id = result.get("evidence_run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(
            f"calculation {calculation_id!r} predates calculation evidence runs; "
            "rerun it with `terra calculation run`"
        )
    return link_run(project_root, unknown_id, run_id, primary=primary)


def unlink_run(
    project_root: Path, unknown_id: str, run_id: str
) -> dict[str, Any]:
    """Detach a run; typed stats recompute on save (bad sample drops out)."""
    rec = load_unknown(project_root, unknown_id)
    rids = [x for x in (rec.get("run_ids") or []) if x != run_id]
    rec["run_ids"] = rids
    if rec.get("primary_run_id") == run_id:
        rec["primary_run_id"] = rids[-1] if rids else None
    save_unknown(project_root, rec)
    return load_unknown(project_root, unknown_id)


def delete_unknown(project_root: Path, unknown_id: str) -> Path:
    """Remove unknown record from the active map (irreversible)."""
    path = unknown_path(project_root, unknown_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown not found: {unknown_id}")
    path.unlink()
    return path


def describe_unknown(project_root: Path, unknown_id: str) -> dict[str, Any]:
    """Record plus expanded linked runs (for show)."""
    rec = load_unknown(project_root, unknown_id)
    if rec.get("type") in MAP_TYPES:
        rec = recompute_typed_node(
            rec, project_root=project_root, run_dir_fn=run_dir
        )
    runs_out: list[dict[str, Any]] = []
    for rid in rec.get("run_ids") or []:
        meta_path = _run_meta_path(project_root, rid)
        entry: dict[str, Any] = {
            "id": rid,
            "primary": rid == rec.get("primary_run_id"),
            "exists": meta_path.is_file(),
            "status": None,
            "probe_id": None,
            "captured_at": None,
            "path": str(meta_path) if meta_path.is_file() else None,
        }
        if meta_path.is_file():
            try:
                m = json.loads(meta_path.read_text(encoding="utf-8"))
                entry["status"] = m.get("status")
                entry["probe_id"] = m.get("probe_id")
                time = m.get("time") or {}
                entry["captured_at"] = (
                    time.get("captured_at")
                    or time.get("finished_at")
                    or time.get("started_at")
                )
            except (json.JSONDecodeError, OSError):
                entry["exists"] = False
        runs_out.append(entry)
    return {"record": rec, "linked_runs": runs_out}


def list_unknowns(project_root: Path) -> list[dict[str, Any]]:
    root = unknowns_root(project_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            out.append(
                {
                    "id": path.stem,
                    "ok": False,
                    "blocks": [f"unreadable: {e}"],
                    "record": None,
                }
            )
            continue
        blocks = validate_unknown_record(data, expected_id=path.stem)
        out.append(
            {
                "id": path.stem,
                "ok": len(blocks) == 0,
                "blocks": blocks,
                "record": data,
            }
        )
    return out


def list_role(project_root: Path, role: str) -> list[dict[str, Any]]:
    """List unresolved records by epistemic role (legacy records are unknowns)."""
    return [
        row
        for row in list_unknowns(project_root)
        if (row.get("record") or {}).get("role", "unknown") == role
    ]


def validate_unknown_file(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        return {
            "ok": False,
            "id": path.stem,
            "path": str(path),
            "blocks": [f"not a file: {path}"],
            "record": None,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "id": path.stem,
            "path": str(path),
            "blocks": [f"invalid JSON: {e}"],
            "record": None,
        }
    blocks = validate_unknown_record(data, expected_id=path.stem)
    return {
        "ok": len(blocks) == 0,
        "id": path.stem,
        "path": str(path),
        "blocks": blocks,
        "record": data,
    }


def validate_all_unknowns(project_root: Path) -> dict[str, Any]:
    rows = list_unknowns(project_root)
    store_blocks: list[str] = []
    if not rows:
        # empty is ok — no unknowns yet is not a failure of the store
        pass
    any_fail = any(not r["ok"] for r in rows)
    active = [
        r
        for r in rows
        if r.get("record")
        and r["record"].get("status") in ACTIVE_STATUSES
    ]
    unknown_active = [
        r
        for r in active
        if r.get("record")
        and r["record"].get("role", "unknown") == "unknown"
    ]
    assumptions = [
        r
        for r in active
        if r.get("record") and r["record"].get("role") == "assumption"
    ]
    return {
        "ok": not any_fail,
        "blocks": store_blocks,
        "unknowns": rows,
        "active_count": len(active),
        "blocking_count": len(unknown_active),
        "assumption_count": len(assumptions),
    }
