"""The controller's library: what other briefs asked, what was measured for them, and how it went.

The worker has procedures and widgets; the controller routed every brief from scratch, so nothing one
project learned to measure reached the next — a landing page measured for contrast and line length by
one brief, for gutters, list markers and a call-to-action's box by five others, and the sixth brief
would mint only what its own text named. A finished run records its brief, the unknowns it minted (type,
the need or deliverable each cited, whether it resolved) and the outcome; a route or eval step is shown
the briefs nearest the one in hand and their unknowns. The controller still decides; it decides having
seen what the same kind of deliverable was measured for elsewhere.

Store: <config playbook_store>/../../mizpah/briefs/<project>-<stamp>.json (beside the playbook store).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any

from . import layout

STOP = {'with', 'from', 'that', 'this', 'into', 'every', 'each', 'must', 'their', 'when', 'than', 'then', 'against',
        'know', 'known', 'unknown', 'whether', 'number', 'count', 'the', 'and', 'for', 'its', 'not', 'yet', 'has',
        'have', 'does', 'under', 'over', 'file', 'files', 'project', 'agree', 'agreeing', 'agrees', 'map'}


def store_dir(config: dict[str, Any]) -> Path:
    base = Path(config['mizpah'].get('brief_store') or Path(config['mizpah']['playbook_store']).parent.parent/'mizpah'/'briefs')
    base.mkdir(parents=True, exist_ok=True)
    return base


USABLE_STOPS = ('completed', 'nothing_owed')   # a run the controller may learn from ended on its own merits ('nothing_owed' is the old name)


def record(config: dict[str, Any], project: Path, stop: str, cycles: list[dict[str, Any]], session: Path | None = None) -> Path | None:
    """Write this run's brief, unknowns and outcome to the store (one file per run) — only a run that completed.
    Before this every exit was recorded (77 of 93 records were interrupted, blocked, stalled or crashed runs,
    2026-09-20), and the controller's prior art drew on unknowns nothing had resolved."""
    if stop not in USABLE_STOPS:
        return None
    brief_path = project/layout.dirname(project)/'brief.json'
    if not brief_path.exists():
        return None
    brief = json.loads(brief_path.read_text())
    unknowns = []
    for path in sorted((layout.map_root(project)/'unknowns').glob('*.json')):
        u = json.loads(path.read_text())
        notes = str(u.get('notes') or '')
        cites = re.search(r'cites ((?:need|deliverable):\d+)', notes)
        unknowns.append(dict(id=u['id'], type=u.get('type'), claim=str(u.get('claim') or '')[:200],
                             cites=cites.group(1) if cites else None, resolved=u.get('status') == 'resolved'))
    tasks = [t for c in cycles for t in (c.get('tasks') or [])]
    doc = dict(project=project.name, session=str(session) if session else None, recorded_at=time.time(), stop=stop, title=brief.get('title'),
               mission=brief.get('mission'), needs=list(brief.get('needs') or []), deliverables=list(brief.get('deliverables') or []),
               unknowns=unknowns, tasks=len(tasks), turns=sum(int(t.get('turns') or 0) for t in tasks),
               proposals=[str(p.get('summary') or '')[:200] for p in brief.get('proposals') or []])
    path = store_dir(config)/(re.sub(r'[^a-z0-9]+', '-', project.name.lower())+'-'+time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+'.json')
    path.write_text(json.dumps(doc, indent=1)+'\n')
    return path


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r'[a-z0-9]{4,}', text.lower()) if w not in STOP}


def _stems(words: set[str]) -> set[str]:
    return {w[:5] for w in words}


def _archived(doc: dict[str, Any]) -> bool:
    """A record whose session was archived in the app is not usable: the sidebar is where sessions are managed."""
    session = doc.get('session')
    if not session:
        return False
    try:
        return bool(json.loads((Path(session)/'run.json').read_text()).get('archived'))
    except (OSError, ValueError):
        return False


def stored(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every usable recorded run in the library: completed, not archived; unreadable files skipped."""
    out = []
    for path in sorted(store_dir(config).glob('*.json')):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if doc.get('stop') not in USABLE_STOPS or _archived(doc):
            continue
        out.append(doc)
    return out


def prune(config: dict[str, Any]) -> list[str]:
    """Remove records the library must not read: runs that did not complete, and archived sessions. Returns the
    file names removed."""
    gone = []
    for path in sorted(store_dir(config).glob('*.json')):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if doc.get('stop') not in USABLE_STOPS or _archived(doc):
            path.unlink()
            gone.append(path.name)
    return gone


def related(config: dict[str, Any], brief: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    """The stored briefs nearest this one: ranked by shared stems across mission, needs and deliverables,
    excluding the same project; each with the unknowns it minted."""
    here = _stems(_words(' '.join([str(brief.get('mission') or '')]+list(brief.get('needs') or [])+list(brief.get('deliverables') or []))))
    if not here:
        return []
    scored: dict[str, tuple[float, dict[str, Any]]] = {}
    for path in store_dir(config).glob('*.json'):
        try:
            doc = json.loads(path.read_text())
        except ValueError:
            continue
        if doc.get('title') == brief.get('title') and doc.get('mission') == brief.get('mission'):
            continue   # the same brief, an earlier run: its unknowns are this project's own history, not a neighbour
        there = _stems(_words(' '.join([str(doc.get('mission') or '')]+list(doc.get('needs') or [])+list(doc.get('deliverables') or []))))
        if not there:
            continue
        overlap = len(here & there)/len(here | there)
        if overlap < 0.08:
            continue
        key = str(doc.get('title'))
        if key not in scored or scored[key][0] < overlap:
            scored[key] = (overlap, doc)
    return [dict(doc, overlap=round(score, 2)) for score, doc in sorted(scored.values(), key=lambda sd: -sd[0])[:limit]]


def render(briefs: list[dict[str, Any]], *, max_unknowns: int = 8) -> list[str]:
    if not briefs:
        return []
    # Title and mission only: enough to know a project is worth asking about. What it measured is read on
    # request, not carried on every sitrep.
    lines = ['# Related briefs (other projects near this one, by title and mission)']
    for doc in briefs:
        lines.append('  '+str(doc.get('title'))+' — '+str(doc.get('mission') or '')[:160]+' ('+str(doc.get('stop') or '?')+')')
    return lines
