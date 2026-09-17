"""Load probe modules with shared map lib on sys.path."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

from .paths import map_lib_root, probe_dir


@contextmanager
def probe_sys_path(project_root: Path, probe_package: Path | None = None) -> Iterator[None]:
    """Push `.terra/map/lib` (and optional probe dir) onto sys.path for imports."""
    added: list[str] = []
    lib = map_lib_root(project_root)
    candidates = [lib]
    if probe_package is not None:
        candidates.append(probe_package.resolve())
    for p in candidates:
        s = str(p)
        if p.is_dir() and s not in sys.path:
            sys.path.insert(0, s)
            added.append(s)
    try:
        yield
    finally:
        for s in added:
            try:
                sys.path.remove(s)
            except ValueError:
                pass


def load_probe_module(
    project_root: Path,
    script_path: Path,
    *,
    module_name: str,
) -> tuple[ModuleType | None, str | None]:
    """Import probe script with map lib available.

    Compiles straight from source, bypassing the pyc cache: bytecode
    validation is mtime+size, so a probe.py edited twice within one second
    (same length) would silently run STALE code — instruments must always
    execute what is on disk.
    """
    script_path = script_path.resolve()
    try:
        with probe_sys_path(project_root, script_path.parent):
            source = script_path.read_text(encoding="utf-8")
            code = compile(source, str(script_path), "exec")
            mod = ModuleType(module_name)
            mod.__file__ = str(script_path)
            sys.modules[module_name] = mod
            try:
                exec(code, mod.__dict__)  # noqa: S102 — probe scripts are the product
            finally:
                sys.modules.pop(module_name, None)
            return mod, None
    except Exception as e:  # noqa: BLE001
        return None, f"probe script failed to import: {type(e).__name__}: {e}"


def resolve_probe_package(project_root: Path, probe_id: str) -> Path:
    pdir = probe_dir(project_root, probe_id)
    if not pdir.is_dir():
        raise FileNotFoundError(f"probe not found: {probe_id} ({pdir})")
    return pdir
