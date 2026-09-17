"""
Language engine registry.

Maps language name strings (as they appear in widget.json tech_stack.language)
to the appropriate LanguageEngine subclass instance.

Engines are auto-discovered from Python modules in this package. Any module
that defines a LanguageEngine subclass with a `name` attribute will be
registered automatically. To hide a WIP engine, set `supported = False`
on the class - it will be registered but excluded from supported_languages().

To add a new language:
  1. Create languages/<lang>.py with a class that subclasses LanguageEngine
  2. Set the `name` class attribute to the canonical language name
  3. Add a scaffold template in scaffolding/templates.py
  That's it - no registry edits needed.

Aliases (e.g. "js" -> "javascript") are declared on engine classes via an
optional `aliases` class attribute (list of strings).
"""

import importlib
import logging
import os
import pkgutil

from .base import LanguageEngine

log = logging.getLogger("cartograph")

_ENGINES: dict[str, LanguageEngine] = {}
_ALIASES: dict[str, str] = {}


def _discover_engines():
    """Scan this package for LanguageEngine subclasses and register them."""
    package_dir = os.path.dirname(__file__)
    for finder, module_name, is_pkg in pkgutil.iter_modules([package_dir]):
        if module_name in ("base", "registry", "__init__"):
            continue
        try:
            mod = importlib.import_module(f".{module_name}", package=__package__)
        except Exception as e:
            log.debug("Failed to import language module %s: %s", module_name, e)
            continue

        for attr_name in dir(mod):
            cls = getattr(mod, attr_name)
            if (
                isinstance(cls, type)
                and issubclass(cls, LanguageEngine)
                and cls is not LanguageEngine
                and hasattr(cls, "name")
                and cls.name != "base"
            ):
                engine = cls()
                _ENGINES[engine.name] = engine
                for alias in getattr(cls, "aliases", []):
                    _ALIASES[alias] = engine.name


_discover_engines()


def get_engine(language: str) -> LanguageEngine | None:
    """Return the engine for a language string, or None if unknown."""
    key = language.lower().strip()
    key = _ALIASES.get(key, key)
    return _ENGINES.get(key)


def supported_languages() -> list[str]:
    """Return languages with full validation support - derived from _ENGINES."""
    return sorted(name for name, engine in _ENGINES.items() if engine.supported)


def available_languages() -> set[str]:
    """Return languages whose toolchain is installed on this machine."""
    return {name for name, engine in _ENGINES.items()
            if engine.supported and engine.check_available()[0]}


def allowed_extensions() -> set[str]:
    """Return all file extensions used by registered language engines.
    Used by the cloud registry to validate widget zip contents — automatically
    includes any new language without cloud-side changes.

    Covers each engine's source extension plus the extensions of its
    engine-owned manifest files (manifest_patterns), so build files like
    Java's build.gradle are accepted by the cloud zip validator."""
    exts = {engine.file_ext for engine in _ENGINES.values() if engine.file_ext}
    for engine in _ENGINES.values():
        for pattern in engine.manifest_patterns:
            ext = os.path.splitext(pattern)[1].lstrip(".")
            if ext:
                exts.add(ext)
    return exts
