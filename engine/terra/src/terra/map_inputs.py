"""Declared map-value bindings shared by calculations and probes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .assumptions import read_assumption
from .readings import read_known

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_map_bindings(rows: list[str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for raw in rows:
        name, sep, source = raw.partition("=")
        name = name.strip()
        if not sep or not _SLUG_RE.fullmatch(name):
            raise ValueError("inputs must be NAME=known:ID or NAME=assumption:ID")
        kind, colon, source_id = source.strip().partition(":")
        if not colon or kind not in ("known", "assumption"):
            raise ValueError("inputs may only be known:ID or assumption:ID")
        if not _SLUG_RE.fullmatch(source_id):
            raise ValueError(f"input source id must be a slug: {source_id!r}")
        if name in bindings:
            raise ValueError(f"duplicate input: {name}")
        bindings[name] = f"{kind}:{source_id}"
    return bindings


def validate_map_bindings(bindings: Any, *, require: bool = False) -> list[str]:
    if bindings is None and not require:
        return []
    if not isinstance(bindings, dict):
        return ["inputs must be an object of NAME -> known:ID|assumption:ID"]
    try:
        parsed = parse_map_bindings([f"{k}={v}" for k, v in bindings.items()])
        if require and not parsed:
            return ["at least one declared input is required"]
    except ValueError as exc:
        return [str(exc)]
    return []


def resolve_map_bindings(
    project_root: Path,
    bindings: dict[str, str],
    *,
    consumer: str,
    record: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    values: dict[str, Any] = {}
    snapshots: dict[str, Any] = {}
    assumptions: list[str] = []
    for name, source in bindings.items():
        kind, source_id = source.split(":", 1)
        if kind == "known":
            reading = read_known(
                project_root, source_id, consumer=consumer, record=record
            )
            conditional = bool(reading.get("conditional"))
            assumptions.extend(reading.get("assumptions") or [])
        else:
            reading = read_assumption(project_root, source_id)
            conditional = True
            assumptions.extend(reading.get("assumptions") or [source_id])
        values[name] = reading.get("value")
        snapshots[name] = {
            "source": source,
            "value": reading.get("value"),
            "updated_at": reading.get("updated_at"),
            "map": reading.get("map"),
            "conditional": conditional,
        }
    return values, snapshots, sorted(set(assumptions))


def snapshots_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(
        right, sort_keys=True, default=str
    )
