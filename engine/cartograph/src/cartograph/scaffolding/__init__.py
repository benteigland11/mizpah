"""
Widget and blueprint scaffolding.

Usage:
    from cartograph.scaffolding import create_widget, create_blueprint
"""

import json
import logging
import os
import re
import sys

log = logging.getLogger("cartograph")


from cartograph.engine import DEFAULT_INSTALL_DIR, python_dir_name

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "library_config.json")

def _library_notes(language: str, domain: str = "") -> dict:
    """Load general + language-specific + domain-specific notes from library_config.json."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return {}
    notes = {
        "general": cfg.get("general_notes", ""),
        "language": cfg.get("language_notes", {}).get(language.lower(), ""),
    }
    domain_note = cfg.get("domain_notes", {}).get(domain.lower(), "")
    if domain_note:
        notes["domain"] = domain_note
    return notes

from cartograph.validator import VALID_DOMAINS as _VALID_DOMAINS


# A widget/blueprint slug becomes a code identifier (Python module, SystemVerilog
# module, JS export, etc.). Across every supported language an identifier must
# start with a letter or underscore and contain only letters, digits, and
# underscores - so the slug, with hyphens mapping to underscores, must follow the
# same rule. Reject bad names loudly at create time rather than silently mangling
# them into something the author never chose (e.g. "8b10b-encoder" would become
# the illegal identifier "8b10b_encoder" and only blow up later at compile time).
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _suggest_name(name_base):
    """Best-effort legal alternative: move digit-leading segments to the end
    (8b10b-encoder -> encoder-8b10b). Returns None if no clean reorder exists."""
    parts = [p for p in name_base.split("-") if p]
    head = [p for p in parts if not p[0].isdigit()]
    tail = [p for p in parts if p[0].isdigit()]
    reordered = head + tail
    if reordered and not reordered[0][0].isdigit():
        return "-".join(reordered)
    return None


def validate_name_identifier(name_base, kind="widget"):
    """Return an error message if name_base can't become a legal cross-language
    identifier, else None. name_base is the slug minus domain/language affixes."""
    ident = name_base.replace("-", "_")
    label = kind.capitalize()
    if not ident:
        return f"{label} name is empty - provide a name like 'retry-backoff'."
    if ident[0].isdigit():
        msg = (
            f"{label} name '{name_base}' can't start with a digit: it becomes the "
            f"code identifier '{ident}', which is illegal in every supported "
            f"language (identifiers must start with a letter)."
        )
        suggestion = _suggest_name(name_base)
        if suggestion:
            msg += f" Try '{suggestion}' instead."
        return msg
    if not _IDENT_RE.match(ident):
        bad = sorted({c for c in name_base if not (c.isalnum() or c in "-_")})
        return (
            f"{label} name '{name_base}' has characters that aren't allowed in a "
            f"code identifier ({', '.join(repr(c) for c in bad)}). Use only "
            f"letters, digits, and hyphens."
        )
    return None


def create_widget(carto, item_id, language, name=None, domain=None, tags=None,
                  target_dir=None, gpu_targets=None, widget_type=None):
    """Scaffold a new widget directory. Returns a status dict."""
    if tags is None:
        tags = []
    if gpu_targets is None:
        gpu_targets = []

    if not language:
        return {"status": "error", "message": "Language is required for widget creation."}

    normalized_lang = carto._normalize_language(language)

    # Strip language suffix to get the base id for name/domain inference
    name_base = item_id
    if name_base.endswith(f"-{normalized_lang}"):
        name_base = name_base[: -len(normalized_lang) - 1]

    # Derive display name from base parts
    base_parts = name_base.split("-")
    if domain is None:
        domain = "backend"

    # Strip domain prefix if already present (avoid backend-backend-...)
    if name_base.startswith(f"{domain}-"):
        name_base = name_base[len(domain) + 1:]

    # Reject names that can't become a legal identifier (e.g. leading digit)
    # before creating anything on disk.
    name_error = validate_name_identifier(name_base, kind="widget")
    if name_error:
        return {"status": "error", "message": name_error}

    # Strip domain prefix from display name if it matches
    if not name:
        display_parts = base_parts[1:] if base_parts[0] in _VALID_DOMAINS else base_parts
        name = " ".join(display_parts).title() if display_parts else name_base.replace("-", " ").title()

    # Build full widget_id: <domain>-<name>-<language>
    item_id = f"{domain}-{name_base}"
    if not item_id.endswith(f"-{normalized_lang}"):
        item_id = f"{item_id}-{normalized_lang}"

    if not target_dir:
        target_dir = os.getcwd()

    # Always place under <project_root>/cg/<widget_id>
    target_dir = os.path.join(target_dir, DEFAULT_INSTALL_DIR, python_dir_name(item_id))

    if os.path.exists(target_dir):
        return {"status": "error", "message": f"Directory already exists: {target_dir}"}

    log.info("Creating widget '%s' (%s) in %s", item_id, language, target_dir)

    os.makedirs(target_dir)
    for d in ("src", "tests", "examples"):
        os.makedirs(os.path.join(target_dir, d))

    # Derive module name from item_id (strip category prefix + language suffix)
    parts = item_id.split("-")
    module_name = "_".join(parts[1:-1]) if len(parts) >= 3 else parts[0]
    module_name = re.sub(r"[^a-zA-Z0-9_]", "_", module_name)
    if module_name.startswith("test_") or module_name == "test":
        module_name = "mod_" + module_name

    # Build widget.json
    meta = {"id": item_id, "name": name, "version": "1.0.0", "domain": domain, "tags": tags or ["[TODO: add 3-5 tags]"]}

    tech_stack = {"language": normalized_lang, "dependencies": []}

    manifest = {
        "meta": meta,
        "description": f"[TODO] Describe what {name} does",
        "tech_stack": tech_stack,
        "custom_notes": "",
        "library_notes": _library_notes(normalized_lang, domain),
    }
    with open(os.path.join(target_dir, "widget.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Write language-specific files from engine scaffold
    from cartograph.languages import get_engine
    from cartograph.languages.base import LanguageEngine as _BaseEngine
    engine = get_engine(normalized_lang)
    if engine and engine.__class__.scaffold is not _BaseEngine.scaffold:
        engine.scaffold(target_dir, module_name, name, item_id=item_id, gpu_targets=gpu_targets, domain=domain)
    else:
        return {"status": "error", "message": f"No scaffold template for language '{normalized_lang}'"}

    log.info("Created widget: %s", target_dir)
    return {"status": "success", "path": target_dir, "item_id": item_id, "language": normalized_lang}


def create_blueprint(carto, name, language, target_dir=None, description=None, tags=None):
    """Scaffold a new blueprint directory.

    A blueprint is a higher-order widget: same shape on disk (src/, tests/,
    examples/) but with a blueprint.json manifest instead of widget.json
    and a `dependencies` array that pins specific widget versions.

    Returns a status dict.
    """
    from cartograph.blueprints import compose_blueprint_id, BP_PREFIX

    if not language:
        return {"status": "error", "message": "Language is required for blueprint creation."}
    if not name:
        return {"status": "error", "message": "Name is required for blueprint creation."}

    normalized_lang = carto._normalize_language(language)

    # v0.7 supports blueprints in any language whose engine is installed and
    # exposes a blueprint scaffold + validation surface. Each engine owns its
    # own skeleton via blueprint_scaffold(); the validator drives off
    # engine.file_ext / src_import_pattern / run_blueprint_example.
    from cartograph.languages import get_engine
    engine = get_engine(normalized_lang)
    if engine is None or not engine.supported:
        return {
            "status": "error",
            "message": (
                f"No language engine available for '{language}'. Run "
                f"`cartograph doctor` to see which engines are installed."
            ),
        }

    # Author supplies just the slug. Strip any leading bp- they may have typed.
    slug = name
    if slug.startswith(BP_PREFIX):
        slug = slug[len(BP_PREFIX):]
    # Strip trailing language if user typed the full id by accident.
    if slug.endswith(f"-{normalized_lang}"):
        slug = slug[: -len(normalized_lang) - 1]

    # Reject names that can't become a legal identifier (e.g. leading digit).
    name_error = validate_name_identifier(slug, kind="blueprint")
    if name_error:
        return {"status": "error", "message": name_error}

    try:
        item_id = compose_blueprint_id(slug, normalized_lang)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    if not target_dir:
        target_dir = os.getcwd()

    target_dir = os.path.join(target_dir, DEFAULT_INSTALL_DIR, python_dir_name(item_id))

    if os.path.exists(target_dir):
        return {"status": "error", "message": f"Directory already exists: {target_dir}"}

    log.info("Creating blueprint '%s' (%s) in %s", item_id, language, target_dir)

    os.makedirs(target_dir)
    for d in ("src", "tests", "examples"):
        os.makedirs(os.path.join(target_dir, d))

    manifest = {
        "id": item_id,
        "name": slug,
        "language": normalized_lang,
        "version": "0.1.0",
        "description": description or f"[TODO] Describe what {slug} does",
        "tags": tags or ["[TODO: add 3-5 tags]"],
        "dependencies": [],
        "domains": [],
    }
    with open(os.path.join(target_dir, "blueprint.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # Derive a module name from the slug for placeholder files.
    module_name = re.sub(r"[^a-zA-Z0-9_]", "_", slug.replace("-", "_"))
    if module_name.startswith("test_") or module_name == "test":
        module_name = "mod_" + module_name

    # Each engine owns its own opinionated blueprint skeleton. The base
    # default writes minimal placeholder files keyed off engine.file_ext so
    # validate doesn't immediately complain about empty src/.
    engine.blueprint_scaffold(target_dir, module_name, slug)

    log.info("Created blueprint: %s", target_dir)
    return {
        "status": "success",
        "path": target_dir,
        "item_id": item_id,
        "language": normalized_lang,
        "kind": "blueprint",
    }


