## Probes

- A probe is a measurement of one source: it declares the quantities it reads and reports exactly those, `{"<quantity>": value}` each. One run links to every unknown it measures.
- A reading comes from the source through code that reads it — never a constant, a guess, or a value chosen to pass. A reading that would come out the same for a wrong artifact resolves nothing.
- The reviewer's bar does not move; among the readings that clear it, the one the pipeline gives cheapest is the one to take.

One probe, several quantities, one source. Create it declaring what it measures, then `measure.py` returns exactly those:

```
terra probe create audio_render --purpose "what the rendered audio is like" --measure duration_s,peak_dbfs,is_silent
```

```python
# .mizpah/map/probes/audio_render/measure.py — one decode, three readings
import numpy as np, soundfile as sf

def measure(ctx):
    samples, rate = sf.read("piece.wav")
    peak = float(np.abs(samples).max())
    return {
        "duration_s": len(samples) / rate,
        "peak_dbfs": 20 * np.log10(peak) if peak > 0 else -120.0,
        "is_silent": peak < 1e-4,
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
