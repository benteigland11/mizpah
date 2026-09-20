"""`python -m mizpah.init <repo> --title ... --mission ...`: make a repository a Mizpah project.

Writes one dot-directory, `.mizpah/`, inside the repository: Terra's brief, route and map (the team's record,
versioned with the code), a per-project `config.json` for what the sandbox needs to know about this tree,
and `sessions/` for loops and their journals (gitignored — working state, not record). Refuses outside a
git repository: the worker edits the real tree in bind mode, and git is what makes a half-finished task
recoverable.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from . import layout

# Directories that hold toolchains and builds rather than the work: bound read-only in the sandbox, never
# snapshotted. A project's config.json may add to or replace them.
DEFAULT_CACHE_DIRS = ('.venv', 'node_modules', 'build', 'dist', 'target', '.dart_tool', '.gradle', '.cache', '__pycache__')

GITIGNORE_LINE = layout.STATE_DIRNAME+'/'+layout.SESSIONS_DIRNAME+'/'


def git_toplevel(path: Path) -> Path | None:
    try:
        out = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=path, capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return Path(out) if out else None


def default_config(cache_dirs: tuple[str, ...] = DEFAULT_CACHE_DIRS) -> dict[str, Any]:
    return dict(
        schema_version=1,
        sandbox=dict(workspace='bind', cache_dirs=list(cache_dirs)),
        notes='Per-project settings the loop layers over the user config: which directories are caches (bound '
              'read-only, never snapshotted), the workspace mode, network policy. Providers and models live in '
              'the user config, never here.',
    )


def gyms_root() -> Path:
    """Where projects that belong to no repository live: training gyms, drills, a brief tried on scratch."""
    base = Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local'/'share')/'mizpah'/'gyms'
    base.mkdir(parents=True, exist_ok=True)
    return base


def new_gym(title: str) -> Path:
    """A fresh git-initialised folder under the gyms root, named for the brief. The project is ordinary from
    here: same `.mizpah/`, same loop, same record; it just has no code of the person's around it."""
    slug = layout._slug(title)
    stamp = __import__('time').strftime('%Y%m%dT%H%M%SZ', __import__('time').gmtime())
    folder = gyms_root()/(slug+'-'+stamp)
    folder.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q'], cwd=folder, check=True)
    (folder/'README.md').write_text('# '+title+'\n\nA Mizpah gym: a project with no repository of its own.\n')
    subprocess.run(['git', 'add', '-A'], cwd=folder, check=True)
    subprocess.run(['git', '-c', 'user.name=mizpah', '-c', 'user.email=mizpah@local', 'commit', '-qm', 'gym: '+title], cwd=folder, check=True)
    return folder


def init(repo: Path, *, title: str, mission: str, terra: str, require_git: bool = True) -> dict[str, Any]:
    repo = Path(repo).resolve()
    top = git_toplevel(repo)
    if require_git and (top is None or top != repo):
        raise SystemExit(str(repo)+' is not the top of a git repository; Mizpah edits the real tree and git is its safety net '
                         '(run `git init` first, or --no-git to proceed anyway)')
    state = repo/layout.STATE_DIRNAME
    if (state/'brief.json').exists():
        raise SystemExit(str(state)+' already holds a brief')
    if (repo/layout.LEGACY_DIRNAME).exists():
        raise SystemExit(str(repo)+' has a .terra/ tree already; this engine reads it as is, no init needed')
    env = dict(os.environ, TERRA_DIRNAME=layout.STATE_DIRNAME)

    def run(*args: str) -> None:
        proc = subprocess.run([terra, *args], cwd=repo, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise SystemExit('terra '+' '.join(args)+' failed: '+(proc.stderr or proc.stdout)[-400:])
    run('init')
    run('brief', 'init', '--title', title, '--mission', mission)
    run('route', 'init')
    (state/'config.json').write_text(json.dumps(default_config(), indent=1)+'\n')
    (state/layout.SESSIONS_DIRNAME).mkdir(exist_ok=True)
    ignore = repo/'.gitignore'
    lines = ignore.read_text().splitlines() if ignore.exists() else []
    if GITIGNORE_LINE not in lines:
        with ignore.open('a') as handle:
            handle.write(('' if not lines or lines[-1] == '' else '\n')+GITIGNORE_LINE+'\n')
    return dict(project=str(repo), state=str(state), brief=str(state/'brief.json'), sessions=str(state/layout.SESSIONS_DIRNAME),
                gitignore=GITIGNORE_LINE)


def project_config(project: Path) -> dict[str, Any]:
    """The per-project layer, `{}` when the project has none."""
    path = layout.state(project)/'config.json'
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('repo', nargs='?', default='.')
    parser.add_argument('--gym', action='store_true', help='house the project under the gyms root instead of a repository of yours')
    parser.add_argument('--title', required=True)
    parser.add_argument('--mission', required=True)
    parser.add_argument('--terra', default=str(Path(sys.executable).parent/'terra'), help='the terra CLI (default: beside this python)')
    parser.add_argument('--no-git', action='store_true', help='allow a directory that is not a git repository')
    args = parser.parse_args(argv)
    repo = new_gym(args.title) if args.gym else Path(args.repo)
    print(json.dumps(dict(init(repo, title=args.title, mission=args.mission, terra=args.terra, require_git=not args.no_git),
                          gym=bool(args.gym)), indent=1))


if __name__ == '__main__':
    main()


def apply_project_config(config: dict[str, Any], project: Path) -> dict[str, Any]:
    """Layer the project's `.mizpah/config.json` over the loaded user config: only the sandbox keys a project
    may own (workspace mode, cache dirs, network, extra binds). Providers, models and policies stay the user's."""
    layer = project_config(project).get('sandbox') or {}
    allowed = {'workspace', 'cache_dirs', 'network', 'share_network', 'read_only_binds', 'services'}
    picked = {k: v for k, v in layer.items() if k in allowed}
    if picked:
        config['mizpah']['sandbox'] = dict(config['mizpah'].get('sandbox') or {}, **picked)
    return config
