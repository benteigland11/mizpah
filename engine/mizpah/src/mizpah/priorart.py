"""What the library already holds for the things a brief still owes — looked up by the host, shown to the controller.

The controller's leverage is before tasks exist: a deliverable that is a page means the page reader is an install,
not a build; a reading three projects took with one widget is minted the way they minted it, the instrument named.
The one similarity lookup at observe time (whole brief against stored briefs) cannot carry that: it returns the same
two briefs at every step whatever is being decided. This looks up each owed entry — an uncovered need or deliverable,
a freshly minted unknown — against three stores: the brief library (unknowns other projects resolved, with what),
the widget library and the playbook. Local searches, no model; results cached per entry text for the process.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from . import briefs

_CACHE: dict[str, list[str]] = {}
MAX_ENTRIES = 6


def _query(text: str) -> str:
    words = [w for w in re.findall(r'[a-z0-9]{4,}', text.lower()) if w not in briefs.STOP]
    return ' '.join(dict.fromkeys(words))[:160]


def _brief_hits(config: dict[str, Any], stems: set[str], limit: int = 3) -> list[str]:
    """Unknowns other projects resolved whose id or claim shares stems with the owed entry."""
    hits: list[tuple[int, str]] = []
    for doc in briefs.stored(config):
        for u in doc.get('unknowns') or []:
            if not u.get('resolved'):
                continue
            there = briefs._stems(briefs._words(str(u.get('id') or '').replace('_', ' ')+' '+str(u.get('claim') or '')))
            shared = len(stems & there)
            if shared >= 3:
                hits.append((shared, str(u['id'])+' ('+str(doc.get('project'))+', '+str(u.get('type'))+')'))
    hits.sort(key=lambda h: -h[0])
    return [h[1] for h in dict.fromkeys(hits)][:limit]


def _widget_hits(config: dict[str, Any], project: Path, query: str, stems: set[str], limit: int = 3) -> list[str]:
    tool = config['mizpah'].get('cartograph')
    if not tool or not query:
        return []
    env = dict(os.environ, WIDGET_LIBRARY_PATH=str(config['mizpah'].get('widget_library') or ''))
    try:
        out = subprocess.run([tool, 'search', query, '--top-k', str(limit), '--local-only'], cwd=project,
                             capture_output=True, text=True, timeout=60, env=env).stdout
        widgets = (json.loads(out).get('local') or {}).get('widgets') or []
    except (ValueError, OSError, subprocess.SubprocessError):
        return []
    hits = []
    for w in widgets:
        text = (str(w.get('id'))+' '+str(w.get('name') or '')+' '+str(w.get('description') or '')).lower().replace('-', ' ')
        shared = len(stems & briefs._stems(briefs._words(text)))
        if shared >= 3 or (shared >= 2 and w.get('relevance_score', 0) >= 0.9):
            hits.append('widget '+str(w['id'])+' — '+str(w.get('description') or '').split('. ')[0][:90])
    return hits


def _procedure_hits(config: dict[str, Any], query: str, stems: set[str], limit: int = 3) -> list[str]:
    tool = config['mizpah'].get('playbook')
    if not tool or not query:
        return []
    try:
        out = subprocess.run([tool, 'search', query, '--limit', str(limit)], capture_output=True, text=True, timeout=30).stdout
        doc = json.loads(out[out.find('{'):])
    except (ValueError, OSError, subprocess.SubprocessError):
        return []
    hits = []
    for h in doc.get('hits') or []:
        text = (str(h.get('id') or '')+' '+str(h.get('title') or '')).lower().replace('-', ' ')
        if len(stems & briefs._stems(briefs._words(text))) >= 3:
            hits.append('procedure '+str(h.get('id'))+' — '+str(h.get('title') or '')[:80])
    return hits


def lookup(config: dict[str, Any], project: Path, entry: str) -> list[str]:
    """Prior art for one owed entry: resolved unknowns elsewhere, widgets, procedures. Empty when nothing shares
    three real words with it — two let a Flutter diff widget answer 'leads-with note' (headline3)."""
    key = entry.strip()
    if key in _CACHE:
        return _CACHE[key]
    stems = briefs._stems(briefs._words(key))
    query = _query(key)
    found = _brief_hits(config, stems)+_widget_hits(config, project, query, stems)+_procedure_hits(config, query, stems)
    _CACHE[key] = found
    return found


def render(config: dict[str, Any], project: Path, owed: list[tuple[str, str]]) -> list[str]:
    """Lines for the controller: per owed entry (label, text), what the library holds. At most MAX_ENTRIES looked up."""
    lines: list[str] = []
    for label, text in owed[:MAX_ENTRIES]:
        hits = lookup(config, project, text)
        if hits:
            lines.append('  '+label+': '+'; '.join(hits[:5]))
    if lines:
        lines.insert(0, 'Prior art for what is still owed (the library, looked up by entry: a widget or procedure here is the '
                        'instrument to name in the unknown\'s source, an install to route rather than a build; a reading another '
                        'project resolved is the shape to mint):')
    return lines
