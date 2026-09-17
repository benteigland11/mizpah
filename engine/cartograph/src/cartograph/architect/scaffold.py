"""Scaffold a new architect.py file."""

import os


_TEMPLATE = '''"""Project architecture map.

This file describes how the parts of this project relate so an agent
can plan glue code and widget choices at the app level. It's a vibes
layer - free-form prose plus structured connections - not a live state
tracker, not a widget binding (that's blueprints), and not a geometric
layout.

Replace the example below with your own project. Run
`cartograph architect validate` after editing to check structure, and
`cartograph architect render` to produce a Mermaid diagram.

Rendering constraints (this file becomes a Mermaid flowchart):
  - `id` becomes a Mermaid node id. Stick to letters, digits, and
    underscores - the renderer sanitizes, but readable ids beat
    sanitized ones.
  - `kind` and `description` are shown as labels. Quotes and newlines
    are escaped but kept; keep labels short for legibility.
  - `parent` groupings render as `subgraph` blocks. Nesting works but
    reads poorly past two or three levels.
  - Edges may target either a Component id or a parent-cluster id;
    both are valid endpoints.
  - Cycles in `edges` are allowed and often meaningful (services
    calling each other). Only `parent` chains must be acyclic.
"""

from cartograph.architect.schema import Architecture, Component, Edge


# Examples of `kind` (free-form, choose anything that fits):
#   software:  service, process, queue, store, external
#   modeling:  part, assembly, fastener, fluid
#   rtl:       module, clock_domain, bus, register_file
#   physical:  motor, sensor, power_rail, enclosure
#
# Examples of edge `kind`:
#   software:  depends_on, calls, publishes_to, subscribes_to
#   modeling:  meshes_with, mounted_on, fastens_to, contains
#   rtl:       clocks, drives, samples, resets
#   physical:  powers, monitors, actuates, transmits

# `domains` accepts zero or more entries. Each is either a Cartograph
# widget domain (backend, data, ml, security, infra, frontend, universal,
# modeling, rtl, devops) or "glue" - the architect-special value meaning
# "no widget will fit here, I'll write it myself."
#
# Glue is a SLOT STATUS, not a code-style tag. Drop it the moment you
# attach a widget - the slot is no longer "I'll write it from scratch."
# Bespoke wiring around an attached widget is implementation detail, not
# glue at the slot level. Validate refuses widgets+glue on the same
# Component.
#
# Multi-domain is allowed: a sensor parser that runs inference is
# ["data", "ml"]; a backend slot you've decided to write yourself is
# ["backend", "glue"].

# `widgets` (optional, list) links one or more installed widgets to
# the slot. Each entry is a directory name under cg/. Multiple widgets
# are allowed: a service slot can compose router + validator + logger,
# all linked to the same Component. Each widget must already be
# installed - validate refuses a phantom. CLI:
#   cartograph architect link <component_id> <widget_dir>
#   cartograph architect link <component_id> <widget_dir> --clear
#   cartograph architect link <component_id> --clear   (clears all)
# Linked components render with a diamond marker and the directory
# list in the label, so the three states - filled, glue, unfilled -
# read at a glance.

architecture = Architecture(
    schema_version="0.1",
    goal="Replace this with one or two sentences about what this project does.",
    components=[
        Component(
            id="user",
            kind="external",
            description="The person interacting with the system.",
        ),
        Component(
            id="ui",
            kind="frontend",
            domains=["frontend"],
            description="What the user touches first.",
            parent="client",
        ),
        Component(
            id="client",
            kind="application",
            description="The shell that hosts the UI.",
        ),
        Component(
            id="api",
            kind="service",
            domains=["backend", "glue"],
            description="Request handler. Mostly bespoke wiring, "
                        "no widget will fit it whole.",
            parent="server",
        ),
        Component(
            id="store",
            kind="datastore",
            domains=["data"],
            description="Persistent storage.",
            parent="server",
        ),
        Component(
            id="server",
            kind="deployment",
            description="The hosted side of the app.",
        ),
    ],
    edges=[
        Edge(source="user", target="ui", kind="interacts_with"),
        Edge(source="ui", target="api", kind="calls", what="HTTPS request"),
        Edge(source="api", target="store", kind="reads_writes"),
    ],
    notes="Free-form prose about cross-cutting concerns: auth, logging, "
          "deployment topology, etc.",
)
'''


def write_architect_template(path: str, *, overwrite: bool = False) -> None:
    """Write the starter architect.py to `path`.

    Refuses to overwrite an existing file unless overwrite=True.
    """
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(
            f"{path} already exists. Pass overwrite=True to replace."
        )
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_TEMPLATE)
