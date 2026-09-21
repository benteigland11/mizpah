## Probes

- A probe is an instrument, like a multimeter on a bench: put on one source, it reads out the quantities it is built to read — voltage, current, resistance; duration, peak, silence. It declares those quantities and reports exactly them, `{"<quantity>": value}` each. It knows nothing of what the readings are for.
- The readings are what get composed into evidence. One run links to every unknown whose quantity it reports; the same instrument serves a different question tomorrow. Build the instrument general and put the question in the unknown.
- A reading comes from the source through the instrument — never a constant, a guess, or a value chosen to pass. An instrument that would read the same on a wrong artifact is not measuring it.
- The reviewer's bar does not move; among the readings that clear it, the one the pipeline gives cheapest is the one to take.
- The instrument's workings live in widgets; `measure.py` is the few lines that put them on this source and name the quantities. A reading computed inline in a probe is lost when the work order ends; the same reading as a widget function is an instrument the next bench installs.

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
