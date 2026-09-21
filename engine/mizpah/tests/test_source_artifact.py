"""A source artifact (the composition the readings are taken of) is minted without naming a known, and its
readers depend on it — never the builder on its readers. The piano benchmark's attempt 2 (2026-09-20) had
piece_mid_built refused on every briefing and build_piece_mid told to wait on validate_audio_duration."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT.parent.parent))
from mizpah import controller  # noqa: E402
from test_phases import terra, TERRA  # noqa: E402

CONFIG = dict(mizpah=dict(terra=str(TERRA), brief_library=False, capability_store=tempfile.mkdtemp(prefix='mizpah-caps-')))


@pytest.fixture
def gym(tmp_path: Path) -> Path:
    p = tmp_path/'piano'
    p.mkdir()
    (p/'README.md').write_text('# gym\n')
    terra(p, 'init')
    terra(p, 'brief', 'init', '--title', 'Romantic piano piece', '--mission', 'compose and perform a piece')
    terra(p, 'brief', 'set', '--status', 'active', '--budget-points', '60',
          '--need', 'A solo piano piece between 80 and 100 seconds as a standard MIDI file `piece.mid` with program 0.',
          '--need', 'Performed: velocities span at least 40; the sustain pedal changes at least once per bar.',
          '--need', 'The piece is rendered to piece.mp3; the audio duration is within 2 s of the MIDI duration.',
          '--deliverable', 'piece.mid', '--deliverable', 'piece.mp3',
          '--deliverable', 'notes.md: key, form, tempo plan')
    terra(p, 'route', 'init')
    return p


def test_the_composition_is_minted_first_and_its_readers_depend_on_it(gym: Path) -> None:
    observation = controller.observe(CONFIG, gym)
    # The first briefing, as attempt 1 made it: the builders, nothing else on the map yet.
    decision = dict(unknowns=[
        dict(id='piece_mid_built', cites='deliverable:1', type='boolean', creates='piece.mid',
             claim='piece.mid exists as a standard MIDI file with program 0', evidence_needed='write it, read it back with mido'),
        dict(id='notes_md_built', cites='deliverable:3', type='boolean', creates='notes.md',
             claim='notes.md records the key, form and tempo plan of piece.mid', evidence_needed='read it'),
    ], tasks=[dict(id='compose_piece_mid', unknowns=['piece_mid_built'], bucket='medium', title='compose'),
              dict(id='write_piece_notes', unknowns=['notes_md_built'], bucket='low', title='notes', deps=['compose_piece_mid'])])
    accepted, refusals = controller.guard(decision, observation, gym)
    assert [u['id'] for u in accepted['unknowns']] == ['piece_mid_built', 'notes_md_built'], refusals
    assert [t['id'] for t in accepted['tasks']] == ['compose_piece_mid', 'write_piece_notes'], refusals
    controller.apply(CONFIG, gym, accepted)

    # The second briefing, as attempt 2 made it: readings of the MIDI, a render, and (again) the builder.
    observation = controller.observe(CONFIG, gym)
    decision = dict(unknowns=[
        dict(id='piece_duration_seconds', cites='need:1', type='number', source='piece.mid',
             claim='the duration of piece.mid in seconds', evidence_needed='pretty_midi get_end_time'),
        dict(id='pedal_changes_per_bar', cites='need:2', type='number', source='piece.mid',
             claim='CC64 changes per bar in piece.mid', evidence_needed='count CC64 events'),
        dict(id='piece_mp3_built', cites='deliverable:2', type='boolean', creates='piece.mp3',
             claim='piece.mp3 is rendered from piece.mid and its duration matches piece_duration_seconds', evidence_needed='render, ffprobe'),
    ], tasks=[dict(id='validate_music', unknowns=['piece_duration_seconds', 'pedal_changes_per_bar'], bucket='medium', title='validate'),
              dict(id='build_piece_mp3', unknowns=['piece_mp3_built'], bucket='low', title='render')])
    accepted, refusals = controller.guard(decision, observation, gym)
    ids = {t['id']: t for t in accepted['tasks']}
    assert 'validate_music' in ids and 'build_piece_mp3' in ids, refusals
    # Readers of the composition depend on its builder; the render (derived) depends on the readings it must agree with.
    assert 'compose_piece_mid' in ids['validate_music']['deps'], (ids, refusals)
    assert not any('it builds an artifact that must agree with the map' in r and 'compose' in r for r in refusals), refusals


def test_the_model_need_not_send_creates(gym: Path) -> None:
    """The controller derives `creates` from the deliverable's text; the source-artifact rule must see that,
    not the raw decision (melody_bass stalled on the same refusal after the first fix, 2026-09-20)."""
    observation = controller.observe(CONFIG, gym)
    decision = dict(unknowns=[
        dict(id='piece_mid_built', cites='deliverable:1', type='boolean',
             claim='`piece.mid` exists as a parseable piano MIDI file implementing the piece',
             evidence_needed='a MIDI inspection confirms piece.mid exists and uses program 0'),
    ], tasks=[dict(id='build_piece', unknowns=['piece_mid_built'], bucket='medium', title='compose')])
    accepted, refusals = controller.guard(decision, observation, gym)
    assert [u['id'] for u in accepted['unknowns']] == ['piece_mid_built'], refusals
    assert accepted['unknowns'][0]['creates'] == 'piece.mid'


def test_a_report_with_no_anchor_is_cautioned_not_refused(gym: Path) -> None:
    """The anchor rule is a method caution now: the unknown is minted and the caution rides with the briefing."""
    observation = controller.observe(CONFIG, gym)
    decision = dict(unknowns=[
        dict(id='summary_built', cites='deliverable:3', type='boolean', creates='summary.md',
             claim='summary.md is written', evidence_needed='read it'),
    ], tasks=[dict(id='write_summary', unknowns=['summary_built'], bucket='low', title='summary')])
    accepted, refusals = controller.guard(decision, observation, gym)
    assert [u['id'] for u in accepted['unknowns']] == ['summary_built'] and not refusals
    assert any('summary_built' in c and 'names no known' in c for c in accepted['cautions'])


def test_a_resolved_reading_minted_again_is_reopened_not_refused(gym: Path) -> None:
    """After a repair the controller wants the reading taken afresh; its only vocabulary is a new unknown, which
    was refused as a twin twice and stalled the pedal gym (2026-09-20). Now the original is reopened and the task
    routes on it."""
    (gym/'piece.mid').write_bytes(b'MThd')   # the piece exists; the reading is of it
    observation = controller.observe(CONFIG, gym)
    first = dict(unknowns=[
        dict(id='pedal_timing_valid', cites='need:2', type='boolean', source='piece.mid',
             claim='every CC64 down in piece.mid follows its chord onset within 50 ms', evidence_needed='count'),
    ], tasks=[dict(id='validate_pedal', unknowns=['pedal_timing_valid'], bucket='low', title='validate')])
    accepted, refusals = controller.guard(first, observation, gym)
    assert not refusals, refusals
    controller.apply(CONFIG, gym, accepted)
    # The worker resolved it false and its task closed (by hand here).
    terra(gym, 'route', 'cancel', 'validate_pedal', '--reason', 'done by hand in the test')
    path = gym/'.terra'/'map'/'unknowns'/'pedal_timing_valid.json'
    doc = json.loads(path.read_text()); doc['status'] = 'resolved'; path.write_text(json.dumps(doc))
    observation = controller.observe(CONFIG, gym)
    again = dict(unknowns=[
        dict(id='pedal_timing_valid_after_fix', cites='need:2', type='boolean', source='piece.mid',
             claim='every CC64 down in piece.mid follows its chord onset within 50 ms', evidence_needed='count again'),
    ], tasks=[dict(id='revalidate_pedal', unknowns=['pedal_timing_valid_after_fix'], bucket='low', title='revalidate')])
    accepted, refusals = controller.guard(again, observation, gym)
    assert not refusals, refusals
    assert accepted['unknowns'] == [] and accepted['reopen'] == ['pedal_timing_valid']
    assert accepted['tasks'][0]['unknowns'] == ['pedal_timing_valid'], accepted['tasks']
    applied = controller.apply(CONFIG, gym, accepted)
    assert applied.get('reopen') == ['pedal_timing_valid'] and 'revalidate_pedal' in applied['tasks']
    assert json.loads(path.read_text())['status'] == 'open'


def test_a_note_from_the_person_is_put_first_and_read_once(gym: Path, tmp_path: Path) -> None:
    """A reply to a stopped notice lands in operator.jsonl under the session; the next briefing sees it under
    'From the person', and the one after does not."""
    root = tmp_path/'sess'
    root.mkdir()
    (root/controller.OPERATOR_NOTES).write_text(json.dumps(dict(at=1.0, text='The pedal must not hold through a harmony change.'))+'\n')
    notes = controller.operator_notes(root)
    assert [n['text'] for n in notes] == ['The pedal must not hold through a harmony change.']
    observation = controller.observe(CONFIG, gym)
    observation['operator_notes'] = notes
    text = controller.render_observation(observation, 'eval')
    assert text.startswith('# From the person') and 'harmony change' in text.splitlines()[1]
    controller.mark_notes_read(root, notes)
    assert controller.operator_notes(root) == []
    assert controller.operator_notes(root, unread_only=False)[0]['read'] is True


def test_a_task_on_a_false_reading_reopens_it(gym: Path) -> None:
    """The refusal text says 'route its own id again'; doing so was refused as 'neither minted nor open' and the
    nocturne gym stalled. A task naming a resolved-false boolean reopens it."""
    (gym/'piece.mid').write_bytes(b'MThd')
    observation = controller.observe(CONFIG, gym)
    first = dict(unknowns=[
        dict(id='left_hand_rolls', cites='need:2', type='boolean', source='piece.mid',
             claim='the left hand of piece.mid is a wide rolling figure', evidence_needed='read the bass pattern'),
    ], tasks=[dict(id='validate_lh', unknowns=['left_hand_rolls'], bucket='low', title='validate')])
    accepted, refusals = controller.guard(first, observation, gym)
    assert not refusals, refusals
    controller.apply(CONFIG, gym, accepted)
    terra(gym, 'route', 'cancel', 'validate_lh', '--reason', 'done by hand in the test')
    path = gym/'.terra'/'map'/'unknowns'/'left_hand_rolls.json'
    doc = json.loads(path.read_text()); doc['status'] = 'resolved'; path.write_text(json.dumps(doc))
    observation = controller.observe(CONFIG, gym)
    # The known reads false (planted: observe() reads knowns from the map; a boolean with rate 0).
    observation['knowns'].append(dict(id='left_hand_rolls', type='boolean', rate=0.0, stale=False))
    again = dict(unknowns=[], tasks=[dict(id='repair_lh', unknowns=['left_hand_rolls'], bucket='medium', title='repair the left hand')])
    accepted, refusals = controller.guard(again, observation, gym)
    assert not refusals, refusals
    assert accepted['reopen'] == ['left_hand_rolls'] and accepted['tasks'][0]['unknowns'] == ['left_hand_rolls']


def test_a_budget_ask_is_its_own_patch(gym: Path) -> None:
    """A proposal for more points carries budget_delta (+N) and nothing else, and the budget is never written as a
    number: every budget CR on 2026-09-20 was smuggled into a need, and one carrying budget_points 3 beside a
    need was accepted for the need and cut 120 to 3."""
    observation = controller.observe(CONFIG, gym)
    ok = dict(proposals=[dict(summary='six more points for the two outstanding readings', evidence='0 of 60 unallocated, two low tasks owed',
                              budget_delta=6, blocking=True)])
    accepted, refusals = controller.guard(ok, observation, gym)
    assert not refusals and accepted['proposals'][0]['budget_delta'] == '+6'
    smuggled = dict(proposals=[dict(summary='more points', evidence='x', budget_delta=6, need='Provide six more budget points.')])
    accepted, refusals = controller.guard(smuggled, observation, gym)
    assert not accepted['proposals'] and any('budget_delta alone' in r for r in refusals)
    absolute = dict(proposals=[dict(summary='name the procedure file', evidence='x', budget_points=3,
                                    need='Improve the named procedure in a project file.')])
    accepted, refusals = controller.guard(absolute, observation, gym)
    assert not accepted['proposals'] and any('never written as a number' in r for r in refusals)
    accepted, refusals = controller.guard(dict(proposals=[dict(summary='less', evidence='x', budget_delta=-10)]), observation, gym)
    assert not accepted['proposals'] and any("person's call" in r for r in refusals)
    controller.apply(CONFIG, gym, controller.guard(ok, observation, gym)[0])
    brief = json.loads((gym/'.terra'/'brief.json').read_text())
    assert brief['proposals'][-1]['patch'] == {'budget_delta': 6, 'was_budget_points': 60}


def test_a_claim_that_is_the_whole_spec_is_cautioned(gym: Path) -> None:
    """One reading per quantity is a method rule, so a boolean that is the specification itself is minted with a
    caution on the next briefing, never refused (counting-a-bar's build task spent forty turns after Terra had it
    done because one probe had to carry the whole need, 2026-09-21)."""
    observation = controller.observe(CONFIG, gym)
    def decide(uid, claim):
        return dict(unknowns=[dict(id=uid, cites='deliverable:1', type='boolean', claim=claim, evidence_needed='parse piece.mid')],
                    tasks=[dict(id='build_piece', unknowns=[uid], bucket='low', title='compose')])
    for uid, claim in (('piece_mid_meets_passage_spec', '`piece.mid` meets the passage spec'),
                       ('piece_mid_built', '`piece.mid` holds a steady-tempo passage, in 4/4, four sections, each in one subdivision, and a bar of rest between them')):
        accepted, refusals = controller.guard(decide(uid, claim), observation, gym)
        assert [x['id'] for x in accepted['unknowns']] == [uid] and not refusals
        assert any('whole specification' in c for c in accepted.get('cautions') or []), (uid, accepted.get('cautions'))
    accepted, _ = controller.guard(decide('piece_mid_built', '`piece.mid` exists and parses as a MIDI file'), observation, gym)
    assert not any('whole specification' in c for c in accepted.get('cautions') or [])


def test_reviewer_doubts_reach_the_controller(gym: Path, tmp_path: Path) -> None:
    """A doubt the reviewer still held when its completion budget ran out is a line at the top of the next
    briefing — a candidate reading of its own — never another round for the same worker."""
    session = tmp_path/'session'
    session.mkdir()
    (session/'loop.json').write_text(json.dumps(dict(cycles=[dict(cycle=1, tasks=[dict(
        task='build_piece_mid', unknowns=['piece_mid_built'], verdict='complete',
        reviewer_doubts=[dict(turn=61, correction='measure() never verifies the MIDI is 4/4', evidence='no time_signature read')])])])))
    doubts = controller.reviewer_doubts(session/'controller.jsonl')
    assert doubts == [dict(task='build_piece_mid', unknowns=['piece_mid_built'], correction='measure() never verifies the MIDI is 4/4',
                           evidence='no time_signature read')]
    observation = controller.observe(CONFIG, gym) | dict(reviewer_doubts=doubts)
    text = controller.render_observation(observation, 'eval')
    assert 'What the check-in reviewer still doubted' in text and 'never verifies the MIDI is 4/4' in text
    assert text.index('reviewer still doubted') < text.index('# Brief')
    assert controller.reviewer_doubts(tmp_path/'nowhere'/'controller.jsonl') == []


def test_a_task_may_continue_a_workspace_the_loop_holds(gym: Path, tmp_path: Path) -> None:
    """The controller sees the worker workspaces and may route a task onto one (continue_from); the guard drops a
    name it does not hold with a caution; the worker adopts the session (state, journal, merge base) under the
    new task's root, leaving the old report behind."""
    root = tmp_path/'session'
    old = root/'tasks'/'build_piece_mid'
    (old/'events').mkdir(parents=True)
    (old/'state.sqlite3').write_bytes(b'state')
    (old/'events'/'session.jsonl').write_text(json.dumps(dict(event_type='worker_turn', payload=dict(response=dict(tool_calls=[
        dict(id='c', function=dict(name='playbook_open', arguments=json.dumps(dict(id='midi-piano-voicing-melody', purpose='x'))))]))))+'\n'
        + json.dumps(dict(event_type='worker_turn', payload=dict(response=dict(tool_calls=[dict(id='d', function=dict(name='edit',
        arguments=json.dumps(dict(path='cg/data_music_x_python/src/x.py'))))]))))+'\n')
    (old/'task.json').write_text(json.dumps(dict(task=dict(id='build_piece_mid'), unknowns=[dict(id='piece_mid_built')], map='t_build_piece_mid')))
    (old/'result.json').write_text(json.dumps(dict(verdict='complete', turns=30)))
    (old/'writeup.started').write_text('30')
    (root/'controller.jsonl').write_text('')
    spaces = controller.task_workspaces(root)
    assert spaces == [dict(task='build_piece_mid', unknowns=['piece_mid_built'], verdict='complete', turns=30, probes=[],
                           walks=['midi-piano-voicing-melody'], widgets=['data_music_x_python'])]
    observation = controller.observe(CONFIG, gym) | dict(workspaces=spaces)
    text = controller.render_observation(observation, 'route')
    assert 'Worker workspaces this loop holds' in text and 'walked: midi-piano-voicing-melody' in text
    decision = dict(unknowns=[dict(id='piece_mid_built', cites='deliverable:1', type='boolean', claim='`piece.mid` exists and parses',
                                   evidence_needed='parse it'),
                              dict(id='piece_mid_parses', cites='deliverable:1', type='boolean', claim='`piece.mid` parses with mido',
                                   evidence_needed='parse it')],
                    tasks=[dict(id='retake_piece', unknowns=['piece_mid_built'], bucket='low', title='take it again', continue_from='build_piece_mid'),
                           dict(id='other', unknowns=['piece_mid_parses'], bucket='low', title='x', continue_from='no_such_task')])
    accepted, refusals = controller.guard(decision, observation, gym)
    by = {t['id']: t for t in accepted['tasks']}
    assert by['retake_piece']['continue_from'] == 'build_piece_mid'
    assert by.get('other', {}).get('continue_from', '') == '' and any('names no workspace' in c for c in accepted['cautions'])
    # The worker adopts the session under the new root; the old report and marker stay behind.
    from mizpah import worker
    new = root/'tasks'/'retake_piece'
    assert worker.adopt_workspace(old, new)
    assert (new/'state.sqlite3').read_bytes() == b'state' and (new/'events'/'session.jsonl').exists()
    assert not (new/'result.json').exists() and not (new/'writeup.started').exists()
    assert not worker.adopt_workspace(old, new)   # already holds a session
    assert worker.continue_from(dict(acceptance=['unknown:x', 'continue_from:build_piece_mid'])) == 'build_piece_mid'


def test_a_root_adopted_but_never_retargeted_resumes_as_a_continuation() -> None:
    from mizpah import worker
    saved = dict(task=dict(id='check_count'), continued_from='check_count')
    assert worker.half_adopted(saved, 'validate_parts') == 'check_count'
    assert worker.half_adopted(saved | dict(task=dict(id='validate_parts')), 'validate_parts') == ''
    assert worker.half_adopted(dict(task=dict(id='check_count')), 'validate_parts') == ''

