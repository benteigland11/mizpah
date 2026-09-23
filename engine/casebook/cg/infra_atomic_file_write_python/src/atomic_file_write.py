"""Atomically write bytes or text to a file.

Writes content to a sibling temp file in the target directory, then
renames over the destination via :func:`os.replace`. Readers either
see the previous contents or the new contents — never a partial,
half-written file.

Why both functions live here: text vs bytes use different open modes
and different fsync semantics. Sharing one function with a mode flag
made callers easier to write wrong (forgetting to encode, double-
encoding). Two named entry points are clearer.

Durability: ``fsync=True`` flushes both the file and (best-effort) the
parent directory before the rename, guaranteeing the new content
survives a sudden power loss. Default is ``False`` because most
callers want speed; manifests, journals, and crash-critical state
should opt in.

Cross-platform: ``os.replace`` is atomic on POSIX and Windows alike
when source and destination are on the same filesystem. The temp file
is always created next to the target precisely to ensure this. Crash
during write leaves a ``<target><temp_suffix>`` file in the
destination directory; callers wanting crash cleanup can glob for it.

The destination directory must already exist; the widget does not
``mkdir -p`` for you. That keeps the surface narrow and prevents
accidentally creating directories on typo'd paths.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Union

PathLike = Union[str, Path]

DEFAULT_TEMP_SUFFIX = ".tmp"


def _validate_suffix(temp_suffix: str) -> str:
    if not isinstance(temp_suffix, str):
        raise TypeError("temp_suffix must be a string")
    if not temp_suffix:
        raise ValueError("temp_suffix must be non-empty")
    return temp_suffix


def _fsync_dir(directory: Path) -> None:
    # Directory fsync is the only way to guarantee the rename itself
    # is durable across power loss. Not all platforms support opening
    # a directory for fsync — Windows in particular doesn't — so we
    # swallow the error rather than fail the write.
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(
    path: Path,
    data: bytes,
    *,
    fsync: bool,
    temp_suffix: str,
    before_replace: Callable[[], None] | None,
) -> Path:
    parent = path.parent
    if not parent.is_dir():
        raise FileNotFoundError(
            f"destination directory does not exist: {parent}"
        )
    tmp = path.with_name(path.name + temp_suffix)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        if before_replace is not None:
            before_replace()
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if fsync:
        _fsync_dir(parent)
    return path


def atomic_write_bytes(
    path: PathLike,
    data: bytes,
    *,
    fsync: bool = False,
    temp_suffix: str = DEFAULT_TEMP_SUFFIX,
    before_replace: Callable[[], None] | None = None,
) -> Path:
    """Atomically write ``data`` to ``path``.

    ``before_replace`` runs after the complete temp file is closed but before
    replacement; if it raises, the prior destination is preserved and the
    temp file is removed. Returns the resolved destination ``Path``.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes")
    temp_suffix = _validate_suffix(temp_suffix)
    return _atomic_write(
        Path(path), bytes(data), fsync=fsync, temp_suffix=temp_suffix,
        before_replace=before_replace,
    )


def atomic_write_text(
    path: PathLike,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync: bool = False,
    temp_suffix: str = DEFAULT_TEMP_SUFFIX,
    before_replace: Callable[[], None] | None = None,
) -> Path:
    """Atomically write ``text`` to ``path`` using ``encoding``.

    ``before_replace`` has the same commit-boundary contract as the bytes API.
    Returns the resolved destination ``Path``.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(encoding, str) or not encoding:
        raise ValueError("encoding must be a non-empty string")
    temp_suffix = _validate_suffix(temp_suffix)
    return _atomic_write(
        Path(path),
        text.encode(encoding),
        fsync=fsync,
        temp_suffix=temp_suffix,
        before_replace=before_replace,
    )
