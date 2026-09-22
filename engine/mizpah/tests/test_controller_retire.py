"""The controller retires an unknown the map no longer needs answered; not one that is owed."""
from __future__ import annotations

from mizpah import controller


def _observation(tasks, unknowns):
    return dict(brief=dict(needs=['n'], deliverables=['d'], proposals=[]), tasks=tasks, unknowns=unknowns,
                knowns=[], gate=dict(ok=False, violations=[]), cautions=[], operator_notes=[], reviewer_doubts=[],
                memory='', related_briefs=[], registry=[], prior_art=[], workspaces=[])


def _accept(decision, tasks, unknowns):
    accepted, refusals = controller.guard(decision, _observation(tasks, unknowns))
    return accepted, refusals


def test_retire_takes_an_unowed_unknown_and_refuses_the_owed() -> None:
    unknowns = [dict(id='ghost', status='open', type='boolean', notes='cites need:1'),
                dict(id='carried', status='open', type='boolean', notes='cites need:1'),
                dict(id='built', status='open', type='boolean', notes='cites deliverable:1; creates piece.mid'),
                dict(id='answered', status='resolved', type='boolean', notes='cites need:1')]
    tasks = [dict(id='live', status='ready', unknown='carried', unknowns=['carried']),
             dict(id='gone', status='cancelled', unknown='ghost', unknowns=['ghost'])]
    accepted, refusals = _accept(dict(retire=[
        dict(unknown='ghost', why='its work order was cancelled and the reading is not owed'),
        dict(unknown='carried', why='no longer interesting'),
        dict(unknown='built', why='no longer interesting'),
        dict(unknown='answered', why='no longer interesting'),
        dict(unknown='ghost', why=''),
        dict(unknown='missing', why='no such thing'),
    ]), tasks, unknowns)
    assert [r['unknown'] for r in accepted['retire']] == ['ghost']
    assert any('still carries it' in r for r in refusals)
    assert any('deliverable:1' in r for r in refusals)
    assert any('already stands' in r for r in refusals)
    assert any('say why' in r for r in refusals)
    assert any('no such unknown' in r for r in refusals)
