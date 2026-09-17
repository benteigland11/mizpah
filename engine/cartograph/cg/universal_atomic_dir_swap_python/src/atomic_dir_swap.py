"""Atomically replace a directory by building a sibling and renaming it in.

The hazard this removes: the ``rmtree(dest)`` then ``rebuild`` pattern, which
destroys the live directory for the entire (failure-prone) duration of the
rebuild. A crash mid-rebuild leaves ``dest`` deleted-but-not-replaced.

Instead, build the full replacement in a sibling temp directory and swap it in
with renames. The live copy is moved aside first and restored if the swap
fails, so a failure never leaves ``dest`` torn down. Because the staged
directory is a sibling, the swap is a same-filesystem rename (atomic on POSIX
and Windows), not a copy.

``staged_dir`` is the high-level entry point: a context manager that yields an
empty build directory and swaps it into place on clean exit, discarding it on
any exception. ``atomic_swap_dir`` is the lower-level swap if you have already
built the replacement elsewhere on the same filesystem.

Note: this is whole-directory replacement, not a merge - ``dest`` ends up
containing exactly the staged contents, nothing from the previous version.
"""

import os
import shutil
import stat
import sys
import tempfile
import uuid
from contextlib import contextmanager

__all__ = ["staged_dir", "atomic_swap_dir", "robust_rmtree"]


def _clear_readonly_and_retry(func, path, exc_info):
    """``shutil.rmtree`` error handler: strip the read-only attribute and retry.

    On Windows a read-only file makes ``os.unlink``/``os.rmdir`` raise
    ``PermissionError`` (WinError 5), so a plain ``rmtree`` stops partway
    through and leaves the tree half-deleted. POSIX deletes are governed by
    the parent directory's permissions instead, so the handler also chmods
    the parent when retrying the entry itself doesn't help.
    """
    del exc_info
    try:
        os.chmod(path, stat.S_IRWXU)
        func(path)
        return
    except OSError:
        pass
    parent = os.path.dirname(path)
    if parent:
        os.chmod(parent, stat.S_IRWXU)
    func(path)


def robust_rmtree(path: str, ignore_errors: bool = False) -> None:
    """``shutil.rmtree`` that survives read-only entries.

    Drop-in replacement for the ``rmtree(path)`` / ``rmtree(path,
    ignore_errors=True)`` call sites. Read-only files (the Windows attribute
    or a POSIX mode with the write bit stripped) are made writable and
    deleted instead of aborting mid-tree.
    """
    # ``onerror`` is deprecated since 3.12; ``onexc`` doesn't exist before it.
    if sys.version_info >= (3, 12):
        kwargs = {"onexc": lambda f, p, e: _clear_readonly_and_retry(f, p, None)}
    else:
        kwargs = {"onerror": _clear_readonly_and_retry}
    try:
        shutil.rmtree(path, **kwargs)
    except FileNotFoundError:
        if not ignore_errors:
            raise
    except OSError:
        if not ignore_errors:
            raise


def atomic_swap_dir(new_dir: str, dest: str) -> None:
    """Replace ``dest`` with the fully-built ``new_dir`` using renames.

    ``new_dir`` must already contain the complete replacement and live on the
    same filesystem as ``dest``. The live ``dest`` (if any) is moved aside
    first and restored if the swap fails, so a crash never leaves ``dest``
    deleted-but-not-replaced. ``dest``'s parent is created if missing.
    """
    dest_abs = os.path.abspath(dest)
    parent = os.path.dirname(dest_abs)
    os.makedirs(parent, exist_ok=True)

    backup = None
    if os.path.lexists(dest_abs):
        backup = os.path.join(parent, f".cg-old-{uuid.uuid4().hex}")
        os.replace(dest_abs, backup)  # atomic: live copy moved aside
    try:
        os.replace(new_dir, dest_abs)  # atomic: replacement moved into place
    except BaseException:
        # Roll back: restore the original if we cleared the slot.
        if backup is not None and not os.path.lexists(dest_abs):
            os.replace(backup, dest_abs)
            backup = None
        raise
    finally:
        if backup is not None:
            robust_rmtree(backup, ignore_errors=True)


@contextmanager
def staged_dir(dest: str):
    """Yield an empty temp dir (sibling of ``dest``) to build a replacement in.

    On clean exit the staged dir is atomically swapped into ``dest``. On any
    exception it is discarded and ``dest`` is left untouched. The temp dir is a
    sibling so the swap is a same-filesystem rename.
    """
    dest_abs = os.path.abspath(dest)
    parent = os.path.dirname(dest_abs)
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=parent, prefix=".cg-new-")
    try:
        yield tmp
    except BaseException:
        robust_rmtree(tmp, ignore_errors=True)
        raise
    else:
        try:
            atomic_swap_dir(tmp, dest_abs)
        finally:
            # On a successful swap, tmp was renamed into place; only a failed
            # swap leaves it behind.
            if os.path.isdir(tmp):
                robust_rmtree(tmp, ignore_errors=True)
