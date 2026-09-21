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

try:  # the workspace's path widget; the same rules the app resolves with
    from cg.infra_app_paths_python.src.app_paths import resolve_app_paths
except ImportError:  # pragma: no cover — a bare checkout without cg on the path
    resolve_app_paths = None

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


def crew_label(spec: dict[str, Any]) -> dict[str, Any]:
    """A role spec as the person reads it: provider (the subscription's name, or the transport for a local
    server), model, effort."""
    generation = spec.get('generation') or {}
    return dict(provider=spec.get('subscription') or spec.get('provider'), model=generation.get('model'),
                effort=generation.get('reasoning_effort'))


def pin_crew(project: Path, engine_config: Path, roles: tuple[str, ...] = ('worker', 'controller')) -> dict[str, dict[str, Any]]:
    """Lock the crew in: every role the project has no choice of its own for gets the user's current harness
    spec copied into `.mizpah/config.json` (`models.<role>`), so the run uses what the brief was signed on
    whatever the defaults become later. A choice the task already made stays. Returns the crew as labels."""
    import copy
    engine = json.loads(Path(engine_config).read_text())
    harness = json.loads((Path(engine_config).parent/engine['harness_config']).read_text())
    project = Path(project).resolve()
    pc = project_config(project) or default_config()
    models = pc.setdefault('models', {})
    changed = False
    for role in roles:
        if role not in models and isinstance(harness.get(role), dict):
            models[role] = copy.deepcopy(harness[role])
            changed = True
    if changed:
        path = layout.state(project)/'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pc, indent=1)+'\n')
    return {role: crew_label(models[role]) for role in roles if isinstance(models.get(role), dict)}


def gyms_root() -> Path:
    """Where projects that belong to no repository live: training gyms, drills, a brief tried on scratch, and
    the drafts the Deputy writes. `MIZPAH_GYMS` when set (the Deputy's sandbox sets it to /work)."""
    env = os.environ.get('MIZPAH_GYMS')
    if env:
        return Path(env)
    if resolve_app_paths is not None:
        base = resolve_app_paths('mizpah').data_dir/'gyms'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local'/'share')/'mizpah'/'gyms'
    base.mkdir(parents=True, exist_ok=True)
    return base


def projects_registry() -> Path:
    """Every project this machine has initialised, one line each: how a front end lists a task before its first
    run has written a session anywhere. Beside the runs registry, in the per-user state directory."""
    if resolve_app_paths is not None:
        return resolve_app_paths('mizpah').state_dir/'projects.jsonl'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local'/'state')/'mizpah'/'projects.jsonl'


def register_project(project: Path, title: str, *, gym: bool) -> None:
    try:
        path = projects_registry()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as handle:
            handle.write(json.dumps(dict(project=str(project), title=title, gym=gym,
                                         created_at=__import__('time').time()))+'\n')
    except OSError:
        pass  # a registry that cannot be written never stops an init


def new_gym(title: str, name: str | None = None) -> Path:
    """A fresh git-initialised folder under the gyms root, named for the brief (with a stamp) or as given. The
    project is ordinary from here: same `.mizpah/`, same loop, same record; it just has no code of the person's
    around it."""
    slug = layout._slug(title)
    stamp = __import__('time').strftime('%Y%m%dT%H%M%SZ', __import__('time').gmtime())
    folder = gyms_root()/(name or slug+'-'+stamp)
    folder.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q'], cwd=folder, check=True)
    (folder/'README.md').write_text('# '+title+'\n\nA Mizpah gym: a project with no repository of its own.\n')
    subprocess.run(['git', 'add', '-A'], cwd=folder, check=True)
    subprocess.run(['git', '-c', 'user.name=mizpah', '-c', 'user.email=mizpah@local', 'commit', '-qm', 'gym: '+title], cwd=folder, check=True)
    return folder


def set_base(project: Path, name: str | None) -> dict[str, Any]:
    """Declare the environment base a project runs on (`base: <name>` in its config); None clears it."""
    from . import bases
    if name is not None:
        bases.load(name)   # must exist
    path = layout.state(project)/'config.json'
    pc = project_config(project) or default_config()
    if name is None:
        pc.pop('base', None)
    else:
        pc['base'] = name
    path.write_text(json.dumps(pc, indent=1)+'\n')
    return pc


def init(repo: Path, *, title: str, mission: str, terra: str, require_git: bool = True, base: str | None = None) -> dict[str, Any]:
    repo = Path(repo).resolve()
    if base is not None:
        from . import bases
        bases.load(base)   # refuse before anything is written
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
    out = furnish(repo, title)
    if base is not None:
        set_base(repo, base)
        out['base'] = base
    return out


def furnish(repo: Path, title: str) -> dict[str, Any]:
    """What makes a Terra tree a Mizpah project: the per-project config, the sessions directory, the gitignore
    line and the registry entry. `init` does it after creating the brief; `draft authorize` after moving one in."""
    repo = Path(repo).resolve()
    state = repo/layout.STATE_DIRNAME
    if not (state/'config.json').exists():
        (state/'config.json').write_text(json.dumps(default_config(), indent=1)+'\n')
    (state/layout.SESSIONS_DIRNAME).mkdir(exist_ok=True)
    ignore = repo/'.gitignore'
    lines = ignore.read_text().splitlines() if ignore.exists() else []
    if GITIGNORE_LINE not in lines:
        with ignore.open('a') as handle:
            handle.write(('' if not lines or lines[-1] == '' else '\n')+GITIGNORE_LINE+'\n')
    register_project(repo, title, gym=repo.is_relative_to(gyms_root()) if hasattr(repo, 'is_relative_to') else False)
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
    parser.add_argument('--base', default=None, help='an environment base (`mizpah.bases list`) bound read-only into every task')
    args = parser.parse_args(argv)
    repo = new_gym(args.title) if args.gym else Path(args.repo)
    print(json.dumps(dict(init(repo, title=args.title, mission=args.mission, terra=args.terra, require_git=not args.no_git,
                               base=args.base), gym=bool(args.gym)), indent=1))


if __name__ == '__main__':
    main()


def project_environment(project: Path) -> str:
    """The gym environment the brief names (`environment`), or '' — the brief is the authority; `base` in the
    project config is the older place and only counts when the brief names nothing. Three practice gyms
    started bare (no mido, no synth) because the app's signature path never set a base: the brief now says."""
    try:
        brief = json.loads((project/layout.STATE_DIRNAME/'brief.json').read_text())
    except (OSError, ValueError):
        return ''
    return str(brief.get('environment') or '').strip()


def apply_project_config(config: dict[str, Any], project: Path) -> dict[str, Any]:
    """Layer the project's `.mizpah/config.json` over the loaded user config: the sandbox keys a project may
    own (workspace mode, cache dirs, network, extra binds), and — when the task chose its own — the model
    behind each seat (`models.worker` / `models.controller`, written by `mizpah-provider use --project`).
    Policies stay the user's."""
    pc = project_config(project)
    layer = pc.get('sandbox') or {}
    allowed = {'workspace', 'cache_dirs', 'network', 'share_network', 'read_only_binds', 'services'}
    picked = {k: v for k, v in layer.items() if k in allowed}
    if picked:
        config['mizpah']['sandbox'] = dict(config['mizpah'].get('sandbox') or {}, **picked)
    environment = project_environment(project) or pc.get('base')
    if environment:
        from . import bases
        bases.apply(config, str(environment))   # the environment the gym runs in: bound read-only, env set
    for role in ('worker', 'controller'):
        spec = (pc.get('models') or {}).get(role)
        if isinstance(spec, dict) and spec:
            base = dict(config.get(role) or {})
            for key, value in spec.items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    base[key] = dict(base[key], **value)   # endpoint, generation: merge, do not replace
                else:
                    base[key] = value
            config[role] = base
    return config
