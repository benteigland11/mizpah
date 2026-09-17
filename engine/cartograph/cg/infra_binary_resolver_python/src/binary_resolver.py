"""
Binary Resolver - configurable external-binary lookup for Python CLIs.

Tools that shell out to external binaries (compilers, package managers,
interpreters) usually assume the binary is on PATH. That assumption
breaks for users who install via zip archives, bundled stacks, or
custom prefixes - their binary is perfectly functional, but PATH
doesn't know about it.

This widget gives a CLI a uniform way to accept an optional override
path for any binary it depends on, validate that the override actually
points at something runnable, and fall back to PATH when no override
is configured. Each resolved binary carries a `source` tag so the CLI
can show the user where its binaries came from (useful in `doctor`
output).

Usage:
    from binary_resolver import resolve, ResolveError

    result = resolve("nim")                        # PATH lookup
    result = resolve("nim", override="/opt/nim/bin/nim")  # explicit

    print(result.path, result.source)   # "/opt/nim/bin/nim", "override"
"""

import os
import shutil
from dataclasses import dataclass
from typing import Literal, Optional


Source = Literal["override", "path"]


@dataclass(frozen=True)
class ResolvedBinary:
    """Result of a successful lookup.

    Attributes:
        name: The logical name that was looked up (e.g. "nim").
        path: Absolute path to the executable.
        source: Where the path came from - "override" if the caller
            supplied it, "path" if it was discovered on PATH.
    """
    name: str
    path: str
    source: Source


class ResolveError(RuntimeError):
    """Raised when a binary cannot be resolved.

    The message is meant to be surfaced to end users verbatim - it
    names the binary, explains what was tried, and suggests the
    config key they would set to provide an override.
    """


def _is_executable_file(candidate: str) -> bool:
    """True if `candidate` is a file the current user can execute."""
    return os.path.isfile(candidate) and os.access(candidate, os.X_OK)


def _which(name: str) -> Optional[str]:
    """PATH lookup with a Windows `.cmd` fallback.

    `shutil.which` consults PATHEXT on Windows, which normally covers
    `.exe`, `.bat`, and `.cmd`. Some shells (notably Git Bash) start
    with a trimmed PATHEXT, so tools like `npx` - which ship as
    `npx.cmd` - are invisible to a bare `which("npx")`. Trying the
    `.cmd` name explicitly closes that gap without affecting POSIX.
    """
    found = shutil.which(name)
    if found:
        return found
    if not name.endswith(".cmd"):
        found = shutil.which(name + ".cmd")
        if found:
            return found
    return None


def resolve(name: str, override: Optional[str] = None,
            override_key: Optional[str] = None) -> ResolvedBinary:
    """Resolve a logical binary name to an absolute executable path.

    Args:
        name: The binary's logical name (e.g. "nim", "iverilog").
        override: An explicit path supplied by configuration. When
            provided, it must point at an existing executable file -
            a missing or non-executable override raises ResolveError
            instead of silently falling through to PATH. This prevents
            silent config drift where a typo or uninstalled binary
            makes the caller think their override is active when PATH
            is actually being used.
        override_key: Optional config key name (e.g. "paths.nim") used
            only to make error messages more actionable. The resolver
            itself does not read any config.

    Returns:
        A ResolvedBinary with the absolute path and the source it was
        resolved from ("override" or "path").

    Raises:
        ResolveError: If an override is given but invalid, or if no
            override is given and the binary is not found on PATH.
    """
    if not name:
        raise ResolveError("resolve() requires a non-empty binary name")

    if override is not None:
        if not os.path.isabs(override):
            override = os.path.abspath(override)
        if not os.path.exists(override):
            raise ResolveError(
                f"Configured override for '{name}' does not exist: "
                f"{override}"
                + (f" (from {override_key})" if override_key else "")
            )
        if not _is_executable_file(override):
            raise ResolveError(
                f"Configured override for '{name}' is not an executable "
                f"file: {override}"
                + (f" (from {override_key})" if override_key else "")
            )
        return ResolvedBinary(name=name, path=override, source="override")

    found = _which(name)
    if found:
        return ResolvedBinary(name=name, path=found, source="path")

    hint = (f" Set {override_key} to the absolute path of the "
            f"executable." if override_key else "")
    raise ResolveError(
        f"'{name}' not found on PATH.{hint}"
    )
