"""The controller retires an unknown the map no longer needs answered; not one that is owed."""
from __future__ import annotations

import json

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


def test_a_route_refusal_carries_what_terra_said_not_the_echoed_command() -> None:
    """A long route add used to be cut inside its own echo; the controller must see why it was refused."""
    args = ('route add build_score_video_measurement_instrument --title Build and validate reusable score-video '
            'measurement instrument --map score_video_measurement_instrument_exists --bucket high --skill terra-probe '
            '--accept unknown:marker_timing_measurement_validated --accept unknown:page_visibility_measurement_validated')
    said = ('route add build_score_video_measurement_instrument: unsectored plan points 71 exceed free pool 60 '
            '(budget 60 − sector reserves 0). Put work in a sector, lower buckets, or raise budget.')
    error = RuntimeError('terra '+args+' failed: '+json.dumps(dict(message=said, code='route_add')))
    assert controller.terra_refusal(error) == said
    assert 'exceed free pool 60' in controller.terra_refusal(error)[:300]
    assert controller.terra_refusal(RuntimeError('terra map create x failed: no such map')) == 'no such map'
    assert controller.terra_refusal(RuntimeError('something else')) == 'something else'


def test_a_long_note_from_the_person_is_marked_where_it_is_cut() -> None:
    """The person's note was cut silently at 1,200 characters, and the controller cannot open operator.jsonl."""
    long = 'keep the tempo. '*400
    observation = dict(_observation([], []), operator_notes=[dict(at=0, text=long)])
    text = controller.render_observation(observation, 'eval')
    assert 'keep the tempo. '*200 in text
    assert f'cut here at {controller.NOTE_CHARACTERS:,} of {len(long.strip()):,} characters' in text
    short = controller.render_observation(dict(_observation([], []), operator_notes=[dict(at=0, text='the video marks no note')]), 'eval')
    assert 'the video marks no note' in short and 'cut here' not in short


def test_a_decision_reason_reaches_the_controller_as_the_persons_note_once(tmp_path) -> None:
    """A reject that says what to do instead is a reply to the run, answered first — not only a line of history."""
    decided = [dict(id='CR-001', status='rejected', decision_reason='Investigate whether an existing tool does this.'),
               dict(id='CR-002', status='accepted', decision_reason=''),
               dict(id='CR-003', status='rejected')]
    assert controller.record_decisions(tmp_path, decided) == ['CR-001']
    assert controller.record_decisions(tmp_path, decided) == []   # once
    notes = controller.operator_notes(tmp_path)
    assert len(notes) == 1 and notes[0]['text'] == 'On CR-001 (you rejected it): Investigate whether an existing tool does this.'
    text = controller.render_observation(dict(_observation([], []), operator_notes=notes), 'eval')
    assert 'The person wrote to this run' in text and 'On CR-001 (you rejected it)' in text
    controller.mark_notes_read(tmp_path, notes)
    assert controller.operator_notes(tmp_path) == []
