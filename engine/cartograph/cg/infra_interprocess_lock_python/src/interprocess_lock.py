"""Cross-process advisory file lock, stdlib only.

A context manager that serializes a critical section across separate
processes (and threads) using an OS advisory lock on a lock file:
``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows. No third-party
dependency such as ``filelock`` is required.

Scope: this widget owns the *lock mechanism* only - acquire (blocking or
non-blocking) and release. It deliberately does not implement a
poll-with-timeout wait: a busy ``sleep`` loop is a caller policy, not a
primitive, and belongs in the consumer. ``blocking=True`` uses the OS's own
blocking wait (no spinning); ``blocking=False`` tries once and raises
:class:`LockBusy` if the lock is already held, which is the hook a caller
builds a timed retry around.

Two correctness details that trip up naive implementations:

* **Reentrancy is per-thread, keyed by lock path.** POSIX ``flock`` is
  associated with the open file *description*, so two ``open()`` calls to
  the same file from one process conflict - a nested acquire on the same
  call stack would self-deadlock. A thread-local depth counter (keyed by
  the resolved lock path) lets nested acquires on one thread share the held
  lock, while a different thread or process still contends through the OS.

* **Windows ``blocking=True`` is not indefinite.** ``msvcrt.locking`` with
  ``LK_LOCK`` retries for roughly ten seconds and then raises ``OSError``;
  POSIX ``LOCK_EX`` waits forever. Callers needing a guaranteed bound should
  use ``blocking=False`` and time their own retries.
"""

import os
import threading
from contextlib import contextmanager

__all__ = ["file_lock", "LockBusy"]


class LockBusy(RuntimeError):
    """Raised by a non-blocking acquire when the lock is already held."""


try:
    import fcntl

    def _acquire(fd: "object", blocking: bool) -> bool:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd.fileno(), flags)
            return True
        except OSError:
            return False

    def _release(fd: "object") -> None:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

except ImportError:  # Windows  # pragma: no cover
    import msvcrt

    def _acquire(fd: "object", blocking: bool) -> bool:
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), mode, 1)
            return True
        except OSError:
            return False

    def _release(fd: "object") -> None:
        try:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


_reentry = threading.local()


def _depths() -> dict:
    d = getattr(_reentry, "depths", None)
    if d is None:
        d = {}
        _reentry.depths = d
    return d


@contextmanager
def file_lock(lock_path: str, blocking: bool = True):
    """Hold an exclusive cross-process lock on ``lock_path`` for the block.

    Parameters
    ----------
    lock_path:
        Path to the lock file. Created if missing. Its parent directory must
        already exist.
    blocking:
        ``True`` waits (via the OS) until the lock is free. ``False`` tries
        once and raises :class:`LockBusy` if another holder has it.

    Reentrant within a single thread for the same ``lock_path``.
    """
    key = os.path.abspath(lock_path)
    depths = _depths()
    if depths.get(key, 0) > 0:  # already held on this thread
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return

    fd = open(key, "a+")
    try:
        if not _acquire(fd, blocking):
            raise LockBusy(f"Lock {key!r} is held by another process.")
        depths[key] = 1
        try:
            yield
        finally:
            depths[key] = 0
            _release(fd)
    finally:
        fd.close()
