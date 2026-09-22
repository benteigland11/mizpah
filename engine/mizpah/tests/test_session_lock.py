"""One loop per session: waking a loop is safe to repeat (2026-09-22)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from mizpah import controller, loop

ROOT = Path(__file__).resolve().parents[1]


def test_a_second_loop_on_the_same_session_is_refused_until_the_first_ends(tmp_path: Path) -> None:
    first = loop.session_lock(tmp_path)
    assert first is not None
    assert loop.session_lock(tmp_path) is None
    os.close(first)                                   # the process ended, however it ended
    again = loop.session_lock(tmp_path)
    assert again is not None
    os.close(again)


def test_a_start_that_finds_a_loop_running_leaves_its_note_and_steps_aside(tmp_path: Path) -> None:
    held = loop.session_lock(tmp_path)
    try:
        out = subprocess.run([sys.executable, '-m', 'mizpah.loop', '--config', str(tmp_path/'unused.json'),
                              '--project', str(tmp_path), '--root', str(tmp_path), '--note', 'play it slower'],
                             capture_output=True, text=True, timeout=60, cwd=ROOT,
                             env=dict(os.environ, PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT.parent.parent)))
        assert json.loads(out.stdout)['status'] == 'already_running', out.stderr[-500:]
        assert [n['text'] for n in controller.operator_notes(tmp_path)] == ['play it slower']
    finally:
        os.close(held)
