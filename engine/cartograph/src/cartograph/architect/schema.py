"""Dataclasses describing a project's architecture.

This file is the schema target for project-local `architect.py` files.
Users import these dataclasses, build a single `Architecture` instance
at module level, and let Cartograph load + validate + render it.

Architect is a vibes-layer planning artifact. It describes how the
pieces of a project relate so an agent can reason about glue code and
widget choices at the app level. It is not a live state tracker, not a
widget binding (that's blueprints), and not a geometric layout.

The vocabulary is deliberately cross-domain:

  Components can be software services, processes, mechanical parts,
  RTL modules, sensors, teams, or anything else that participates in
  the system.

  Edges describe relationships of any kind: depends_on, flows,
  meshes_with, clocks, mounted_on, actuates, etc.
"""

from dataclasses import dataclass, field
from typing import List, Optional


# Architect-only domain values. Not Cartograph widget domains - these
# describe planning-time roles a Component plays in the architecture.
#
#   glue   Project-specific code the user will write. Not a widget
#          target. Tells the planning skill "do not search the registry
#          for this; it's bespoke."
ARCHITECT_SPECIAL_DOMAINS = frozenset({"glue"})


@dataclass
class Component:
    """A distinct part of the system.

    id          Unique identifier (no spaces, used by edges and parents).
    kind        Free-form type tag. Cross-domain examples:
                  software:  service, process, queue, store, external
                  modeling:  part, assembly, fastener, fluid
                  rtl:       module, clock_domain, bus, register_file
                  physical:  motor, sensor, power_rail, enclosure
    domains     Zero or more domain tags. Each is either a Cartograph
                widget domain (backend, data, ml, security, infra,
                frontend, universal, modeling, rtl, devops) or an
                architect-special domain ("glue" for project-specific
                code that will not become a widget). Multi-domain is
                allowed: a sensor parser that runs inference might be
                ["data", "ml"]; a backend handler that's bespoke might
                be ["backend", "glue"]. The planning skill uses this to
                route widget search and to skip glue-only components.
    description Free-form prose. The skill reads this to understand the
                component's role.
    parent      id of another Component this one lives inside. Empty
                means top level. Used for natural groupings (frontend
                lives inside the web client, gear lives inside gearbox).
    widgets     Directory names under cg/ of installed widgets that
                fill this slot. Empty means unfilled (whiteboard) or
                glue. Multiple widgets are allowed: a service slot can
                be composed of e.g. a router + validator + logger, all
                attached to the same Component. Each widget must be
                installed at cg/<dir>/ before it can be referenced.
                Use `cartograph architect attach` and `detach` to
                manage the list from the CLI; hand-editing is fine.
    """
    id: str
    kind: str = ""
    domains: List[str] = field(default_factory=list)
    description: str = ""
    parent: Optional[str] = None
    widgets: List[str] = field(default_factory=list)


@dataclass
class Edge:
    """How two components are connected.

    source       id of the originating component.
    target       id of the receiving component.
    kind         Free-form relationship tag. Cross-domain examples:
                   software:  depends_on, calls, publishes_to
                   modeling:  meshes_with, mounted_on, fastens_to
                   rtl:       clocks, drives, samples, resets
                   physical:  powers, monitors, actuates
    what         Optional payload, signal, or material name. Examples:
                   "UserCreatedEvent", "12V power", "torque",
                   "clock signal", "sheet metal blanks".
    description  Free-form prose for the edge.
    """
    source: str
    target: str
    kind: str = ""
    what: str = ""
    description: str = ""


@dataclass
class Architecture:
    """The root planning artifact.

    schema_version  Format version. Bumped when the architect schema
                    changes incompatibly.
    goal            One- or two-sentence description of what this
                    project is trying to achieve.
    components      All components in the system.
    edges           Relationships between components.
    notes           Free-form prose attached to the whole architecture.
    """
    schema_version: str = "0.1"
    goal: str = ""
    components: List[Component] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    notes: str = ""


SUPPORTED_SCHEMA_VERSIONS = frozenset({"0.1"})
