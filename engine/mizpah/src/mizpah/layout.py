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




def _slug(text: str) -> str:
    import re
    s = re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_')
    return s[:40] or 'brief'


def brief_map(project: Path) -> str:
    """The map a brief's work lands on: `b_<brief slug>`, a child of global, so a repository that runs its tenth
    brief keeps each brief's readings apart and global holds only what was promoted as durable. A project made by
    the earlier engine (`.terra/`) keeps everything on global, as it always did."""
    if dirname(project) == LEGACY_DIRNAME:
        return 'global'
    try:
        import json
        brief = json.loads((state(project)/'brief.json').read_text())
    except (OSError, ValueError):
        return 'global'
    return 'b_'+_slug(brief.get('title') or brief.get('id') or 'brief')


def map_root(project: Path) -> Path:
    """Where the current brief's unknowns, knowns and runs live on disk (probes are always under global's map)."""
    m = brief_map(project)
    base = state(project)/'map'
    return base if m == 'global' else base/'sessions'/m


def terra_env(project: Path) -> dict[str, str]:
    """The environment a Terra CLI (host side) needs: the state directory's name and the brief's map."""
    env = {'TERRA_DIRNAME': dirname(project)}
    m = brief_map(project)
    if m != 'global':
        env['TERRA_MAP'] = m
    return env


def bind(project: Path) -> None:
    """Point this process's Terra calls at the project's tree and brief map; call once per project."""
    for k, v in terra_env(project).items():
        os.environ[k] = v
    if 'TERRA_MAP' not in terra_env(project):
        os.environ.pop('TERRA_MAP', None)
