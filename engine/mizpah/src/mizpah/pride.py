"""The controller looks at what was delivered on its own, as the one who has to sign it: is it proud of this?

Everything else in the loop asks "is it wrong?" — the gate, the probes, the reviewer. Asked that, a worker makes
the safest thing that passes, and the pieces came out bland, careful and timid. Asked the question a maker asks,
the same model names exactly what is wrong with its own green work (2026-09-23: proud of 2 of 42 deliverables,
each "no" specific — a sequencer grid, parallel fifths, a waltz with no waltz accompaniment).

So at a landing the controller opens a fresh conversation holding nothing but the delivered assets: not the brief,
not the map, not the harness's framing. Its verdict goes into the step's message; not proud means route the work
that would make it proud. A verdict is kept by the assets' content hash, so an unchanged asset is judged once."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from cg.data_smf_byte_notes_python.src.smf_byte_notes import read_smf, read_sustain

from . import prompts

VERDICTS = 'pride.json'
TEXT_ASSET_CHARS = 40000     # a document is read whole: this is the pride call's own window, and the asset is all it holds
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
TEXT_EXTENSIONS = {'.md', '.txt', '.json', '.svg', '.csv', '.ly', '.html', '.css', '.py', '.yaml', '.yml', '.toml'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg'}
NAMES = 'C C# D Eb E F F# G Ab A Bb B'.split()


def deliverable_files(project: Path, brief: dict[str, Any]) -> list[Path]:
    """The files the brief names as deliverables that exist now."""
    found: list[Path] = []
    for text in brief.get('deliverables') or []:
        for groups in re.findall(r'`([^`]+)`|([\w./-]+\.[A-Za-z0-9]{1,5})', str(text)):
            name = (groups[0] or groups[1]).strip().lstrip('/')
            if not name or ' ' in name or '<' in name:
                continue
            path = project/name
            if path.is_file() and path not in found:
                found.append(path)
    return found


def fingerprint(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode()+b'\0'+path.read_bytes())
    return digest.hexdigest()[:16]


def midi_text(data: bytes) -> str:
    """A MIDI file as a musician reads a score off it: meter, key, tempo and pedal changes, then each bar's notes as
    name@beat/length v velocity."""
    division, meter, key, tempos, programs, _channels, notes = read_smf(data)
    pedal = read_sustain(data)
    numerator, denominator = (int(x) for x in (meter or '4/4').split('/'))
    bar_ticks = int(division*4*numerator/denominator)
    events: list[tuple[int, str]] = []
    for tick, bpm in tempos:
        events.append((tick, f'[tempo {round(bpm, 1)}]'))
    for tick, _channel, value in pedal:
        events.append((tick, '[pedal '+('down' if value >= 64 else 'up')+']'))
    for _channel, pitch, start, end, velocity in notes:
        events.append((start, f'{NAMES[pitch % 12]}{pitch//12-1}@{{beat}}/{round((end-start)/division, 2)}v{velocity}'))
    events.sort(key=lambda e: e[0])
    lines = [f'meter {meter}, key {key}, programs {sorted({p for _, p in programs}) or [0]}, {len(notes)} notes']
    bar, row = 1, []
    for tick, text in events:
        while tick >= bar*bar_ticks:
            if row:
                lines.append(f'bar {bar}: '+'  '.join(row))
            row, bar = [], bar+1
        beat = round((tick-(bar-1)*bar_ticks)/division+1, 2)
        row.append(text.replace('{beat}', str(beat)) if '{beat}' in text else text.replace(']', f' @{beat}]'))
    if row:
        lines.append(f'bar {bar}: '+'  '.join(row))
    return '\n'.join(lines)


def _image(path: Path) -> dict[str, Any]:
    kind = 'jpeg' if path.suffix.lower() in ('.jpg', '.jpeg') else path.suffix.lower().lstrip('.')
    return dict(type='image_url', image_url=dict(url=f'data:image/{kind};base64,'+base64.b64encode(path.read_bytes()).decode()))


def render(path: Path, scratch: Path) -> tuple[str, list[dict[str, Any]]]:
    """One asset as the model can take it in: text as text, pictures as pictures, a score's pages and a video's
    frames as pictures, MIDI as its notes. What cannot be shown is said, not hidden."""
    suffix = path.suffix.lower()
    head = f'## {path.name}'
    if suffix in ('.mid', '.midi'):
        try:
            return f'{head} (MIDI, as notes: name@beat/length-in-beats v velocity)\n{midi_text(path.read_bytes())}', []
        except Exception as error:  # noqa: BLE001 — an unreadable file is itself something to see
            return f'{head}\n(MIDI that could not be read: {error})', []
    if suffix in IMAGE_EXTENSIONS:
        return f'{head}\n(attached as an image)', [_image(path)]
    if suffix == '.pdf' and shutil.which('pdftoppm'):
        subprocess.run(['pdftoppm', '-png', '-r', '80', '-l', '4', str(path), str(scratch/(path.stem+'-page'))], capture_output=True)
        pages = sorted(scratch.glob(path.stem+'-page*.png'))
        return f'{head}\n(its first {len(pages)} page(s) attached as images)', [_image(p) for p in pages]
    if suffix in ('.mp4', '.mov', '.webm') and shutil.which('ffmpeg'):
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                               capture_output=True, text=True).stdout.strip()
        length = float(probe or 0)
        frames = []
        for i, at in enumerate((min(1.0, length/10), length/2, max(length-0.5, 0))):
            out = scratch/f'{path.stem}-frame{i}.png'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(at), '-i', str(path), '-frames:v', '1', '-vf', 'scale=960:-1', str(out)],
                           capture_output=True)
            if out.exists():
                frames.append(_image(out))
        return f'{head} (video, {length:.1f} s: frames at the start, middle and end attached)', frames
    if suffix in AUDIO_EXTENSIONS:
        return f'{head}\n(audio; it cannot be played here — judge it by the source it was rendered from, if that is shown)', []
    if suffix in TEXT_EXTENSIONS:
        text = path.read_text(errors='replace')
        if len(text) > TEXT_ASSET_CHARS:
            text = text[:TEXT_ASSET_CHARS]+f'\n…[{len(text)-TEXT_ASSET_CHARS:,} more characters]'
        return f'{head}\n{text}', []
    return f'{head}\n({suffix or "extensionless"} file of {path.stat().st_size:,} bytes; not shown)', []


def judge(client: Any, config: dict[str, Any], files: list[Path]) -> dict[str, Any]:
    """The fresh conversation: the assets and the question, nothing else."""
    from cg.backend_persistent_model_session_python.src.persistent_model_session import parse_turn
    with tempfile.TemporaryDirectory() as scratch:
        parts = [render(p, Path(scratch)) for p in files]
        text = prompts.message('pride_controller', assets='\n\n'.join(t for t, _ in parts))
        images = [image for _, imgs in parts for image in imgs]
        content: Any = [dict(type='text', text=text)]+images if images else text
        payload = dict(config['controller']['generation'], messages=[dict(role='user', content=content)],
                       max_tokens=config['mizpah'].get('controller_output_tokens', 8192))
        response = client.complete(payload, 'pride')
    reply = str(parse_turn(response).message.get('content') or '')
    match = re.search(r'\{.*\}', reply, re.S)
    try:
        verdict = json.loads(match.group(0)) if match else None
    except ValueError:
        verdict = None
    if not isinstance(verdict, dict) or not isinstance(verdict.get('proud'), bool):
        return dict(proud=None, error='the verdict was not one JSON object with "proud"', reply=reply[:600])
    return {k: verdict.get(k) for k in ('proud', 'proudest', 'why', 'holds_it_back', 'would_make_me_proud')}


def review(client: Any, config: dict[str, Any], project: Path, root: Path | None, brief: dict[str, Any]) -> dict[str, Any] | None:
    """The verdict on the deliverables as they stand now: judged afresh when they changed, else the kept one."""
    files = deliverable_files(project, brief)
    if not files:
        return None
    key = fingerprint(files)
    kept_path = (root/VERDICTS) if root is not None else None
    kept: dict[str, Any] = {}
    if kept_path is not None and kept_path.exists():
        try:
            kept = json.loads(kept_path.read_text())
        except ValueError:
            kept = {}
    if key in kept:
        return dict(kept[key], files=[p.name for p in files], fresh=False)
    verdict = judge(client, config, files)
    if kept_path is not None and verdict.get('proud') is not None:
        kept[key] = verdict
        kept_path.write_text(json.dumps(kept, indent=1, ensure_ascii=False))
    return dict(verdict, files=[p.name for p in files], fresh=True)
