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
    assert accepted['unknowns'] == [] and accepted['reopen_unknowns'] == ['pedal_timing_valid']
    assert accepted['tasks'][0]['unknowns'] == ['pedal_timing_valid'], accepted['tasks']
    applied = controller.apply(CONFIG, gym, accepted)
    assert applied.get('reopen') == ['pedal_timing_valid'] and 'revalidate_pedal' in applied['tasks']
    assert json.loads(path.read_text())['status'] == 'open'


def test_a_note_from_the_person_is_put_first_and_read_once(gym: Path, tmp_path: Path) -> None:
    """A reply to a stopped notice lands in operator.jsonl under the session; the next briefing sees it under
    'From the person', and the one after does not."""
    root = tmp_path/'sess'
    root.mkdir()
    (root/controller.OPERATOR_NOTES).write_text(json.dumps(dict(at=1.0, text='The pedal must not hold through a harmony change.'))+'\n'
                                                +json.dumps(dict(at=2.0, text='Use the softer voicing.', to='worker:build_piece'))+'\n')
    notes = controller.operator_notes(root)
    # A memo to a worker is on the same record but is not the controller's.
    assert [n['text'] for n in notes] == ['The pedal must not hold through a harmony change.']
    observation = controller.observe(CONFIG, gym)
    observation['operator_notes'] = notes
    text = controller.render_observation(observation, 'eval')
    assert text.startswith('# What just happened') and 'The person wrote to this run' in text and 'harmony change' in text.split('# What is asked')[0]
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
    assert accepted['reopen_unknowns'] == ['left_hand_rolls'] and accepted['tasks'][0]['unknowns'] == ['left_hand_rolls']


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
    assert 'The check-in reviewer still doubted' in text and 'never verifies the MIDI is 4/4' in text
    assert text.index('reviewer still doubted') < text.index('# Brief')
    assert controller.reviewer_doubts(tmp_path/'nowhere'/'controller.jsonl') == []






def test_a_continued_task_opens_its_unknown_on_the_adopted_map(gym: Path) -> None:
    """The continuation keeps the workspace's map: the new unknown lands beside the readings already there and
    no `t_<new task>` map is left with an open unknown nobody works on."""
    from mizpah import worker, layout
    observation = controller.observe(CONFIG, gym)
    decision = dict(unknowns=[dict(id='piece_mid_built', cites='deliverable:1', type='boolean', claim='`piece.mid` exists', evidence_needed='parse it'),
                              dict(id='piece_mid_retaken', cites='deliverable:1', type='boolean', claim='`piece.mid` parses again', evidence_needed='parse it')],
                    tasks=[dict(id='build_piece_mid', unknowns=['piece_mid_built'], bucket='low', title='build'),
                           dict(id='retake_piece', unknowns=['piece_mid_retaken'], bucket='low', title='again')])
    accepted, _ = controller.guard(decision, observation, gym)
    controller.apply(CONFIG, gym, accepted)
    tasks = {t['id']: t for t in terra(gym, 'route', 'status')['tasks']}
    first = worker.open_task_map(CONFIG, gym, tasks['build_piece_mid'])
    assert first == 't_build_piece_mid'
    again = worker.open_task_map(CONFIG, gym, tasks['retake_piece'], map_id=first)
    assert again == first
    sessions = gym/layout.dirname(gym)/'map'/'sessions'
    assert (sessions/first/'unknowns'/'piece_mid_retaken.json').exists() and not (sessions/'t_retake_piece').exists()


def test_a_reading_newer_than_its_artifact_is_not_reopened_for_a_duplicate(gym: Path) -> None:
    """The reopen-on-duplicate rule is for a repaired artifact. When the known's last run postdates every file
    the cited entry names, the reading is current: the duplicate is dropped and a task naming only it is not
    routed (changing-meter reopened a known re-taken minutes earlier, right after the re-take landed)."""
    (gym/'piece.mid').write_bytes(b'MThd')
    observation = controller.observe(CONFIG, gym)
    first = dict(unknowns=[dict(id='piece_mid_valid', cites='deliverable:1', type='boolean', source='piece.mid',
                                claim='piece.mid is a standard MIDI file with program 0', evidence_needed='parse')],
                 tasks=[dict(id='validate_piece', unknowns=['piece_mid_valid'], bucket='low', title='validate')])
    accepted, refusals = controller.guard(first, observation, gym)
    assert not refusals, refusals
    controller.apply(CONFIG, gym, accepted)
    terra(gym, 'route', 'cancel', 'validate_piece', '--reason', 'done by hand in the test')
    path = gym/'.terra'/'map'/'unknowns'/'piece_mid_valid.json'
    doc = json.loads(path.read_text()); doc['status'] = 'resolved'; path.write_text(json.dumps(doc))
    observation = controller.observe(CONFIG, gym)
    observation['knowns'].append(dict(id='piece_mid_valid', type='boolean', rate=1.0,
                                      stats=dict(by_run=[dict(run_id='20990101T000000Z_piece_mid_valid_probe_abc123')])))
    again = dict(unknowns=[dict(id='piece_mid_valid_again', cites='deliverable:1', type='boolean', source='piece.mid',
                                claim='piece.mid is a standard MIDI file with program 0', evidence_needed='parse again')],
                 tasks=[dict(id='revalidate_piece', unknowns=['piece_mid_valid_again'], bucket='low', title='again')])
    accepted, refusals = controller.guard(again, observation, gym)
    assert not refusals, refusals
    assert accepted['unknowns'] == [] and accepted['reopen_unknowns'] == [] and accepted['tasks'] == []
    assert json.loads(path.read_text())['status'] == 'resolved'


def test_a_cancel_frees_its_unknown_for_a_task_in_the_same_decision(gym: Path) -> None:
    """The controller replaces a blocked validation with the repair it calls for: cancel and route in one reply.
    Refusing the repair ("already has an open task") stalled syncopation's controller (2026-09-21)."""
    observation = controller.observe(CONFIG, gym)
    first = dict(unknowns=[dict(id='piece_mid_built', cites='deliverable:1', type='boolean', creates='piece.mid',
                                claim='piece.mid exists as eight bars', evidence_needed='parse it')],
                 tasks=[dict(id='validate_piece', unknowns=['piece_mid_built'], bucket='low', title='validate')])
    accepted, refusals = controller.guard(first, observation, gym)
    assert not refusals, refusals
    controller.apply(CONFIG, gym, accepted)
    terra(gym, 'route', 'block', 'validate_piece', '--reason', 'the piece is 8.5 bars; the artifact must change')
    observation = controller.observe(CONFIG, gym)
    again = dict(cancel=[dict(task='validate_piece', why='replaced by the repair')],
                 tasks=[dict(id='rebuild_piece_eight_bars', unknowns=['piece_mid_built'], bucket='low', title='rebuild to eight bars')])
    accepted, refusals = controller.guard(again, observation, gym)
    assert not refusals, refusals
    assert [t['id'] for t in accepted['tasks']] == ['rebuild_piece_eight_bars'] and accepted['cancel'] == [dict(task='validate_piece', why='replaced by the repair')]
    applied = controller.apply(CONFIG, gym, accepted)
    assert applied['cancel'] == ['validate_piece'] and 'rebuild_piece_eight_bars' in applied['tasks']
    status = {t['id']: t['status'] for t in terra(gym, 'route', 'status')['tasks']}
    assert status == {'validate_piece': 'cancelled', 'rebuild_piece_eight_bars': 'ready'}


def test_a_task_names_the_walk_its_worker_opens(gym: Path, tmp_path: Path, monkeypatch) -> None:
    """The controller plans by method: a task carries the procedure to walk (and where a long method's next walk
    starts); the worker's assignment opens it instead of searching."""
    from mizpah import worker
    store = tmp_path/'procedures'
    store.mkdir()
    (store/'compose-piano.json').write_text(json.dumps(dict(id='compose-piano', title='Compose', description='d', tags=['midi'],
                                                             steps=[dict(id='s1', title='Notes', do='write')])))
    observation = controller.observe(CONFIG, gym) | dict(methods=[dict(id='compose-piano', title='Compose', steps=1, walks=1, procedures=1)],
                                                         playbook_store=str(store))
    decision = dict(unknowns=[dict(id='piece_mid_built', cites='deliverable:1', type='boolean', creates='piece.mid',
                                   claim='piece.mid exists', evidence_needed='parse it'),
                              dict(id='piece_mid_parses', cites='deliverable:1', type='boolean',
                                   claim='piece.mid parses with mido', evidence_needed='parse it')],
                    tasks=[dict(id='build_piece_mid', unknowns=['piece_mid_built'], bucket='low', title='build', walk='compose-piano', walk_from=0),
                           dict(id='other', unknowns=['piece_mid_parses'], bucket='low', title='x', walk='no-such-procedure')])
    accepted, refusals = controller.guard(decision, observation, gym)
    by = {t['id']: t for t in accepted['tasks']}
    assert by['build_piece_mid']['walk'] == 'compose-piano'
    assert any('is not a procedure in the playbook' in c for c in accepted['cautions'])
    controller.apply(CONFIG, gym, accepted)
    task = next(t for t in terra(gym, 'route', 'status')['tasks'] if t['id'] == 'build_piece_mid')
    assert 'walk:compose-piano@0' in task['acceptance'] and worker.assigned_walk(task) == ('compose-piano', 0)
    # The sitrep no longer carries the methods section (the controller does not use the playbook; whether it names
    # a walk is undecided) — the guard still accepts a walk when one is named.


def test_a_next_walk_without_a_first_starts_from_zero(gym: Path, tmp_path: Path) -> None:
    store = tmp_path/'procedures'
    store.mkdir()
    (store/'render.json').write_text(json.dumps(dict(id='render', title='Render', description='d', tags=['midi'], steps=[dict(id='s1', title='A', do='a')])))
    observation = controller.observe(CONFIG, gym) | dict(methods=[dict(id='render', title='Render', steps=81, walks=2, procedures=12)], playbook_store=str(store))
    decision = dict(unknowns=[dict(id='piece_mp3_built', cites='deliverable:2', type='boolean', creates='piece.mp3', claim='piece.mp3 exists', evidence_needed='ffprobe')],
                    tasks=[dict(id='render_part2', unknowns=['piece_mp3_built'], bucket='low', title='render, second walk', walk='render', walk_from=50)])
    accepted, _ = controller.guard(decision, observation, gym)
    assert accepted['tasks'][0]['walk_from'] == 0 and any('no earlier walk' in c for c in accepted['cautions'])
    decision['tasks'] = [dict(id='render_part1', unknowns=['piece_mp3_built'], bucket='low', title='render', walk='render', walk_from=0),
                         dict(id='render_part2', unknowns=['piece_mp3_built'], bucket='low', title='render, second walk', walk='render', walk_from=50,
                              deps=['render_part1'], continue_from='render_part1')]
    accepted, _ = controller.guard(decision, observation, gym)
    by = {t['id']: t.get('walk_from') for t in accepted['tasks']}
    assert by.get('render_part1') == 0


def test_the_controller_reads_with_its_verbs_then_decides(gym: Path, tmp_path: Path) -> None:
    """decide() runs the read-only verbs the model asks for, appends their results, and ends on the reply that
    carries no tool call; every call is kept for the journal. A verb that writes is refused."""
    (gym/'notes.txt').write_text('line one\nline two\n')
    replies = [
        dict(usage=dict(prompt_tokens=1, completion_tokens=1), choices=[dict(finish_reason='tool_calls', message=dict(role='assistant', content='', tool_calls=[
            dict(id='c1', type='function', function=dict(name='read', arguments=json.dumps(dict(path='notes.txt')))),
            dict(id='c2', type='function', function=dict(name='terra', arguments=json.dumps(dict(args='route cancel x --reason y')))),
            dict(id='c3', type='function', function=dict(name='terra', arguments=json.dumps(dict(args='route status')))),
        ]))]),
        dict(usage=dict(prompt_tokens=1, completion_tokens=1), choices=[dict(finish_reason='stop', message=dict(role='assistant', content='{"unknowns": [], "tasks": [], "memory": "read the notes; nothing owed", "why": "x"}'))]),
    ]
    seen: list[dict] = []

    class Client:
        def complete(self, payload, purpose):
            seen.append(payload)
            return replies.pop(0)

    decision, raw = controller.decide(Client(), dict(CONFIG, controller=dict(generation={})), 'sys', 'user', project=gym, root=tmp_path)
    assert decision['memory'] == 'read the notes; nothing owed'
    calls = controller._last_tools[0]
    assert [c['name'] for c in calls] == ['read', 'terra', 'terra'] and calls[1]['refused'] and not calls[2]['refused']
    tool_messages = [m for m in seen[1]['messages'] if m.get('role') == 'tool']
    assert '     1\tline one' in tool_messages[0]['content'] and tool_messages[1]['content'].startswith('refused')
    assert 'tools' in seen[0]



def test_past_work_orders_are_listed_by_what_they_left_on_disk(gym: Path, tmp_path: Path) -> None:
    """The sitrep names past work orders and what is on disk from them — probes, walks, widgets — for a fresh
    worker to find; nothing about continuing their windows (continue_from is retired)."""
    root = tmp_path/'sess'
    old = root/'tasks'/'build_piece_mid'
    (old/'events').mkdir(parents=True)
    (old/'events'/'session.jsonl').write_text(json.dumps(dict(event_type='worker_turn', payload=dict(response=dict(tool_calls=[
        dict(id='c', function=dict(name='bash', arguments=json.dumps(dict(command='playbook open midi-piano-voicing-melody --for x; python cg/data_music_x_python/src/x.py'))))]))))+'\n')
    (old/'task.json').write_text(json.dumps(dict(task=dict(id='build_piece_mid'), unknowns=[dict(id='piece_mid_built')], map='t_build_piece_mid')))
    (old/'result.json').write_text(json.dumps(dict(verdict='complete', turns=30)))
    (old/'state.sqlite3').write_bytes(b'')
    spaces = controller.task_workspaces(root)
    assert spaces[0]['task'] == 'build_piece_mid' and spaces[0]['walks'] == ['midi-piano-voicing-melody'] and spaces[0]['widgets'] == ['data_music_x_python']
    observation = controller.observe(CONFIG, gym) | dict(workspaces=spaces)
    text = controller.render_observation(observation, 'route')
    # The map and past work orders are read through the tools (`result <task>`), not printed.
    assert 'continue_from' not in text
    accepted, refusals = controller.guard(dict(unknowns=[], tasks=[dict(id='t', unknowns=['piece_mid_built'], bucket='low', title='x', continue_from='build_piece_mid')]), observation, gym)
    assert 'continue_from' not in json.dumps(accepted)


def test_a_formula_composes_readings_and_a_transitional_unknown_cites_the_one_it_serves(gym: Path) -> None:
    """The compose move: a need's known is a formula over knowns on the map. A transitional unknown cites the
    unknown it must be resolved before, not a brief entry."""
    observation = controller.observe(CONFIG, gym)
    decision = dict(unknowns=[
        dict(id='grid_lock', cites='need:1', type='number', source='piece.mid', claim='beat grid lock of piece.mid', evidence_needed='cluster onsets'),
        dict(id='corpus_parses', cites='unknown:grid_lock', type='boolean', source='corpus/', claim='the reference corpus parses', evidence_needed='parse it'),
        dict(id='composition_level', cites='need:1', type='formula', expression='grid_lock < 0.7', vars={'grid_lock': 'known:grid_lock'},
             claim='the composition is at repertoire level', evidence_needed='composed from the readings'),
        dict(id='bad_formula', cites='need:1', type='formula', claim='x', evidence_needed='y'),
    ], tasks=[dict(id='measure_grid', unknowns=['grid_lock', 'corpus_parses'], bucket='low', title='read piece.mid'),
              dict(id='compose_level', unknowns=['composition_level'], bucket='low', title='compose the need', deps=['measure_grid'])])
    accepted, refusals = controller.guard(decision, observation, gym)
    ids = [u['id'] for u in accepted['unknowns']]
    assert ids == ['grid_lock', 'corpus_parses', 'composition_level'], refusals
    assert any('bad_formula' in r and 'expression' in r for r in refusals)
    controller.apply(CONFIG, gym, accepted)
    # Terra refuses to *show* a formula whose variables are not yet knowns (the policy says mint it when they
    # exist); the record is on the map with its expression and its variable bound to the known.
    rec = json.loads((gym/'.terra'/'map'/'unknowns'/'composition_level.json').read_text())
    assert rec.get('type') == 'formula' and rec.get('expression') == 'grid_lock < 0.7' and rec['vars']['grid_lock'] == dict(known_id='grid_lock')


def test_a_task_maps_readings_are_on_the_sitrep(tmp_path: Path, gym: Path) -> None:
    """Every quantity a worker's probes read on its task map is listed with the past work order, whether or not an
    unknown asked for it — the controller composes from these and links a run before minting a probe."""
    state = tmp_path/'.mizpah'
    root = state/'sessions'/'s1'
    old = root/'tasks'/'compose'
    (old/'events').mkdir(parents=True)
    (old/'events'/'session.jsonl').write_text('')
    (old/'task.json').write_text(json.dumps(dict(task=dict(id='compose'), unknowns=[dict(id='piece_mid_built')], map='t_compose')))
    (old/'state.sqlite3').write_bytes(b'')
    for i, (voided, val) in enumerate([(False, 0.407), (False, 0.41), (True, 0.9)]):
        r = state/'map'/'sessions'/'t_compose'/'runs'/f'r{i}'
        r.mkdir(parents=True)
        (r/'meta.json').write_text(json.dumps(dict(id=f'r{i}', probe_id='performance', status='ok', voided=voided,
                                                  measures=[dict(quantity='grid_lock', value=val), dict(quantity='piece_mid_built', value=True)])))
    spaces = controller.task_workspaces(root)
    readings = {r['quantity']: r for r in spaces[0]['readings']}
    assert readings['grid_lock'] == dict(quantity='grid_lock', probe='performance', runs=2, value=0.41)
    assert readings['piece_mid_built']['runs'] == 2
