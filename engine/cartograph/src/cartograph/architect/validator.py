"""Structural validation for an Architecture.

Pure structural checks. The vibes layer (kind strings, descriptions,
free-form what payloads) is intentionally not validated.

Checks:
  - schema_version is supported
  - Component ids are non-empty and unique
  - Edge.source and Edge.target reference declared component ids
  - Component.parent references a declared component id
  - No cycles in the parent chain
  - Component.domains entries are known Cartograph domains or glue
  - Each entry in Component.widgets points at a real cg/<dir>/ install
  - Component.widgets is empty when "glue" is in domains
  - No duplicate widget entries in a single Component.widgets
"""

import json
import os
from dataclasses import dataclass
from typing import List, Optional

from cg.cg_universal_graph_cycles_python.src.graph_cycles import find_cycles

from .schema import (
    ARCHITECT_SPECIAL_DOMAINS,
    Architecture,
    Component,
    Edge,
    SUPPORTED_SCHEMA_VERSIONS,
)


@dataclass
class ValidationIssue:
    code: str
    message: str
    where: str = ""


def _load_valid_domains() -> frozenset:
    """Read Cartograph's domain whitelist from library_config.json."""
    config_path = os.path.join(
        os.path.dirname(__file__), os.pardir, "library_config.json"
    )
    try:
        with open(config_path, encoding="utf-8") as f:
            return frozenset(json.load(f)["domains"].keys())
    except Exception:
        return frozenset()


def validate_architecture(
    arch: Architecture,
    project_root: Optional[str] = None,
) -> List[ValidationIssue]:
    """Return a list of structural issues. Empty list = valid.

    project_root is where cg/ is expected to live. Used to verify
    Component.widget references point at real installs. Defaults to the
    current working directory.
    """
    issues: List[ValidationIssue] = []
    if project_root is None:
        project_root = os.getcwd()

    if arch.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        issues.append(
            ValidationIssue(
                code="schema_version",
                message=(
                    f"schema_version {arch.schema_version!r} is not supported. "
                    f"Known: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
                ),
            )
        )

    seen_ids = set()
    duplicates = set()
    blank_ids = 0
    for c in arch.components:
        if not c.id or not c.id.strip():
            blank_ids += 1
            continue
        if c.id in seen_ids:
            duplicates.add(c.id)
        seen_ids.add(c.id)

    if blank_ids:
        issues.append(
            ValidationIssue(
                code="component_id",
                message=f"{blank_ids} component(s) have an empty id",
            )
        )
    for dup in sorted(duplicates):
        issues.append(
            ValidationIssue(
                code="component_id",
                message=f"Duplicate component id: {dup!r}",
                where=dup,
            )
        )

    for c in arch.components:
        if c.parent is None or c.parent == "":
            continue
        if c.parent not in seen_ids:
            issues.append(
                ValidationIssue(
                    code="parent_ref",
                    message=(
                        f"Component {c.id!r} parent {c.parent!r} "
                        f"is not a declared component"
                    ),
                    where=c.id,
                )
            )

    parent_graph = {}
    for c in arch.components:
        if not c.id:
            continue
        if c.parent and c.parent in seen_ids:
            parent_graph[c.id] = [(c.parent, c.id)]
        else:
            parent_graph[c.id] = []
    cycles = find_cycles(parent_graph)
    for cycle in cycles:
        chain = " -> ".join(node for node, _ in cycle)
        issues.append(
            ValidationIssue(
                code="parent_cycle",
                message=f"Parent chain cycle: {chain}",
            )
        )

    for i, e in enumerate(arch.edges):
        if not e.source or e.source not in seen_ids:
            issues.append(
                ValidationIssue(
                    code="edge_source",
                    message=(
                        f"Edge[{i}] source {e.source!r} "
                        f"is not a declared component"
                    ),
                    where=f"edges[{i}]",
                )
            )
        if not e.target or e.target not in seen_ids:
            issues.append(
                ValidationIssue(
                    code="edge_target",
                    message=(
                        f"Edge[{i}] target {e.target!r} "
                        f"is not a declared component"
                    ),
                    where=f"edges[{i}]",
                )
            )

    cartograph_domains = _load_valid_domains()
    if cartograph_domains:
        accepted = cartograph_domains | ARCHITECT_SPECIAL_DOMAINS
        for c in arch.components:
            if not c.domains:
                continue
            seen_domains = set()
            for d in c.domains:
                if not d:
                    issues.append(
                        ValidationIssue(
                            code="domain",
                            message=f"Component {c.id!r} has an empty domain entry",
                            where=c.id,
                        )
                    )
                    continue
                if d in seen_domains:
                    issues.append(
                        ValidationIssue(
                            code="domain",
                            message=(
                                f"Component {c.id!r} lists domain {d!r} more than once"
                            ),
                            where=c.id,
                        )
                    )
                seen_domains.add(d)
                if d not in accepted:
                    issues.append(
                        ValidationIssue(
                            code="domain",
                            message=(
                                f"Component {c.id!r} domain {d!r} is not "
                                f"a Cartograph domain or architect-special "
                                f"value. Cartograph domains: "
                                f"{sorted(cartograph_domains)}. "
                                f"Architect-special: "
                                f"{sorted(ARCHITECT_SPECIAL_DOMAINS)}."
                            ),
                            where=c.id,
                        )
                    )

    for c in arch.components:
        if not c.widgets:
            continue
        if "glue" in (c.domains or []):
            issues.append(
                ValidationIssue(
                    code="widget_glue_conflict",
                    message=(
                        f"Component {c.id!r} has widgets {c.widgets!r} "
                        f"but also lists 'glue' in domains. Glue means "
                        f"project code you write yourself, not widget "
                        f"targets - drop one or the other."
                    ),
                    where=c.id,
                )
            )
        seen_widgets = set()
        for w in c.widgets:
            if not w:
                issues.append(
                    ValidationIssue(
                        code="widget_invalid",
                        message=f"Component {c.id!r} has an empty widget entry",
                        where=c.id,
                    )
                )
                continue
            if w in seen_widgets:
                issues.append(
                    ValidationIssue(
                        code="widget_duplicate",
                        message=(
                            f"Component {c.id!r} lists widget {w!r} more "
                            f"than once"
                        ),
                        where=c.id,
                    )
                )
            seen_widgets.add(w)
            widget_dir = os.path.join(project_root, "cg", w)
            manifest = os.path.join(widget_dir, "widget.json")
            if not os.path.isdir(widget_dir):
                issues.append(
                    ValidationIssue(
                        code="widget_missing",
                        message=(
                            f"Component {c.id!r} references widget "
                            f"{w!r} but cg/{w}/ does not exist. "
                            f"Install it first: "
                            f"`cartograph install <widget_id>`."
                        ),
                        where=c.id,
                    )
                )
            elif not os.path.isfile(manifest):
                issues.append(
                    ValidationIssue(
                        code="widget_invalid",
                        message=(
                            f"Component {c.id!r} references widget "
                            f"{w!r} but cg/{w}/widget.json is missing - "
                            f"the directory does not look like a "
                            f"Cartograph widget install."
                        ),
                        where=c.id,
                    )
                )

    return issues


def format_issues(issues: List[ValidationIssue]) -> str:
    """Render issues as a human-readable multi-line string."""
    if not issues:
        return "Architecture is valid."
    lines = [f"{len(issues)} issue(s) found:"]
    for issue in issues:
        prefix = f"  - [{issue.code}]"
        if issue.where:
            prefix += f" ({issue.where})"
        lines.append(f"{prefix} {issue.message}")
    return "\n".join(lines)
