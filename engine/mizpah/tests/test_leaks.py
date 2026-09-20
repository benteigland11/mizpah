"""The loop reaps what its tasks leave running on the host and counts it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from mizpah import ops


def _spawn_orphan(root: Path, mark: str) -> int:
    """A child that starts a grandchild and exits: the grandchild is reparented to PID 1 with the mark."""
    code = ('import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
            'print(p.pid, flush=True)')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                         env=dict(os.environ, **{ops.LEAK_MARK: mark}), check=True).stdout
    pid = int(out.strip())
    for _ in range(50):
        try:
            if int(Path('/proc', str(pid), 'stat').read_text().split(') ')[1].split()[1]) == 1:
                break
        except OSError:
            break
        time.sleep(0.05)
    return pid


def test_reap_kills_marked_orphans_of_this_root_and_counts_them(tmp_path: Path) -> None:
    root = tmp_path/'run'
    root.mkdir()
    other = tmp_path/'other_live'
    mine = _spawn_orphan(root, str(root))
    theirs = _spawn_orphan(root, str(other))
    try:
        leaks = ops.reap_leaks(root, live_roots=[other], where='t1')
        assert [l['pid'] for l in leaks] == [mine]           # the live loop's orphan is its own to reap
        time.sleep(0.2)
        assert not Path('/proc', str(mine)).exists()
        assert Path('/proc', str(theirs)).exists()
        summary = ops.leak_summary(root)
        assert summary['count'] == 1 and list(summary['by_comm']) == [leaks[0]['comm']]
        rows = [json.loads(l) for l in (root/'leaks.jsonl').read_text().splitlines()]
        assert rows[0]['where'] == 't1' and rows[0]['pid'] == mine
        # A dead loop's orphan is fair game for whoever runs next.
        leaks = ops.reap_leaks(root, live_roots=[], where='t2')
        assert [l['pid'] for l in leaks] == [theirs]
    finally:
        for pid in (mine, theirs):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def test_unmarked_orphans_are_not_ours(tmp_path: Path) -> None:
    assert all(l['root'] for l in ops.leaked_processes(tmp_path))
