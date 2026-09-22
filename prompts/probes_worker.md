- `terra probe create <id> --purpose "…" --measure q1,q2` makes the instrument; write its `measure.py`; `terra probe validate <id>` before the first run; `terra probe run <id>` stamps a run with every declared quantity read.
- One run, many unknowns: close each unknown whose quantity the run reports with `terra known land <unknown> --run <run> --on file:<the artifact it read>` — it links, graduates, declares the dependency, promotes to med and adopts, and stops at the first thing it cannot do with the command that gets past it (`terra known ladder <unknown>` when a variable reading needs more samples). A run that turns out wrong is swapped, not piled on: `terra known replace-run <known> <old> <new>`.
- The unknown asks a question, not for a method. Pick the instrument the way a technician does: the field multimeter for terminals on a panel, the lab one when the last digit matters. The cheapest instrument that answers the question at the bar is the right one; a more precise reading than the claim needs is time spent, not evidence gained.
- Just because it can be measured does not make it evidence: a quantity is worth reporting when an unknown asks for it or a later one plausibly will. An instrument that reads out forty numbers nobody composes is noise on the map.
- A false or unwelcome reading is a valid reading: fix the artifact and measure again; never force the value. A bad run is voided (`terra run void <run> --reason …`), never edited.

Create the instrument declaring what it reads; `measure.py` returns exactly those:

```
terra probe create audio_render --purpose "what the rendered audio is like" --measure duration_s,peak_dbfs,is_silent
```

```python
# .mizpah/map/probes/audio_render/measure.py — the widget decodes and reads; the probe names the quantities
import sys
sys.path.insert(0, "cg/data-audio-levels-python")
from src.audio_levels import duration_seconds, peak_dbfs, is_silent

def measure(ctx):
    return {
        "duration_s": duration_seconds("piece.wav"),
        "peak_dbfs": peak_dbfs("piece.wav"),
        "is_silent": is_silent("piece.wav"),
    }
```

```
terra probe create suite_run --purpose "what the test suite says" --measure tests_passed,suite_green,slowest_test
```

```python
# one pytest invocation, parsed once; a number, a boolean, a label
import json, subprocess

def measure(ctx):
    subprocess.run(["pytest", "-q", "--json-report", "--json-report-file=.tool-output/suite.json"], check=False)
    r = json.load(open(".tool-output/suite.json"))
    slowest = max(r["tests"], key=lambda t: t["duration"])["nodeid"]
    return {
        "tests_passed": r["summary"].get("passed", 0),
        "suite_green": r["summary"].get("failed", 0) == 0,
        "slowest_test": slowest,
    }
```

```
terra probe create score_vs_performance --purpose "the engraved score is this performance" --measure notes_match,bars
```

```python
# a rendering read at its own pipeline: the engraver writes MIDI from the same score it engraves,
# and that output is compared to the performance — not the rendered surface decoded back
import subprocess, mido

def _notes(path):
    return [m.note for m in mido.MidiFile(path) if m.type == "note_on" and m.velocity]

def _bars(path, beats_per_bar=4):
    mid = mido.MidiFile(path)
    ticks = sum(m.time for m in mido.merge_tracks(mid.tracks))
    return -(-ticks // (mid.ticks_per_beat * beats_per_bar))

def measure(ctx):
    subprocess.run(["lilypond", "-o", ".tool-output/score", "piece.ly"], check=True)   # piece.ly has a \midi block
    return {
        "notes_match": _notes(".tool-output/score.midi") == _notes("piece.mid"),
        "bars": _bars("piece.mid"),
    }
```
