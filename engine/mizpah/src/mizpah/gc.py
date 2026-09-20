"""Reclaim what finished runs no longer need, and say what would go before it goes.

    python -m mizpah.gc <runs root> [--apply] [--keep-days N]

A session root (`<name>-sess/`) holds, per task, the evidence the loop reads back — task.json, result.json,
the workspace snapshots under workspaces/, remeasure/ — and the working state it does not: the sandbox's
service work directories (a copy of the project plus a browser's profile and cache), every revision of the
session store, the write-ahead checkpoint. For a run that has ended, only the evidence stays. Live runs are
never touched. Nothing under the project itself (`<name>/`) is ever touched: that is the deliverable.

Reports bytes per session and in total; applies only with --apply.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any

# What goes from a finished task. Service work dirs are the sandbox's copies; the evidence the loop harvests
# was taken from workspaces/*.sqlite3 at task end. Chromium leaves a profile and cache under a service's HOME.
DISPOSABLE_DIRS = ('scratch/services', 'scratch/.cache', 'scratch/.chrome', 'scratch/.config')
DISPOSABLE_FILES = ('events/checkpoint.wal.json', 'events/checkpoint.tmp')


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def _ended(session: Path) -> bool:
    """A run is finished when its run.json says so, or its loop is dead; a session without run.json (an
    older engine) counts as finished when its loop.json carries a stop."""
    run = session/'run.json'
    if run.exists():
        try:
            doc = json.loads(run.read_text())
        except ValueError:
            return False
        if doc.get('ended_at') is not None or doc.get('archived'):
            return True
        pid = doc.get('pid')
        return not (isinstance(pid, int) and Path('/proc', str(pid)).exists())
    loop = session/'loop.json'
    if loop.exists():
        try:
            return json.loads(loop.read_text()).get('stop') is not None
        except ValueError:
            return False
    return False


def _prune_store(path: Path, keep: int = 1) -> int:
    """Every revision but the newest of a session store; returns bytes freed."""
    before = path.stat().st_size
    try:
        with sqlite3.connect(path) as db:
            row = db.execute('SELECT MAX(revision) FROM revisions').fetchone()
            if row and row[0]:
                db.execute('DELETE FROM revisions WHERE revision <= ?', (row[0]-keep,))
        with sqlite3.connect(path) as db:
            db.execute('VACUUM')
    except sqlite3.Error:
        return 0
    return before-path.stat().st_size


def plan(session: Path) -> list[tuple[Path, int, str]]:
    """(path, bytes, kind) for everything gc would remove or shrink under one finished session."""
    out: list[tuple[Path, int, str]] = []
    for task in sorted((session/'tasks').glob('*')) if (session/'tasks').is_dir() else []:
        for rel in DISPOSABLE_DIRS:
            p = task/rel
            if p.exists():
                out.append((p, _size(p), 'remove'))
        for rel in DISPOSABLE_FILES:
            p = task/rel
            if p.exists():
                out.append((p, _size(p), 'remove'))
        store = task/'state.sqlite3'
        if store.exists():
            try:
                with sqlite3.connect(store) as db:
                    n = db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]
            except sqlite3.Error:
                n = 0
            if n > 1:
                out.append((store, store.stat().st_size*(n-1)//n, 'prune'))
    return out


def sessions(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.rglob('run.json') if '/tasks/' not in str(p)) + \
        sorted(p.parent for p in root.rglob('loop.json') if '/tasks/' not in str(p) and not (p.parent/'run.json').exists())


def run(root: Path, *, apply: bool, keep_days: float) -> dict[str, Any]:
    cutoff = time.time()-keep_days*86400
    report: dict[str, Any] = dict(sessions=[], bytes=0, applied=apply)
    for session in sessions(root):
        if not _ended(session):
            continue
        newest = max((p.stat().st_mtime for p in session.rglob('*') if p.is_file()), default=0)
        if newest > cutoff:
            continue
        items = plan(session)
        total = sum(b for _, b, _ in items)
        if not total:
            continue
        freed = 0
        if apply:
            for path, size, kind in items:
                if kind == 'remove':
                    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
                    freed += size
                else:
                    freed += _prune_store(path)
        report['sessions'].append(dict(session=str(session), bytes=total, freed=freed if apply else None,
                                       items=len(items)))
        report['bytes'] += total
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('root')
    parser.add_argument('--apply', action='store_true', help='remove; without it, report only')
    parser.add_argument('--keep-days', type=float, default=0.0, help='leave sessions touched within N days alone')
    args = parser.parse_args()
    report = run(Path(args.root).resolve(), apply=args.apply, keep_days=args.keep_days)
    for row in report['sessions']:
        print(f"{row['bytes']/1e9:6.2f} GB  {row['items']:3} items  {row['session']}")
    print(f"{report['bytes']/1e9:.2f} GB {'freed' if args.apply else 'reclaimable'} across {len(report['sessions'])} finished sessions"
          + ('' if args.apply else '  (pass --apply to remove)'))


if __name__ == '__main__':
    main()
