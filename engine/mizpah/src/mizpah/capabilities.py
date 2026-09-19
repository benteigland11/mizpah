"""The capability registry: every enabler a project graduated, queryable across projects.

An enabler is not always a widget. A page reader is; a served catalog, a trained model, a harness, a
dataset or a blueprint of several widgets are enablers too. There is no taxonomy: an enabler is a title,
notes on what it does, where it lives (`path`) and what it became (`graduates_to` — a widget id, a service
name, a checksum, whatever the thing is). This store keeps those records after the project ends, so the
next brief that declares an enabler is shown what already exists — and the enabler task is an install or
a pointer, not a build. Also what a person or the app asks when they ask "what can this thing do".

An enabler is packed as a small repo: the directory at its path, with `enabler.json` (id, title, notes,
what it became) and a README that is its interface, plus whatever it is (a widget's src and tests, a
service's start script, a dataset). Graduation copies the pack into the store; a later brief that declares
the same enabler gets the pack installed at its path before routing starts, and the enabler is ready
without a task. Interfacing with an enabler is reading its README and calling what it documents.

Store: <brief_store>/../enablers/<enabler id>/record.json (latest graduation, earlier ones under `history`)
and <enabler id>/pack/ (the directory as graduated).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import time
from typing import Any

from . import briefs

def store_dir(config: dict[str, Any]) -> Path:
    base = Path(config['mizpah'].get('capability_store') or Path(config['mizpah']['playbook_store']).parent.parent/'mizpah'/'enablers')
    base.mkdir(parents=True, exist_ok=True)
    return base


IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache', '.venv', 'node_modules', '.git')


def slug(enabler_id: str) -> str:
    return re.sub(r'[^a-z0-9_]+', '_', str(enabler_id).lower())


def manifest(project: Path, enabler: dict[str, Any], *, widget: str | None = None) -> dict[str, Any]:
    """enabler.json: what the pack is and how to talk to it (the README head is the interface)."""
    root = resolve_path(project, str(enabler.get('path') or '')) or project/str(enabler.get('path') or '')
    readme = next((p for p in (root/'README.md', root/'README.txt', root/'readme.md') if p.exists()), None)
    interface = readme.read_text(errors='replace').strip().splitlines()[:40] if readme else []
    files = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()
                   and not any(part in ('__pycache__', '.venv', '.git', 'node_modules', '.pytest_cache') for part in p.parts))[:200]
    return dict(id=enabler['id'], title=enabler.get('title') or '', notes=enabler.get('notes') or '',
                path=enabler.get('path') or '', graduates_to=widget or enabler.get('graduates_to'),
                interface=interface, files=files, packed_from=project.name, packed_at=time.time())


def pack_procedures(config: dict[str, Any], pack: Path, procedure_ids: list[str]) -> list[str]:
    """Copy the procedures an enabler came with into pack/procedures/: the method ships with the tool."""
    store = Path(config['mizpah'].get('playbook_store') or '')
    packed = []
    for pid in dict.fromkeys(procedure_ids):
        source = store/(pid+'.json')
        if store and source.exists():
            (pack/'procedures').mkdir(exist_ok=True)
            shutil.copy2(source, pack/'procedures'/(pid+'.json'))
            packed.append(pid)
    return packed


def install_procedures(config: dict[str, Any], pack: Path) -> list[str]:
    """Procedures in the pack enter the Playbook store when the store has no procedure of that id."""
    store = Path(config['mizpah'].get('playbook_store') or '')
    installed = []
    for source in sorted((pack/'procedures').glob('*.json')) if (pack/'procedures').is_dir() and store else []:
        target = store/source.name
        if not target.exists():
            store.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            installed.append(source.stem)
    return installed


def resolve_path(project: Path, path: str) -> Path | None:
    """The enabler's directory, whichever spelling it landed in: Cartograph installs `a-b-c` at `cg/a_b_c`."""
    if not path:
        return None
    candidates = [project/path, project/path.replace('-', '_'), project/path.replace('_', '-')]
    parent, name = Path(path).parent, Path(path).name
    candidates += [project/parent/name.replace('-', '_'), project/parent/name.replace('_', '-')]
    return next((c for c in candidates if c.is_dir()), None)


def record(config: dict[str, Any], project: Path, enabler: dict[str, Any], *, widget: str | None = None,
           procedures: list[str] = ()) -> Path:
    """Register a graduated (or ready) enabler and pack its directory; the record is what a future brief is shown."""
    home = store_dir(config)/slug(enabler['id'])
    home.mkdir(parents=True, exist_ok=True)
    record_path = home/'record.json'
    previous = json.loads(record_path.read_text()) if record_path.exists() else {}
    history = list(previous.get('history') or [])
    if previous:
        history.append({k: previous.get(k) for k in ('project', 'recorded_at', 'status', 'path', 'graduates_to')})
    source = resolve_path(project, str(enabler.get('path') or ''))
    packed = False
    if enabler.get('path') and source is not None and source.is_dir():
        pack = home/'pack'
        if pack.exists():
            shutil.rmtree(pack)
        shutil.copytree(source, pack, ignore=IGNORE)
        shipped = pack_procedures(config, pack, list(procedures))
        (pack/'enabler.json').write_text(json.dumps(manifest(project, enabler, widget=widget) | dict(procedures=shipped), indent=1)+'\n')
        packed = True
    doc = dict(id=enabler['id'], title=enabler.get('title') or '',
               notes=enabler.get('notes') or '', status=enabler.get('status') or 'ready', path=enabler.get('path') or '',
               graduates_to=widget or enabler.get('graduates_to'), project=project.name, recorded_at=time.time(),
               packed=packed, uses=int(previous.get('uses') or 0)+1, history=history[-10:])
    record_path.write_text(json.dumps(doc, indent=1)+'\n')
    return record_path


def install(config: dict[str, Any], project: Path, enabler: dict[str, Any]) -> dict[str, Any] | None:
    """Copy a registered pack into the project at the enabler's path; None when the store has no pack for it."""
    home = store_dir(config)/slug(enabler['id'])
    pack, record_path = home/'pack', home/'record.json'
    if not (pack.is_dir() and record_path.exists() and enabler.get('path')):
        return None
    target = project/str(enabler['path'])
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(pack, target, ignore=IGNORE)
    procedures = install_procedures(config, pack)
    doc = json.loads(record_path.read_text())
    doc['procedures_installed'] = procedures
    doc['uses'] = int(doc.get('uses') or 0)+1
    doc.setdefault('installed_by', []).append(dict(project=project.name, at=time.time()))
    record_path.write_text(json.dumps(doc, indent=1)+'\n')
    return doc


def registered(config: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for path in sorted(store_dir(config).glob('*/record.json')):
        try:
            out.append(json.loads(path.read_text()))
        except ValueError:
            continue
    return out


def matches(config: dict[str, Any], enabler: dict[str, Any]) -> list[dict[str, Any]]:
    """Registered capabilities for a brief's enabler: same id first, then by shared words in title and notes."""
    same, near = [], []
    here = briefs._stems(briefs._words(str(enabler.get('title') or '')+' '+str(enabler.get('notes') or '')))
    for cap in registered(config):
        if cap['id'] == enabler['id']:
            same.append(cap)
            continue
        there = briefs._stems(briefs._words(str(cap.get('title') or '')+' '+str(cap.get('notes') or '')))
        if here and there and len(here & there)/len(here | there) >= 0.2:
            near.append(cap)
    return same+near[:2]


def render(config: dict[str, Any], brief: dict[str, Any]) -> list[str]:
    """Lines for the controller: what the registry already holds for each enabler the brief declares."""
    lines = []
    for enabler in brief.get('enablers') or []:
        if not isinstance(enabler, dict) or str(enabler.get('status') or 'needed') in ('ready', 'graduated'):
            continue
        for cap in matches(config, enabler):
            lines.append('  registry: '+cap['id']+' '+str(cap.get('title'))+' — '
                         +('widget '+str(cap['graduates_to']) if cap.get('graduates_to') else 'at '+str(cap.get('path')))
                         +', graduated by '+str(cap.get('project'))+', used '+str(cap.get('uses'))+'×'
                         +(' — same id as '+enabler['id'] if cap['id'] == enabler['id'] else ' — near '+enabler['id'])
                         +(' (packed: the loop installs it)' if cap.get('packed') and cap['id'] == enabler['id'] else ''))
    if lines:
        lines.insert(0, 'Capability registry (enablers other projects graduated; a packed one with the same id is installed '
                        'by the loop before routing, so it is ready without a task; a near one is a starting point for the '
                        'enabler task, bucketed low):')
    return lines
