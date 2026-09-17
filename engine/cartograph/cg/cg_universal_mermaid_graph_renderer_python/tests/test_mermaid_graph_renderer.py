"""Tests for mermaid_graph_renderer."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mermaid_graph_renderer import (
    ClassDef,
    Cluster,
    Edge,
    MermaidRenderError,
    Node,
    render,
)


def test_minimal_two_node_graph():
    out = render(
        nodes=[Node(id="a"), Node(id="b")],
        edges=[Edge(source="a", target="b")],
    )
    assert "flowchart TD\n" in out
    assert 'a["a"]' in out
    assert 'b["b"]' in out
    assert "a --> b" in out


def test_label_defaults_to_id_when_empty():
    out = render(nodes=[Node(id="user_service")])
    assert 'user_service["user_service"]' in out


def test_explicit_label_is_used():
    out = render(nodes=[Node(id="u", label="User Service")])
    assert 'u["User Service"]' in out


def test_label_escapes_quotes_and_pipes():
    out = render(
        nodes=[Node(id="x", label='has "quote" and |pipe|')],
    )
    assert "&quot;quote&quot;" in out
    assert "&#124;pipe&#124;" in out


def test_label_escapes_ampersand_first():
    out = render(nodes=[Node(id="x", label='A&B "ok"')])
    assert "A&amp;B" in out
    assert "&quot;ok&quot;" in out


def test_edge_with_label():
    out = render(
        nodes=[Node(id="a"), Node(id="b")],
        edges=[Edge(source="a", target="b", label="user event")],
    )
    assert 'a -->|"user event"| b' in out


def test_edge_custom_style():
    out = render(
        nodes=[Node(id="a"), Node(id="b")],
        edges=[Edge(source="a", target="b", style="-.->")],
    )
    assert "a -.-> b" in out


def test_direction_validation():
    with pytest.raises(MermaidRenderError):
        render(nodes=[Node(id="a")], direction="DIAGONAL")


def test_direction_lr():
    out = render(nodes=[Node(id="a")], direction="LR")
    assert "flowchart LR\n" in out


def test_default_renderer_is_elk():
    out = render(nodes=[Node(id="a")])
    assert out.startswith(
        "%%{init: {'flowchart': {'defaultRenderer': 'elk'}}}%%\n"
    )
    assert "flowchart TD\n" in out


def test_dagre_renderer_omits_init_block():
    out = render(nodes=[Node(id="a")], renderer="dagre")
    assert "init:" not in out
    assert out.startswith("flowchart TD\n")


def test_empty_renderer_omits_init_block():
    out = render(nodes=[Node(id="a")], renderer="")
    assert "init:" not in out
    assert out.startswith("flowchart TD\n")


def test_invalid_renderer_raises():
    with pytest.raises(MermaidRenderError):
        render(nodes=[Node(id="a")], renderer="graphviz")


def test_id_sanitization_preserves_edges():
    out = render(
        nodes=[Node(id="auth-service"), Node(id="user-db")],
        edges=[Edge(source="auth-service", target="user-db")],
    )
    assert 'auth_service["auth-service"]' in out
    assert 'user_db["user-db"]' in out
    assert "auth_service --> user_db" in out


def test_id_starting_with_digit_is_prefixed():
    out = render(nodes=[Node(id="3-axis-mill")])
    assert "n_3_axis_mill" in out


def test_classdef_emission():
    out = render(
        nodes=[Node(id="a", css_class="hot")],
        class_defs=[ClassDef(name="hot", style="fill:#f99,stroke:#900")],
    )
    assert "classDef hot fill:#f99,stroke:#900" in out
    assert 'a["a"]:::hot' in out


def test_cluster_grouping():
    out = render(
        nodes=[
            Node(id="a", parent="grp"),
            Node(id="b", parent="grp"),
            Node(id="external"),
        ],
        clusters=[Cluster(id="grp", label="Backend")],
    )
    assert 'subgraph grp["Backend"]' in out
    body = out.split('subgraph grp["Backend"]', 1)[1]
    grp_block, _ = body.split("\n    end", 1)
    assert 'a["a"]' in grp_block
    assert 'b["b"]' in grp_block
    assert 'external["external"]' not in grp_block


def test_nested_cluster():
    out = render(
        nodes=[Node(id="leaf", parent="inner")],
        clusters=[
            Cluster(id="outer"),
            Cluster(id="inner", parent="outer"),
        ],
    )
    assert "subgraph outer" in out
    assert "subgraph inner" in out
    outer_block = out.split("subgraph outer", 1)[1]
    assert "subgraph inner" in outer_block.split("\n    end", 1)[0]


def test_cluster_label_defaults_to_id():
    out = render(
        nodes=[],
        clusters=[Cluster(id="grp")],
    )
    assert 'subgraph grp["grp"]' in out


def test_dangling_edge_source_raises():
    with pytest.raises(MermaidRenderError):
        render(
            nodes=[Node(id="a")],
            edges=[Edge(source="ghost", target="a")],
        )


def test_dangling_edge_target_raises():
    with pytest.raises(MermaidRenderError):
        render(
            nodes=[Node(id="a")],
            edges=[Edge(source="a", target="ghost")],
        )


def test_dangling_node_parent_raises():
    with pytest.raises(MermaidRenderError):
        render(
            nodes=[Node(id="a", parent="missing")],
            clusters=[],
        )


def test_dangling_cluster_parent_raises():
    with pytest.raises(MermaidRenderError):
        render(
            nodes=[],
            clusters=[Cluster(id="c", parent="ghost")],
        )


def test_cluster_parent_cycle_raises():
    with pytest.raises(MermaidRenderError):
        render(
            nodes=[],
            clusters=[
                Cluster(id="a", parent="b"),
                Cluster(id="b", parent="a"),
            ],
        )


def test_duplicate_node_id_raises():
    with pytest.raises(MermaidRenderError):
        render(nodes=[Node(id="a"), Node(id="a")])


def test_duplicate_cluster_id_raises():
    with pytest.raises(MermaidRenderError):
        render(
            nodes=[],
            clusters=[Cluster(id="x"), Cluster(id="x")],
        )


def test_node_cluster_id_collision_after_sanitize_raises():
    with pytest.raises(MermaidRenderError):
        render(
            nodes=[Node(id="auth-service")],
            clusters=[Cluster(id="auth_service")],
        )


def test_edge_to_cluster():
    """Edges may target clusters as endpoints, not just nodes."""
    out = render(
        nodes=[Node(id="user"), Node(id="api", parent="backend")],
        edges=[Edge(source="user", target="backend", label="HTTPS")],
        clusters=[Cluster(id="backend", label="Backend")],
    )
    assert 'user -->|"HTTPS"| backend' in out


def test_edge_from_cluster():
    """Edges may originate from clusters."""
    out = render(
        nodes=[Node(id="user"), Node(id="api", parent="backend")],
        edges=[Edge(source="backend", target="user")],
        clusters=[Cluster(id="backend")],
    )
    assert "backend --> user" in out


def test_full_render_smoke():
    out = render(
        nodes=[
            Node(id="ui", label="UI", css_class="ext"),
            Node(id="api", label="API", parent="backend"),
            Node(id="db", label="DB", parent="backend"),
        ],
        edges=[
            Edge(source="ui", target="api", label="HTTP"),
            Edge(source="api", target="db", style="-.->"),
        ],
        clusters=[Cluster(id="backend", label="Backend Services")],
        class_defs=[ClassDef(name="ext", style="fill:#eef")],
        direction="LR",
    )
    assert "flowchart LR" in out
    assert "classDef ext fill:#eef" in out
    assert ":::ext" in out
    assert 'subgraph backend["Backend Services"]' in out
    assert 'ui -->|"HTTP"| api' in out
    assert "api -.-> db" in out


def test_default_shape_is_rectangle():
    out = render(nodes=[Node(id="a", label="A")])
    assert 'a["A"]' in out


def test_explicit_rect_shape_matches_default():
    out_default = render(nodes=[Node(id="a", label="A")])
    out_rect = render(nodes=[Node(id="a", label="A", shape="rect")])
    assert out_default == out_rect


def test_rounded_shape():
    out = render(nodes=[Node(id="svc", label="Service", shape="rounded")])
    assert 'svc("Service")' in out


def test_stadium_shape():
    out = render(nodes=[Node(id="ext", label="User", shape="stadium")])
    assert 'ext(["User"])' in out


def test_cylinder_shape():
    out = render(nodes=[Node(id="db", label="DB", shape="cylinder")])
    assert 'db[("DB")]' in out


def test_subroutine_shape():
    out = render(nodes=[Node(id="f", label="F", shape="subroutine")])
    assert 'f[["F"]]' in out


def test_circle_shape():
    out = render(nodes=[Node(id="o", label="O", shape="circle")])
    assert 'o(("O"))' in out


def test_rhombus_shape():
    out = render(nodes=[Node(id="g", label="Gate", shape="rhombus")])
    assert 'g{"Gate"}' in out


def test_hexagon_shape():
    out = render(nodes=[Node(id="h", label="H", shape="hexagon")])
    assert 'h{{"H"}}' in out


def test_parallelogram_shape():
    out = render(nodes=[Node(id="p", label="P", shape="parallelogram")])
    assert 'p[/"P"/]' in out


def test_unknown_shape_raises():
    with pytest.raises(MermaidRenderError, match="Unknown shape"):
        render(nodes=[Node(id="a", label="A", shape="triangle")])


def test_shape_combines_with_css_class():
    out = render(
        nodes=[Node(id="db", label="DB", shape="cylinder", css_class="data")],
        class_defs=[ClassDef(name="data", style="fill:#fff5e6")],
    )
    assert 'db[("DB")]:::data' in out


def test_shape_label_escaping_inside_wrapper():
    out = render(nodes=[Node(id="x", label='He said "hi"', shape="rounded")])
    assert 'x("He said &quot;hi&quot;")' in out
