"""Render the known dependency graph — the chain, readable in one look.

Answers "which single upstream moved?" without walking `known show`
node by node. Pure rendering over existing state: dep edges on known
records, computed staleness, and the consumer ledger.

Node ids are namespaced: ``known:<id>`` and ``file:<relpath>``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import get_active_map_id, knowns_root
from .readings import list_consumers
from .staleness import compute_staleness, file_sha256


def _load_all_knowns(project_root: Path) -> dict[str, dict[str, Any]]:
    root = knowns_root(project_root)
    out: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.json")):
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def build_graph(project_root: Path) -> dict[str, Any]:
    """→ {map_id, nodes, edges, roots, counts}. Edges point upstream→downstream."""
    knowns = _load_all_knowns(project_root)
    stale = compute_staleness(project_root)

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    for kid, rec in knowns.items():
        info = stale.get(kid) or {}
        stats = rec.get("stats") or {}
        corr = (rec.get("stats") or {}).get("corroboration") or {}
        nodes[f"known:{kid}"] = {
            "id": f"known:{kid}",
            "kind": "known",
            "known_id": kid,
            "type": rec.get("type"),
            "confidence": rec.get("confidence"),
            "n": stats.get("n"),
            "methods": corr.get("methods"),
            "methods_agree": corr.get("agree"),
            "stale": bool(info.get("stale")),
            "stale_reasons": list(info.get("reasons") or []),
            "consumers": [
                c.get("consumer") for c in list_consumers(project_root, kid)
            ],
        }

    for kid, rec in knowns.items():
        deps = rec.get("deps") or {}
        for entry in deps.get("files") or []:
            rel = str(entry.get("path") or "")
            fid = f"file:{rel}"
            if fid not in nodes:
                current = file_sha256(project_root / rel)
                nodes[fid] = {
                    "id": fid,
                    "kind": "file",
                    "path": rel,
                    "missing": current is None,
                    "changed": False,  # refined per-edge below
                }
            current = file_sha256(project_root / rel)
            if current is None or current != entry.get("sha256"):
                nodes[fid]["changed"] = True
            edges.append({"from": fid, "to": f"known:{kid}", "kind": "file"})
        for entry in deps.get("knowns") or []:
            up = f"known:{entry.get('id')}"
            if up not in nodes:
                nodes[up] = {
                    "id": up,
                    "kind": "known",
                    "known_id": str(entry.get("id")),
                    "missing": True,
                    "stale": True,
                    "stale_reasons": ["record missing"],
                    "consumers": [],
                }
            edges.append({"from": up, "to": f"known:{kid}", "kind": "known"})

    has_incoming = {e["to"] for e in edges}
    has_edge = has_incoming | {e["from"] for e in edges}
    roots = sorted(
        nid for nid in nodes if nid not in has_incoming and nid in has_edge
    )
    isolated = sorted(nid for nid in nodes if nid not in has_edge)

    return {
        "command": "known.graph",
        "map_id": get_active_map_id(project_root),
        "nodes": [nodes[k] for k in sorted(nodes)],
        "edges": edges,
        "roots": roots,
        "isolated": isolated,
        "counts": {
            "knowns": sum(1 for n in nodes.values() if n["kind"] == "known"),
            "files": sum(1 for n in nodes.values() if n["kind"] == "file"),
            "edges": len(edges),
            "stale": sum(1 for n in nodes.values() if n.get("stale")),
            "changed_files": sum(
                1
                for n in nodes.values()
                if n["kind"] == "file" and (n.get("changed") or n.get("missing"))
            ),
        },
    }


def _children(graph: dict[str, Any], nid: str) -> list[str]:
    return sorted(e["to"] for e in graph["edges"] if e["from"] == nid)


def _parents(graph: dict[str, Any], nid: str) -> list[str]:
    return sorted(e["from"] for e in graph["edges"] if e["to"] == nid)


def _node_label(node: dict[str, Any]) -> str:
    if node["kind"] == "file":
        flag = (
            "  MISSING"
            if node.get("missing")
            else ("  CHANGED" if node.get("changed") else "")
        )
        return f"file:{node.get('path')}{flag}"
    bits = [node.get("known_id") or node["id"]]
    if node.get("missing"):
        return f"{bits[0]}  MISSING"
    meta = f"[{node.get('type')} n={node.get('n')} {node.get('confidence')}]"
    line = f"{bits[0]}  {meta}"
    m = node.get("methods")
    if m and m >= 2:
        agree = node.get("methods_agree")
        mark = "✓" if agree else ("✗ DISAGREE" if agree is False else "?")
        line += f"  {m} methods {mark}"
    if node.get("stale"):
        line += "  STALE: " + "; ".join(node.get("stale_reasons") or [])
    return line


def render_graph_text(graph: dict[str, Any]) -> str:
    by_id = {n["id"]: n for n in graph["nodes"]}
    c = graph["counts"]
    out = [
        f"KNOWN GRAPH  map={graph['map_id']}  "
        f"({c['knowns']} knowns, {c['files']} files, {c['edges']} edges, "
        f"{c['stale']} stale)"
    ]

    expanded: set[str] = set()

    def walk(nid: str, prefix: str, is_last: bool, seen: frozenset[str]) -> None:
        node = by_id.get(nid) or {"id": nid, "kind": "known", "known_id": nid}
        connector = "" if prefix == "" and is_last is None else (
            "└─ " if is_last else "├─ "
        )
        if nid in seen:
            out.append(f"{prefix}{connector}{_node_label(node)}  ↺ cycle")
            return
        if nid in expanded and _children(graph, nid):
            # shared subtree already printed in full above
            out.append(f"{prefix}{connector}{_node_label(node)}  …")
            return
        expanded.add(nid)
        out.append(f"{prefix}{connector}{_node_label(node)}")
        consumers = node.get("consumers") or []
        child_prefix = prefix + (
            "" if is_last is None else ("   " if is_last else "│  ")
        )
        if consumers:
            out.append(f"{child_prefix}← consumers: {', '.join(consumers)}")
        kids = _children(graph, nid)
        for i, kid in enumerate(kids):
            walk(kid, child_prefix, i == len(kids) - 1, seen | {nid})

    for rid in graph["roots"]:
        walk(rid, "", None, frozenset())
    if graph["isolated"]:
        out.append("unwired (no deps, no dependents):")
        for nid in graph["isolated"]:
            out.append(f"  {_node_label(by_id[nid])}")
    if not graph["roots"] and not graph["isolated"]:
        out.append("(no knowns on this map)")
    return "\n".join(out)


def build_tree(graph: dict[str, Any], known_id: str) -> dict[str, Any]:
    """Focused view of one known: upstream chain, downstream fan, consumers."""
    nid = known_id if known_id.startswith(("known:", "file:")) else f"known:{known_id}"
    by_id = {n["id"]: n for n in graph["nodes"]}
    if nid not in by_id:
        raise FileNotFoundError(f"not in graph: {known_id}")

    def collect(start: str, step) -> list[str]:
        seen: list[str] = []
        frontier = [start]
        while frontier:
            cur = frontier.pop(0)
            for nxt in step(cur):
                if nxt not in seen and nxt != start:
                    seen.append(nxt)
                    frontier.append(nxt)
        return seen

    return {
        "command": "known.tree",
        "map_id": graph["map_id"],
        "node": by_id[nid],
        "upstream": [by_id.get(x) or {"id": x} for x in collect(nid, lambda n: _parents(graph, n))],
        "downstream": [by_id.get(x) or {"id": x} for x in collect(nid, lambda n: _children(graph, n))],
    }


def render_tree_text(tree: dict[str, Any]) -> str:
    out = [_node_label(tree["node"])]
    consumers = tree["node"].get("consumers") or []
    if consumers:
        out.append(f"← consumers: {', '.join(consumers)}")
    out.append("upstream:" if tree["upstream"] else "upstream: (none)")
    for n in tree["upstream"]:
        out.append(f"  {_node_label(n)}")
    out.append("downstream:" if tree["downstream"] else "downstream: (none)")
    for n in tree["downstream"]:
        out.append(f"  {_node_label(n)}")
        cs = n.get("consumers") or []
        if cs:
            out.append(f"    ← consumers: {', '.join(cs)}")
    return "\n".join(out)
