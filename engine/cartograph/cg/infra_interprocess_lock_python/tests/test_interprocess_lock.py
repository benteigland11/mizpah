import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.interprocess_lock import file_lock, LockBusy


def _lock_path(tmp_path):
    return str(tmp_path / "x.lock")


def test_acquire_and_release_runs_body(tmp_path):
    ran = []
    with file_lock(_lock_path(tmp_path)):
        ran.append(True)
    assert ran == [True]
    # Released: a second acquire on a fresh handle succeeds immediately.
    with file_lock(_lock_path(tmp_path)):
        pass


def test_lock_file_is_created(tmp_path):
    lp = _lock_path(tmp_path)
    assert not os.path.exists(lp)
    with file_lock(lp):
        assert os.path.exists(lp)


def test_reentrant_same_thread(tmp_path):
    lp = _lock_path(tmp_path)
    with file_lock(lp):
        with file_lock(lp):  # nested acquire must not deadlock
            with file_lock(lp, blocking=False):  # even non-blocking reuses it
                pass


def test_distinct_paths_do_not_share_reentrancy(tmp_path):
    a = str(tmp_path / "a.lock")
    b = str(tmp_path / "b.lock")
    with file_lock(a):
        with file_lock(b):  # independent lock, freely acquirable
            pass


def test_blocking_serializes_across_threads(tmp_path):
    lp = _lock_path(tmp_path)
    order = []

    def worker(tag, hold):
        with file_lock(lp):  # blocking: waits for the other thread
            order.append(f"{tag}-in")
            time.sleep(hold)
            order.append(f"{tag}-out")

    t1 = threading.Thread(target=worker, args=("a", 0.3))
    t1.start()
    time.sleep(0.05)
    t2 = threading.Thread(target=worker, args=("b", 0.0))
    t2.start()
    t1.join()
    t2.join()
    assert order == ["a-in", "a-out", "b-in", "b-out"]


def test_non_blocking_raises_when_held_by_another_process(tmp_path):
    """A separate process holds the lock; a non-blocking acquire must raise
    LockBusy immediately rather than block."""
    import subprocess
    import textwrap

    lp = _lock_path(tmp_path)
    src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
    holder = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {src_dir!r})
        from interprocess_lock import file_lock
        with file_lock({lp!r}):
            print("HELD", flush=True)
            time.sleep(3)
    """)
    proc = subprocess.Popen([sys.executable, "-c", holder],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "HELD"
        with pytest.raises(LockBusy):
            with file_lock(lp, blocking=False):
                pass
    finally:
        proc.wait(timeout=10)


def test_non_blocking_succeeds_when_free(tmp_path):
    with file_lock(_lock_path(tmp_path), blocking=False):
        pass


def test_lock_busy_is_runtimeerror():
    assert issubclass(LockBusy, RuntimeError)
