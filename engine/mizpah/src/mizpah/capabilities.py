"""The capability registry: every enabler a project graduated, queryable across projects.

An enabler is not always a widget. A page reader is; a served catalog, a trained model, a harness, a
dataset or a blueprint of several widgets are enablers too, and Cartograph only knows about the first.
Terra's brief record already carries what an enabler is (`kind`), where it lives (`path`), what it became
(`graduates_to`) and what it does (`notes`); this store keeps those records after the project ends, so the
next brief that declares an enabler is shown what already exists — and the enabler task is an install or
a pointer, not a build. Also what a person or the app asks when they ask "what can this thing do".

Store: <brief_store>/../enablers/<enabler id>.json — one record per enabler id, updated on every
graduation (latest wins; earlier graduations kept under `history`).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any

from . import briefs

KINDS = ('widget', 'blueprint', 'service', 'dataset', 'model', 'harness', 'procedure', 'tooling', 'other')


def store_dir(config: dict[str, Any]) -> Path:
    base = Path(config['mizpah'].get('capability_store') or Path(config['mizpah']['playbook_store']).parent.parent/'mizpah'/'enablers')
    base.mkdir(parents=True, exist_ok=True)
    return base


def record(config: dict[str, Any], project: Path, enabler: dict[str, Any], *, widget: str | None = None) -> Path:
    """Register a graduated (or ready) enabler; the record is what a future brief is shown."""
    path = store_dir(config)/(re.sub(r'[^a-z0-9_]+', '_', str(enabler['id']).lower())+'.json')
    previous = json.loads(path.read_text()) if path.exists() else {}
    history = list(previous.get('history') or [])
    if previous:
        history.append({k: previous.get(k) for k in ('project', 'recorded_at', 'status', 'path', 'graduates_to')})
    doc = dict(id=enabler['id'], title=enabler.get('title') or '', kind=enabler.get('kind') or ('widget' if widget else 'tooling'),
               notes=enabler.get('notes') or '', status=enabler.get('status') or 'ready', path=enabler.get('path') or '',
               graduates_to=widget or enabler.get('graduates_to'), project=project.name, recorded_at=time.time(),
               uses=int(previous.get('uses') or 0)+1, history=history[-10:])
    path.write_text(json.dumps(doc, indent=1)+'\n')
    return path


def registered(config: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for path in sorted(store_dir(config).glob('*.json')):
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
            lines.append('  registry: '+cap['id']+' ('+str(cap.get('kind'))+') '+str(cap.get('title'))+' — '
                         +('widget '+str(cap['graduates_to']) if cap.get('graduates_to') else 'at '+str(cap.get('path')))
                         +', graduated by '+str(cap.get('project'))+', used '+str(cap.get('uses'))+'×'
                         +(' — same id as '+enabler['id'] if cap['id'] == enabler['id'] else ' — near '+enabler['id']))
    if lines:
        lines.insert(0, 'Capability registry (enablers other projects graduated; an enabler the registry holds is an '
                        'install, and its task is bucketed low):')
    return lines
