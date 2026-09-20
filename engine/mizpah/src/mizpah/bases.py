"""Bases: an environment several gyms share without copying it.

A gym is a folder and a brief; it has no environment beyond Python and a shell. Some work needs more — a
synth and a soundfont, a compiler, a toolchain — and that is environment, not work: it is provided the way
Python is provided, and the brief's enablers say it is there. A base is a directory under the data dir
(`bases/<name>/`) holding that environment once; a gym that declares `base: <name>` gets it bound read-only
into its sandbox at the base's own host path (so a venv made there keeps its shebangs), with the base's
environment variables set and `MIZPAH_BASE` pointing at it. Nothing the worker writes lands in the base:
files accumulate in the gym, the 200 MB exists once.

`base.json` in the base directory:

    {"name": "orchestra", "note": "fluidsynth, LilyPond, FluidR3 GM soundfont, venv with mido and music21",
     "env": {"SOUNDFONT": "$BASE/soundfonts/FluidR3_GM.sf2"}}

`$BASE` in a value is the base's path. A `venv/bin` under the base goes on PATH ahead of the sandbox's own.
The note is what the worker is told, verbatim, as an enabler of every task in the gym.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from cg.infra_app_paths_python.src.app_paths import resolve_app_paths
except ImportError:   # pragma: no cover
    resolve_app_paths = None


def bases_root() -> Path:
    if resolve_app_paths is not None:
        root = resolve_app_paths('mizpah').data_dir/'bases'
    else:
        root = Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local'/'share')/'mizpah'/'bases'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _valid(name: str) -> str:
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', name or ''):
        raise ValueError('a base name is lowercase letters, digits, - and _: '+repr(name))
    return name


def path_of(name: str) -> Path:
    return bases_root()/_valid(name)


def create(name: str, *, note: str = '') -> dict[str, Any]:
    """An empty base: the directory and its base.json. Fill it by hand or by a brief whose deliverable is the
    environment (the one gym that may write into it)."""
    folder = path_of(name)
    if (folder/'base.json').exists():
        raise FileExistsError('base exists: '+str(folder))
    folder.mkdir(parents=True, exist_ok=True)
    record = dict(name=name, note=note, env={})
    (folder/'base.json').write_text(json.dumps(record, indent=1)+'\n')
    return dict(record, path=str(folder))


def load(name: str) -> dict[str, Any]:
    folder = path_of(name)
    try:
        record = json.loads((folder/'base.json').read_text())
    except OSError as error:
        raise FileNotFoundError('no base named '+repr(name)+' under '+str(bases_root())) from error
    env = record.get('env') or {}
    if not isinstance(env, dict) or any(not isinstance(v, str) for v in env.values()):
        raise ValueError('base.json env must map names to strings: '+str(folder))
    return dict(name=name, note=str(record.get('note') or ''), env=dict(env), path=str(folder))


def list_bases() -> list[dict[str, Any]]:
    out = []
    for folder in sorted(bases_root().iterdir()):
        if (folder/'base.json').exists():
            try:
                out.append(load(folder.name))
            except (ValueError, FileNotFoundError):
                continue
    return out


def apply(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Bind the base into the sandbox config: read-only at its own path, its env with `$BASE` filled in,
    `venv/bin` first on PATH when the base has one, and `MIZPAH_BASE` for the enabler text to point at."""
    base = load(name)
    root = base['path']
    sandbox = config['mizpah'].setdefault('sandbox', {})
    binds = list(sandbox.get('read_only_binds') or ())
    if root not in binds:
        binds.append(root)
    sandbox['read_only_binds'] = binds
    env = dict(sandbox.get('environment') or {})
    venv = Path(root)/'venv'/'bin'
    if venv.is_dir():
        env['PATH'] = ':'.join(p for p in [str(venv), env.get('PATH', '')] if p)
        env['VIRTUAL_ENV'] = str(Path(root)/'venv')
    for key, value in base['env'].items():
        env[key] = value.replace('$BASE', root)
    env['MIZPAH_BASE'] = root
    sandbox['environment'] = env
    config['mizpah']['base'] = base
    return config


def enabler_text(config: dict[str, Any]) -> str:
    """What the worker is told about the base, as part of its assignment; empty when the project has none."""
    base = (config.get('mizpah') or {}).get('base')
    if not base:
        return ''
    lines = ['The environment base "'+base['name']+'" is mounted read-only at '+base['path']+' ($MIZPAH_BASE).']
    if base['note']:
        lines.append('It provides: '+base['note'])
    if Path(base['path'], 'venv', 'bin').is_dir():
        lines.append('Its venv is first on PATH: `python` and `pip` are the base\'s; install nothing there, it is read-only. '
                     'What you need beyond it goes in the project (a venv under the project, `pip install --target`).')
    if base['env']:
        lines.append('Environment: '+', '.join(k+'='+v.replace('$BASE', base['path']) for k, v in sorted(base['env'].items())))
    return '\n'.join(lines)+'\n'


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('create', help='an empty base directory with its base.json')
    c.add_argument('name')
    c.add_argument('--note', default='', help='what the base provides, in one line: the worker reads it')
    sub.add_parser('list', help='every base and what it provides')
    s = sub.add_parser('show', help='one base, resolved')
    s.add_argument('name')
    args = parser.parse_args(argv)
    if args.command == 'create':
        print(json.dumps(create(args.name, note=args.note), indent=1))
    elif args.command == 'list':
        for base in list_bases():
            print(base['name']+'  '+base['path']+('  — '+base['note'] if base['note'] else ''))
    else:
        print(json.dumps(load(args.name), indent=1))


if __name__ == '__main__':
    main()
