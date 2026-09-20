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


def test_a_report_with_no_anchor_is_still_refused(gym: Path) -> None:
    observation = controller.observe(CONFIG, gym)
    decision = dict(unknowns=[
        dict(id='summary_built', cites='deliverable:3', type='boolean', creates='summary.md',
             claim='summary.md is written', evidence_needed='read it'),
    ], tasks=[dict(id='write_summary', unknowns=['summary_built'], bucket='low', title='summary')])
    accepted, refusals = controller.guard(decision, observation, gym)
    assert not accepted['unknowns'] and any('summary_built' in r and 'names no known' in r for r in refusals)
