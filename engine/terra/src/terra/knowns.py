"""Knowns — typed anchors. Types: number, boolean (scalar filters only).

Multi/sequence evidence lives one layer up: ``terra.plans`` / ``terra plan``.
"""

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
    CONFIDENCE_SET,
    KNOWN_STATUSES,
    MAP_TYPES,
    RETIRED_STATUSES,
    SCALAR_TYPES,
    can_claim_confidence,
    empty_stats,
    recompute_typed_node,
)
from .paths import (
    ensure_knowns_store,
    known_path,
    knowns_root,
    map_chain,
    map_parent,
    run_dir,
    scoped_map,
)
from .probe_run import RUN_META_NAME

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
KNOWN_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def validate_known_record(data: Any, *, expected_id: str | None = None) -> list[str]:
    blocks: list[str] = []
    if not isinstance(data, dict):
        return ["known must be a JSON object"]
    if data.get("schema_version") != KNOWN_SCHEMA_VERSION:
        blocks.append(
            f"schema_version must be {KNOWN_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )
    kid = data.get("id")
    if not isinstance(kid, str) or not kid.strip():
        blocks.append("id must be a non-empty string")
    elif expected_id is not None and kid != expected_id:
        blocks.append(f"id {kid!r} does not match filename {expected_id!r}")

    claim = data.get("claim")
    if not isinstance(claim, str) or not claim.strip():
        blocks.append("claim must be a non-empty string")

    mtype = data.get("type")
    if mtype not in MAP_TYPES:
        blocks.append(
            f"type must be one of {sorted(MAP_TYPES)}, got {mtype!r}"
        )

    status = data.get("status")
    if status not in KNOWN_STATUSES:
        blocks.append(
            f"status must be one of {sorted(KNOWN_STATUSES)}, got {status!r}"
        )

    conf = data.get("confidence")
    if conf not in CONFIDENCE_SET:
        blocks.append(
            f"confidence must be one of {sorted(CONFIDENCE_SET)}, got {conf!r}"
        )

    if mtype in SCALAR_TYPES:
        q = data.get("quantity")
        if not isinstance(q, str) or not q.strip():
            blocks.append(
                f"type={mtype} requires quantity (stable measure name, "
                f"e.g. hostile_count or rcon_reachable)"
            )
        stats = data.get("stats")
        if stats is not None and not isinstance(stats, dict):
            blocks.append("stats must be an object when present")
        elif isinstance(stats, dict) and conf in CONFIDENCE_SET:
            ok, msg = can_claim_confidence(stats, conf, map_type=mtype)
            if not ok:
                blocks.append(msg)
    elif mtype == "relation":
        for field, what in (
            ("quantity", "y measure name"),
            ("x_quantity", "x meaning, e.g. alpha_deg"),
        ):
            v = data.get(field)
            if not isinstance(v, str) or not v.strip():
                blocks.append(f"type=relation requires {field} ({what})")
        stats = data.get("stats")
        if stats is not None and not isinstance(stats, dict):
            blocks.append("stats must be an object when present")
        elif isinstance(stats, dict) and conf in CONFIDENCE_SET:
            ok, msg = can_claim_confidence(stats, conf, map_type="relation")
            if not ok:
                blocks.append(msg)
    elif mtype == "formula":
        blocks.extend(
            validate_formula_fields(data.get("expression"), data.get("vars"))
        )
        stats = data.get("stats")
        if stats is not None and not isinstance(stats, dict):
            blocks.append("stats must be an object when present")
        elif isinstance(stats, dict) and conf in CONFIDENCE_SET:
            ok, msg = can_claim_confidence(stats, conf, map_type="formula")
            if not ok:
                blocks.append(msg)

    for key in ("run_ids", "probe_ids"):
        if key in data and data[key] is not None:
            if not isinstance(data[key], list):
                blocks.append(f"{key} must be a list")
            else:
                for i, item in enumerate(data[key]):
                    if not isinstance(item, str) or not item.strip():
                        blocks.append(f"{key}[{i}] must be a non-empty string")

    return blocks


_EVIDENCE_AT_BIRTH_MSG = (
    "knowns require evidence at birth — no run, no known.\n"
    "  Survey path (the only birth path):\n"
    "    terra unknown create <slug> --type … --quantity … --claim \"…\" --evidence \"…\"\n"
    "    terra unknown link-run <slug> <run_id>\n"
    "    terra unknown graduate <slug>            # → known + resolves the unknown"
)


def create_known(
    project_root: Path,
    known_id: str,
    *,
    claim: str,
    quantity: str | None = None,
    map_type: str = "number",
    unit: str = "",
    confidence: str = "low",
    status: str = "provisional",
    run_id: str | None = None,
    notes: str = "",
    force: bool = False,
    expression: str | None = None,
    vars: dict[str, Any] | list[str] | str | None = None,
    run_ids: list[str] | None = None,
    probe_ids: list[str] | None = None,
    origin_unknown_id: str | None = None,
    tolerance: Any = None,
    x_quantity: str | None = None,
    x_unit: str = "",
) -> Path:
    if not _SLUG_RE.match(known_id):
        raise ValueError(f"known id {known_id!r} must match {_SLUG_RE.pattern}")
    if not claim or not str(claim).strip():
        raise ValueError("claim is required")
    if not run_id and not run_ids:
        raise ValueError(_EVIDENCE_AT_BIRTH_MSG)
    if map_type not in MAP_TYPES:
        raise ValueError(
            f"type must be one of {sorted(MAP_TYPES)} "
            f"(use `terra plan` for multi/sequence evidence)"
        )
    if confidence not in CONFIDENCE_SET:
        raise ValueError(f"confidence must be one of {sorted(CONFIDENCE_SET)}")
    if status not in KNOWN_STATUSES:
        raise ValueError(f"status must be one of {sorted(KNOWN_STATUSES)}")

    ensure_knowns_store(project_root)
    path = known_path(project_root, known_id)
    if path.exists() and not force:
        raise FileExistsError(f"known already exists: {path}")

    all_run_ids = list(run_ids or [])
    if run_id and run_id not in all_run_ids:
        all_run_ids.insert(0, run_id)
    primary_run = run_id or (all_run_ids[0] if all_run_ids else None)

    now = _now()
    if map_type == "formula":
        vars_spec = parse_vars_arg(vars)
        expr = (expression or "").strip()
        ferr = validate_formula_fields(expr, vars_spec)
        if ferr:
            raise ValueError("invalid formula:\n  - " + "\n  - ".join(ferr))
        record: dict[str, Any] = {
            "schema_version": KNOWN_SCHEMA_VERSION,
            "id": known_id,
            "type": "formula",
            "role": "known",
            "claim": claim.strip(),
            "expression": expr,
            "vars": vars_spec,
            "status": status,
            "confidence": "low",
            "run_ids": all_run_ids,
            "probe_ids": list(probe_ids or []),
            "primary_run_id": primary_run,
            "stats": empty_formula_stats(),
            "confidence_derived": "low",
            "notes": notes or "",
            "created_at": now,
            "updated_at": now,
        }
        bound_known_ids = [
            str(spec.get("known_id"))
            for spec in vars_spec.values()
            if isinstance(spec, dict) and spec.get("known_id")
        ]
        if known_id in bound_known_ids:
            raise ValueError(f"formula known {known_id!r} cannot bind to itself")
        if bound_known_ids:
            record["deps"] = {
                "knowns": [{"id": kid, "as_of": None} for kid in bound_known_ids],
                "files": [],
            }
    else:
        if not quantity or not str(quantity).strip():
            raise ValueError(f"quantity is required for type={map_type}")
        if map_type == "relation" and (
            not x_quantity or not str(x_quantity).strip()
        ):
            raise ValueError("type=relation requires x_quantity")
        record = {
            "schema_version": KNOWN_SCHEMA_VERSION,
            "id": known_id,
            "type": map_type,
            "role": "known",
            "claim": claim.strip(),
            "quantity": quantity.strip(),
            "unit": (unit or "").strip(),
            "status": status,
            "confidence": "low",
            "run_ids": all_run_ids,
            "probe_ids": list(probe_ids or []),
            "primary_run_id": primary_run,
            **(
                {
                    "x_quantity": str(x_quantity).strip(),
                    "x_unit": (x_unit or "").strip(),
                }
                if map_type == "relation"
                else {}
            ),
            "stats": empty_stats(map_type),
            "confidence_derived": "low",
            "notes": notes or "",
            "created_at": now,
            "updated_at": now,
        }
    for rid in all_run_ids:
        meta_path = run_dir(project_root, rid) / RUN_META_NAME
        if not meta_path.is_file() and map_type == "formula":
            from .paths import find_run_dir

            visible = find_run_dir(project_root, rid)
            if visible is not None:
                meta_path = visible[0] / RUN_META_NAME
        if not meta_path.is_file():
            raise FileNotFoundError(f"run not found: {rid}")
    if origin_unknown_id:
        record["origin_unknown_id"] = origin_unknown_id
    if tolerance is not None:
        from .corroboration import parse_tolerance

        parse_tolerance(tolerance)  # loud on junk
        record["tolerance"] = tolerance
    record = recompute_typed_node(
        record, project_root=project_root, run_dir_fn=run_dir
    )
    if record.get("deps"):
        from .staleness import stamp_deps

        stamp_deps(project_root, record)
    ok, _ = can_claim_confidence(
        record["stats"], confidence, map_type=map_type
    )
    if ok:
        record["confidence"] = confidence

    blocks = validate_known_record(record, expected_id=known_id)
    if blocks:
        raise ValueError("invalid known:\n  - " + "\n  - ".join(blocks))

    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _live_runs(
    project_root: Path, run_ids: list[str], *, through_parents: bool = False
) -> list[str]:
    out: list[str] = []
    for rid in run_ids or []:
        meta_path = run_dir(project_root, rid) / RUN_META_NAME
        if not meta_path.is_file() and through_parents:
            from .paths import find_run_dir

            visible = find_run_dir(project_root, rid)
            if visible is not None:
                meta_path = visible[0] / RUN_META_NAME
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not meta.get("voided"):
            out.append(rid)
    return out


def _check_mergeable(contributors: list[dict[str, Any]]) -> None:
    """Same question, different angles: type + quantity must match."""
    first = contributors[0]
    mtype = first.get("type")
    for unk in contributors:
        uid = unk.get("id")
        if unk.get("type") not in MAP_TYPES:
            raise ValueError(
                f"unknown {uid!r} is untyped — graduation needs a typed "
                f"question ({sorted(MAP_TYPES)}).\n"
                "  Set the type where the question lives:\n"
                f"    terra unknown create {uid} --force --type number "
                "--quantity <q> --claim \"…\"  # or boolean/formula"
            )
        if unk.get("type") != mtype:
            raise ValueError(
                f"cannot merge: unknown {uid!r} is type={unk.get('type')!r} "
                f"but {first.get('id')!r} is type={mtype!r} — different "
                "questions do not funnel into one known"
            )
        if mtype in (*SCALAR_TYPES, "relation") and unk.get(
            "quantity"
        ) != first.get("quantity"):
            raise ValueError(
                f"cannot merge: unknown {uid!r} measures "
                f"{unk.get('quantity')!r} but {first.get('id')!r} measures "
                f"{first.get('quantity')!r} — same known needs same quantity"
            )
        if mtype == "relation" and unk.get("x_quantity") != first.get(
            "x_quantity"
        ):
            raise ValueError(
                f"cannot merge: unknown {uid!r} has x_quantity="
                f"{unk.get('x_quantity')!r} but {first.get('id')!r} has "
                f"{first.get('x_quantity')!r}"
            )
        if mtype == "formula" and (
            unk.get("expression") != first.get("expression")
            or unk.get("vars") != first.get("vars")
        ):
            raise ValueError(
                f"cannot merge formula unknowns {first.get('id')!r} and "
                f"{uid!r}: expression/vars differ"
            )
    tolerances = {
        str(u.get("tolerance"))
        for u in contributors
        if u.get("tolerance") is not None
    }
    if len(tolerances) > 1:
        raise ValueError(
            f"cannot merge: contributors declare conflicting tolerances "
            f"{sorted(tolerances)} — align them first "
            "(terra unknown create --force --within …)"
        )


def graduate_unknown(
    project_root: Path,
    unknown_id: str,
    *,
    known_id: str | None = None,
    with_ids: list[str] | None = None,
    into: str | None = None,
    notes: str | None = None,
    force: bool = False,
    cohort_id: str | None = None,
) -> dict[str, Any]:
    """Graduate unknown(s) into a known — the only birth path.

    ``with_ids`` merges sibling unknowns asking the same question (same
    type + quantity) into one known at birth: evidence unions, every
    contributor resolves, ``origin_unknown_ids`` records them all.
    ``into`` merges the unknown(s) into an EXISTING known instead.
    Every path requires >=1 live (non-voided) linked run in the union.
    """
    from .unknowns import load_unknown, save_unknown

    if into and known_id:
        raise ValueError("--as and --into are mutually exclusive")
    kid = into or known_id or unknown_id
    if not _SLUG_RE.match(kid):
        raise ValueError(f"known id {kid!r} must match {_SLUG_RE.pattern}")

    ids: list[str] = [unknown_id]
    for extra in with_ids or []:
        if extra not in ids:
            ids.append(extra)
    contributors = [load_unknown(project_root, uid) for uid in ids]
    _check_mergeable(contributors)
    first = contributors[0]
    mtype = str(first.get("type"))

    # Union live evidence across contributors (order-preserving)
    all_runs: list[str] = []
    all_probes: list[str] = []
    calculation_input_knowns: list[str] = []
    for unk in contributors:
        for rid in _live_runs(
            project_root,
            list(unk.get("run_ids") or []),
            through_parents=mtype == "formula",
        ):
            if rid not in all_runs:
                all_runs.append(rid)
        for pid in unk.get("probe_ids") or []:
            if pid not in all_probes:
                all_probes.append(pid)
    for rid in all_runs:
        meta_path = run_dir(project_root, rid) / RUN_META_NAME
        if not meta_path.is_file() and mtype == "formula":
            from .paths import find_run_dir

            visible = find_run_dir(project_root, rid)
            if visible is not None:
                meta_path = visible[0] / RUN_META_NAME
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("source_type") != "calculation":
            continue
        for binding in (meta.get("input_bindings") or {}).values():
            if isinstance(binding, str) and binding.startswith("known:"):
                dep_id = binding.removeprefix("known:")
                if dep_id not in calculation_input_knowns:
                    calculation_input_knowns.append(dep_id)
    if not all_runs:
        raise ValueError(
            f"unknown(s) {ids} have no live (non-voided) linked runs — "
            "no run, no known.\n"
            f"    terra probe run <probe_id> --to '…' --json\n"
            f"    terra unknown link-run {unknown_id} <run_id>\n"
            f"    terra unknown graduate {unknown_id}"
        )
    tolerance = next(
        (u.get("tolerance") for u in contributors if u.get("tolerance") is not None),
        None,
    )

    if into:
        rec = load_known(project_root, kid)  # loud if missing
        if rec.get("type") != mtype:
            raise ValueError(
                f"cannot merge into known {kid!r}: it is type="
                f"{rec.get('type')!r}, unknowns are type={mtype!r}"
            )
        if mtype in (*SCALAR_TYPES, "relation") and rec.get(
            "quantity"
        ) != first.get("quantity"):
            raise ValueError(
                f"cannot merge into known {kid!r}: it measures "
                f"{rec.get('quantity')!r}, unknowns measure "
                f"{first.get('quantity')!r}"
            )
        rids = list(rec.get("run_ids") or [])
        for rid in all_runs:
            if rid not in rids:
                rids.append(rid)
        rec["run_ids"] = rids
        pids = list(rec.get("probe_ids") or [])
        for pid in all_probes:
            if pid not in pids:
                pids.append(pid)
        rec["probe_ids"] = pids
        origins = list(rec.get("origin_unknown_ids") or [])
        if rec.get("origin_unknown_id") and rec["origin_unknown_id"] not in origins:
            origins.insert(0, rec["origin_unknown_id"])
        for uid in ids:
            if uid not in origins:
                origins.append(uid)
        rec["origin_unknown_ids"] = origins
        if tolerance is not None and rec.get("tolerance") is None:
            rec["tolerance"] = tolerance
        if rec.get("deps"):
            from .staleness import stamp_deps

            stamp_deps(project_root, rec)
        save_known(project_root, rec)
    else:
        primary = first.get("primary_run_id")
        if primary not in all_runs:
            primary = all_runs[0]
        create_known(
            project_root,
            kid,
            claim=str(first.get("claim") or ""),
            quantity=first.get("quantity"),
            map_type=mtype,
            unit=str(first.get("unit") or ""),
            run_id=primary,
            run_ids=all_runs,
            probe_ids=all_probes,
            notes=notes if notes is not None else str(first.get("notes") or ""),
            force=force,
            expression=first.get("expression"),
            vars=first.get("vars"),
            origin_unknown_id=unknown_id,
            tolerance=tolerance,
            x_quantity=first.get("x_quantity"),
            x_unit=str(first.get("x_unit") or ""),
        )
        if len(ids) > 1:
            rec = load_known(project_root, kid)
            rec["origin_unknown_ids"] = list(ids)
            save_known(project_root, rec)

    if calculation_input_knowns:
        rec = load_known(project_root, kid)
        deps = rec.setdefault("deps", {"knowns": [], "files": []})
        known_deps = deps.setdefault("knowns", [])
        existing = {str(row.get("id")) for row in known_deps if isinstance(row, dict)}
        for dep_id in calculation_input_knowns:
            if dep_id not in existing:
                known_deps.append({"id": dep_id, "as_of": None})
        from .staleness import stamp_deps

        stamp_deps(project_root, rec)
        save_known(project_root, rec)

    for unk in contributors:
        unk["status"] = "resolved"
        unk["resolved_by"] = f"known:{kid}"
        save_unknown(project_root, unk)

    if cohort_id:
        # Coupled births: join the cohort (created on first member)
        from .cohorts import add_member, create_cohort
        from .paths import cohort_path

        if cohort_path(project_root, cohort_id).is_file():
            add_member(project_root, cohort_id, kid)
        else:
            create_cohort(project_root, cohort_id, members=[kid])

    return load_known(project_root, kid)


def load_known(project_root: Path, known_id: str) -> dict[str, Any]:
    path = known_path(project_root, known_id)
    if not path.is_file():
        raise FileNotFoundError(f"known not found: {known_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def find_known_map(
    project_root: Path, known_id: str, map_id: str | None = None
) -> str | None:
    """Owning map for a known, walking the parent chain child-first."""
    for mid in map_chain(project_root, map_id):
        with scoped_map(mid):
            if known_path(project_root, known_id).is_file():
                return mid
    return None


def load_known_chain(
    project_root: Path, known_id: str
) -> tuple[dict[str, Any], str]:
    """Load a known through the parent chain → (record, owning map id)."""
    owner = find_known_map(project_root, known_id)
    if owner is None:
        raise FileNotFoundError(
            f"known not found: {known_id} (searched map chain "
            f"{' -> '.join(map_chain(project_root))})"
        )
    with scoped_map(owner):
        return load_known(project_root, known_id), owner


def shadowed_ancestor(
    project_root: Path, known_id: str, map_id: str | None = None
) -> str | None:
    """Map id of an ancestor whose known this map's shadows, else None."""
    chain = map_chain(project_root, map_id)
    if not chain:
        return None
    with scoped_map(chain[0]):
        if not known_path(project_root, known_id).is_file():
            return None
    for mid in chain[1:]:
        with scoped_map(mid):
            if known_path(project_root, known_id).is_file():
                return mid
    return None


def save_known(project_root: Path, record: dict[str, Any]) -> Path:
    kid = record["id"]
    record = dict(record)
    record["updated_at"] = _now()
    if record.get("type") in MAP_TYPES:
        record = recompute_typed_node(
            record, project_root=project_root, run_dir_fn=run_dir
        )
    # validate after recompute (confidence demotion already applied)
    blocks = validate_known_record(record, expected_id=kid)
    if blocks:
        raise ValueError("invalid known:\n  - " + "\n  - ".join(blocks))
    ensure_knowns_store(project_root)
    path = known_path(project_root, kid)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def link_run_known(
    project_root: Path,
    known_id: str,
    run_id: str,
    *,
    primary: bool = False,
) -> dict[str, Any]:
    rec = load_known(project_root, known_id)
    meta_path = run_dir(project_root, run_id) / RUN_META_NAME
    if not meta_path.is_file() and rec.get("type") == "formula":
        from .paths import find_run_dir

        visible = find_run_dir(project_root, run_id)
        if visible is not None:
            meta_path = visible[0] / RUN_META_NAME
    if not meta_path.is_file():
        raise FileNotFoundError(f"run not found: {run_id}")
    try:
        meta = json.loads(
            meta_path.read_text(encoding="utf-8")
        )
        if meta.get("voided"):
            raise ValueError(
                f"run {run_id} is voided — cannot link "
                f"(terra run unvoid, or use another run)"
            )
        conv = meta.get("convergence")
        if isinstance(conv, dict) and not conv.get("converged"):
            raise ValueError(
                f"run {run_id} did not converge (residual="
                f"{conv.get('residual')!r} vs tol={conv.get('tol')!r}) — "
                f"an unsettled iterate is not evidence; fix the solve and "
                f"re-run the probe"
            )
        pid = meta.get("probe_id")
        if isinstance(pid, str) and pid.strip():
            pids = list(rec.get("probe_ids") or [])
            if pid not in pids:
                pids.append(pid)
            rec["probe_ids"] = pids
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
    if rec.get("deps"):
        # New evidence = honest re-derivation → refresh dep freshness stamps
        from .staleness import stamp_deps

        stamp_deps(project_root, rec)
    save_known(project_root, rec)
    return load_known(project_root, known_id)


def unlink_run_known(
    project_root: Path,
    known_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Detach a sample; stats + confidence recompute (over-claimed conf demotes)."""
    rec = load_known(project_root, known_id)
    rids = [x for x in (rec.get("run_ids") or []) if x != run_id]
    rec["run_ids"] = rids
    if rec.get("primary_run_id") == run_id:
        rec["primary_run_id"] = rids[-1] if rids else None
    save_known(project_root, rec)
    return load_known(project_root, known_id)


def delete_known(project_root: Path, known_id: str) -> Path:
    path = known_path(project_root, known_id)
    if not path.is_file():
        raise FileNotFoundError(f"known not found: {known_id}")
    path.unlink()
    return path


def promote_known(
    project_root: Path,
    known_id: str,
    confidence: str,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    if confidence not in CONFIDENCE_SET:
        raise ValueError(f"confidence must be one of {sorted(CONFIDENCE_SET)}")
    rec = load_known(project_root, known_id)
    rec = recompute_typed_node(rec, project_root=project_root, run_dir_fn=run_dir)
    ok, msg = can_claim_confidence(
        rec["stats"], confidence, map_type=rec.get("type")
    )
    if not ok:
        raise ValueError(msg)
    rec["confidence"] = confidence
    if status is not None:
        if status not in KNOWN_STATUSES:
            raise ValueError(f"status must be one of {sorted(KNOWN_STATUSES)}")
        rec["status"] = status
    elif confidence in ("med", "high") and rec.get("status") == "provisional":
        rec["status"] = "active"
    save_known(project_root, rec)
    return load_known(project_root, known_id)


ADOPTION_MIN_CONF = "med"


def _adoption_problems(
    project_root: Path, known_id: str, rec: dict[str, Any]
) -> list[str]:
    """Admission bar at the map border — same spirit as design admission."""
    from .staleness import compute_staleness

    problems: list[str] = []
    stats = rec.get("stats") or {}
    if not (rec.get("run_ids") or []) or not (stats.get("n") or 0):
        problems.append(f"known {known_id} is unbacked (n=0)")
    conf = str(rec.get("confidence") or "low")
    rank = {"low": 0, "med": 1, "high": 2}
    if rank.get(conf, 0) < rank[ADOPTION_MIN_CONF]:
        problems.append(
            f"known {known_id} is confidence={conf}, adoption needs "
            f">= {ADOPTION_MIN_CONF} (link more runs + promote)"
        )
    corr = stats.get("corroboration") or {}
    if corr.get("agree") is False and corr.get("accepted") is not True:
        problems.append(
            f"known {known_id}: methods disagree — resolve before adopting"
        )
    info = compute_staleness(project_root).get(known_id) or {}
    if info.get("stale"):
        problems.append(
            f"known {known_id} is stale: " + "; ".join(info.get("reasons") or [])
        )
    return problems


def adopt_known(
    project_root: Path,
    known_id: str,
    *,
    from_map: str,
    _cohort_ok: bool = False,
) -> dict[str, Any]:
    """Promote a known one hop up the map tree (child → its parent map).

    Copies the record plus its live run directories; stamps provenance
    (``adopted_from`` / ``adopted_to``). Admission bar re-checked at the
    border; deps must already resolve from the destination map.
    """
    import shutil

    to_map = map_parent(project_root, from_map)
    if to_map is None:
        raise ValueError(
            "cannot adopt from 'global' — it has no parent (global is the top)"
        )

    with scoped_map(from_map):
        rec = load_known(project_root, known_id)  # loud if missing
        problems = _adoption_problems(project_root, known_id, rec)
        if problems:
            raise ValueError(
                f"adoption refused ({from_map} -> {to_map}):\n  - "
                + "\n  - ".join(problems)
            )
        from .cohorts import find_cohort_for

        cohort = find_cohort_for(project_root, known_id)
        if cohort is not None and not _cohort_ok:
            raise ValueError(
                f"known {known_id} is a member of cohort {cohort['id']!r} — "
                f"coupled knowns adopt as a set:\n"
                f"    terra cohort adopt {cohort['id']} --from {from_map}"
            )
        live = _live_runs(project_root, list(rec.get("run_ids") or []))
        src_runs = {rid: run_dir(project_root, rid) for rid in live}

    # Deps must resolve from the destination chain
    missing = [
        d.get("id")
        for d in (rec.get("deps") or {}).get("knowns") or []
        if find_known_map(project_root, str(d.get("id") or ""), to_map) is None
    ]
    if missing:
        raise ValueError(
            f"adoption refused: deps not resolvable from {to_map!r}: "
            f"{missing} — adopt the dependency chain bottom-up first:\n"
            + "\n".join(
                f"    terra known adopt {m} --from {from_map}" for m in missing
            )
        )

    with scoped_map(to_map):
        if known_path(project_root, known_id).is_file():
            raise FileExistsError(
                f"known {known_id} already exists on {to_map!r} — merge new "
                f"evidence there instead (terra unknown graduate --into "
                f"{known_id}), or rename before adopting"
            )
        for rid, src in src_runs.items():
            dst = run_dir(project_root, rid)
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst)
        adopted = dict(rec)
        adopted["run_ids"] = live
        adopted["adopted_from"] = {
            "map": from_map,
            "known_id": known_id,
            "at": _now(),
        }
        if adopted.get("deps"):
            from .staleness import stamp_deps

            stamp_deps(project_root, adopted)
        save_known(project_root, adopted)

    with scoped_map(from_map):
        src_rec = load_known(project_root, known_id)
        src_rec["adopted_to"] = {"map": to_map, "at": _now()}
        save_known(project_root, src_rec)

    with scoped_map(to_map):
        return load_known(project_root, known_id)


def add_dependency(
    project_root: Path, known_id: str, dep_specs: list[str]
) -> dict[str, Any]:
    """Declare deps: ``known:<id>`` / ``file:<relpath>``. Stamps freshness now."""
    from .staleness import DEP_KIND_KNOWN, parse_dep, stamp_deps

    rec = load_known(project_root, known_id)
    deps = rec.setdefault("deps", {})
    for spec in dep_specs:
        kind, target = parse_dep(spec)
        if kind == DEP_KIND_KNOWN:
            if target == known_id:
                raise ValueError(f"known {known_id} cannot depend on itself")
            if find_known_map(project_root, target) is None:
                raise FileNotFoundError(
                    f"known dep not found: {target} (searched the map "
                    f"parent chain)"
                )
            rows = deps.setdefault("knowns", [])
            if not any(r.get("id") == target for r in rows):
                rows.append({"id": target, "as_of": None})
        else:
            if not (project_root / target).is_file():
                raise FileNotFoundError(
                    f"file dep not found: {target} (path is relative to "
                    f"project root)"
                )
            rows = deps.setdefault("files", [])
            if not any(r.get("path") == target for r in rows):
                rows.append({"path": target, "sha256": None, "linked_at": _now()})
    stamp_deps(project_root, rec)
    save_known(project_root, rec)
    return load_known(project_root, known_id)


def set_tolerance(
    project_root: Path, known_id: str, *, within: Any
) -> dict[str, Any]:
    """Declare method-agreement tolerance ('5%' rel or absolute number)."""
    from .corroboration import parse_tolerance

    parse_tolerance(within)  # loud on junk
    rec = load_known(project_root, known_id)
    rec["tolerance"] = within
    save_known(project_root, rec)  # recompute re-judges corroboration
    return load_known(project_root, known_id)


def accept_spread(
    project_root: Path, known_id: str, *, reason: str
) -> dict[str, Any]:
    """Accept an irreducible cross-method spread as honest uncertainty.

    Recorded decision (like reaffirm): reads unblock and carry the band,
    confidence caps at med, gate violation clears. Auto-revoked when the
    spread grows beyond the accepted stamp; obsolete when methods agree.
    """
    if not reason or not str(reason).strip():
        raise ValueError("accept-spread requires --reason (no silent acceptance)")
    rec = load_known(project_root, known_id)
    rec = recompute_typed_node(rec, project_root=project_root, run_dir_fn=run_dir)
    corr = (rec.get("stats") or {}).get("corroboration") or {}
    if corr.get("agree") is None:
        raise ValueError(
            f"known {known_id}: nothing to accept — agreement is not "
            "judgeable (need 2+ methods and a declared tolerance: "
            f"terra known tolerance {known_id} --within 5%)"
        )
    if corr.get("agree") is True:
        raise ValueError(
            f"known {known_id}: methods already agree within tolerance — "
            "nothing to accept"
        )
    rec["accepted_spread"] = {
        "at": _now(),
        "reason": reason.strip(),
        "spread": corr.get("spread"),
        "tolerance": corr.get("tolerance"),
        "band": corr.get("band"),
    }
    save_known(project_root, rec)
    return load_known(project_root, known_id)


def reaffirm_known(
    project_root: Path, known_id: str, *, reason: str
) -> dict[str, Any]:
    """Verified-unchanged escape hatch: re-stamp deps, keep the trail."""
    from .staleness import stamp_deps

    if not reason or not str(reason).strip():
        raise ValueError("reaffirm requires --reason (no silent freshness)")
    rec = load_known(project_root, known_id)
    stamp_deps(project_root, rec)
    log = list(rec.get("reaffirmed") or [])
    log.append({"at": _now(), "reason": reason.strip()})
    rec["reaffirmed"] = log
    save_known(project_root, rec)
    return load_known(project_root, known_id)


def set_known_status(
    project_root: Path,
    known_id: str,
    status: str,
    *,
    notes: str | None = None,
) -> dict[str, Any]:
    if status not in KNOWN_STATUSES:
        raise ValueError(f"status must be one of {sorted(KNOWN_STATUSES)}")
    rec = load_known(project_root, known_id)
    rec["status"] = status

    # Un-retiring must not leave the tombstone behind. The read path keys the
    # refusal on `status`, so restoring `active` restores the value — but a
    # stale `superseded` block then sits on a live belief, reading as "this was
    # retired for <reason>" while it is being served as current. That is a
    # record contradicting itself. Move it to `superseded_history` so the audit
    # trail survives without masquerading as the current state.
    # Keyed on the RESULTING state, not the transition: the invariant is that a
    # non-retired known never carries a tombstone. Transition-keying missed
    # records already sitting in the contradictory state (e.g. un-retired via
    # an earlier code path), so they could never self-heal.
    if status not in RETIRED_STATUSES and rec.get("superseded"):
        hist = list(rec.get("superseded_history") or [])
        hist.append({**rec.pop("superseded"), "reverted_at": _now()})
        rec["superseded_history"] = hist

    if notes is not None:
        rec["notes"] = notes
    save_known(project_root, rec)
    return load_known(project_root, known_id)


def supersede_known(
    project_root: Path,
    known_id: str,
    *,
    reason: str,
    superseded_by: str | None = None,
    refuted: bool = False,
) -> dict[str, Any]:
    """Retire a known-wrong belief: keep it as history, refuse it as current.

    The soft tombstone between `known set` (metadata only, can't touch a
    bug-derived value) and `known delete` (destructive, dangling consumers/
    design params, map-chain ambiguity). Sets status to `refuted` (the belief
    was wrong) or `superseded` (a better belief replaces it) and stamps a
    `superseded` block. The read path (`readings.read_known`) then REFUSES to
    hand the value out as current unless `--allow-superseded`, so a bug-derived
    number can never silently be consumed again.
    """
    if not reason or not str(reason).strip():
        raise ValueError(
            "supersede requires --reason (no silent retirement of a belief)"
        )
    rec = load_known(project_root, known_id)
    if superseded_by is not None:
        by = str(superseded_by).strip()
        if by:
            if by == known_id:
                raise ValueError(
                    f"known {known_id} cannot supersede itself"
                )
            # The replacement must exist so the tombstone points somewhere real.
            find = load_known(project_root, by)  # raises if missing
            del find
        else:
            superseded_by = None
    rec["status"] = "refuted" if refuted else "superseded"
    rec["superseded"] = {
        "at": _now(),
        "reason": reason.strip(),
        "by": superseded_by,
        "refuted": bool(refuted),
    }
    save_known(project_root, rec)

    # Supersession is a LOCAL write. A copy of the same id on another map keeps
    # its own status, so retiring a belief here can leave an identical belief
    # standing ACTIVE elsewhere — and a child retiring its copy does nothing
    # upward, so the PARENT keeps serving the retired value to every consumer.
    # This left a TAILLESS airframe certified as the master OML on `global` for
    # days after `cad` retired its own copy (2026-07-28), and did the same to
    # `mtow_final`. Terra gave no signal either time. Warn loudly; do NOT
    # auto-propagate — writes stay local by design, and silently retiring a
    # belief on a map the caller never named would be worse than the bug.
    _warn_sibling_copies(project_root, known_id)
    return load_known(project_root, known_id)


def _warn_sibling_copies(project_root: Path, known_id: str) -> None:
    """stderr NOTE naming every OTHER map still holding this id un-retired."""
    import sys as _sys

    try:
        from .number_type import RETIRED_STATUSES
        from .paths import get_active_map_id, list_maps, scoped_map

        here = get_active_map_id(project_root)
        others: list[tuple[str, str]] = []
        for m in list_maps(project_root):
            mid = m.get("id")
            if not mid or mid == here:
                continue
            with scoped_map(mid):
                if not known_path(project_root, known_id).is_file():
                    continue
                try:
                    sib = load_known(project_root, known_id)
                except (OSError, ValueError):
                    continue
            if sib.get("status") not in RETIRED_STATUSES:
                others.append(
                    (str(mid), f"{sib.get('status')}/{sib.get('confidence') or '?'}")
                )
        if others:
            listed = ", ".join(f"{mid} ({st})" for mid, st in others)
            print(
                f"NOTE: {known_id} is retired HERE ({here}) but still ACTIVE "
                f"on: {listed}. Supersession does NOT propagate — those copies "
                "keep serving the value to their consumers.",
                file=_sys.stderr,
            )
            for mid, _ in others:
                print(
                    f"    terra --map {mid} known supersede {known_id} "
                    "--reason '…'",
                    file=_sys.stderr,
                )
    except Exception:  # noqa: BLE001 — a warning must never fail a write
        pass


# Freehand --value is the agent footgun that used to look like a silent no-op
# when agents invented `terra known set` (subcommand missing) + 2>/dev/null.
_FREEHAND_VALUE_MSG = (
    "freehand --value is not supported (would skip probe evidence and stats).\n"
    "  Knowns are born by graduating an evidence-bearing unknown:\n"
    "    terra unknown create <slug> --type … --quantity … --claim \"…\"\n"
    "    terra unknown link-run <slug> <run_id>\n"
    "    terra unknown graduate <slug>\n"
    "  Metadata only (existing known):\n"
    "    terra known set <id> --claim \"…\" --notes \"…\""
)


def set_known(
    project_root: Path,
    known_id: str,
    *,
    claim: str | None = None,
    notes: str | None = None,
    map_type: str | None = None,
    quantity: str | None = None,
    unit: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    run_id: str | None = None,
    expression: str | None = None,
    vars: dict[str, Any] | list[str] | str | None = None,
    value: Any = None,
) -> tuple[dict[str, Any], str]:
    """Update an existing known (agent-facing ``terra known set``).

    Returns ``(record, action)`` where action is ``\"updated\"``. Knowns are
    born only via :func:`graduate_unknown`; freehand ``value`` is rejected.
    """
    if value is not None:
        raise ValueError(_FREEHAND_VALUE_MSG)

    path = known_path(project_root, known_id)
    exists = path.is_file()

    if not exists:
        raise ValueError(
            f"known {known_id!r} does not exist — set only updates.\n"
            "  " + _EVIDENCE_AT_BIRTH_MSG.replace("\n", "\n  ")
        )

    rec = load_known(project_root, known_id)
    changed = False

    if claim is not None:
        if not str(claim).strip():
            raise ValueError("claim must be non-empty when provided")
        rec["claim"] = claim.strip()
        changed = True
    if notes is not None:
        rec["notes"] = notes
        changed = True
    if unit is not None:
        rec["unit"] = unit.strip()
        changed = True
    if status is not None:
        if status not in KNOWN_STATUSES:
            raise ValueError(f"status must be one of {sorted(KNOWN_STATUSES)}")
        rec["status"] = status
        changed = True
    if quantity is not None:
        if rec.get("type") not in SCALAR_TYPES:
            raise ValueError(
                f"cannot set --quantity on type={rec.get('type')!r} "
                f"(only {sorted(SCALAR_TYPES)})"
            )
        if not str(quantity).strip():
            raise ValueError("quantity must be non-empty when provided")
        rec["quantity"] = quantity.strip()
        changed = True
    if expression is not None:
        if rec.get("type") != "formula":
            raise ValueError("cannot set --expression on non-formula known")
        rec["expression"] = expression.strip()
        changed = True
    if vars is not None:
        if rec.get("type") != "formula":
            raise ValueError("cannot set --var on non-formula known")
        rec["vars"] = parse_vars_arg(vars)
        bound_known_ids = [
            str(spec.get("known_id"))
            for spec in rec["vars"].values()
            if isinstance(spec, dict) and spec.get("known_id")
        ]
        if known_id in bound_known_ids:
            raise ValueError(f"formula known {known_id!r} cannot bind to itself")
        rec["deps"] = {
            "knowns": [{"id": kid, "as_of": None} for kid in bound_known_ids],
            "files": list((rec.get("deps") or {}).get("files") or []),
        }
        changed = True
    if map_type is not None and map_type != rec.get("type"):
        raise ValueError(
            f"cannot change type {rec.get('type')!r} → {map_type!r} "
            "(delete + recreate, or create a new id)"
        )

    if confidence is not None:
        if confidence not in CONFIDENCE_SET:
            raise ValueError(f"confidence must be one of {sorted(CONFIDENCE_SET)}")
        rec = recompute_typed_node(
            rec, project_root=project_root, run_dir_fn=run_dir
        )
        ok, msg = can_claim_confidence(
            rec.get("stats") or {}, confidence, map_type=rec.get("type")
        )
        if not ok:
            raise ValueError(msg)
        rec["confidence"] = confidence
        changed = True

    if run_id is not None:
        save_known(project_root, rec) if changed else None
        # link always writes
        link_run_known(project_root, known_id, run_id)
        return load_known(project_root, known_id), "updated"

    if not changed:
        raise ValueError(
            "nothing to set — pass at least one of: "
            "--claim --notes --quantity --unit --status --confidence "
            "--from-run --expression --var\n"
            "(freehand --value is rejected; see terra known set --help)"
        )

    if vars is not None and rec.get("deps"):
        from .staleness import stamp_deps

        stamp_deps(project_root, rec)
    save_known(project_root, rec)
    return load_known(project_root, known_id), "updated"


def list_knowns(project_root: Path) -> list[dict[str, Any]]:
    root = knowns_root(project_root)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("type") in MAP_TYPES:
                data = recompute_typed_node(
                    data, project_root=project_root, run_dir_fn=run_dir
                )
        except (json.JSONDecodeError, OSError) as e:
            out.append({"id": path.stem, "ok": False, "blocks": [str(e)], "record": None})
            continue
        blocks = validate_known_record(data, expected_id=path.stem)
        out.append(
            {
                "id": path.stem,
                "ok": len(blocks) == 0,
                "blocks": blocks,
                "record": data,
            }
        )
    return out


def validate_known_file(project_root: Path, known_id: str) -> dict[str, Any]:
    path = known_path(project_root, known_id)
    if not path.is_file():
        return {
            "ok": False,
            "id": known_id,
            "blocks": [f"not found: {known_id}"],
            "record": None,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("type") in MAP_TYPES:
        data = recompute_typed_node(
            data, project_root=project_root, run_dir_fn=run_dir
        )
    blocks = validate_known_record(data, expected_id=known_id)
    return {
        "ok": len(blocks) == 0,
        "id": known_id,
        "blocks": blocks,
        "record": data,
        "path": str(path),
    }


def describe_known(project_root: Path, known_id: str) -> dict[str, Any]:
    rec = load_known(project_root, known_id)
    if rec.get("type") in MAP_TYPES:
        rec = recompute_typed_node(
            rec, project_root=project_root, run_dir_fn=run_dir
        )
    return {"record": rec}
