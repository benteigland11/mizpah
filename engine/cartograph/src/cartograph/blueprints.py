"""
Blueprint identity, schema, and ID utilities.

A blueprint is a higher-order widget: same shape on disk, but its src/
is allowed to import from a declared list of widgets in cg/. See
.claude/reports/v0.7-blueprints-plan.md for the full contract.

This module owns:
  - the blueprint ID format (`<registry>?-bp-<name>-<language>`)
  - blueprint.json schema validation
  - composition / decomposition of blueprint IDs

It does NOT touch the filesystem or run validation. It is pure data.
"""

import re
from dataclasses import dataclass

# Reserved kind marker. No leaf widget may begin with `bp-`.
BP_PREFIX = "bp-"

# A blueprint name is the slug between bp- and the language suffix.
# Lowercase letters, digits, and hyphens. Must start with a letter.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$")

# Semver-ish: M.m.p with optional pre-release. Strict-enough for our needs.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class BlueprintId:
    """Decomposed blueprint identifier.

    For a local blueprint, `registry` is None.
    For a published blueprint, `registry` is the prefix string ("cg",
    "myorg", etc.).
    """

    registry: str | None
    name: str
    language: str

    def compose(self) -> str:
        if self.registry:
            return f"{self.registry}-{BP_PREFIX}{self.name}-{self.language}"
        return f"{BP_PREFIX}{self.name}-{self.language}"


def is_blueprint_id(item_id: str) -> bool:
    """Return True if `item_id` looks like a blueprint ID.

    Cheap structural check used by code paths that need to branch on
    kind without loading a manifest (e.g. the install resolver).
    """
    if not item_id:
        return False
    parts = item_id.split("-")
    # `bp-<name>-<lang>` -> at least 3 parts starting with bp.
    # `<reg>-bp-<name>-<lang>` -> at least 4 parts with bp at index 1.
    if parts[0] == "bp":
        return len(parts) >= 3
    if len(parts) >= 4 and parts[1] == "bp":
        return True
    return False


def parse_blueprint_id(item_id: str) -> BlueprintId:
    """Decompose a blueprint ID. Raises ValueError on malformed input.

    Accepts both forms:
      - `bp-auth-flow-python`               -> registry=None
      - `cg-bp-auth-flow-python`            -> registry='cg'
      - `myorg-bp-auth-flow-python`         -> registry='myorg'

    The language is the trailing token. The name is everything between
    the bp marker and the language token, joined with hyphens.
    """
    if not item_id or not isinstance(item_id, str):
        raise ValueError(f"Blueprint ID must be a non-empty string, got: {item_id!r}")

    parts = item_id.split("-")
    if len(parts) < 3:
        raise ValueError(
            f"Blueprint ID '{item_id}' is malformed. "
            f"Expected '<registry>?-bp-<name>-<language>'."
        )

    if parts[0] == "bp":
        registry = None
        name_parts = parts[1:-1]
    elif len(parts) >= 4 and parts[1] == "bp":
        registry = parts[0]
        name_parts = parts[2:-1]
    else:
        raise ValueError(
            f"Blueprint ID '{item_id}' is missing the 'bp-' kind marker."
        )

    if not name_parts:
        raise ValueError(
            f"Blueprint ID '{item_id}' has no name segment between 'bp-' and the language."
        )

    name = "-".join(name_parts)
    language = parts[-1]

    if not _NAME_RE.match(name):
        raise ValueError(
            f"Blueprint name '{name}' must be lowercase letters, digits, and hyphens; "
            "must start with a letter."
        )
    if not language:
        raise ValueError(f"Blueprint ID '{item_id}' is missing the language segment.")

    return BlueprintId(registry=registry, name=name, language=language)


def compose_blueprint_id(name: str, language: str, registry: str | None = None) -> str:
    """Build a blueprint ID from parts. Used by `cartograph blueprint create`.

    The author supplies the slug (e.g. 'auth-flow') and language; the
    registry prefix is added later at publish time.
    """
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Blueprint name '{name}' must be lowercase letters, digits, and hyphens; "
            "must start with a letter."
        )
    if not language:
        raise ValueError("Blueprint language is required.")
    return BlueprintId(registry=registry, name=name, language=language.lower()).compose()


# --- Schema validation -----------------------------------------------------

# Fields the author is expected to fill in.
_AUTHOR_REQUIRED = ("id", "name", "language", "version", "description",
                    "tags", "dependencies")


def validate_blueprint_manifest(
    raw: dict,
    *,
    supported_languages: set[str] | list[str] | None = None,
    valid_domains: set[str] | frozenset[str] | None = None,
    library_widget_ids: set[str] | None = None,
) -> dict:
    """Validate a blueprint.json dict against the v0.7 schema.

    Returns {"ok": bool, "errors": [str, ...], "warnings": [str, ...]}.

    `supported_languages` and `valid_domains` are injected to keep this
    module free of side effects. `library_widget_ids` is the set of IDs
    available in the local library (used to verify dep widgets exist).
    Any of these may be omitted and the corresponding check is skipped.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Required top-level fields exist.
    for field in _AUTHOR_REQUIRED:
        if field not in raw:
            errors.append(f"Missing required field: '{field}'")
    if errors:
        return _result(errors, warnings)

    # 2. ID parses cleanly.
    try:
        bid = parse_blueprint_id(raw["id"])
    except ValueError as e:
        return _result([str(e)], warnings)

    # 3. ID parts agree with declared name and language.
    if bid.name != raw["name"]:
        errors.append(
            f"id '{raw['id']}' has name '{bid.name}' but manifest declares "
            f"name '{raw['name']}'. They must match."
        )
    if bid.language != str(raw["language"]).lower():
        errors.append(
            f"id '{raw['id']}' has language '{bid.language}' but manifest "
            f"declares language '{raw['language']}'. They must match."
        )

    # 4. Local manifests must NOT carry a registry prefix.
    if bid.registry is not None:
        errors.append(
            f"id '{raw['id']}' carries registry prefix '{bid.registry}-'. "
            "Local blueprints use the unprefixed form 'bp-<name>-<language>'. "
            "Registry prefixes are added at publish time."
        )

    # 5. Language is supported.
    if supported_languages is not None:
        if bid.language not in supported_languages:
            errors.append(
                f"Language '{bid.language}' is not supported. "
                f"Supported: {sorted(supported_languages)}"
            )

    # 6. Version is semver-shaped.
    if not _VERSION_RE.match(str(raw["version"])):
        errors.append(
            f"version '{raw['version']}' must be semver (e.g. '0.1.0', '1.2.3')."
        )

    # 7. Description: non-empty, no [TODO].
    desc = raw.get("description", "")
    if not isinstance(desc, str) or not desc.strip():
        errors.append("description must be a non-empty string.")
    elif "[TODO]" in desc:
        errors.append("description still contains a [TODO] placeholder.")

    # 8. Tags: list of strings, no TODOs.
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        errors.append("tags must be a list of strings.")
    else:
        if not tags:
            errors.append("tags must not be empty (3-5 tags recommended).")
        for t in tags:
            if "[TODO" in t:
                errors.append(f"tag '{t}' is a placeholder; replace with a real tag.")
                break

    # 9. Dependencies: list of {id, version}, no self-references, no
    #    blueprint-on-blueprint, all leaf widgets.
    deps = raw.get("dependencies")
    if not isinstance(deps, list):
        errors.append("dependencies must be a list.")
    elif not deps:
        errors.append(
            "dependencies must not be empty. A blueprint with no widgets is "
            "just a widget — author it as one."
        )
    else:
        seen: set[str] = set()
        for i, d in enumerate(deps):
            label = f"dependencies[{i}]"
            if not isinstance(d, dict):
                errors.append(f"{label} must be an object with 'id' and 'version'.")
                continue
            if "id" not in d or "version" not in d:
                errors.append(f"{label} must have both 'id' and 'version'.")
                continue
            dep_id = d["id"]
            dep_ver = d["version"]
            if not isinstance(dep_id, str) or not dep_id:
                errors.append(f"{label}.id must be a non-empty string.")
                continue
            if not isinstance(dep_ver, str) or not _VERSION_RE.match(dep_ver):
                errors.append(
                    f"{label}.version '{dep_ver}' must be exact semver (no ranges)."
                )
            if dep_id == raw["id"]:
                errors.append(f"{label} is a self-reference: '{dep_id}'.")
            if is_blueprint_id(dep_id):
                errors.append(
                    f"{label} '{dep_id}' is a blueprint. v0.7 blueprints "
                    "compose widgets only (leaf-only)."
                )
            if dep_id in seen:
                errors.append(f"{label} '{dep_id}' is duplicated.")
            seen.add(dep_id)
            # Dep language must match blueprint language (single-language v0.7).
            if isinstance(dep_id, str) and dep_id.endswith(f"-{bid.language}") is False:
                errors.append(
                    f"{label} '{dep_id}' is not a {bid.language} widget. "
                    f"v0.7 blueprints compose widgets in a single language."
                )
            if library_widget_ids is not None and dep_id not in library_widget_ids:
                errors.append(
                    f"{label} '{dep_id}' is not installed in the project's cg/. "
                    "Run `cartograph install <id>` from the project root, then re-validate."
                )

    # 10. Domains: optional on author input (derived at checkin), but if
    #     present must be a list of valid domain strings.
    domains = raw.get("domains")
    if domains is not None:
        if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
            errors.append("domains must be a list of strings (or omitted).")
        elif valid_domains is not None:
            for d in domains:
                if d not in valid_domains:
                    errors.append(
                        f"domains entry '{d}' is not a valid domain. "
                        f"Valid: {sorted(valid_domains)}"
                    )

    return _result(errors, warnings)


def _result(errors: list[str], warnings: list[str]) -> dict:
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def derive_domains(deps: list[dict], lookup_widget: callable) -> list[str]:
    """Compute the union of dep widget domains.

    `lookup_widget` is a callable taking a widget id and returning the
    widget dict (or None). Domains are de-duplicated and sorted for a
    stable on-disk representation.
    """
    domains: set[str] = set()
    for d in deps:
        widget = lookup_widget(d.get("id"))
        if not widget:
            continue
        meta = widget.get("meta", widget)
        domain = (meta.get("domain") or "").strip().lower()
        if domain:
            domains.add(domain)
    return sorted(domains)
