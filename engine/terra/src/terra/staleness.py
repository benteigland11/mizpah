"""Dependency edges + derived staleness for knowns.

A known may declare deps on other knowns and on project files:

  deps: {
    "knowns": [{"id": "mtow", "as_of": "<upstream updated_at at stamp time>"}],
    "files":  [{"path": "geometry/airframe.stl", "sha256": "…", "linked_at": "…"}]
  }

Staleness is **computed at status time, never stored as truth**:
  - file dep: current sha256 differs (or file missing) → stale
  - known dep: upstream missing, upstream updated_at newer than as_of,
    or upstream itself stale (cascade) → stale
  - dependency cycles are stale by definition (loud, not silent)

Stamps refresh only on honest re-derivation: link-run, reaffirm, or depend.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .paths import knowns_root, map_chain, scoped_map

DEP_KIND_KNOWN = "known"
DEP_KIND_FILE = "file"


def file_sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def parse_dep(spec: str) -> tuple[str, str]:
    """Parse ``known:<id>`` / ``file:<relpath>`` → (kind, target). Loud on junk."""
    if not isinstance(spec, str) or ":" not in spec:
        raise ValueError(
            f"dep must be 'known:<id>' or 'file:<relpath>', got {spec!r}"
        )
    kind, _, target = spec.partition(":")
    kind = kind.strip()
    target = target.strip()
    if kind not in (DEP_KIND_KNOWN, DEP_KIND_FILE) or not target:
        raise ValueError(
            f"dep must be 'known:<id>' or 'file:<relpath>', got {spec!r}"
        )
    return kind, target


def stamp_deps(project_root: Path, rec: dict[str, Any]) -> dict[str, Any]:
    """Refresh dep stamps in place: file sha256 now, known as_of = upstream now.

    Call only on honest re-derivation (depend / link-run / reaffirm).
    """
    deps = rec.get("deps") or {}
    for entry in deps.get("files") or []:
        p = project_root / str(entry.get("path") or "")
        entry["sha256"] = file_sha256(p)
    if deps.get("knowns"):
        upstream = _load_all_knowns(project_root)
        for entry in deps.get("knowns") or []:
            up = upstream.get(str(entry.get("id") or ""))
            entry["as_of"] = up.get("updated_at") if up else None
    return rec


def _load_all_knowns(project_root: Path) -> dict[str, dict[str, Any]]:
    """Chain overlay for the active map: ancestors first, child shadows.

    Inherited knowns participate in dep resolution and cascade, so a child
    known goes stale when an ancestor dep moves.
    """
    out: dict[str, dict[str, Any]] = {}
    for mid in reversed(map_chain(project_root)):
        with scoped_map(mid):
            root = knowns_root(project_root)
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*.json")):
                try:
                    out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
    return out


def compute_staleness(
    project_root: Path,
) -> dict[str, dict[str, Any]]:
    """→ {known_id: {"stale": bool, "reasons": [str]}} for the active map.

    Direct reasons first, then cascade through known→known edges.
    Cycles mark every member stale.
    """
    knowns = _load_all_knowns(project_root)
    result: dict[str, dict[str, Any]] = {
        kid: {"stale": False, "reasons": []} for kid in knowns
    }

    # Direct staleness
    for kid, rec in knowns.items():
        deps = rec.get("deps") or {}
        for entry in deps.get("files") or []:
            rel = str(entry.get("path") or "")
            current = file_sha256(project_root / rel)
            if current is None:
                result[kid]["reasons"].append(f"file dep missing: {rel}")
            elif current != entry.get("sha256"):
                result[kid]["reasons"].append(f"file dep changed: {rel}")
        for entry in deps.get("knowns") or []:
            up_id = str(entry.get("id") or "")
            up = knowns.get(up_id)
            if up is None:
                result[kid]["reasons"].append(f"known dep missing: {up_id}")
                continue
            as_of = entry.get("as_of")
            up_at = up.get("updated_at")
            if as_of is None or (up_at and str(up_at) > str(as_of)):
                result[kid]["reasons"].append(
                    f"known dep moved: {up_id} (updated after stamp)"
                )
        if result[kid]["reasons"]:
            result[kid]["stale"] = True

    # Cascade (DFS with cycle detection: gray = in-progress)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {kid: WHITE for kid in knowns}

    def visit(kid: str) -> bool:
        """Returns True if kid is stale (direct, upstream, or cycle)."""
        if color.get(kid) == GRAY:
            if "dependency cycle" not in result[kid]["reasons"]:
                result[kid]["reasons"].append("dependency cycle")
                result[kid]["stale"] = True
            return True
        if color.get(kid) == BLACK:
            return result[kid]["stale"]
        color[kid] = GRAY
        stale = result[kid]["stale"]
        for entry in (knowns[kid].get("deps") or {}).get("knowns") or []:
            up_id = str(entry.get("id") or "")
            if up_id in knowns and visit(up_id):
                reason = f"upstream stale: known:{up_id}"
                if reason not in result[kid]["reasons"]:
                    result[kid]["reasons"].append(reason)
                stale = True
        color[kid] = BLACK
        result[kid]["stale"] = stale
        return stale

    for kid in knowns:
        visit(kid)
    return result
