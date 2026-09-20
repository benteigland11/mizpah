"""Piano: one benchmark brief and a ladder of practice gyms, all on the `piano` base.

Usage: python -m fixtures.piano make benchmark [attempt]|melody_bass|voice_leading|pedal_dynamics|rubato_phrase|nocturne_lh|voicing_touch
       python -m fixtures.piano score <project_dir>            readings off the MIDI the gym produced

Practice gyms are I/O contracts: a mission, piece.mid + notes.md out, three needs in a pianist's words, no
thresholds and no commands. The controller decomposes them into readings and the worker picks the numbers; a
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
             'No `random` note generation presented as composition: every pitch and rhythm follows a stated plan in notes.md.']


def terra(project: Path, *args: str) -> None:
    env = dict(os.environ, TERRA_DIRNAME=layout.STATE_DIRNAME)
    proc = subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit('terra '+' '.join(args)+' failed: '+(proc.stderr or proc.stdout)[-600:])


def gym(title: str, mission: str, needs: list[str], deliverables: list[str], budget: int, *,
        non_goals: list[str] = ()) -> Path:
    folder = init_module.new_gym(title)
    init_module.init(folder, title=title, mission=mission, terra=str(TERRA), base=BASE)
    args = ['brief', 'set', '--status', 'active', '--budget-points', str(budget)]
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
        'Melody over a broken-chord bass',
        'Write and play a short tune for piano: the right hand singing over a left hand that keeps moving.',
        needs=[
            'It has a tune: a line you could hum, in one major key, that ends properly.',
            'The left hand moves under it in a broken-chord figure.',
            'The tune is louder than what is under it, and each phrase has a shape.',
        ],
        deliverables=['piece.mid', 'notes.md'],
        budget=120, non_goals=NON_GOALS)


def voice_leading() -> Path:
    return gym(
        'Four-voice chorale for piano',
        'Write and play a short chorale for piano: four voices, in a minor key, with voice leading a teacher would pass.',
        needs=[
            'Four voices throughout, two to a hand, in a minor key with a proper ending.',
            'The voices lead well: no parallel fifths or octaves, common tones kept, no voice crossing.',
            'The top voice carries, and the pedal follows the chord changes.',
        ],
        deliverables=['piece.mid', 'notes.md'],
        budget=120, non_goals=NON_GOALS)


def pedal_dynamics() -> Path:
    return gym(
        'Pedal and dynamics over a progression',
        'Perform a given progression (I vi IV V, twice, then I) as block chords with sustain pedal, a crescendo and '
        'diminuendo, and a closing ritardando, rendered.',
        needs=[
            '9 bars in 4/4, the progression I vi IV V I vi IV V I in a stated major key, one chord per bar as block '
            'chords in both hands, `piece.mid` program 0.',
            'Pedal: CC64 goes down within 50 ms after each chord onset and up within 50 ms before the next chord; '
            'never held across a chord change.',
            'Dynamics: velocities rise steadily over bars 1-4 (each bar louder than the last by at least 6), fall '
            'over bars 5-8 the same way, and the final bar is the softest.',
            'Timing: bars 1-8 at a steady tempo within 2%; the final bar at least 20% slower.',
        ],
        deliverables=['piece.mid', 'notes.md: the velocity and pedal plan per bar'],
        budget=120, non_goals=NON_GOALS)


def rubato_phrase() -> Path:
    return gym(
        'Rubato on a cantabile phrase',
        'Write an 8-bar cantabile melody with a simple accompaniment and perform it with rubato: agogic stretch on '
        'the phrase peak, ritardando at the end, and a velocity arc that follows the line, rendered.',
        needs=[
            '8 bars in 3/4, a single-line right-hand melody (one note at a time) over sustained left-hand chords, '
            '`piece.mid` program 0, in a stated key with a cadence at the end.',
            'Rubato: the melody\'s highest note is lengthened by at least 20% against the grid; the last two bars '
            'slow by at least 15%; elsewhere onsets stay within 8% of the grid so the pulse is felt.',
            'Velocity: rises to its maximum at the highest note and falls to its minimum on the last note; the '
            'span is at least 30.',
            'The accompaniment never exceeds the melody in velocity in any bar.',
        ],
        deliverables=['piece.mid', 'notes.md: the timing map (bar, beat, stretch) and the velocity arc'],
        budget=120, non_goals=NON_GOALS)


def nocturne_lh() -> Path:
    return gym(
        'Nocturne left hand',
        'Write and play a short passage in a nocturne texture: a wide, rolling left hand under a singing right hand.',
        needs=[
            'The left hand is wide and rolls: bass low, chord tones above it, in compound time.',
            'The melody floats above it, with a couple of ornamental runs.',
            'The accompaniment stays under the melody, and the pedal follows the harmony.',
        ],
        deliverables=['piece.mid', 'notes.md'],
        budget=120, non_goals=NON_GOALS)


def voicing_touch() -> Path:
    return gym(
        'Voicing and touch',
        'Perform a given 8-bar chord sequence so the top voice sings: the melody note of each chord louder than '
        'the rest, the left hand under it, legato in the melody and a detached final bar, rendered.',
        needs=[
            '8 bars in 4/4 in a stated major key, two chords per bar as four-note voicings (two notes per hand), '
            'the 16-chord progression I V vi iii IV I IV V | I V vi iii IV I V I (bar by bar, two per bar), '
            '`piece.mid` program 0, the top note of each chord forming a stepwise melody.',
            'Voicing: in every chord the top note is at least 12 velocity louder than each other note of the '
            'chord, and the left-hand notes are at least 8 softer than the right-hand inner voice.',
            'Touch: in bars 1-7 each melody note overlaps the next by 20-60 ms (legato); in bar 8 every note ends '
            'at least 80 ms before the next begins (detached); the final chord is held for its full length.',
            'Pedal changes with every chord; the last bar has a ritardando of at least 15%.',
        ],
        deliverables=['piece.mid', 'notes.md: the voicings by chord and how the velocities were shaped'],
        budget=120, non_goals=NON_GOALS)


MAKERS = dict(benchmark=benchmark, melody_bass=melody_bass, voice_leading=voice_leading,
              pedal_dynamics=pedal_dynamics, rubato_phrase=rubato_phrase, nocturne_lh=nocturne_lh,
              voicing_touch=voicing_touch)

PINNED = Path.home()/'mizpah-runs'/'piano'/'benchmark.brief.json'


def benchmark_attempt(attempt: int) -> Path:
    """The benchmark from its pinned brief — the file attempt 1 wrote, byte for byte — never from this
    module's text again: an edit here must not move the ruler. The gym is named for the attempt."""
    pinned = json.loads(PINNED.read_text())
    folder = init_module.new_gym('Romantic piano piece, attempt '+str(attempt))
    init_module.init(folder, title=pinned['title'], mission=pinned['mission'], terra=str(TERRA), base=BASE)
    fresh = json.loads((folder/'.mizpah'/'brief.json').read_text())
    keep = {k: pinned[k] for k in pinned if k not in ('created_at', 'updated_at', 'history', 'proposals')}
    (folder/'.mizpah'/'brief.json').write_text(json.dumps(dict(fresh, **keep, proposals=[]), indent=1)+'\n')
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


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == 'make' and sys.argv[2] == 'benchmark' and PINNED.exists():
        attempt = int(sys.argv[3]) if len(sys.argv) > 3 else 2
        folder = benchmark_attempt(attempt)
        print(json.dumps(dict(project=str(folder), base=BASE, attempt=attempt, pinned=str(PINNED)), indent=1))
    elif len(sys.argv) >= 3 and sys.argv[1] == 'make':
        folder = MAKERS[sys.argv[2]]()
        print(json.dumps(dict(project=str(folder), base=BASE), indent=1))
    elif len(sys.argv) >= 3 and sys.argv[1] == 'score':
        print(json.dumps(score(Path(sys.argv[2]).resolve()), indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
