"""Render an Architecture as Mermaid using the cg widget."""

from cg.cg_universal_mermaid_graph_renderer_python.src.mermaid_graph_renderer import (
    ClassDef,
    Cluster,
    Edge as MermaidEdge,
    Node as MermaidNode,
    render as render_mermaid,
)

from .schema import Architecture


_DOMAIN_STYLES = {
    "backend":   "fill:#e6f3ff,stroke:#3a78c9,color:#1a3a6a",
    "data":      "fill:#fff5e6,stroke:#c98c3a,color:#6a4a1a",
    "ml":        "fill:#f0e6ff,stroke:#7a3ac9,color:#3a1a6a",
    "security":  "fill:#ffe6e6,stroke:#c93a3a,color:#6a1a1a",
    "infra":     "fill:#e6ffe6,stroke:#3ac93a,color:#1a6a1a",
    "frontend":  "fill:#fffae6,stroke:#c9b03a,color:#6a5a1a",
    "universal": "fill:#f0f0f0,stroke:#666666,color:#222222",
    "modeling":  "fill:#e6fff5,stroke:#3ac98c,color:#1a6a4a",
    "rtl":       "fill:#f5e6ff,stroke:#9a3ac9,color:#4a1a6a",
    "devops":    "fill:#ffe6f5,stroke:#c93a8c,color:#6a1a4a",
    # Architect-special: glue is project code, not a widget target.
    # Dashed feel via lighter contrast - rendered same as nodes but
    # visually distinct so the agent's "what's left to write" view is
    # easy to scan.
    "glue":      "fill:#fafafa,stroke:#999999,color:#444444,stroke-dasharray:4 3",
}


# Known kind vocabulary that gets a distinct Mermaid shape. Anything not
# in this map falls back to the default rectangle, which keeps `kind`
# free-form for cross-domain use (modeling, rtl, physical) while letting
# the most common software-architecture kinds carry visual meaning.
#
# - external: stadium - the system boundary actor (developer, agent, GCP)
# - datastore: cylinder - universal database/storage glyph
# - service / engine / pipeline: rounded - long-running process
# - policy: rhombus - decision/gate
# - application / deployment: hexagon - top-level container
_KIND_SHAPES = {
    "external":    "stadium",
    "datastore":   "cylinder",
    "service":     "rounded",
    "engine":      "rounded",
    "pipeline":    "rounded",
    "policy":      "rhombus",
    "application": "hexagon",
    "deployment":  "hexagon",
}


# Edge kind → Mermaid arrow operator. Lets us drop label dependence on
# obvious edges and emphasize the heavy data/deployment paths visually.
#
# - thick (==>): data flow and binding constraints
# - dotted (-.->): deployment/hosting relationships
# - default (-->): everything else (delegates_to, calls, invokes, ...)
_EDGE_STYLES = {
    "reads":        "==>",
    "writes":       "==>",
    "reads_writes": "==>",
    "enforces":     "==>",
    "hosted_on":    "-.->",
    "served_by":    "-.->",
}


# Human-readable labels for the legend's shape entries. Mirrors the
# vocabulary of _KIND_SHAPES but keys by shape (not kind) since the
# legend describes the visual language, not the source field.
_SHAPE_LEGEND_LABELS = {
    "stadium":   "external",
    "cylinder":  "datastore",
    "rounded":   "service / engine / pipeline",
    "rhombus":   "policy",
    "hexagon":   "application / deployment",
}


# Edge style → human-readable description for the legend.
_EDGE_LEGEND_LABELS = {
    "==>":  "data flow / constraint",
    "-.->": "hosted / served",
}


def render(arch: Architecture, *, direction: str = "TD") -> str:
    """Render an Architecture as Mermaid flowchart text.

    Component.parent groupings turn into subgraphs only when the parent
    has children that are themselves grouped — that is, parent ids are
    treated as cluster ids. Components with no parent live at the top
    level. Components whose id is referenced as a parent become both a
    cluster and a containing node visually; we render them as a cluster
    and skip the standalone node so the user does not see a phantom
    duplicate.
    """
    parent_ids = {c.parent for c in arch.components if c.parent}

    nodes = []
    clusters = []
    used_classes = set()
    used_shapes = set()

    for c in arch.components:
        shape = _KIND_SHAPES.get(c.kind, "")
        if c.id in parent_ids:
            clusters.append(
                Cluster(
                    id=c.id,
                    label=_node_label(c.id, c.kind, c.domains, c.widgets),
                    parent=c.parent or "",
                )
            )
            # Clusters always render as subgraphs; they don't carry a
            # shape themselves. We still record the kind in the legend
            # via the rendering rules so the user understands the
            # vocabulary, but only via nodes that actually use it.
            continue
        css_class = _pick_node_class(c.domains)
        if css_class:
            used_classes.add(css_class)
        if shape:
            used_shapes.add(shape)
        label = _node_label(c.id, c.kind, c.domains, c.widgets)
        nodes.append(
            MermaidNode(
                id=c.id,
                label=label,
                css_class=css_class,
                parent=c.parent or "",
                shape=shape,
            )
        )

    used_edge_styles = set()
    edges = []
    for e in arch.edges:
        style = _EDGE_STYLES.get(e.kind, "-->")
        # Skip routine labels that the styling already conveys. Heavy
        # and deployment edges still get labels (useful clarification),
        # but ordinary `delegates_to` / `calls` / `invokes` arrows shed
        # their label-noise when they're already self-explanatory.
        label = _edge_label(e.kind, e.what)
        if style != "-->":
            used_edge_styles.add(style)
        edges.append(
            MermaidEdge(
                source=e.source,
                target=e.target,
                label=label,
                style=style,
            )
        )

    # Legend: a small subgraph with one entry per visual element used in
    # this diagram. Built from the actual usage so it never lies.
    legend_nodes, legend_clusters, legend_edges = _build_legend(
        used_classes=used_classes,
        used_shapes=used_shapes,
        used_edge_styles=used_edge_styles,
    )
    nodes.extend(legend_nodes)
    clusters.extend(legend_clusters)
    edges.extend(legend_edges)

    class_defs = [
        ClassDef(name=name, style=_DOMAIN_STYLES[name])
        for name in sorted(used_classes)
    ]

    diagram = render_mermaid(
        nodes=nodes,
        edges=edges,
        clusters=clusters,
        class_defs=class_defs,
        direction=direction,
    )
    return _append_click_targets(diagram, arch.components)


def _build_legend(
    *,
    used_classes,
    used_shapes,
    used_edge_styles,
):
    """Build the legend subgraph from the elements actually used.

    Returns (nodes, clusters, edges). Edges are returned so the caller
    can append them to the main edge list - the legend uses real edges
    to demonstrate its arrow-style vocabulary.
    """
    if not (used_classes or used_shapes or used_edge_styles):
        return [], [], []

    nodes = []
    legend_edges = []

    # Domain color swatches.
    for name in sorted(used_classes):
        nodes.append(
            MermaidNode(
                id=f"_legend_domain_{name}",
                label=name,
                css_class=name,
                parent="_legend",
            )
        )
    # Shape vocabulary.
    for shape in sorted(used_shapes):
        nodes.append(
            MermaidNode(
                id=f"_legend_shape_{shape}",
                label=_SHAPE_LEGEND_LABELS.get(shape, shape),
                shape=shape,
                parent="_legend",
            )
        )
    # Edge-style demos: paired source/target nodes connected by the
    # actual arrow operator so the user sees the visual, not just words.
    for style in sorted(used_edge_styles):
        a = f"_legend_edge_{_safe_style_id(style)}_a"
        b = f"_legend_edge_{_safe_style_id(style)}_b"
        nodes.append(
            MermaidNode(id=a, label=" ", parent="_legend")
        )
        nodes.append(
            MermaidNode(id=b, label=_EDGE_LEGEND_LABELS.get(style, style),
                        parent="_legend")
        )
        legend_edges.append(
            MermaidEdge(source=a, target=b, style=style)
        )

    cluster = Cluster(id="_legend", label="Legend")
    return nodes, [cluster], legend_edges


def _safe_style_id(style: str) -> str:
    """Convert an arrow operator to a safe id fragment."""
    return (style
            .replace("-", "d")
            .replace(">", "g")
            .replace(".", "p")
            .replace("=", "e"))


def _append_click_targets(diagram: str, components) -> str:
    """Append Mermaid click directives so widget-attached nodes are
    navigable to their source directory in the rendered diagram.

    Only emitted for Components with exactly one widget - a single
    click target per node is unambiguous. For multi-widget slots the
    label already shows the directory names so navigation falls back
    to the user.
    """
    lines = []
    for c in components:
        if len(c.widgets or []) == 1:
            widget = c.widgets[0]
            lines.append(
                f'    click {c.id} href "cg/{widget}/" "Open {widget}"'
            )
    if not lines:
        return diagram
    sep = "" if diagram.endswith("\n") else "\n"
    return diagram + sep + "\n".join(lines) + "\n"


def _node_label(component_id: str, kind: str, domains, widgets) -> str:
    """Render the node label.

    Multi-domain components show all domains in the bottom tag so the
    agent (and the human) can see the full categorization at a glance.
    Mermaid styling only takes one classDef, but the label carries the
    full truth.

    A leading diamond marker indicates one or more attached widgets so
    the three states (filled / glue / unfilled) read at a glance
    alongside the classDef colors. Multiple widgets all show in the
    label - this is how composed slots stay legible on the diagram.
    """
    has_widgets = bool(widgets)
    head = f"◆ {component_id}" if has_widgets else component_id
    parts = [head]
    if kind:
        parts.append(f"[{kind}]")
    if has_widgets:
        parts.append("<" + ", ".join(widgets) + ">")
    if domains and len(domains) > 1:
        parts.append("(" + ", ".join(domains) + ")")
    return "\n".join(parts)


def _edge_label(kind: str, what: str) -> str:
    if kind and what:
        return f"{kind}: {what}"
    return kind or what


def _pick_node_class(domains) -> str:
    """Pick the classDef to apply to a node from its domain list.

    "glue" wins over Cartograph domains when both are present so the
    visual prioritizes "this is where you write code" - that's what the
    user is most likely to need to see at a glance. Otherwise the first
    known domain wins, in declaration order.
    """
    if not domains:
        return ""
    if "glue" in domains:
        return "glue"
    for d in domains:
        if d in _DOMAIN_STYLES:
            return d
    return ""
