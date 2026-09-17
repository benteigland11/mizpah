"""Deterministic directory tarball packer.

Packs a directory into a reproducible tarball: entries sorted by their
in-archive path, fixed mtime, zeroed uid/gid and empty uname/gname,
mode normalized to 0o644 / 0o755 (preserving only the executable bit
on regular files), and configurable glob excludes.

Two outputs - bytes (in-memory) or a file on disk. Both produce
identical archives for identical inputs, so callers can hash + compare
or content-address them. Symlinks are stored as symlinks by default
(not dereferenced), which is reproducible across systems but can be
disabled if the caller wants files only.

Pure stdlib (tarfile, fnmatch, hashlib, io). No third-party deps.
"""

import fnmatch
import hashlib
import io
import os
import stat
import tarfile
from dataclasses import dataclass


class TarballError(Exception):
    """Base error for directory tarball operations."""


class SourceNotFound(TarballError):
    """Raised when the source directory does not exist."""


@dataclass(frozen=True)
class PackedTarball:
    """Result of packing a directory.

    `data` is the archive bytes. `entries` is the in-archive paths
    actually included, in archive order (sorted). `sha256` is the
    hex digest of `data`, useful for content-addressing or verifying
    reproducibility across runs.
    """

    data: bytes
    entries: tuple[str, ...]
    sha256: str


def _excluded(rel_path: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    parts = rel_path.split("/")
    for pat in patterns:
        if fnmatch.fnmatch(rel_path, pat):
            return True
        # Also match on any path segment so callers can write `__pycache__`
        # without specifying `**/__pycache__`.
        if any(fnmatch.fnmatch(p, pat) for p in parts):
            return True
    return False


def _normalize_mode(path: str, is_dir: bool, *,
                    file_mode: int, exec_mode: int, dir_mode: int) -> int:
    if is_dir:
        return dir_mode
    try:
        st_mode = os.lstat(path).st_mode
    except OSError:
        return file_mode
    is_exec = bool(st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return exec_mode if is_exec else file_mode


def _walk_sorted(source_dir: str, excludes: tuple[str, ...],
                 follow_symlinks: bool) -> list[tuple[str, str]]:
    """Return [(absolute_path, archive_relative_path), ...] sorted by archive path."""
    out: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(source_dir,
                                                followlinks=follow_symlinks):
        rel_dir = os.path.relpath(dirpath, source_dir).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        # Filter dirnames in-place so excluded subtrees are not descended into.
        kept_dirs = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if not _excluded(rel, excludes):
                kept_dirs.append(d)
        dirnames[:] = kept_dirs

        if rel_dir and not _excluded(rel_dir, excludes):
            out.append((dirpath, rel_dir))

        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if _excluded(rel, excludes):
                continue
            out.append((os.path.join(dirpath, name), rel))

    out.sort(key=lambda pair: pair[1])
    return out


def pack_directory(
    source_dir: str,
    *,
    arcname: str | None = None,
    exclude: tuple[str, ...] = (),
    mtime: int = 0,
    compression: str | None = "gz",
    follow_symlinks: bool = False,
    file_mode: int = 0o644,
    exec_mode: int = 0o755,
    dir_mode: int = 0o755,
) -> PackedTarball:
    """Pack `source_dir` into a deterministic tarball returned as bytes.

    `arcname` is the root path inside the archive (defaults to the
    basename of source_dir). `compression` is one of "gz", "bz2", "xz",
    or None for an uncompressed tar. `mtime` is the fixed timestamp
    written for every entry.
    """
    if not os.path.isdir(source_dir):
        raise SourceNotFound(f"source_dir not found or not a directory: {source_dir!r}")
    if compression not in (None, "gz", "bz2", "xz"):
        raise TarballError(f"unsupported compression: {compression!r}")

    root_arcname = arcname if arcname is not None else os.path.basename(
        os.path.abspath(source_dir)
    )

    entries: list[str] = []
    buf = io.BytesIO()
    mode = "w" if compression is None else f"w:{compression}"
    with tarfile.open(fileobj=buf, mode=mode, format=tarfile.USTAR_FORMAT) as tf:
        # Root directory entry.
        root_info = tarfile.TarInfo(name=root_arcname)
        root_info.type = tarfile.DIRTYPE
        root_info.mode = dir_mode
        root_info.mtime = mtime
        root_info.uid = root_info.gid = 0
        root_info.uname = root_info.gname = ""
        tf.addfile(root_info)
        entries.append(root_arcname)

        for abs_path, rel_path in _walk_sorted(source_dir, exclude, follow_symlinks):
            arc_path = f"{root_arcname}/{rel_path}"
            is_link = os.path.islink(abs_path) and not follow_symlinks
            is_dir = os.path.isdir(abs_path) and not is_link

            info = tarfile.TarInfo(name=arc_path)
            info.mtime = mtime
            info.uid = info.gid = 0
            info.uname = info.gname = ""

            if is_link:
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(abs_path)
                info.mode = file_mode
                tf.addfile(info)
            elif is_dir:
                info.type = tarfile.DIRTYPE
                info.mode = _normalize_mode(
                    abs_path, is_dir=True,
                    file_mode=file_mode, exec_mode=exec_mode, dir_mode=dir_mode,
                )
                tf.addfile(info)
            else:
                info.type = tarfile.REGTYPE
                info.mode = _normalize_mode(
                    abs_path, is_dir=False,
                    file_mode=file_mode, exec_mode=exec_mode, dir_mode=dir_mode,
                )
                with open(abs_path, "rb") as fh:
                    data = fh.read()
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            entries.append(arc_path)

    data = buf.getvalue()
    return PackedTarball(
        data=data,
        entries=tuple(entries),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def write_tarball(packed: PackedTarball, output_path: str) -> str:
    """Write a packed tarball to disk. Returns the path written."""
    with open(output_path, "wb") as fh:
        fh.write(packed.data)
    return output_path
