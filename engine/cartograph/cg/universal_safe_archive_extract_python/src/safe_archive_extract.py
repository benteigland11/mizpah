"""Zip extraction that refuses members escaping the destination ("zip-slip").

``zipfile.ZipFile.extractall`` will happily write a member named
``../../etc/foo`` or ``/abs/path`` outside the target directory. When the
archive comes from an untrusted or partially-trusted source (a registry
download, a user-supplied import file), that is an arbitrary-write
vulnerability.

``safe_extractall`` validates every member path against the destination
*before writing anything*, raising :class:`UnsafeArchiveError` on the first
member that would escape. Guards absolute paths, ``..`` traversal, and (on
Windows) drive- and backslash-based escapes by normalizing each member the
same way the OS will when it joins it onto the destination.

This guards the *path* of each member. Python's ``zipfile`` does not restore
unix symlinks as symlinks (it writes a regular file with the link text), so
there is no symlink-escape vector to guard here.
"""

import os
import zipfile

__all__ = [
    "safe_extractall",
    "safe_extract_zip",
    "assert_members_safe",
    "UnsafeArchiveError",
]


class UnsafeArchiveError(ValueError):
    """Raised when an archive member would be written outside the destination."""


def assert_members_safe(names, dest: str) -> None:
    """Raise :class:`UnsafeArchiveError` if any of ``names`` would escape ``dest``.

    Use this when extracting members selectively (skipping some, renaming
    others) rather than via :func:`safe_extractall` - validate the whole name
    list up front, then do the selective writes knowing every target is safe.
    """
    dest_abs = os.path.abspath(dest)
    for name in names:
        target = os.path.abspath(os.path.join(dest_abs, name))
        if target != dest_abs and not target.startswith(dest_abs + os.sep):
            raise UnsafeArchiveError(
                f"Refusing to extract unsafe archive member {name!r}: it "
                f"escapes the destination directory. The archive may be "
                f"malicious or corrupt."
            )


def safe_extractall(zf: zipfile.ZipFile, dest: str) -> None:
    """Extract all members of ``zf`` into ``dest``, rejecting any escape.

    Validates every member path first, so a single unsafe member aborts the
    whole extraction before anything is written.
    """
    dest_abs = os.path.abspath(dest)
    assert_members_safe(zf.namelist(), dest_abs)
    os.makedirs(dest_abs, exist_ok=True)
    zf.extractall(dest_abs)


def safe_extract_zip(zip_path: str, dest: str) -> None:
    """Open the zip at ``zip_path`` and :func:`safe_extractall` it into ``dest``."""
    with zipfile.ZipFile(zip_path) as zf:
        safe_extractall(zf, dest)
