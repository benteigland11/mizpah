"""Piano: one benchmark brief and a ladder of practice gyms, all on the `piano` base.

Usage: python -m fixtures.piano make benchmark [attempt]|melody_bass|voice_leading|pedal_dynamics|rubato_phrase|nocturne_lh|voicing_touch
       python -m fixtures.piano score <project_dir>            readings off the MIDI the gym produced

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
        'Write and perform a 16-bar piano melody in a major key over a left-hand broken-chord figure, with phrase '
        'shape and a proper cadence, rendered to audio.',
        needs=[
            '16 bars in 4/4 in one major key, four 4-bar phrases, `piece.mid` program 0; the melody sits in the '
            'right hand between C4 and C6 and moves mostly by step (at least 60% of its intervals are seconds).',
            'The left hand plays a broken-chord figure (Alberti or arpeggiated) that changes chord with the harmony '
            'at least once per bar and never holds more than one chord per beat.',
            'Harmony: I, IV, V and vi appear; the last two bars are a perfect authentic cadence (V then I, melody '
            'ending on the tonic).',
            'Performed: the melody is louder than the accompaniment by at least 15 velocity on average; each phrase '
            'has a velocity arc (rises then falls); the final bar has a ritardando of at least 10%.',
            RENDER,
        ],
        deliverables=['piece.mid', 'piece.mp3', 'piece.pdf', 'notes.md: the chord plan by bar and the phrase plan'],
        budget=30, non_goals=NON_GOALS)


def voice_leading() -> Path:
    return gym(
        'Four-voice chorale for piano',
        'Write an 8-bar four-voice chorale for piano with clean voice leading, and perform it with pedal, rendered.',
        needs=[
            '8 bars, four voices throughout (two per hand), one chord per beat, `piece.mid` program 0, in a minor '
            'key with a picardy or minor tonic ending after a cadence.',
            'Voice leading: no parallel perfect fifths or octaves between any two voices; no voice leaps more than '
            'an octave; common tones between adjacent chords are kept in the same voice at least 70% of the time; '
            'voices never cross.',
            'Spacing: adjacent upper voices within an octave; the bass may be wider; the whole chord within the '
            'two-hand span rule ('+PLAYABLE.split(': ', 1)[1]+')',
            'Performed: pedal changes on every chord change; the soprano is the loudest voice on average; the '
            'final cadence has a ritardando of at least 15%.',
            RENDER,
        ],
        deliverables=['piece.mid', 'piece.mp3', 'piece.pdf', 'notes.md: the roman-numeral plan and how each rule was checked'],
        budget=30, non_goals=NON_GOALS)


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
            RENDER,
        ],
        deliverables=['piece.mid', 'piece.mp3', 'notes.md: the velocity and pedal plan per bar'],
        budget=20, non_goals=NON_GOALS)


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
            RENDER,
        ],
        deliverables=['piece.mid', 'piece.mp3', 'piece.pdf', 'notes.md: the timing map (bar, beat, stretch) and the velocity arc'],
        budget=25, non_goals=NON_GOALS)


def nocturne_lh() -> Path:
    return gym(
        'Nocturne left hand',
        'Write 16 bars in a nocturne texture: a wide-spaced left-hand figure (bass, then chord tones above) in 12/8 '
        'under a right-hand melody, performed with pedal per harmony, rendered.',
        needs=[
            '16 bars in 12/8, `piece.mid` program 0, minor key; the left hand plays a bass note on beat 1 and 7 of '
            'each bar with chord tones above it on the other eighths, spanning at least a tenth per bar.',
            'The right-hand melody has at least two ornamental runs (four or more notes within a beat) and '
            'otherwise moves in longer values; it stays above the left hand at every onset.',
            'Pedal changes with each harmony (at least twice per bar); the left-hand figure is at least 10 velocity '
            'softer than the melody on average.',
            PLAYABLE, RENDER,
        ],
        deliverables=['piece.mid', 'piece.mp3', 'piece.pdf', 'notes.md: the harmony by bar and the figure\'s voicing'],
        budget=30, non_goals=NON_GOALS)


def voicing_touch() -> Path:
    return gym(
        'Voicing and touch',
        'Perform a given 8-bar chord sequence so the top voice sings: the melody note of each chord louder than '
        'the rest, the left hand under it, legato in the melody and a detached final bar, rendered.',
        needs=[
            '8 bars in 4/4 in a stated major key, the progression I V vi iii IV I IV V then I, two chords per bar '
            'as four-note voicings (two notes per hand), `piece.mid` program 0, the top note of each chord forming '
            'a stepwise melody.',
            'Voicing: in every chord the top note is at least 12 velocity louder than each other note of the '
            'chord, and the left-hand notes are at least 8 softer than the right-hand inner voice.',
            'Touch: in bars 1-7 each melody note overlaps the next by 20-60 ms (legato); in bar 8 every note ends '
            'at least 80 ms before the next begins (detached); the final chord is held for its full length.',
            'Pedal changes with every chord; the last bar has a ritardando of at least 15%.',
            RENDER,
        ],
        deliverables=['piece.mid', 'piece.mp3', 'notes.md: the voicings by chord and how the velocities were shaped'],
        budget=20, non_goals=NON_GOALS)


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
    bars = max(1, duration/(4*60/max(pm.estimate_tempo(), 1)))
    tempi = pm.get_tempo_changes()[1]
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
    return dict(ok=True, file=str(mids[0].relative_to(project)), seconds=round(duration, 1), notes=len(notes),
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
