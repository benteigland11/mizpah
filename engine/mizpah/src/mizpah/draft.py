"""Drafts: the projects the Deputy is writing with the Administrator before either signs anything.

A draft is a gym — a Terra project directory under the gyms root, beside every other project that has no
repository of its own — whose brief is in `draft` status. There is no separate area for drafts (there was a
formulation root; a draft is a draft, 2026-09-20): the Deputy's `/work` is the gyms root, and what keeps it from
touching an issued project is the mount, not the location — every gym whose brief is issued is bound read-only
over its place in `/work`. Signing the brief (the app's authorize) makes the draft a project: furnished in place,
or moved into the person's repository with `mizpah init` run there; either way its brief is then issued by the
app's signature step. Discarding a draft is `rm -rf` of that one directory.

Runs on both sides: inside the Deputy's sandbox (`/work` is the gyms root) and on the host. Every command
prints one JSON object.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from . import init as init_module, layout

SLUG_RULE = 'a slug is lowercase letters, digits and hyphens, 3 to 40 characters'


def valid_slug(slug: str) -> bool:
    import re
    return bool(re.fullmatch(r'[a-z0-9][a-z0-9-]{1,38}[a-z0-9]', slug))


def draft_dir(slug: str) -> Path:
    if not valid_slug(slug):
        raise SystemExit(json.dumps(dict(status='error', error=SLUG_RULE)))
    return init_module.gyms_root()/slug


def terra_bin() -> str:
    """The Terra beside this interpreter (one environment, one Terra), unless `MIZPAH_TERRA` says otherwise."""
    beside = Path(sys.executable).parent/'terra'
    return os.environ.get('MIZPAH_TERRA') or (str(beside) if beside.exists() else shutil.which('terra') or 'terra')


def terra(project: Path, *args: str) -> dict[str, Any]:
    env = dict(os.environ, TERRA_DIRNAME=layout.STATE_DIRNAME)
    proc = subprocess.run([terra_bin(), *args], cwd=project, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit(json.dumps(dict(status='error', error='terra '+' '.join(args)+' failed: '+(proc.stderr or proc.stdout)[-400:].strip())))
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return dict(output=proc.stdout.strip())


def brief_of(project: Path) -> dict[str, Any]:
    try:
        return json.loads((project/layout.STATE_DIRNAME/'brief.json').read_text())
    except (OSError, ValueError):
        return {}


def is_draft(project: Path) -> bool:
    """A gym the Deputy may write: its brief exists and is not issued."""
    return (project/layout.STATE_DIRNAME/'brief.json').exists() and brief_of(project).get('status') in ('', 'draft')


def summary(project: Path) -> dict[str, Any]:
    brief = brief_of(project)
    return dict(slug=project.name, path=str(project), title=brief.get('title', ''), brief_status=brief.get('status', ''),
                needs=len(brief.get('needs') or []), deliverables=len(brief.get('deliverables') or []),
                budget_points=brief.get('budget_points'), environment=brief.get('environment') or '')


def environment_exists(name: str) -> bool:
    from . import bases
    try:
        bases.load(name)
    except (FileNotFoundError, ValueError):
        return False
    return True


def environments() -> list[dict[str, Any]]:
    """The saved gym environments a brief may name: name and note, the default first and marked."""
    from . import bases
    default = bases.default_name()   # makes `bare` when nothing else is there
    out = [dict(name=b['name'], note=b['note'], default=b['name'] == default) for b in bases.list_bases()]
    return sorted(out, key=lambda e: (not e['default'], e['name']))


BARE = 'none'

# Where an environment gym may fetch from: package indexes and the release hosts the usual tools ship on. The
# gym's config.json carries the list, so a person can add a host for one build without touching the engine.
BUILD_DOMAINS = ('pypi.org', 'files.pythonhosted.org', 'github.com', 'objects.githubusercontent.com',
                 'codeload.github.com', 'release-assets.githubusercontent.com', 'cdn.playwright.dev',
                 'playwright.download.prss.microsoft.com', 'nodejs.org', 'lilypond.org', 'gitlab.com')


def new(slug: str, title: str, mission: str, environment: str = '', builds: str = '') -> dict[str, Any]:
    """A gym is a training ground, and the environment it is set up with is the point of making one: the choice is
    made here, out loud — a saved environment by name, or `none` for a bare gym (Python and a shell) — never left
    to default."""
    project = draft_dir(slug)
    if project.exists():
        raise SystemExit(json.dumps(dict(status='error', error='a gym named '+slug+' exists; discard it or pick another slug')))
    from . import bases
    # A gym is always set up in an environment: the one named, else the default (`bare` until another is chosen).
    environment = environment.strip()
    environment = bases.ensure_bare()['name'] if environment == BARE else environment or bases.default_name()
    if not environment_exists(environment):
        raise SystemExit(json.dumps(dict(status='error', error='no saved environment named '+repr(environment),
                                         environments=[e['name'] for e in environments()])))
    if builds:
        # An environment gym: bare, may install and reach the package hosts, and is adopted as base `builds` on green.
        from . import bases
        try:
            bases._valid(builds)
        except ValueError as error:
            raise SystemExit(json.dumps(dict(status='error', error=str(error))))
        if environment != bases.BARE:
            raise SystemExit(json.dumps(dict(status='error', error='an environment gym is set up bare: it builds '
                                             +repr(builds)+', it does not run in another environment')))
        if environment_exists(builds):
            raise SystemExit(json.dumps(dict(status='error', error='a saved environment named '+repr(builds)
                                             +' exists already; pick another name or adopt --replace by hand')))
    init_module.new_gym(title, name=slug)
    terra(project, 'init')
    # A mission on `brief init` issues the brief (status active); a draft is initialised bare and told its mission.
    terra(project, 'brief', 'init', '--title', title)
    terra(project, 'brief', 'set', '--mission', mission)
    terra(project, 'brief', 'set', '--environment', environment)
    terra(project, 'route', 'init')
    if builds:
        config = init_module.default_config()
        config['builds_base'] = builds
        config['sandbox']['network'] = dict(allowed_domains=list(BUILD_DOMAINS))
        (project/layout.STATE_DIRNAME/'config.json').write_text(json.dumps(config, indent=1)+'\n')
    return dict(status='ok', **summary(project), **(dict(builds=builds, allowed_domains=list(BUILD_DOMAINS)) if builds else {}),
                next='add needs and deliverables one at a time with `terra brief set --need "..."` / `--deliverable "..."` '
                     'from inside '+str(project)+' (TERRA_DIRNAME=.mizpah), set --budget-points, name the environment '
                     'it runs in with --environment <name> (`mizpah.draft environments`), then show it')


def discard(slug: str) -> dict[str, Any]:
    project = draft_dir(slug)
    if not project.is_dir():
        raise SystemExit(json.dumps(dict(status='error', error='no draft named '+slug)))
    if not is_draft(project):
        raise SystemExit(json.dumps(dict(status='error', error='the brief of '+slug+' is issued; an issued brief is not yours to discard')))
    shutil.rmtree(project)
    return dict(status='ok', discarded=slug)


def show(slug: str) -> dict[str, Any]:
    project = draft_dir(slug)
    if not project.is_dir():
        raise SystemExit(json.dumps(dict(status='error', error='no draft named '+slug+'; drafts: '+', '.join(p.name for p in listing()))))
    return dict(status='ok', showing=dict(draft=slug), **summary(project))


def authorize(project: Path, repo: Path | None = None, engine_config: Path | None = None) -> dict[str, Any]:
    """The Administrator signed: the draft becomes a project. In place (a gym), or moved into the person's
    repository (its top, holding no Mizpah tree yet). A file the repository already has is left alone and named.
    The brief is still in draft status — issuing it (status active) is the app's signature step, right after
    this, so a project that fails to furnish never shows as issued. Furnishing is idempotent: a project that is
    already one is registered again and nothing else changes. With `engine_config`, the crew is locked in too
    (`init.pin_crew`) and returned, for the signature to record."""
    project = Path(project).resolve()
    if not (project/layout.STATE_DIRNAME/'brief.json').exists():
        raise SystemExit(json.dumps(dict(status='error', error=str(project)+' holds no brief')))
    brief = brief_of(project)
    environment = str(brief.get('environment') or '').strip()
    if environment and not environment_exists(environment):
        # Signed against an environment the host does not have: the run would start bare and every task would
        # fail on the first import. Refuse here, where the person can still fix the brief.
        raise SystemExit(json.dumps(dict(status='error', error='the brief names environment '+repr(environment)
                                         +' and there is no saved environment by that name',
                                         environments=[e['name'] for e in environments()])))
    if repo is None:
        target = project
    else:
        if brief.get('status') not in ('', 'draft'):
            raise SystemExit(json.dumps(dict(status='error', error='the brief of '+project.name+' is already issued; it stays where it is')))
        target = Path(repo).resolve()
        top = init_module.git_toplevel(target)
        if top is None or top != target:
            raise SystemExit(json.dumps(dict(status='error', error=str(target)+' is not the top of a git repository')))
        if (target/layout.STATE_DIRNAME).exists() or (target/layout.LEGACY_DIRNAME).exists():
            raise SystemExit(json.dumps(dict(status='error', error=str(target)+' already holds a Mizpah project')))
    kept = []
    if target != project:
        for entry in sorted(project.iterdir()):
            if entry.name in ('.git', '.tool-output', '.session-history', '.home'):
                continue
            if (target/entry.name).exists():
                kept.append(entry.name)
                continue
            shutil.move(str(entry), str(target/entry.name))
    if not (target/layout.STATE_DIRNAME/'route.json').exists():
        terra(target, 'route', 'init')
    furnished = init_module.furnish(target, brief.get('title') or project.name)
    if environment:
        init_module.set_base(target, environment)   # the gym says what it was set up with, beside the brief
    crew = init_module.pin_crew(target, engine_config) if engine_config else {}
    if target != project:
        shutil.rmtree(project)
    return dict(status='ok', authorized=project.name, gym=repo is None, kept=kept, crew=crew, **furnished)


def listing() -> list[Path]:
    root = init_module.gyms_root()
    return sorted(p for p in root.iterdir() if p.is_dir() and is_draft(p)) if root.is_dir() else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='mizpah.draft', description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='verb', required=True)
    p = sub.add_parser('new', help='Start a draft: a gym with an empty brief in draft status')
    p.add_argument('slug')
    p.add_argument('--title', required=True)
    p.add_argument('--mission', required=True)
    p.add_argument('--environment', default='', help='the saved environment this gym is set up in (see `environments`); `none` is the bare one; omitted, the default')
    p.add_argument('--builds', default='', help='an environment gym: bare, may install and reach package hosts, adopted as this base on green')
    p = sub.add_parser('discard', help='Remove a draft and everything in it')
    p.add_argument('slug')
    p = sub.add_parser('show', help='Pull a draft up on the desk for the Administrator to look at')
    p.add_argument('slug')
    p = sub.add_parser('authorize', help='Signed: make the draft a project, in place or in --repo <top of a git repository>')
    p.add_argument('project', help='the draft\'s slug under the gyms root, or a project path')
    p.add_argument('--repo', type=Path)
    p.add_argument('--config', type=Path, default=None, help='the engine config: lock the crew in from its harness config')
    sub.add_parser('list', help='Every draft: a gym whose brief is not issued')
    sub.add_parser('environments', help='The saved gym environments a brief may name')
    args = parser.parse_args(argv)
    if args.verb == 'new':
        out = new(args.slug, args.title, args.mission, args.environment, args.builds)
    elif args.verb == 'environments':
        out = environments()
    elif args.verb == 'discard':
        out = discard(args.slug)
    elif args.verb == 'show':
        out = show(args.slug)
    elif args.verb == 'authorize':
        project = Path(args.project) if os.sep in args.project or Path(args.project).is_absolute() else draft_dir(args.project)
        out = authorize(project, args.repo, args.config)
    else:
        out = dict(status='ok', drafts=[summary(p) for p in listing()])
    print(json.dumps(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
