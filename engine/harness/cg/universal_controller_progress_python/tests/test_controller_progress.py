import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.controller_progress import ControllerProgress, ReviewPolicy


def state():
    return ControllerProgress(ReviewPolicy(20, 10, 1, 2000, 500, 20000), '# Project\n- [x] Explore: verified the public format.\n- [ ] Implement\n- [ ] Validate\n')


def decision(correction='None'):
    return json.dumps(dict(correction=correction, evidence='' if correction == 'None' else 'Observed result.',
        warrant='' if correction == 'None' else 'Required outcome.'))


def test_periodic_causal_window_is_independent_of_durable_state():
    controller = state()
    reviews = []
    for turn in range(1, 43):
        controller.observe(dict(turn=turn, input='request', response='result'))
        if controller.due():
            reviews.append(turn)
            controller.accept(decision('Keep the public format.' if turn == 1 else 'None'))
    assert reviews == [1, 21, 41]
    restored = ControllerProgress.from_state(controller.export_state())
    assert [item['turn'] for item in restored.recent_turns] == list(range(33, 43))
    assert '[x] Explore: verified the public format.' in restored.document
    assert restored.guidance['correction'] == 'Keep the public format.'
    envelope = json.loads(restored.envelope('Fixed goal', 'Next input', boundary='periodic'))
    assert envelope['project_document']['text'] == restored.document
    assert envelope['held_guidance']['correction'] == 'Keep the public format.'


def test_hold_replace_and_explicit_unwind():
    controller = state()
    for turn, correction in enumerate(['Keep the format.', 'None', ''], 1):
        controller.observe(dict(turn=turn))
        controller.accept(decision(correction))
        assert bool(controller.applied_guidance()) == (turn < 3)
    assert '- [ ] Validate' in controller.document
    with pytest.raises(ValueError, match='already reviewed'):
        controller.accept(decision())


@pytest.mark.parametrize('raw', ['{}', 'null', '{"correction":"None","correction":"x"}',
    '{"bad":NaN}', decision(' '), decision('x'*501)])
def test_invalid_review_never_changes_state(raw):
    controller = state()
    controller.observe(dict(turn=1))
    before = controller.export_state()
    with pytest.raises(ValueError):
        controller.accept(raw)
    assert controller.export_state() == before


def test_bad_evidence_and_corrupt_resume_fail_explicitly():
    controller = state()
    with pytest.raises(ValueError):
        controller.observe(dict(turn=2))
    controller.observe(dict(turn=1))
    with pytest.raises(ValueError, match='no evidence was truncated'):
        controller.envelope('goal', 'x'*20001, boundary='periodic')
    saved = controller.export_state()
    saved['recent_turns'] = []
    with pytest.raises(ValueError):
        ControllerProgress.from_state(saved)


def test_schema_matches_contract_and_policy_is_explicit():
    assert set(state().response_schema()['required']) == {'correction', 'evidence', 'warrant'}
    with pytest.raises(ValueError):
        ReviewPolicy(0, 10, 1, 2000, 500, 20000)


def test_incremental_project_edits_preserve_completed_work_and_future_phases():
    controller = state()
    controller.edit_document(0, '- [ ] Implement', '- [x] Implement: tests pass.\n- [ ] Package')
    controller.edit_document(1, '- [ ] Validate', '- [ ] Validate\n  - [ ] Exercise the empty case')
    restored = ControllerProgress.from_state(controller.export_state())
    assert restored.document_revision == 2
    assert '[x] Explore: verified the public format.' in restored.document
    assert '[x] Implement: tests pass.' in restored.document
    assert '- [ ] Package' in restored.document
    pieces, offset = [], 0
    while True:
        page = restored.read_document(offset, 17)
        pieces.append(page['text'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert ''.join(pieces) == restored.document


@pytest.mark.parametrize('revision,old,new', [
    (1, 'Implement', 'Build'), (0, 'missing', 'Build'), (0, '[ ]', '[x]'),
    (0, '', 'Overwrite'), (0, 'Implement', 'x'*2001),
])
def test_bad_document_edits_leave_state_unchanged(revision, old, new):
    controller = state()
    before = controller.export_state()
    with pytest.raises(ValueError):
        controller.edit_document(revision, old, new)
    assert controller.export_state() == before


def test_document_initialization_and_required_project_memory():
    controller = ControllerProgress(state().policy)
    controller.observe(dict(turn=1))
    with pytest.raises(ValueError, match='Create the project document'):
        controller.accept(decision())
    controller.edit_document(0, '', '# Project\n- [ ] Deliver the requested result')
    controller.accept(decision())
    with pytest.raises(ValueError, match='cannot be erased'):
        controller.edit_document(1, controller.document, '')
    legacy = controller.export_state()
    del legacy['schema']
    with pytest.raises(ValueError, match='Legacy controller state'):
        ControllerProgress.from_state(legacy)


def test_range_edits_target_repeated_text_and_survive_restore():
    controller = ControllerProgress(state().policy, 'Done: café\nNext: repeat\nLater: repeat\n')
    page = controller.read_document(11, 12)
    assert page['offset'] == 11 and page['end_offset'] == 23
    assert page['text'] == controller.document[11:23]
    start = controller.document.index('repeat')
    result = controller.edit_range(page['revision'], start, start+6, 'verify')
    assert result['start_offset'] == start and result['new_end_offset'] == start+6
    assert controller.document == 'Done: café\nNext: verify\nLater: repeat\n'
    restored = ControllerProgress.from_state(controller.export_state())
    restored.edit_range(1, len(restored.document), len(restored.document), 'Evidence: retained\n')
    assert restored.document.endswith('Later: repeat\nEvidence: retained\n')
    before = restored.export_state()
    with pytest.raises(ValueError, match='current revision'):
        restored.edit_range(1, start, start+6, 'stale')
    assert restored.export_state() == before


@pytest.mark.parametrize('start,end,text', [(-1, 2, 'x'), (5, 2, 'x'), (0, 10000, 'x'),
    (True, 2, 'x'), (0, 2, None), (0, 2, 'x'*2001)])
def test_invalid_range_edits_are_atomic(start, end, text):
    controller = state()
    before = controller.export_state()
    with pytest.raises(ValueError):
        controller.edit_range(0, start, end, text)
    assert controller.export_state() == before


def test_execution_technique_in_correction_is_rejected_and_guidance_unchanged():
    from src.controller_progress import execution_term_found
    terms = ('bash', 'edit', 'heredoc', 'cat <<', 'size limit', 'smaller commands')
    controller = ControllerProgress(ReviewPolicy(20, 10, 1, 2000, 500, 20000, execution_terms=terms), '# Project\n- [ ] Deliver\n')
    turn = 0
    def advance():
        nonlocal turn
        turn += 1; controller.observe(dict(turn=turn, input='request', response='result'))
    advance()
    controller.accept(decision('Deliver the report the reference requires.'))
    assert controller.guidance['correction'] == 'Deliver the report the reference requires.'
    for bad in ['Break REPORT.md into smaller commands.', 'Use edit to change one function.', 'Write it with cat << EOF.',
                'Avoid the size limit.']:
        advance()
        with pytest.raises(ValueError) as captured:
            controller.accept(decision(bad))
        assert 'execution technique' in str(captured.value)
    assert controller.guidance['correction'] == 'Deliver the report the reference requires.'
    # whole-word matching: 'editor' and 'bashful' are not the tool names
    assert execution_term_found('The editor and the bashful reviewer', terms) is None
    assert execution_term_found('then EDIT it', terms) == 'edit'
    # withdrawing guidance is never blocked by the guard
    advance()
    controller.accept(decision(''))
    assert controller.guidance['correction'] == ''


def test_execution_terms_default_empty_and_validated():
    assert ReviewPolicy(20, 10, 1, 2000, 500, 20000).execution_terms == ()
    with pytest.raises(ValueError):
        ReviewPolicy(20, 10, 1, 2000, 500, 20000, execution_terms=('', 'bash'))


def test_an_empty_correction_withdraws_without_a_warrant_and_holds_when_nothing_is_held():
    controller = state()
    controller.observe(dict(turn=1))
    # Nothing held: "" is a hold (the reviewer looked and found nothing), not a refusal.
    assert controller.accept(decision(''))['operation'] == 'hold'
    controller.observe(dict(turn=2))
    controller.accept(json.dumps(dict(correction='Fix it.', evidence='Observed.', warrant='Required.')))
    controller.observe(dict(turn=3))
    # Held: "" withdraws it, with evidence allowed and no warrant (nothing is violated any more).
    assert controller.accept(json.dumps(dict(correction='', evidence='the file now reads piece.mp3', warrant='')))['operation'] == 'clear'
    assert controller.guidance['correction'] == ''
