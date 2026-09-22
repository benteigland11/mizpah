"""Piano: one benchmark brief and a ladder of practice gyms, all on the `piano` base.

Usage: python -m fixtures.piano make benchmark [attempt]|melody_bass|voice_leading|pedal_dynamics|rubato_phrase|nocturne_lh|voicing_touch|same_piece_two_ways|whole_piece|emotion_arc|rhythm_and_feeling
       python -m fixtures.piano score <project_dir>            readings off the MIDI the gym produced

Practice gyms are I/O contracts: a mission, piece.mid out, three needs in a pianist's words, no thresholds
and no commands; the method is harvested by the harness, never written up. The controller decomposes them into readings and the worker picks the numbers; a
need written as an acceptance test cost a task per clause and handed the worker numbers it never got to learn
(Block A, 2026-09-20). The benchmark keeps its pinned text until its series ends.

The regime: the benchmark ("compose and perform a romantic piano piece, 90 s") runs cold, then again after each
block of practice, its text never edited; the curve is turns, stops and the readings. Two blocks: the mechanics
of playing (pedal and dynamics, rubato, voicing and touch), then composing (a melody over a left-hand figure,
voice leading, a nocturne left hand). The procedures each gym mints are what the next benchmark finds in
`playbook search`.

Every gym is a `mizpah init --gym --base piano` project: the base (fluidsynth, the GM soundfont, LilyPond,
a venv with mido/music21/pretty_midi) is bound read-only; everything the worker writes lands in the gym.
Nothing here writes a note of music: the briefs say what is owed and what can be read off the result.

The scorer reads the same things a worker's probes should: duration, velocity span, pedal changes per bar,
tempo variation, hand spans, the final chord. It is the curve's ruler, kept beside the gym, never inside it.
"""
from __future__ import annotations

import json
from typing import Any
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from mizpah import init as init_module  # noqa: E402
from mizpah import layout  # noqa: E402

TERRA = Path(__file__).resolve().parents[3]/'.venv'/'bin'/'terra'
BASE = 'piano'

RENDER = ('The piece is rendered: `fluidsynth -ni "$SOUNDFONT" piece.mid -F piece.wav` then ffmpeg to `piece.mp3`, and '
          'engraved to `piece.pdf` with LilyPond; the audio duration is within 2 s of the MIDI duration.')
PLAYABLE = ('Playable by two hands: no simultaneous notes in one hand span more than 14 semitones, at most 5 notes '
            'sound at once per hand, the left hand stays below C5 and the right above C3 for at least 90% of onsets.')
PERFORMED = ('Performed, not quantised: note velocities span at least 40 (of 127) over the piece and no bar has every '
             'onset at one velocity; the sustain pedal (CC64) changes at least once per bar on average and is never '
             'held down across more than two bars.')
RUBATO = ('Rubato: the performed tempo is not constant — a tempo map or onset timing deviates from the grid, with a '
          'ritardando of at least 10% over the final two bars.')
NON_GOALS = ['No quotation of an existing piece: melodies are the worker\'s own, not transcriptions.',
             'Piano only: one instrument, program 0, no percussion or other programs.',
             'No `random` note generation presented as composition: every pitch and rhythm is a decision.',
             'No notes, plan or write-up files — the piece is the deliverable.']


def terra(project: Path, *args: str) -> None:
    env = dict(os.environ, TERRA_DIRNAME=layout.STATE_DIRNAME)
    proc = subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit('terra '+' '.join(args)+' failed: '+(proc.stderr or proc.stdout)[-600:])


def gym(title: str, mission: str, needs: list[str], deliverables: list[str], budget: int, *,
        non_goals: list[str] = ()) -> Path:
    folder = init_module.new_gym(title)
    init_module.init(folder, title=title, mission=mission, terra=str(TERRA), base=BASE)
    args = ['brief', 'set', '--budget-points', str(budget)]   # a draft until `issue()` makes it active with its crew
    for need in needs:
        args += ['--need', need]
    for d in deliverables:
        args += ['--deliverable', d]
    for n in non_goals:
        args += ['--non-goal', n]
    terra(folder, *args)
    return folder


def benchmark() -> Path:
    return gym(
        'Romantic piano piece',
        'Compose and perform a romantic solo piano piece of about 90 seconds, as a MIDI performance a listener '
        'would take for a person playing, rendered to audio and engraved as a score.',
        needs=[
            'A solo piano piece in a romantic idiom (singing melody over a moving accompaniment, expressive '
            'harmony with at least two secondary dominants or borrowed chords), between 80 and 100 seconds at its '
            'performed tempo, as a standard MIDI file `piece.mid` with program 0.',
            'Form: phrases of 4 or 8 bars, at least three distinct phrases, and the opening material returns '
            'before the end (A B A or a close relative); the return is a reading, not a claim.',
            'Tonal: a stated home key; the piece ends on a tonic chord after a cadence; at least 85% of note '
            'onsets belong to the home key or to the secondary dominants named in notes.md.',
            PERFORMED, RUBATO, PLAYABLE, RENDER,
        ],
        deliverables=['piece.mid', 'piece.mp3', 'piece.pdf',
                      'notes.md: key, form with bar numbers, tempo plan, what the performance shaping does and why'],
        budget=60, non_goals=NON_GOALS)


def melody_bass() -> Path:
    return gym(
        'Melody over a moving bass',
        'Write and play a short piano tune over a left hand that keeps moving: the tune is what a listener remembers, '
        'the bass is what carries it — and show the two are one piece.',
        needs=[
            'It has a tune: a line you could hum after one hearing, that goes somewhere and ends as if it meant to.',
            'The left hand moves under it in a figure that fits the harmony and never gets in the tune\'s way.',
            'The relation is read, not asserted: readings over the finished file separate the tune from the bass and '
            'show the tune leads and the bass agrees with it — and would fail a piece whose bass contradicts its tune.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


def voice_leading() -> Path:
    return gym(
        'Four voices a teacher would pass',
        'Write and play a short chorale for piano in four voices, the kind a harmony teacher would hand back with '
        'nothing marked — and show why it passes.',
        needs=[
            'Four voices throughout, each singable on its own, in a minor key with a cadence that closes.',
            'The voices move the way a harmony teacher expects: each finds the nearest good note, and the motions a '
            'teacher marks are absent.',
            'It passes on the file, not on the plan: readings follow every voice through every chord and name each '
            'motion a teacher would mark — and would fail a chorale that has one.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


def pedal_dynamics() -> Path:
    return gym(
        'Pedal and dynamics',
        'Play a short chord progression so it sounds like a pianist\'s hands and feet, not a sequencer: the pedal and '
        'the dynamics carry its shape — and show that shape can be read off the file.',
        needs=[
            'Pedalled the way a pianist pedals: the sound held through each harmony and cleared at every change, never '
            'smeared across one.',
            'Shaped in loudness: it grows and recedes the way a phrase breathes, and the close is its softest and '
            'slowest moment.',
            'The playing is read, not asserted: readings over the finished file say where the pedal holds and clears '
            'against the harmony and how the loudness moves — and would fail a version pedalled through the changes or '
            'played at one level.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


def rubato_phrase() -> Path:
    return gym(
        'Rubato on a singing phrase',
        'Write a singing phrase with a simple accompaniment and play it with rubato: time bends where the line leans, '
        'and the line is shaped by touch — and show the bend can be read off the file.',
        needs=[
            'Rubato a listener would call musical: time stretches where the phrase peaks and slows into the close, and '
            'the pulse is otherwise kept.',
            'The line sings over its accompaniment: shaped by touch, fullest where it leans, never covered by what is '
            'under it.',
            'The bend is read, not asserted: readings over the finished file find where time stretches and where the '
            'line peaks — and would fail a version played strictly in time or at one level.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


def nocturne_lh() -> Path:
    return gym(
        'A nocturne texture',
        'Write and play a short passage in a nocturne texture: a wide, rolling left hand under a singing right hand — '
        'and show the texture can be read off the file.',
        needs=[
            'The left hand is wide and rolls under the melody, carrying the harmony and the pulse.',
            'The melody floats above it in long lines, with an ornament where a singer would add one.',
            'The texture is read, not asserted: readings over the finished file say how wide the left hand ranges, where '
            'the melody sits above it and that the pedal follows the harmony — and would fail a texture where the '
            'hands collide.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


def voicing_touch() -> Path:
    return gym(
        'Voicing and touch',
        'Play a chord sequence so the melody inside it sings: the top voice heard above the rest, joined where it '
        'should be and placed where it should be — and show the voicing can be read off the file.',
        needs=[
            'Voiced so the melody sings: the top note of every chord heard above the others, the lower hand beneath.',
            'Touched like a pianist: the melody joined as one line, the chords beneath it placed rather than struck all '
            'alike, the pedal changing with the harmony.',
            'The voicing is read, not asserted: readings over the finished file separate the melody from the chords and '
            'say how far above them it sits and how it is joined — and would fail a version where every note is struck '
            'the same.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


def same_piece_two_ways() -> Path:
    return gym(
        'The same piece, written and played',
        'Write a short piano piece twice: as the written grid a score would show, and as a performance of it a '
        'pianist would give — and show they are the same piece.',
        needs=[
            'The written version is a clean grid: every onset and length on the beat grid of a stated meter, one '
            'key, a proper ending.',
            'The performance is that piece played: the same pitches in the same order, leaned in time and touch the '
            'way a pianist plays, with pedal.',
            'The relation is shown, not claimed: a reading pairs every performed note to its written note and says '
            'how far each leans — no note unpaired, none invented.',
        ],
        deliverables=['written.mid: the grid', 'piece.mid: the performance'],
        budget=120, non_goals=NON_GOALS)


def whole_piece() -> Path:
    return gym(
        'A whole piece, not a phrase',
        'Compose a complete short piano piece — a piece with a beginning, a middle that is somewhere else, and an '
        'ending that has arrived — and show it holds together as one thing.',
        needs=[
            'It is whole: an opening that states something, a middle that goes elsewhere, a return that is changed '
            'by what happened, and an ending that sounds arrived-at rather than stopped.',
            'It travels: the harmony leaves home and comes back, and where it goes is far enough that a listener '
            'would hear the journey.',
            'Wholeness is shown, not claimed: readings over the finished piece say where the sections are, how the '
            'return differs from the opening, and where home was left and regained — and the same readings would '
            'fail a piece that only repeats itself.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


def emotion_arc() -> Path:
    return gym(
        'The story of a song',
        'Compose a short piano piece that carries one feeling through a shape — states it, disturbs it, and lets it '
        'resolve late — and work out how that shape can be read off the file at all.',
        needs=[
            'It has one story: a feeling stated at the start, something that unsettles it, and a resolution that '
            'comes late rather than early.',
            'It has one high point, not four: a single place a listener would call the peak, with everything else '
            'subordinate to it, and stillness somewhere before it or after it.',
            'The shape is read, not asserted: an instrument reports the piece\'s arc over time — where intensity '
            'rises, where it empties, where it peaks — and separates this piece from a flat one and from one that '
            'peaks four times.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


def rhythm_and_feeling() -> Path:
    return gym(
        'Rhythm carries the story',
        'Compose a short piano piece where the rhythm is what moves the feeling — the pulse tightens as it rises '
        'and loosens as it lets go — and show the rhythm and the arc are the same story.',
        needs=[
            'The rhythm is a character, not a grid: the figure that opens the piece changes as the piece does — '
            'denser where it presses, wider where it breathes.',
            'The pulse and the feeling move together: where the piece rises the notes come closer, where it '
            'settles they come further apart, and the two curves are read off the same file.',
            'The agreement is shown, not claimed: a reading puts the rhythmic density and the intensity arc side '
            'by side and says how closely they follow each other — and would say so honestly for a piece where '
            'they do not.',
        ],
        deliverables=['piece.mid'],
        budget=120, non_goals=NON_GOALS)


MAKERS = dict(benchmark=benchmark, melody_bass=melody_bass, voice_leading=voice_leading,
              pedal_dynamics=pedal_dynamics, rubato_phrase=rubato_phrase, nocturne_lh=nocturne_lh,
              voicing_touch=voicing_touch, same_piece_two_ways=same_piece_two_ways,
              whole_piece=whole_piece, emotion_arc=emotion_arc, rhythm_and_feeling=rhythm_and_feeling)

PINNED = Path.home()/'mizpah-runs'/'piano'/'benchmark.brief.json'


def benchmark_attempt(attempt: int) -> Path:
    """The benchmark from its pinned brief — the file attempt 1 wrote, byte for byte — never from this
    module's text again: an edit here must not move the ruler. The gym is named for the attempt."""
    pinned = json.loads(PINNED.read_text())
    folder = init_module.new_gym('Romantic piano piece, attempt '+str(attempt))
    init_module.init(folder, title=pinned['title'], mission=pinned['mission'], terra=str(TERRA), base=BASE)
    fresh = json.loads((folder/'.mizpah'/'brief.json').read_text())
    keep = {k: pinned[k] for k in pinned if k not in ('created_at', 'updated_at', 'history', 'proposals')}
    # Written as a draft: `issue()` makes it active, and Terra records the crew and the signature on that
    # transition (a brief copied in already active was issued with no crew on it).
    (folder/'.mizpah'/'brief.json').write_text(json.dumps({**fresh, **keep, 'proposals': [], 'status': 'draft'}, indent=1)+'\n')
    mark_benchmark(folder)
    return folder


def mark_benchmark(folder: Path) -> None:
    """A benchmark is measured, never remembered: the brief library neither records it nor is consulted for it.
    What it may use is the library proper — widgets and procedures — which is the point of the check."""
    path = folder/'.mizpah'/'config.json'
    pc = json.loads(path.read_text())
    pc['benchmark'] = True
    path.write_text(json.dumps(pc, indent=1)+'\n')


# ---------------------------------------------------------------- the ruler

def score(project: Path) -> dict:
    """Readings off `piece.mid` (or the newest .mid in the gym): the curve's numbers, computed the same way
    every time. `ok` is whether a piece exists at all; the rest are readings, not a grade."""
    import pretty_midi  # the base's venv has it; the scorer runs with it
    mids = sorted(project.glob('piece.mid')) or sorted(p for p in project.rglob('*.mid') if layout.STATE_DIRNAME not in p.parts)
    if not mids:
        return dict(ok=False, reason='no MIDI file')
    pm = pretty_midi.PrettyMIDI(str(mids[0]))
    notes = [n for i in pm.instruments if not i.is_drum for n in i.notes]
    if not notes:
        return dict(ok=False, reason='no notes')
    duration = pm.get_end_time()
    velocities = [n.velocity for n in notes]
    pedal = [c for i in pm.instruments for c in i.control_changes if c.number == 64]
    downs = [c for c in pedal if c.value >= 64]
    tempi = pm.get_tempo_changes()[1]
    # Bars at the file's first tempo (estimate_tempo needs onsets and refuses block chords, 54 notes in 9 bars).
    bars = max(1, duration/(4*60/max(float(tempi[0]) if len(tempi) else 120.0, 1)))
    # hand span: notes sounding together, split at middle C as a crude hand line
    onsets = {}
    for n in notes:
        onsets.setdefault(round(n.start, 2), []).append(n.pitch)
    spans = []
    for ps in onsets.values():
        for hand in ([p for p in ps if p < 60], [p for p in ps if p >= 60]):
            if len(hand) > 1:
                spans.append(max(hand)-min(hand))
    last = sorted(notes, key=lambda n: n.start)[-1]
    final = sorted({p % 12 for p in onsets[max(onsets)]}) if onsets else []
    # Pedal placement, not count: a hold that lasts through a harmony change is mud (attempt 1: 32 of 32 holds,
    # 187 of 254 bass changes smeared); a down that follows its onset within 50 ms is a clean change.
    cc = sorted((c.time, c.value) for c in pm.instruments[0].control_changes if c.number == 64) if pm.instruments else []
    holds, cur = [], None
    for t, v in cc:
        if v >= 64 and cur is None:
            cur = t
        elif v < 64 and cur is not None:
            holds.append((cur, t)); cur = None
    times = sorted(onsets)
    bass = [min(onsets[t]) for t in times]
    changes = [t for t, (a, b) in zip(times[1:], zip(bass, bass[1:])) if a % 12 != b % 12]
    pedal_place = dict(pedal_holds=len(holds),
                       holds_through_harmony_change=sum(1 for s, e in holds if any(s < c < e for c in changes)),
                       downs_within_50ms_of_onset=sum(1 for s, _ in holds if any(0 <= s-t <= 0.05 for t in times)))
    # Texture, the part of "variety" a listener hears: how long notes are held and how many start per second
    # (attempt 1 was busy and scattered, attempt 2 slower and chordal; the counts above said the opposite).
    texture = dict(mean_note_seconds=round(sum(n.end-n.start for n in notes)/len(notes), 2), onsets_per_second=round(len(onsets)/max(duration, 0.1), 2),
                   notes_at_once_mean=round(sum(len(v) for v in onsets.values())/max(len(onsets), 1), 1),
                   pitch_range=[min(n.pitch for n in notes), max(n.pitch for n in notes)])
    return dict(ok=True, file=str(mids[0].relative_to(project)), seconds=round(duration, 1), notes=len(notes), **texture, **pedal_place,
                velocity_min=min(velocities), velocity_max=max(velocities), velocity_span=max(velocities)-min(velocities),
                pedal_changes=len(pedal), pedal_downs_per_bar=round(len(downs)/bars, 2), tempo_changes=len(tempi),
                tempo_min=round(min(tempi), 1), tempo_max=round(max(tempi), 1),
                max_hand_span=max(spans) if spans else 0, final_pitch_classes=final, last_note=last.pitch,
                rendered=dict(mp3=(project/'piece.mp3').exists(), wav=(project/'piece.wav').exists(), pdf=(project/'piece.pdf').exists()))


def issue(folder: Path, engine_config: Path | None, signed_by: str = '') -> dict[str, Any]:
    """What the app does when the person starts a brief: the crew is pinned in the project config from the
    engine config's harness (`init.pin_crew`) and recorded on the brief with the signature (`issued_crew`),
    so the paper says what it was signed to run on. A gym made here without it ran unsigned and crewless."""
    crew: dict[str, Any] = {}
    if engine_config is not None:
        crew = init_module.pin_crew(folder, Path(engine_config).resolve())
    args = ['brief', 'set', '--status', 'active']
    if crew:
        args += ['--crew', json.dumps(crew)]
    if signed_by.strip():
        args += ['--signed-by', signed_by.strip()]
    terra(folder, *args)
    return crew


def main() -> None:
    args = list(sys.argv[1:])
    config = None
    signed_by = ''
    if '--config' in args:
        i = args.index('--config'); config = Path(args[i+1]); del args[i:i+2]
    if '--signed-by' in args:
        i = args.index('--signed-by'); signed_by = args[i+1]; del args[i:i+2]
    if len(args) >= 2 and args[0] == 'make' and args[1] == 'benchmark' and PINNED.exists():
        attempt = int(args[2]) if len(args) > 2 else 2
        folder = benchmark_attempt(attempt)
        crew = issue(folder, config, signed_by)
        print(json.dumps(dict(project=str(folder), base=BASE, attempt=attempt, pinned=str(PINNED), crew=crew), indent=1))
    elif len(args) >= 2 and args[0] == 'make':
        folder = MAKERS[args[1]]()
        crew = issue(folder, config, signed_by)
        print(json.dumps(dict(project=str(folder), base=BASE, crew=crew), indent=1))
    elif len(args) >= 2 and args[0] == 'score':
        print(json.dumps(score(Path(args[1]).resolve()), indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
