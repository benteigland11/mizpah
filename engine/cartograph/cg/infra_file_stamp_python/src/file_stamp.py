"""
File Stamp - fingerprint-based change detection for file sets.

Computes a SHA-256 digest over a set of files (paths + contents) and
stores it as a JSON stamp. On subsequent runs, recomputes and compares
to detect whether any watched file has changed.

Use cases: build cache invalidation, validation short-circuiting,
incremental processing pipelines.
"""

import glob as _glob
import hashlib
import json
import os
from datetime import datetime, timezone


STAMP_FILE = ".file_stamp.json"

_SKIP_EXTENSIONS = frozenset({".pyc", ".pyo", ".o", ".so", ".dylib"})
_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", ".git", "node_modules"})

# Magic-byte signatures for compiled executables that compilers leave behind
# without an extension (e.g. `nimble c -r tests/foo` produces `tests/foo` as an
# ELF binary on Linux). These aren't source and must not feed the fingerprint
# or the stamp goes stale the moment a post-validation cleanup removes them.
_BINARY_MAGIC_PREFIXES = (
    b"\x7fELF",          # ELF (Linux / *BSD)
    b"MZ",               # PE (Windows .exe / .dll, incl. console & GUI subsystems)
    b"\xfe\xed\xfa\xce", # Mach-O 32-bit big-endian
    b"\xfe\xed\xfa\xcf", # Mach-O 64-bit big-endian
    b"\xce\xfa\xed\xfe", # Mach-O 32-bit little-endian
    b"\xcf\xfa\xed\xfe", # Mach-O 64-bit little-endian
    b"\xca\xfe\xba\xbe", # Mach-O universal / fat binary
    b"\x00asm",          # WebAssembly
)


def _is_compiled_binary(path):
    """Return True if *path* starts with an executable-format magic signature."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return False
    return any(head.startswith(sig) for sig in _BINARY_MAGIC_PREFIXES)


def collect_files(root, patterns=None, skip_dirs=None, skip_extensions=None):
    """Return sorted absolute paths matching glob patterns under root.

    Args:
        root: Base directory to search from.
        patterns: Glob patterns relative to root. Defaults to common source
                  patterns (src/, tests/, examples/, plus root config files).
        skip_dirs: Directory names to exclude. Defaults to __pycache__,
                   .pytest_cache, .git, node_modules.
        skip_extensions: File extensions to exclude. Defaults to .pyc, .pyo,
                         .o, .so, .dylib.

    Returns:
        Sorted list of absolute file paths.
    """
    if skip_dirs is None:
        skip_dirs = _SKIP_DIRS
    if skip_extensions is None:
        skip_extensions = _SKIP_EXTENSIONS
    if patterns is None:
        patterns = [
            os.path.join(root, "src", "**", "*"),
            os.path.join(root, "tests", "**", "*"),
            os.path.join(root, "examples", "**", "*"),
            os.path.join(root, "*.json"),
            os.path.join(root, "*.toml"),
            os.path.join(root, "*.yaml"),
            os.path.join(root, "*.yml"),
        ]

    files = set()
    for pattern in patterns:
        for f in _glob.glob(pattern, recursive=True):
            if not os.path.isfile(f):
                continue
            rel_parts = os.path.relpath(f, root).split(os.sep)
            if any(part in skip_dirs for part in rel_parts):
                continue
            if os.path.splitext(f)[1] in skip_extensions:
                continue
            if _is_compiled_binary(f):
                continue
            files.add(os.path.abspath(f))
    return sorted(files)


def fingerprint(root, files=None, **collect_kwargs):
    """Compute a SHA-256 hex digest over (relative-path, content) pairs.

    Args:
        root: Base directory (used for relative path computation).
        files: Explicit list of absolute paths. If None, calls
               collect_files(root, **collect_kwargs).

    Returns:
        Hex digest string.
    """
    if files is None:
        files = collect_files(root, **collect_kwargs)

    h = hashlib.sha256()
    for fpath in files:
        rel = os.path.relpath(fpath, root)
        h.update(rel.encode())
        try:
            with open(fpath, "rb") as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()


def write_stamp(root, metadata=None, stamp_name=STAMP_FILE, **collect_kwargs):
    """Write a stamp file after computing the current fingerprint.

    Args:
        root: Directory to fingerprint and write the stamp into.
        metadata: Optional dict of extra fields to include in the stamp
                  (e.g. {"language": "python", "tool_version": "1.0"}).
        stamp_name: Filename for the stamp. Defaults to .file_stamp.json.
        **collect_kwargs: Passed to collect_files (patterns, skip_dirs, etc).

    Returns:
        The stamp dict that was written.
    """
    stamp = {
        "fingerprint": fingerprint(root, **collect_kwargs),
        "stamped_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        stamp.update(metadata)

    stamp_path = os.path.join(root, stamp_name)
    with open(stamp_path, "w") as f:
        json.dump(stamp, f)
    return stamp


def read_stamp(root, stamp_name=STAMP_FILE):
    """Read an existing stamp file. Returns the stamp dict or None."""
    stamp_path = os.path.join(root, stamp_name)
    try:
        with open(stamp_path) as f:
            return json.load(f)
    except Exception:
        return None


def adopt_stamp(root, stamp, stamp_name=STAMP_FILE, **collect_kwargs):
    """Adopt a stamp produced elsewhere, if it matches root's current files.

    Recomputes the fingerprint over root and compares it to the candidate
    stamp's fingerprint. On match, writes the stamp verbatim (the original
    stamped_at and metadata are preserved - adoption transports a stamping
    event, it does not create one). On mismatch, writes nothing.

    Use cases: restoring a stamp alongside files transported between
    machines (archive download, sync, cache restore) so the receiver can
    prove the files are exactly the ones that were stamped.

    Args:
        root: Directory the stamp claims to describe.
        stamp: Candidate stamp dict. Must contain a "fingerprint" key.
        stamp_name: Filename for the stamp. Defaults to .file_stamp.json.
        **collect_kwargs: Passed to collect_files for recomputing fingerprint.

    Returns:
        True if the fingerprint matched and the stamp was written,
        False otherwise (including a missing/empty fingerprint field).
    """
    expected = stamp.get("fingerprint") if isinstance(stamp, dict) else None
    if not expected:
        return False
    if fingerprint(root, **collect_kwargs) != expected:
        return False
    stamp_path = os.path.join(root, stamp_name)
    with open(stamp_path, "w") as f:
        json.dump(stamp, f)
    return True


def is_stamp_valid(root, metadata_match=None, stamp_name=STAMP_FILE,
                   **collect_kwargs):
    """Check if the stamp is still valid (no files changed).

    Args:
        root: Directory to check.
        metadata_match: Optional dict of metadata keys that must match.
                        e.g. {"language": "python"} ensures the stamp was
                        written for the same language.
        stamp_name: Filename for the stamp.
        **collect_kwargs: Passed to collect_files for recomputing fingerprint.

    Returns:
        True if stamp exists, metadata matches, and fingerprint is current.
    """
    stamp = read_stamp(root, stamp_name)
    if stamp is None:
        return False

    if metadata_match:
        for key, value in metadata_match.items():
            if stamp.get(key) != value:
                return False

    current = fingerprint(root, **collect_kwargs)
    return stamp.get("fingerprint") == current
