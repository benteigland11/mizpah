"""Score video: one benchmark brief (follow the score) and a ladder of practice gyms for putting a piece on screen.

Usage: python -m fixtures.video make benchmark|which_note|mark_the_note|turn_ahead [--config C] [--signed-by S]

Every gym starts from the same performance, pinned outside the repository (`SOURCE`): piece.ly (the score's
source), piece.pdf (LilyPond's engraving of it), piece.mid (the performance) and piece.mp3 (its recording). The
`piano` base supplies LilyPond, the soundfont and a venv with mido/pretty_midi/numpy; ffmpeg and poppler come from
the host. Briefs are I/O contracts: a mission, what comes in, what goes out, and needs stated as a standard, never
a checklist — the controller decomposes them and the worker picks the numbers.

The ladder is the three obstacles GPT-6 Luna hit on the whole task (follow the score, 2026-09-22), one gym each:
knowing which engraved notehead is which performed note; putting a mark on the note that is sounding; and moving
the view ahead of the music. The benchmark is the whole task, its text as issued that day, rerun after the ladder.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fixtures import piano  # noqa: E402  the same gym/issue helpers and base

SOURCE = Path.home()/'mizpah-runs'/'video'/'source'
FILES = ('piece.ly', 'piece.pdf', 'piece.mid', 'piece.mp3')
IN = ('In: piece.ly (the score\'s source), piece.pdf (LilyPond\'s engraving of it), piece.mid (the performance), '
      'piece.mp3 (its recording), LilyPond, ffmpeg and the piano base.')
NON_GOALS = [
    'No reading notes off the page: the engraver knows where every note is; glyph or pixel recognition of piece.pdf '
    'is not asked for.',
    'No re-recording or re-rendering of the audio, and no re-composing: piece.mp3 and piece.mid are the performance.',
    'No notes, plan or write-up files — the deliverable is the work.',
]


def gym(title: str, mission: str, needs: list[str], deliverables: list[str], budget: int = 120,
        non_goals: list[str] = ()) -> Path:
    folder = piano.gym(title, mission+' '+IN, needs, deliverables, budget, non_goals=[*NON_GOALS, *non_goals])
    for name in FILES:
        shutil.copy2(SOURCE/name, folder/name)
    return folder


def which_note() -> Path:
    return gym(
        'Which notehead is which note',
        'Find, for every note the performance plays, the notehead that prints it: where on which page of piece.pdf '
        'a reader\'s eye should be when that note sounds. Out: noteheads.json — each performed note with the page and '
        'the place of its notehead.',
        needs=[
            'Every played note is found on the page: a musician following the list with a finger on the printed score '
            'would never land on the wrong head — chords, ties and grace notes included.',
            'The link comes from how the score was engraved, so it holds for any piece LilyPond sets, not only this one.',
            'The list is checked, not trusted: a reading shows it agrees with the page and would catch a note bound to '
            'the wrong head.',
        ],
        deliverables=['noteheads.json'])


def mark_the_note() -> Path:
    return gym(
        'Mark the sounding note',
        'Show the first line of the score with the note that is sounding marked, in time with the recording. '
        'Out: first_line.mp4 — the opening system on screen, the sounding note marked, piece.mp3 as the soundtrack '
        'for as long as that system plays.',
        needs=[
            'The mark is on the note you hear: pause anywhere and the marked notehead is the one sounding, through the '
            'performance\'s own rubato.',
            'The mark helps the reading and hides nothing: a musician can still read every note under and around it.',
            'The mark is read off the video, not assumed: a reading takes frames from first_line.mp4 and finds the mark '
            'where the performance says it should be.',
        ],
        deliverables=['first_line.mp4'])


def turn_ahead() -> Path:
    return gym(
        'Turn before the music gets there',
        'Show the whole score in time with the recording, the view always on the music being played. '
        'Out: pages.mp4 — the engraved score on screen, piece.mp3 as the soundtrack, no note marker.',
        needs=[
            'The view is always where the music is: at any moment the system being played is in view and legible.',
            'The view moves the way a page-turner would: to the next system or page just before the music reaches it, '
            'never after.',
            'The timing is read off the video: a reading finds each change of view in pages.mp4 and says how far ahead '
            'of the music it came.',
        ],
        deliverables=['pages.mp4'],
        non_goals=['No note marker: this gym is the view alone.'])


def benchmark() -> Path:
    return gym(
        'Follow the score',
        'Make a score-following video of this piece: the engraved score on screen with the sounding note marked, in '
        'time with the recorded performance, the kind of video a music student follows a recording with. '
        'Out: score.mp4, a 1920×1080 H.264 video with AAC sound: the page in view, the sounding note marked, piece.mp3 '
        'as the soundtrack.',
        needs=[
            'The mark follows the performance at the level of a professional score-following video: wherever a '
            'musician pauses it, the mark is on the note they hear, through the performance\'s own rubato.',
            'The score reads like the printed edition while it plays: noteheads legible in the frame, and the view turns '
            'to the next system or page before the mark gets there, the way a page-turner would.',
            'The sound is the recording, untouched and in sync from the first note to the last; the video ends when it does.',
        ],
        deliverables=['score.mp4'])


MAKERS = dict(which_note=which_note, mark_the_note=mark_the_note, turn_ahead=turn_ahead, benchmark=benchmark)


def main() -> None:
    args = list(sys.argv[1:])
    config = None
    signed_by = ''
    if '--config' in args:
        i = args.index('--config'); config = Path(args[i+1]); del args[i:i+2]
    if '--signed-by' in args:
        i = args.index('--signed-by'); signed_by = args[i+1]; del args[i:i+2]
    if len(args) == 2 and args[0] == 'make' and args[1] in MAKERS:
        missing = [n for n in FILES if not (SOURCE/n).is_file()]
        if missing:
            raise SystemExit('the pinned performance is missing from '+str(SOURCE)+': '+', '.join(missing))
        folder = MAKERS[args[1]]()
        crew = piano.issue(folder, config, signed_by)
        print(json.dumps(dict(project=str(folder), base=piano.BASE, crew=crew), indent=1))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
