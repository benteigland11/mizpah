"""Cartograph architect: project-local app/system planning artifact.

Public API:
  - Architecture, Component, Edge: the dataclass schema users build
  - load_architecture(path): load an architect.py and return Architecture
  - validate_architecture(arch): structural validation, returns issues
  - render(arch): produce a Mermaid string for the architecture
  - write_architect_template(path): scaffold a starter architect.py
  - resolve_architect_path(): locate architect.py given an explicit
    path or the current directory
"""

from .loader import ArchitectLoadError, load_architecture
from .markdown import render_markdown
from .mutate import ArchitectMutationError, set_component_widgets
from .paths import ARCHITECT_FILENAME, resolve_architect_path
from .renderer import render
from .scaffold import write_architect_template
from .schema import (
    ARCHITECT_SPECIAL_DOMAINS,
    SUPPORTED_SCHEMA_VERSIONS,
    Architecture,
    Component,
    Edge,
)
from .validator import (
    ValidationIssue,
    format_issues,
    validate_architecture,
)

__all__ = [
    "ARCHITECT_FILENAME",
    "ARCHITECT_SPECIAL_DOMAINS",
    "ArchitectLoadError",
    "ArchitectMutationError",
    "Architecture",
    "Component",
    "Edge",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ValidationIssue",
    "format_issues",
    "load_architecture",
    "render",
    "render_markdown",
    "resolve_architect_path",
    "set_component_widgets",
    "validate_architecture",
    "write_architect_template",
]
