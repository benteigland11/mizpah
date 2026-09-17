"""Verify a tarball's contents match a declared file manifest.

Given a tarball and three optional declarations, returns a structured
report:

  * `expected`  - the full enumerated set of paths the archive should
                  contain. Anything in the archive not in this set is
                  reported as `extra`; anything in this set not in the
                  archive is reported as `missing`. When `expected` is
                  omitted, neither `extra` nor `missing` is reported.
  * `required`  - a subset that MUST be present, even when the full
                  expected list is unknown. Useful for sanity checks
                  ("manifest.json must be in the archive") without
                  enumerating every file.
  * `forbidden` - paths that must NOT be present. Useful for catching
                  build artifacts or vendor dirs that should have been
                  excluded at pack time.

Pure stdlib (tarfile). Sets are returned sorted for stable diffs.
"""

import io
import os
import tarfile
from dataclasses import dataclass


class ConsistencyError(Exception):
    """Base class for consistency errors."""


@dataclass(frozen=True)
class ConsistencyReport:
    """Structured comparison result.

    `ok` is True iff missing/extra/forbidden_present are all empty
    relative to whatever declarations the caller provided. `all_files`
    is the full sorted file list found in the archive (regular files
    only - directories and links are excluded).
    """

    ok: bool
    all_files: tuple[str, ...]
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    forbidden_present: tuple[str, ...]
    required_missing: tuple[str, ...]


def _list_archive_files(archive: bytes | str) -> set[str]:
    """Return the set of regular-file paths inside the archive."""
    if isinstance(archive, (bytes, bytearray)):
        opener = tarfile.open(fileobj=io.BytesIO(archive), mode="r:*")
    elif isinstance(archive, str):
        if not os.path.isfile(archive):
            raise ConsistencyError(f"archive not found: {archive!r}")
        opener = tarfile.open(name=archive, mode="r:*")
    else:
        raise ConsistencyError(
            f"archive must be bytes or path string, got {type(archive).__name__}"
        )
    with opener as tf:
        return {m.name for m in tf.getmembers() if m.isreg()}


def check_consistency(
    archive: bytes | str,
    *,
    expected: set[str] | None = None,
    required: set[str] | None = None,
    forbidden: set[str] | None = None,
) -> ConsistencyReport:
    """Compare the archive's file list to the caller's declarations.

    All declarations are optional - omitted ones contribute nothing to
    the report's `ok` verdict. Path strings are compared literally; the
    caller is responsible for normalizing separators or arcname prefixes
    before calling.
    """
    found = _list_archive_files(archive)

    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    if expected is not None:
        missing = tuple(sorted(expected - found))
        extra = tuple(sorted(found - expected))

    required_missing: tuple[str, ...] = ()
    if required is not None:
        required_missing = tuple(sorted(required - found))

    forbidden_present: tuple[str, ...] = ()
    if forbidden is not None:
        forbidden_present = tuple(sorted(forbidden & found))

    ok = (
        not missing and not extra
        and not required_missing and not forbidden_present
    )
    return ConsistencyReport(
        ok=ok,
        all_files=tuple(sorted(found)),
        missing=missing,
        extra=extra,
        forbidden_present=forbidden_present,
        required_missing=required_missing,
    )
