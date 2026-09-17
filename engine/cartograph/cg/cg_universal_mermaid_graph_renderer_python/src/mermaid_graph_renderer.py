"""Render a graph to Mermaid flowchart syntax.

Inputs are plain dataclasses describing nodes, edges, optional clusters
(subgraphs) for grouping, and optional classDefs for styling. Output is
a Mermaid string suitable for a fenced `mermaid` block in markdown or
direct piping into a Mermaid CLI.

Handles label escaping (quotes, pipes, square brackets), Mermaid-safe
ID sanitization (preserving cross-references between nodes and edges),
nested subgraph grouping via the parent field, classDef emission,
flowchart direction, and Mermaid node shapes (rectangle, rounded,
stadium, subroutine, cylinder, circle, rhombus, hexagon, parallelogram).
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set, Tuple


_VALID_DIRECTIONS = {"TD", "TB", "LR", "BT", "RL"}
_VALID_RENDERERS = {"elk", "dagre", ""}
_ID_SAFE_CHARS = re.compile(r"[A-Za-z0-9_]")


# Mermaid shape syntax. Each entry is (open_wrapper, close_wrapper) placed
# around a quoted, escaped label. Empty key is the default rectangle so
# Node.shape="" produces back-compat output identical to the pre-shape API.
_SHAPE_WRAPPERS = {
    "":              ('["',   '"]'),     # default rectangle
    "rect":          ('["',   '"]'),     # explicit rectangle
    "rounded":       ('("',   '")'),
    "stadium":       ('(["',  '"])'),
    "subroutine":    ('[["',  '"]]'),
    "cylinder":      ('[("',  '")]'),
    "circle":        ('(("',  '"))'),
    "rhombus":       ('{"',   '"}'),
    "hexagon":       ('{{"',  '"}}'),
    "parallelogram": ('[/"',  '"/]'),
}


@dataclass
class Node:
    """A graph node.

    label defaults to id when empty. css_class names a ClassDef to apply.
    parent names a Cluster the node belongs to (empty = top level).
    shape selects a Mermaid node shape - one of: rect, rounded, stadium,
    subroutine, cylinder, circle, rhombus, hexagon, parallelogram. Empty
    string defaults to rectangle.
    """
    id: str
    label: str = ""
    css_class: str = ""
    parent: str = ""
    shape: str = ""


@dataclass
class Edge:
    """A directed edge between two nodes.

    style is the raw Mermaid arrow operator: "-->" (solid), "-.->" (dotted),
    "==>" (thick), "---" (open), etc. label is rendered between pipes.
    """
    source: str
    target: str
    label: str = ""
    style: str = "-->"


@dataclass
class Cluster:
    """A subgraph grouping nodes (and possibly nested clusters).

    parent names another Cluster (empty = top-level cluster).
    """
    id: str
    label: str = ""
    parent: str = ""


@dataclass
class ClassDef:
    """A reusable style applied to nodes via Node.css_class.

    style is the raw Mermaid classDef body, e.g. "fill:#f9f,stroke:#333".
    """
    name: str
    style: str


class MermaidRenderError(Exception):
    """Raised when the input graph cannot be rendered."""


def render(
    *,
    nodes: Sequence[Node],
    edges: Sequence[Edge] = (),
    clusters: Sequence[Cluster] = (),
    class_defs: Sequence[ClassDef] = (),
    direction: str = "TD",
    renderer: str = "elk",
) -> str:
    """Render the graph to a Mermaid flowchart string.

    Parameters:
        nodes: All nodes in the graph.
        edges: Edges between nodes.
        clusters: Optional subgraph groupings.
        class_defs: Optional reusable styles.
        direction: Flowchart direction. One of TD, TB, LR, BT, RL.
        renderer: Mermaid layout engine. "elk" (default) prepends an init
            block selecting Mermaid's ELK renderer, which produces cleaner
            hierarchical layouts than the legacy dagre engine. "dagre" or
            "" emits no init block (Mermaid's built-in default).

    Raises:
        MermaidRenderError: invalid direction or renderer, dangling parent
            reference, duplicate node/cluster id, or cluster parent cycle.
    """
    if direction not in _VALID_DIRECTIONS:
        raise MermaidRenderError(
            f"Invalid direction {direction!r}; "
            f"expected one of {sorted(_VALID_DIRECTIONS)}"
        )
    if renderer not in _VALID_RENDERERS:
        raise MermaidRenderError(
            f"Invalid renderer {renderer!r}; "
            f"expected one of {sorted(_VALID_RENDERERS)}"
        )

    sanitized_node_ids = _sanitize_collection_ids(
        (n.id for n in nodes), label="node"
    )
    sanitized_cluster_ids = _sanitize_collection_ids(
        (c.id for c in clusters), label="cluster"
    )
    overlap = set(sanitized_node_ids.values()) & set(
        sanitized_cluster_ids.values()
    )
    if overlap:
        raise MermaidRenderError(
            f"Node and cluster ids collide after sanitization: "
            f"{sorted(overlap)}"
        )
    edge_endpoint_ids = {**sanitized_node_ids, **sanitized_cluster_ids}

    cluster_index: Dict[str, Cluster] = {c.id: c for c in clusters}
    _check_cluster_parents(clusters, cluster_index)

    nodes_by_parent: Dict[str, List[Node]] = {}
    for n in nodes:
        if n.parent and n.parent not in cluster_index:
            raise MermaidRenderError(
                f"Node {n.id!r} parent {n.parent!r} is not a declared cluster"
            )
        nodes_by_parent.setdefault(n.parent, []).append(n)

    children_by_parent: Dict[str, List[Cluster]] = {}
    for c in clusters:
        children_by_parent.setdefault(c.parent, []).append(c)

    lines: List[str] = []
    if renderer == "elk":
        lines.append(
            "%%{init: {'flowchart': {'defaultRenderer': 'elk'}}}%%"
        )
    lines.append(f"flowchart {direction}")

    for cd in class_defs:
        lines.append(f"    classDef {cd.name} {cd.style}")

    for n in nodes_by_parent.get("", []):
        lines.append("    " + _render_node(n, sanitized_node_ids))

    for c in children_by_parent.get("", []):
        _render_cluster(
            c,
            depth=1,
            children_by_parent=children_by_parent,
            nodes_by_parent=nodes_by_parent,
            sanitized_node_ids=sanitized_node_ids,
            sanitized_cluster_ids=sanitized_cluster_ids,
            lines=lines,
        )

    for e in edges:
        if e.source not in edge_endpoint_ids:
            raise MermaidRenderError(
                f"Edge source {e.source!r} is not a declared node or cluster"
            )
        if e.target not in edge_endpoint_ids:
            raise MermaidRenderError(
                f"Edge target {e.target!r} is not a declared node or cluster"
            )
        lines.append("    " + _render_edge(e, edge_endpoint_ids))

    return "\n".join(lines) + "\n"


def _render_node(node: Node, sanitized_ids: Dict[str, str]) -> str:
    safe_id = sanitized_ids[node.id]
    label = node.label or node.id
    if node.shape not in _SHAPE_WRAPPERS:
        raise MermaidRenderError(
            f"Unknown shape {node.shape!r} on node {node.id!r}; "
            f"expected one of {sorted(s for s in _SHAPE_WRAPPERS if s)}"
        )
    open_wrap, close_wrap = _SHAPE_WRAPPERS[node.shape]
    out = f'{safe_id}{open_wrap}{_escape_label(label)}{close_wrap}'
    if node.css_class:
        out += f":::{node.css_class}"
    return out


def _render_edge(edge: Edge, sanitized_ids: Dict[str, str]) -> str:
    src = sanitized_ids[edge.source]
    dst = sanitized_ids[edge.target]
    if edge.label:
        return f'{src} {edge.style}|"{_escape_label(edge.label)}"| {dst}'
    return f"{src} {edge.style} {dst}"


def _render_cluster(
    cluster: Cluster,
    *,
    depth: int,
    children_by_parent: Dict[str, List[Cluster]],
    nodes_by_parent: Dict[str, List[Node]],
    sanitized_node_ids: Dict[str, str],
    sanitized_cluster_ids: Dict[str, str],
    lines: List[str],
) -> None:
    indent = "    " * depth
    safe_id = sanitized_cluster_ids[cluster.id]
    label = cluster.label or cluster.id
    lines.append(f'{indent}subgraph {safe_id}["{_escape_label(label)}"]')
    inner = "    " * (depth + 1)
    for n in nodes_by_parent.get(cluster.id, []):
        lines.append(inner + _render_node(n, sanitized_node_ids))
    for child in children_by_parent.get(cluster.id, []):
        _render_cluster(
            child,
            depth=depth + 1,
            children_by_parent=children_by_parent,
            nodes_by_parent=nodes_by_parent,
            sanitized_node_ids=sanitized_node_ids,
            sanitized_cluster_ids=sanitized_cluster_ids,
            lines=lines,
        )
    lines.append(f"{indent}end")


def _sanitize_id(raw_id: str) -> str:
    if not raw_id:
        raise MermaidRenderError("Empty id")
    chars = []
    for i, ch in enumerate(raw_id):
        if _ID_SAFE_CHARS.match(ch):
            chars.append(ch)
        else:
            chars.append("_")
    safe = "".join(chars)
    if safe[0].isdigit():
        safe = "n_" + safe
    return safe


def _sanitize_collection_ids(
    raw_ids, *, label: str
) -> Dict[str, str]:
    """Sanitize a set of ids and ensure no collisions or duplicates."""
    out: Dict[str, str] = {}
    seen_safe: Set[str] = set()
    for raw in raw_ids:
        if raw in out:
            raise MermaidRenderError(f"Duplicate {label} id: {raw!r}")
        safe = _sanitize_id(raw)
        if safe in seen_safe:
            raise MermaidRenderError(
                f"Two {label} ids collide after sanitization "
                f"(both become {safe!r})"
            )
        out[raw] = safe
        seen_safe.add(safe)
    return out


def _check_cluster_parents(
    clusters: Sequence[Cluster], cluster_index: Dict[str, Cluster]
) -> None:
    for c in clusters:
        if c.parent and c.parent not in cluster_index:
            raise MermaidRenderError(
                f"Cluster {c.id!r} parent {c.parent!r} is not a declared cluster"
            )
    for c in clusters:
        seen: Set[str] = set()
        cur = c
        while cur.parent:
            if cur.parent in seen or cur.parent == c.id:
                raise MermaidRenderError(
                    f"Cluster parent cycle involving {c.id!r}"
                )
            seen.add(cur.parent)
            cur = cluster_index[cur.parent]


def _escape_label(label: str) -> str:
    """Escape characters that break Mermaid label parsing.

    Mermaid quoted labels treat double-quote as a terminator, and the
    pipe character delimits edge labels. Replace with HTML entities.
    """
    return (
        label.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("|", "&#124;")
    )
