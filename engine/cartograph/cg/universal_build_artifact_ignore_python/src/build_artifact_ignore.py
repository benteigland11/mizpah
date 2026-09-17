"""Canonical build-artifact directory exclusion lists.

Centralizes the dirnames that should never be archived, copied, scanned,
or otherwise crossed when walking a project tree. Framework-aware.
"""

from __future__ import annotations

import os
from typing import Iterable


# Universal: VCS, OS junk, IDE droppings, tool caches that show up
# regardless of language.
_UNIVERSAL: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".DS_Store",
    "__MACOSX",
    "Thumbs.db",
    "desktop.ini",
    ".idea",
    ".vscode",
    ".cache",
    ".tmp",
})


# OS metadata the desktop shell scatters into trees behind the user's back.
# Unlike the artifact-dir sets these need file-level matching: macOS
# AppleDouble resource forks are named `._<original file>`, so no exact-name
# set can express them.
_OS_METADATA_NAMES: frozenset[str] = frozenset({
    ".DS_Store", "__MACOSX", "Thumbs.db", "desktop.ini",
})
_OS_METADATA_PREFIXES: tuple[str, ...] = ("._",)


def is_os_metadata(name: str) -> bool:
    """True if the basename of ``name`` is OS-scattered metadata.

    Covers macOS ``.DS_Store``, AppleDouble ``._*`` resource forks, and
    ``__MACOSX`` zip directories, plus Windows ``Thumbs.db`` and
    ``desktop.ini``. Accepts a bare basename or a path.
    """
    base = os.path.basename(name.rstrip("/\\"))
    if base in _OS_METADATA_NAMES:
        return True
    return base.startswith(_OS_METADATA_PREFIXES)


def os_metadata_glob_patterns() -> tuple[str, ...]:
    """The same set as glob patterns, for ``shutil.ignore_patterns``-style
    consumers that match names against fnmatch patterns.

    Note ``._*`` also matches a directory literally named ``._`` -
    acceptable, since that name only appears as AppleDouble spillage.
    """
    return (".DS_Store", "._*", "__MACOSX", "Thumbs.db", "desktop.ini")


# Per-language artifact dirs. Keys are short language tags; consumers
# pass whichever they're packaging. Unknown tags are ignored (no raise),
# so callers don't need to defend against typos.
_BY_LANGUAGE: dict[str, frozenset[str]] = {
    "python": frozenset({
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".tox", ".nox", ".coverage", "htmlcov", ".hypothesis",
        ".venv", "venv", "env",
        "build", "dist",
    }),
    "javascript": frozenset({
        "node_modules", "coverage", "dist", "build",
        ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache",
        ".yarn", ".pnpm-store", ".rush",
    }),
    "typescript": frozenset({
        "node_modules", "coverage", "dist", "build", "out-tsc",
        ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache",
    }),
    "angular": frozenset({
        "node_modules", "coverage", "dist", "out-tsc", ".angular",
    }),
    "php": frozenset({
        "vendor", "coverage", ".phpunit.cache", ".phpunit.result.cache",
    }),
    "rust": frozenset({"target"}),
    "java": frozenset({"target", "build", ".gradle"}),
    "csharp": frozenset({"bin", "obj", "TestResults"}),
    "kotlin": frozenset({"build", ".gradle"}),
    "go": frozenset({"vendor", "bin"}),
    "nim": frozenset({"nimcache", "htmldocs", "nimble.paths", "nimble.develop"}),
    "openscad": frozenset(),
    "systemverilog": frozenset({"work", "transcript"}),
    "ruby": frozenset({"vendor", ".bundle", "tmp"}),
    "elixir": frozenset({"_build", "deps", "cover"}),
    "swift": frozenset({".build", ".swiftpm", "Pods"}),
    "dart": frozenset({".dart_tool", "build"}),
    # Cartograph flutter widgets: lib/ is the validator-generated copy of
    # src/ and pubspec.lock is regenerated per validation - both artifacts.
    "flutter": frozenset({".dart_tool", "build", "coverage", "lib",
                          "pubspec.lock"}),
    "terraform": frozenset({".terraform"}),
    "lean": frozenset({".lake", "lake-manifest.json"}),
}


# Prefix patterns matched against directory or file basenames, with any
# leading dots stripped before comparison. Catches non-standard variants
# like `.nimcache_example` that exact-name matching misses.
_PREFIX_BY_LANGUAGE: dict[str, frozenset[str]] = {
    "nim": frozenset({"nimcache"}),
}


def default_excludes() -> frozenset[str]:
    """Return the universal exclusion set (VCS, IDE, OS metadata)."""
    return _UNIVERSAL


def excludes_for(
    language: str | None = None,
    languages: Iterable[str] = (),
) -> frozenset[str]:
    """Return the union of universal and language-specific exclusions.

    Pass ``language`` for a single ecosystem or ``languages`` for a
    polyglot project. Unknown tags contribute nothing rather than raising.
    """
    result = set(_UNIVERSAL)
    tags: list[str] = []
    if language is not None:
        tags.append(language)
    tags.extend(languages)
    for tag in tags:
        result.update(_BY_LANGUAGE.get(tag.lower(), frozenset()))
    return frozenset(result)


def prefix_excludes_for(
    language: str | None = None,
    languages: Iterable[str] = (),
) -> frozenset[str]:
    """Return prefix patterns for the given language(s).

    A name matches a prefix when, after stripping any leading dots, it
    starts with the prefix. Example: prefix ``nimcache`` matches
    ``nimcache``, ``.nimcache_example``, and ``nimcache_old``.
    """
    result: set[str] = set()
    tags: list[str] = []
    if language is not None:
        tags.append(language)
    tags.extend(languages)
    for tag in tags:
        result.update(_PREFIX_BY_LANGUAGE.get(tag.lower(), frozenset()))
    return frozenset(result)


def _matches_prefix(name: str, prefixes: Iterable[str]) -> bool:
    if not name:
        return False
    stripped = name.lstrip(".")
    return any(stripped.startswith(p) for p in prefixes)


def all_known_excludes() -> frozenset[str]:
    """Return the union across every known language. Catch-all when the
    consumer doesn't know which ecosystems live in a tree."""
    result = set(_UNIVERSAL)
    for entries in _BY_LANGUAGE.values():
        result.update(entries)
    return frozenset(result)


def should_skip(
    path: str,
    excludes: Iterable[str],
    prefixes: Iterable[str] = (),
) -> bool:
    """Return True if any path component matches an exclude entry.

    Designed for use inside ``os.walk`` loops or against a relative
    path inside a zip-build loop. ``prefixes`` is checked with leading
    dots stripped (so ``nimcache`` matches ``.nimcache_example``).
    OS metadata (``is_os_metadata``) is always skipped regardless of the
    exclude sets passed in.
    """
    exclude_set = set(excludes)
    prefix_tuple = tuple(prefixes)
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if not part:
            continue
        if part in exclude_set:
            return True
        if is_os_metadata(part):
            return True
        if prefix_tuple and _matches_prefix(part, prefix_tuple):
            return True
    return False


def filter_dirs(
    dirs: list[str],
    excludes: Iterable[str],
    prefixes: Iterable[str] = (),
) -> list[str]:
    """Return a new list with excluded entries removed. Convenience
    wrapper for the common ``dirs[:] = filter_dirs(dirs, excludes)``
    pattern inside ``os.walk``. ``prefixes`` is checked with leading
    dots stripped. OS metadata dirs (e.g. ``__MACOSX``) are always
    removed."""
    exclude_set = set(excludes)
    prefix_tuple = tuple(prefixes)
    return [
        d for d in dirs
        if d not in exclude_set
        and not is_os_metadata(d)
        and not (prefix_tuple and _matches_prefix(d, prefix_tuple))
    ]


def supported_languages() -> tuple[str, ...]:
    """Return the language tags this widget knows about."""
    return tuple(sorted(_BY_LANGUAGE.keys()))
