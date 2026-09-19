"""Enablers: the instruments a brief needs before its readings can be taken, as brief-level objects.

The worker minted widgets as a side effect of tasks and the harvest checked them in after green; nothing
at the brief level said "this project needs a page reader" or knew whether one existed. Terra's brief
carries enablers — id, title, path, lifecycle needed → building → ready → graduated → abandoned, and
`graduates_to` — and its route tasks carry `role=enabler` with an `enabler_id`. Here:

- a need or deliverable that names an enabler's id waits: no unknown citing it is minted until the
  enabler is ready (or graduated). The controller routes the enabler first, as a boolean unknown whose
  task builds the instrument at its path — or finds it already in the widget library;
- a finished enabler task marks the enabler ready with its path; a widget harvested from that path
  marks it graduated with the widget id. The next brief that names the same instrument finds it in
  the library, and the enabler task is an install, not a build.

A brief with no enablers runs as before.
"""
from __future__ import annotations

import re
from typing import Any

LIVE = ('ready', 'graduated')


def declared(brief: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in (brief.get('enablers') or []) if isinstance(e, dict) and e.get('id')]


def by_id(brief: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e['id']: e for e in declared(brief)}


def named_in(text: str, brief: dict[str, Any]) -> list[str]:
    """Enabler ids that appear as words in a brief entry's text."""
    words = set(re.findall(r'[a-z][a-z0-9_]*', str(text).lower()))
    return [e['id'] for e in declared(brief) if e['id'] in words]


def entry_text(brief: dict[str, Any], ref: str) -> str:
    kind, _, index = ref.partition(':')
    key = 'needs' if kind == 'need' else 'deliverables' if kind == 'deliverable' else ''
    entries = brief.get(key) or []
    return str(entries[int(index)-1]) if key and index.isdigit() and 1 <= int(index) <= len(entries) else ''


def waiting(brief: dict[str, Any], refs: list[str]) -> list[str]:
    """Why an unknown citing these entries cannot be minted yet: the enablers they name are not ready."""
    table = by_id(brief)
    problems = []
    for ref in refs:
        for eid in named_in(entry_text(brief, ref), brief):
            status = str(table[eid].get('status') or 'needed')
            if status not in LIVE:
                problems.append(ref+' names enabler '+eid+' ('+status+'): route the enabler first, as a boolean unknown '
                                'with "enabler": "'+eid+'"')
    return problems


def render(brief: dict[str, Any]) -> list[str]:
    enablers = declared(brief)
    if not enablers:
        return []
    lines = ['Enablers (instruments the brief needs; a need or deliverable that names one waits until it is ready — '
             'mint the enabler as a boolean unknown with "enabler": "<id>" and a task that builds or installs it, '
             'before the readings that use it):']
    for e in enablers:
        lines.append('  '+e['id']+' ['+str(e.get('status') or 'needed')+'] '+str(e.get('title') or '')
                     +(' — at '+str(e['path']) if e.get('path') else '')
                     +(' — widget '+str(e['graduates_to']) if e.get('graduates_to') else '')
                     +((': '+str(e['notes'])[:200]) if e.get('notes') else ''))
    return lines


def tag(brief: dict[str, Any], ref: str) -> str:
    table = by_id(brief)
    names = [eid for eid in named_in(entry_text(brief, ref), brief) if str(table[eid].get('status') or 'needed') not in LIVE]
    return '  (waits for enabler '+', '.join(names)+')' if names else ''
