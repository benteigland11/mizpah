"""Where Mizpah's state lives inside a project.

Shipped layout: one dot-directory in the person's repository — `.mizpah/` — holding Terra's brief, route
and map, Mizpah's per-project config, and `sessions/` (loops, journals, tasks; gitignored). Everything
else in the repository is theirs, and is the workspace. Terra is told the directory's name through
TERRA_DIRNAME so every CLI call, probe and library read agrees.

A project made by the earlier engine has `.terra/` and no `.mizpah/`; it keeps working as it is.
"""
from __future__ import annotations

import os
from pathlib import Path

STATE_DIRNAME = '.mizpah'
LEGACY_DIRNAME = '.terra'
SESSIONS_DIRNAME = 'sessions'


def dirname(project: Path) -> str:
    """`.mizpah`, or `.terra` for a project that only has the old tree."""
    project = Path(project)
    if not (project/STATE_DIRNAME).is_dir() and (project/LEGACY_DIRNAME).is_dir():
        return LEGACY_DIRNAME
    return STATE_DIRNAME


def state(project: Path) -> Path:
    return Path(project)/dirname(project)


def sessions(project: Path) -> Path:
    return state(project)/SESSIONS_DIRNAME


def terra_env(project: Path) -> dict[str, str]:
    """The environment a Terra CLI (host or sandbox) needs to find this project's tree."""
    return {'TERRA_DIRNAME': dirname(project)}


def bind(project: Path) -> None:
    """Point this process's Terra calls at the project's tree; call once per project before any terra()."""
    os.environ['TERRA_DIRNAME'] = dirname(project)
