"""Filesystem safety glue for library-mutating operations.

Thin wrappers over three widgets that own the actual mechanisms:

* ``cg.infra_interprocess_lock_python`` - cross-process advisory lock
* ``cg.universal_atomic_dir_swap_python`` - crash-safe directory replace
* ``cg.universal_safe_archive_extract_python`` - zip-slip-guarded extraction

This module owns only CLI *policy*: where the library lock file lives, and the
poll/timeout wait loop. The wait loop can't live in the lock widget - a busy
``sleep`` is blocked in widget ``src`` by the validator - so the widget exposes
non-blocking ``file_lock(blocking=False)`` and the timed retry lives here.
"""

import os
import time
from contextlib import contextmanager

from cg.infra_interprocess_lock_python.src.interprocess_lock import (
    file_lock as _file_lock,
    LockBusy as _LockBusy,
)
from cg.universal_atomic_dir_swap_python.src.atomic_dir_swap import (
    staged_dir,
    atomic_swap_dir,
    robust_rmtree,
)
from cg.universal_safe_archive_extract_python.src.safe_archive_extract import (
    safe_extractall,
    safe_extract_zip,
    assert_members_safe,
    UnsafeArchiveError,
)

__all__ = [
    "safe_extractall",
    "safe_extract_zip",
    "assert_members_safe",
    "UnsafeArchiveError",
    "staged_dir",
    "atomic_swap_dir",
    "robust_rmtree",
    "widget_lock",
    "library_lock",
    "LockTimeout",
]

LOCK_FILENAME = ".cartograph.lock"
LOCK_DIRNAME = ".locks"


class LockTimeout(RuntimeError):
    """Raised when a lock can't be acquired within the timeout."""


def _widget_lock_id(widget_id):
    """Filesystem-safe lock-file stem for a widget id.

    Widget ids can carry ``@owner/`` and registry prefixes, so map the path
    separators to a flat name that still collides only for the same widget.
    """
    return widget_id.replace("/", "__").replace("\\", "__").replace("@", "_at_")


@contextmanager
def _path_lock(lock_path, timeout, poll, what):
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    # Acquire with a timed retry, kept separate from the body so an exception
    # raised inside the locked section is never mistaken for contention.
    deadline = time.monotonic() + timeout
    cm = None
    while cm is None:
        attempt = _file_lock(lock_path, blocking=False)
        try:
            attempt.__enter__()
            cm = attempt
        except _LockBusy:
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"Could not acquire the {what} lock within {timeout:.0f}s "
                    f"({lock_path}). Another cartograph process may be holding "
                    f"it; retry once it finishes."
                )
            time.sleep(poll)
    try:
        yield
    finally:
        cm.__exit__(None, None, None)


def widget_lock(library_path, widget_id, timeout=30.0, poll=0.1):
    """Exclusive cross-process lock scoped to a SINGLE widget.

    This is the right granularity for checkin / sync-pull / delete / adopt:
    two agents operating on *different* widgets run concurrently, and only
    operations on the *same* widget serialize (so their staged swaps can't
    interleave). The lock file lives in ``<library>/.locks/`` - a stable
    location outside the widget directory, so it survives the atomic dir swap
    and exists even before a brand-new widget's directory does.

    Reentrant within a single thread for the same widget.
    """
    lock_path = os.path.join(library_path, LOCK_DIRNAME,
                             f"{_widget_lock_id(widget_id)}.lock")
    return _path_lock(lock_path, timeout, poll, f"widget '{widget_id}'")


def library_lock(lock_dir, timeout=30.0, poll=0.1):
    """Exclusive cross-process lock over the WHOLE library.

    Coarse - serializes against every other library mutation regardless of
    widget. Prefer :func:`widget_lock` for per-widget operations; reserve this
    for the rare op that needs a library-wide barrier (e.g. a consistent
    full-library snapshot). Reentrant within a single thread.
    """
    lock_path = os.path.join(lock_dir, LOCK_FILENAME)
    return _path_lock(lock_path, timeout, poll, "library")
