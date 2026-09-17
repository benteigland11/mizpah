"""Example: render a small mixed-domain graph to Mermaid syntax."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.mermaid_graph_renderer import (
    ClassDef,
    Cluster,
    Edge,
    Node,
    render,
)


def main() -> None:
    output = render(
        nodes=[
            Node(id="user", label="User", css_class="external"),
            Node(id="api", label="API Gateway", parent="backend"),
            Node(id="auth", label="Auth Service", parent="backend"),
            Node(id="store", label="Datastore", parent="backend"),
        ],
        edges=[
            Edge(source="user", target="api", label="HTTPS"),
            Edge(source="api", target="auth", label="verify"),
            Edge(source="auth", target="store", style="-.->"),
        ],
        clusters=[Cluster(id="backend", label="Backend")],
        class_defs=[
            ClassDef(name="external", style="fill:#eef,stroke:#88a"),
        ],
        direction="LR",
    )
    print(output)


if __name__ == "__main__":
    main()
