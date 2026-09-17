"""Read path for knowns — the single place a number lives.

Ad-hoc tools and legacy integrations must read values from the map instead of
hardcoding copies. Terra calculations and declared-input probes receive values
through their binding contracts:

  from terra.readings import known
  MTOW = known("mtow")["value"]          # loud if missing/unbacked/stale

Reads are loud by default: missing, unbacked (n=0), stale (dep moved), or
below --min-conf all raise. Every read stamps a consumption edge under
``.terra/map/consumers/<known_id>.json`` so the dependency graph knows who
builds on which belief.

Consumer identity resolution (first hit wins):
  explicit arg > probe context (set by ``terra probe run``) >
  ``TERRA_CONSUMER`` env > ``tool:<sys.argv[0]>``
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .number_type import CONFIDENCE_SET, RETIRED_STATUSES
from .paths import (
    consumer_path,
    ensure_consumers_store,
    find_project_root,
    known_path,
)
from .staleness import compute_staleness

_CONF_RANK = {"low": 0, "med": 1, "high": 2}

# Module-level consumer context (single-process CLI; threads read it fine)
_CURRENT_CONSUMER: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


@contextmanager
def consumer_scope(consumer: str):
    """Set the reader identity for the duration (used by ``terra probe run``)."""
    global _CURRENT_CONSUMER
    prev = _CURRENT_CONSUMER
    _CURRENT_CONSUMER = consumer
    try:
        yield
    finally:
        _CURRENT_CONSUMER = prev


# --- run-level read provenance -------------------------------------------
# Declared probe `inputs` only cover explicit known:/assumption: bindings, and
# most probes declare none — they call read_known() from inside their own
# code. The run record therefore never said WHICH beliefs a measurement was
# computed against, so screening orphaned runs by design-of-record basis was
# only possible by bucketing on timestamp. Capturing the reads makes a run
# self-describing: "this number was produced against mtow_final=11986.5 as of
# 2026-07-24". That is the prerequisite for auditing the ~6,000 unattached
# runs without re-running any of them.
_READ_SINK: list[dict[str, Any]] | None = None


@contextmanager
def record_reads(sink: list[dict[str, Any]]):
    """Collect every known read inside the block. Restores unconditionally."""
    global _READ_SINK
    prev = _READ_SINK
    _READ_SINK = sink
    try:
        yield
    finally:
        _READ_SINK = prev


def _note_read(
    project_root: Path, known_id: str, owner: str, reading: dict[str, Any]
) -> None:
    """Append one read to the active sink. Never raises into the caller.

    The record lookup happens ONLY when a sink is active (i.e. inside a probe
    run), so ordinary CLI/consumer reads pay nothing for provenance they will
    not use. ``as_of`` is the field that makes basis-screening possible — a
    value alone cannot tell you which design era produced it.
    """
    if _READ_SINK is None:
        return
    try:
        as_of = None
        try:
            from .knowns import load_known
            from .paths import scoped_map

            with scoped_map(owner):
                as_of = (load_known(project_root, known_id) or {}).get(
                    "updated_at"
                )
        except Exception:
            as_of = None
        row = {
            "known_id": known_id,
            "map": owner,
            "value": reading.get("value", reading.get("mean")),
            "confidence": reading.get("confidence"),
            "as_of": as_of,
        }
        for flag in ("stale", "inherited", "conditional", "superseded"):
            if reading.get(flag):
                row[flag] = True
        if not any(r.get("known_id") == known_id and r.get("map") == owner
                   for r in _READ_SINK):
            _READ_SINK.append(row)
    except Exception:  # provenance must never break a measurement
        pass


def note_assumption_read(
    assumption_id: str, owner: str, reading: dict[str, Any]
) -> None:
    """Record an ASSUMPTION consumption into the active provenance sink.

    Assumptions are conditional BY DEFINITION, so a run that consumes one and
    does not say so hands every downstream belief an unconditional face. This
    mirrors _note_read but keys on `assumption_id` and always stamps
    `conditional: True` — there is no non-conditional assumption read.
    """
    if _READ_SINK is None:
        return
    try:
        row = {
            "assumption_id": assumption_id,
            "map": owner,
            "value": reading.get("value"),
            "conditional": True,
            "as_of": reading.get("updated_at"),
            "evidence_n": reading.get("evidence_n"),
        }
        if not any(
            r.get("assumption_id") == assumption_id and r.get("map") == owner
            for r in _READ_SINK
        ):
            _READ_SINK.append(row)
    except Exception:  # provenance must never break a measurement
        pass


def resolve_consumer(explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    if _CURRENT_CONSUMER:
        return _CURRENT_CONSUMER
    env = os.environ.get("TERRA_CONSUMER")
    if env and env.strip():
        return env.strip()
    argv0 = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "unknown"
    if argv0 in ("terra", "terra.exe"):
        # Interactive/agent CLI read — real tools should pass --consumer
        # or set TERRA_CONSUMER so the edge names who actually builds on it
        return "cli"
    return f"tool:{argv0}"


def record_consumption(
    project_root: Path, known_id: str, consumer: str
) -> Path:
    ensure_consumers_store(project_root)
    path = consumer_path(project_root, known_id)
    doc: dict[str, Any] = {"known_id": known_id, "consumers": []}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    rows = doc.setdefault("consumers", [])
    now = _now()
    for row in rows:
        if row.get("consumer") == consumer:
            row["last_read_at"] = now
            row["reads"] = int(row.get("reads") or 0) + 1
            break
    else:
        rows.append(
            {
                "consumer": consumer,
                "first_read_at": now,
                "last_read_at": now,
                "reads": 1,
            }
        )
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def list_consumers(project_root: Path, known_id: str) -> list[dict[str, Any]]:
    path = consumer_path(project_root, known_id)
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(doc.get("consumers") or [])


def extract_value(rec: dict[str, Any]) -> Any:
    """Canonical scalar for a known: number→mean, boolean→rate, formula→holds."""
    stats = rec.get("stats") or {}
    mtype = rec.get("type")
    if mtype == "number":
        return stats.get("mean")
    if mtype == "boolean":
        return stats.get("rate")
    if mtype == "formula":
        return stats.get("holds")
    if mtype == "relation":
        return stats.get("stations")
    return None


def read_known(
    project_root: Path,
    known_id: str,
    *,
    min_conf: str = "low",
    allow_stale: bool = False,
    allow_disagree: bool = False,
    allow_cohort_mismatch: bool = False,
    allow_superseded: bool = False,
    consumer: str | None = None,
    record: bool = True,
    at: float | None = None,
) -> dict[str, Any]:
    """Read a known for consumption. Loud on missing/unbacked/stale/low-conf.

    Resolves through the map parent chain (child shadows ancestor); the
    consumer edge lands on the owning map.
    """
    from .knowns import find_known_map
    from .paths import get_active_map_id, scoped_map

    if min_conf not in CONFIDENCE_SET:
        raise ValueError(f"min_conf must be one of {sorted(CONFIDENCE_SET)}")

    owner = find_known_map(project_root, known_id)
    if owner is None:
        raise FileNotFoundError(
            f"known not found: {known_id} (searched the map parent chain) — "
            f"survey it first "
            f"(terra unknown create … && terra unknown graduate …)"
        )
    active = get_active_map_id(project_root)
    with scoped_map(owner):
        reading = _read_known_here(
            project_root,
            known_id,
            min_conf=min_conf,
            allow_stale=allow_stale,
            allow_disagree=allow_disagree,
            allow_cohort_mismatch=allow_cohort_mismatch,
            allow_superseded=allow_superseded,
            consumer=consumer,
            record=record,
            at=at,
        )
    reading["map"] = owner
    if owner != active:
        reading["inherited"] = True
    _note_read(project_root, known_id, owner, reading)
    return reading


def _read_known_here(
    project_root: Path,
    known_id: str,
    *,
    min_conf: str,
    allow_stale: bool,
    allow_disagree: bool,
    allow_cohort_mismatch: bool,
    allow_superseded: bool,
    consumer: str | None,
    record: bool,
    at: float | None,
) -> dict[str, Any]:
    path = known_path(project_root, known_id)
    if not path.is_file():
        raise FileNotFoundError(
            f"known not found: {known_id} — survey it first "
            f"(terra unknown create … && terra unknown graduate …)"
        )
    rec = json.loads(path.read_text(encoding="utf-8"))

    status = str(rec.get("status") or "")
    if status in RETIRED_STATUSES and not allow_superseded:
        tomb = rec.get("superseded") or {}
        by = tomb.get("by")
        raise ValueError(
            f"known {known_id} is {status.upper()} - this belief was retired "
            f"as wrong/replaced ({tomb.get('reason') or 'no reason recorded'}) "
            "and must NOT be consumed as a current value.\n"
            + (
                f"    use the replacement instead: terra known get {by}\n"
                if by
                else ""
            )
            + f"    inspect the tombstone: terra known show {known_id}\n"
            f"    override only to read the historical value: "
            f"terra known get {known_id} --allow-superseded"
        )

    stats = rec.get("stats") or {}
    from .run_inputs import record_input_state

    evidence_inputs = record_input_state(project_root, rec)
    if evidence_inputs["stale"] and not allow_stale:
        raise ValueError(
            f"known {known_id} has stale probe inputs — rerun the probe with "
            f"current declared inputs: {evidence_inputs['stale_runs']}"
        )
    n = stats.get("n") or 0
    if not (rec.get("run_ids") or []) or n == 0:
        raise ValueError(
            f"known {known_id} is unbacked (n={n}, no live evidence) — "
            f"refusing to hand out a number nobody measured.\n"
            f"    terra known link-run {known_id} <run_id>"
        )

    conf = str(rec.get("confidence") or "low")
    if _CONF_RANK.get(conf, 0) < _CONF_RANK.get(min_conf, 0):
        raise ValueError(
            f"known {known_id} is confidence={conf}, below required "
            f"min_conf={min_conf}.\n"
            f"    terra known link-run {known_id} <run_id>  # more samples\n"
            f"    terra known promote {known_id} {min_conf}"
        )

    corr = stats.get("corroboration") or {}
    if (
        corr.get("agree") is False
        and corr.get("accepted") is not True
        and not allow_disagree
    ):
        raise ValueError(
            f"known {known_id}: methods DISAGREE "
            f"(spread={corr.get('spread')!r}, tolerance="
            f"{corr.get('tolerance')!r}) — one instrument is wrong; refusing "
            f"to hand out a number until resolved.\n"
            f"    terra known show {known_id}   # per-probe stats\n"
            f"    terra run void <bad_run> --reason '…'  # or fix the probe"
        )

    stale_info = compute_staleness(project_root).get(known_id) or {}
    if stale_info.get("stale") and not allow_stale:
        reasons = "; ".join(stale_info.get("reasons") or [])
        raise ValueError(
            f"known {known_id} is STALE ({reasons}) — re-derive before "
            f"consuming.\n"
            f"    terra probe run … && terra known link-run {known_id} <run_id>\n"
            f"    # or, if verified unchanged: terra known reaffirm "
            f"{known_id} --reason '…'"
        )

    from .cohorts import check_cohort, find_cohort_for

    cohort_info = None
    cohort_rec = find_cohort_for(project_root, known_id)
    if cohort_rec is not None:
        chk = check_cohort(project_root, cohort_rec)
        cohort_info = {
            "id": chk["id"],
            "consistent": chk["consistent"],
            "common_runs": chk["common_runs"],
        }
        if not chk["consistent"] and not allow_cohort_mismatch:
            raise ValueError(
                f"known {known_id} is a member of cohort {chk['id']!r} whose "
                f"members are NOT backed by the same solve — this value is "
                f"only meaningful jointly with its siblings.\n  - "
                + "\n  - ".join(chk["problems"])
                + f"\n    terra probe run <solver> --json && "
                f"terra cohort link-run {chk['id']} <run_id>"
            )

    who = resolve_consumer(consumer)
    if record:
        record_consumption(project_root, known_id, who)

    value = extract_value(rec)
    x_at = None
    if at is not None:
        if rec.get("type") != "relation":
            raise ValueError(
                f"--at only applies to relation knowns; {known_id} is "
                f"type={rec.get('type')!r}"
            )
        from .relation_type import evaluate_relation

        value = evaluate_relation(stats, float(at))
        x_at = float(at)

    return {
        "id": known_id,
        "value": value,
        **({"at": x_at} if x_at is not None else {}),
        "unit": rec.get("unit") or "",
        "type": rec.get("type"),
        "confidence": conf,
        "n": n,
        "stats": stats,
        "stale": bool(stale_info.get("stale")),
        "stale_reasons": list(stale_info.get("reasons") or []),
        # Deps drive staleness, so omitting them from the consumption view
        # made `known get` report deps=None for EVERY known — indistinguishable
        # from genuinely unwired. A CAD wiring audit run off this path would
        # have reported all 112 beliefs blind (the real figure was 41
        # export-pinned + 49 with no deps). Silent omission of a field the
        # caller is auditing is a lying instrument; carry it.
        "deps": rec.get("deps") or {},
        "corroboration": corr,
        "uncertainty": corr.get("spread"),
        "band": corr.get("band"),
        "conditional": bool(evidence_inputs["conditional"]),
        "assumptions": list(evidence_inputs["assumptions"]),
        **({"cohort": cohort_info} if cohort_info else {}),
        # Only reachable with allow_superseded=True - flag loudly so a caller
        # reading a tombstoned value knows it is history, not current truth.
        **(
            {"superseded": True, "superseded_info": rec.get("superseded") or {}}
            if status in RETIRED_STATUSES
            else {}
        ),
        "consumer": who,
    }


def known(known_id: str, **kwargs: Any) -> dict[str, Any]:
    """Convenience for probes/tools: resolve project root from cwd and read."""
    root = find_project_root()
    if root is None:
        raise FileNotFoundError(
            "no .terra project found from cwd — run inside the project"
        )
    return read_known(root, known_id, **kwargs)
