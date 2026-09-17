"""Render an Architecture as Markdown.

Mermaid is for humans. This is for the LLM agent. The graph is fully
denormalized per component: parent, children, outgoing edges, incoming
edges, and linked widgets are all listed inline so the agent never has
to traverse arrows mentally.
"""

from collections import defaultdict
from typing import Dict, List

from .schema import Architecture, Component, Edge


def render_markdown(arch: Architecture) -> str:
    components_by_id: Dict[str, Component] = {c.id: c for c in arch.components}
    children: Dict[str, List[str]] = defaultdict(list)
    outgoing: Dict[str, List[Edge]] = defaultdict(list)
    incoming: Dict[str, List[Edge]] = defaultdict(list)

    for c in arch.components:
        if c.parent:
            children[c.parent].append(c.id)
    for e in arch.edges:
        outgoing[e.source].append(e)
        incoming[e.target].append(e)

    lines: List[str] = []
    lines.append(f"# Architecture")
    if arch.goal:
        lines.append("")
        lines.append(f"> {arch.goal}")
    lines.append("")
    lines.append(
        f"_schema_version: {arch.schema_version} | "
        f"{len(arch.components)} component(s) | "
        f"{len(arch.edges)} edge(s)_"
    )

    if arch.notes:
        lines.append("")
        lines.append("## Notes")
        lines.append("")
        lines.append(arch.notes.strip())

    lines.append("")
    lines.append("## Component index")
    lines.append("")
    for cid in _index_order(arch.components, children):
        c = components_by_id[cid]
        depth = _depth(cid, components_by_id)
        indent = "  " * depth
        suffix = f" — {c.kind}" if c.kind else ""
        lines.append(f"{indent}- `{cid}`{suffix}")

    lines.append("")
    lines.append("## Components")

    for c in arch.components:
        lines.append("")
        suffix = f" — {c.kind}" if c.kind else ""
        lines.append(f"### `{c.id}`{suffix}")
        lines.append("")
        if c.domains:
            lines.append(f"**Domains:** {', '.join(c.domains)}")
        lines.append(
            f"**Parent:** `{c.parent}`" if c.parent else "**Parent:** (top level)"
        )
        kids = children.get(c.id, [])
        if kids:
            lines.append(
                f"**Children:** {', '.join(f'`{k}`' for k in kids)}"
            )
        if c.description:
            lines.append("")
            lines.append(f"_{c.description.strip()}_")

        outs = outgoing.get(c.id, [])
        if outs:
            lines.append("")
            lines.append("**Depends on / talks to:**")
            for e in outs:
                lines.append(f"- {_format_edge(e, direction='out')}")

        ins = incoming.get(c.id, [])
        if ins:
            lines.append("")
            lines.append("**Depended on by / receives from:**")
            for e in ins:
                lines.append(f"- {_format_edge(e, direction='in')}")

        if c.widgets:
            lines.append("")
            lines.append("**Linked widgets:**")
            for w in c.widgets:
                lines.append(f"- `cg/{w}/`")
        elif _is_widget_slot(c) and not kids:
            lines.append("")
            lines.append("**Linked widgets:** _(unfilled)_")

    orphans = _find_orphans(arch, outgoing, incoming)
    if orphans:
        lines.append("")
        lines.append("## Disconnected components")
        lines.append("")
        lines.append(
            "Components with no edges in either direction. May be "
            "intentional (externals, root containers) or a planning gap."
        )
        lines.append("")
        for cid in orphans:
            lines.append(f"- `{cid}`")

    lines.append("")
    return "\n".join(lines)


def _format_edge(e: Edge, *, direction: str) -> str:
    other = e.target if direction == "out" else e.source
    arrow = "→" if direction == "out" else "←"
    parts = [f"`{other}` {arrow}"]
    if e.kind:
        parts.append(f"_{e.kind}_")
    if e.what:
        parts.append(f"(`{e.what}`)")
    line = " ".join(parts)
    if e.description:
        line = f"{line} — {e.description.strip()}"
    return line


def _depth(cid: str, components_by_id: Dict[str, Component]) -> int:
    depth = 0
    seen = {cid}
    cur = components_by_id.get(cid)
    while cur and cur.parent and cur.parent in components_by_id:
        if cur.parent in seen:
            return depth
        seen.add(cur.parent)
        depth += 1
        cur = components_by_id[cur.parent]
    return depth


def _index_order(components: List[Component], children: Dict[str, List[str]]) -> List[str]:
    by_id = {c.id: c for c in components}
    order: List[str] = []

    def walk(cid: str) -> None:
        order.append(cid)
        for kid in children.get(cid, []):
            walk(kid)

    for c in components:
        if not c.parent or c.parent not in by_id:
            walk(c.id)
    for c in components:
        if c.id not in order:
            order.append(c.id)
    return order


def _is_widget_slot(c: Component) -> bool:
    """Component looks like a slot that *could* hold widgets (not external/glue-only)."""
    if c.kind == "external":
        return False
    if c.domains and set(c.domains) == {"glue"}:
        return False
    return True


def _find_orphans(
    arch: Architecture,
    outgoing: Dict[str, List[Edge]],
    incoming: Dict[str, List[Edge]],
) -> List[str]:
    children_of: Dict[str, List[str]] = defaultdict(list)
    for c in arch.components:
        if c.parent:
            children_of[c.parent].append(c.id)
    orphans = []
    for c in arch.components:
        has_edges = bool(outgoing.get(c.id) or incoming.get(c.id))
        is_container = bool(children_of.get(c.id))
        if not has_edges and not is_container and not c.parent:
            orphans.append(c.id)
    return orphans
