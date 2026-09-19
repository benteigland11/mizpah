"""Phases: a brief too big for one map is routed one scope at a time.

A flat brief with twenty-five needs drowns a 60K worker and lets the controller mint the build-plan
unknowns before the sizing readings exist. Terra's brief already carries ordered phases; here each phase
owns brief entries (needs, deliverables) and the loop treats the first open phase as the only scope:

- the controller sees every phase but may mint only for the current one (a cite into a later phase
  is refused; a cite into a closed phase is allowed — a stale known is re-owed wherever it sits);
- a phase closes mechanically when every entry it owns has a resolved unknown citing it, its
  deliverables' named things are covered, and no task carrying its unknowns is open — the same shape
  as the gate: computed, not argued. The controller's "done" inside a phase means the phase, not the brief;
- later phases name earlier knowns by id in their needs; the worker's anchor guard already checks those
  ids exist on the map, which is how one phase consumes another.

Without phases on the brief none of this applies and the loop runs flat, as before.
"""
from __future__ import annotations

import re
from typing import Any


def declared(brief: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in (brief.get('phases') or []) if isinstance(p, dict) and p.get('id')]


def current(brief: dict[str, Any]) -> dict[str, Any] | None:
    """First declared phase not closed, in brief order; None when the brief has no phases or all are closed."""
    for phase in declared(brief):
        if phase.get('status') != 'closed':
            return phase
    return None


def owner(brief: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """The phase that owns a brief entry ('need:3'), or None when no phase claims it."""
    kind, _, index = ref.partition(':')
    if not index.isdigit():
        return None
    key = 'needs' if kind == 'need' else 'deliverables' if kind == 'deliverable' else ''
    for phase in declared(brief):
        if int(index) in (phase.get(key) or []):
            return phase
    return None


def order(brief: dict[str, Any], phase_id: str) -> int:
    return next((i for i, p in enumerate(declared(brief)) if p['id'] == phase_id), -1)


def refused_cites(brief: dict[str, Any], refs: list[str]) -> list[str]:
    """Cites into a phase after the current one: the readings it depends on do not exist yet."""
    now = current(brief)
    if now is None:
        return []
    problems = []
    for ref in refs:
        phase = owner(brief, ref)
        if phase is not None and order(brief, phase['id']) > order(brief, now['id']):
            problems.append(ref+' belongs to phase '+phase['id']+', which opens when '+now['id']+' closes')
    return problems


def cites_of(unknown: dict[str, Any]) -> list[str]:
    notes = str(unknown.get('notes') or '')
    return re.findall(r'(?:need|deliverable):\d+', notes.split('; source')[0].split('; creates')[0])


def exit_problems(observation: dict[str, Any], phase: dict[str, Any], uncovered: list[str]) -> list[str]:
    """Why the phase cannot close yet; empty means it can."""
    resolved_cites = {ref for u in observation['unknowns'] if u.get('status') == 'resolved' for ref in cites_of(u)}
    open_cites = {ref for u in observation['unknowns'] if u.get('status') != 'resolved' for ref in cites_of(u)}
    problems = []
    for key, kind in (('needs', 'need'), ('deliverables', 'deliverable')):
        for index in phase.get(key) or []:
            ref = kind+':'+str(index)
            if ref in resolved_cites:
                continue
            problems.append(ref+(' has an unresolved unknown' if ref in open_cites else ' has no resolved unknown citing it'))
    owned = {ref for key, kind in (('needs', 'need'), ('deliverables', 'deliverable')) for ref in
             (kind+':'+str(i) for i in phase.get(key) or [])}
    problems += [p for p in uncovered if p.split(' ')[0] in owned]
    open_unknowns = {u['id'] for u in observation['unknowns'] if u.get('status') != 'resolved'}
    for task in observation['tasks']:
        if task['status'] in ('ready', 'in_progress', 'blocked') and any(u in open_unknowns for u in task.get('unknowns') or []):
            problems.append('task '+task['id']+' ['+task['status']+'] still carries an unknown of this phase')
    return problems


def render(brief: dict[str, Any]) -> list[str]:
    phases = declared(brief)
    if not phases:
        return []
    now = current(brief)
    lines = ['Phases (the brief is routed one phase at a time; mint only for the current one — a later phase\'s needs '
             'name knowns the current phase has not measured yet):']
    for phase in phases:
        state = 'closed' if phase.get('status') == 'closed' else 'CURRENT' if now and phase['id'] == now['id'] else 'later'
        owns = ', '.join(['needs '+_span(phase.get('needs') or [])] * bool(phase.get('needs'))
                         + ['deliverables '+_span(phase.get('deliverables') or [])] * bool(phase.get('deliverables')))
        lines.append('  '+phase['id']+' ['+state+'] '+str(phase.get('title') or '')+(' — '+owns if owns else '')
                     +((': '+str(phase['description'])[:160]) if phase.get('description') else ''))
    if now is None:
        lines.append('  every phase is closed: judge the brief as a whole')
    return lines


def tag(brief: dict[str, Any], ref: str) -> str:
    """A suffix for a brief entry in the rendering: which phase owns it and whether that phase is open."""
    phase = owner(brief, ref)
    if phase is None:
        return ''
    now = current(brief)
    if phase.get('status') == 'closed':
        return '  (phase '+phase['id']+', closed)'
    if now and phase['id'] == now['id']:
        return ''
    return '  (phase '+phase['id']+', not open yet — do not mint for it)'


def _span(indices: list[int]) -> str:
    if not indices:
        return ''
    runs, start, prev = [], indices[0], indices[0]
    for i in indices[1:]:
        if i == prev+1:
            prev = i
            continue
        runs.append(str(start) if start == prev else str(start)+'-'+str(prev))
        start = prev = i
    runs.append(str(start) if start == prev else str(start)+'-'+str(prev))
    return ','.join(runs)
