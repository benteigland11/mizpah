"""Persistent on-disk cache with per-entry TTL.

One JSON file per key under a caller-supplied root directory. Each file
holds {"value": <json>, "expires_at": <unix_ts>}. Writes are atomic via
tmp-file + os.replace so a crash mid-write cannot corrupt a previously
valid entry. Files are written 0600 since callers may cache identity
tokens, API responses, or other sensitive payloads.

Designed for short-lived processes (CLIs, scripts, cron jobs) that need
to survive across invocations without paying a network round-trip every
time. For in-process caching with eviction policy, use an in-memory
cache; this widget is the persistence tier.

Keys must match [A-Za-z0-9_-]+ so they can be used directly as filenames
without any escaping logic. Reject anything else at the boundary rather
than silently mangling.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from typing import Any


_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SUFFIX = ".json"


def _key_path(cache_root: str, key: str) -> str:
    if not _KEY_RE.match(key):
        raise ValueError(
            f"cache key must match [A-Za-z0-9_-]+, got {key!r}"
        )
    return os.path.join(cache_root, key + _SUFFIX)


def get(cache_root: str, key: str) -> Any | None:
    """Return the cached value for key, or None on miss/expiry/corruption.

    Treats unreadable, malformed, or expired entries as a miss rather
    than raising — the consumer's fallback path is the source of truth,
    not the cache. Corrupt entries are silently ignored; the next set()
    will overwrite them.
    """
    path = _key_path(cache_root, key)
    try:
        with open(path) as f:
            entry = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    expires_at = entry.get("expires_at")
    if not isinstance(expires_at, (int, float)) or time.time() >= expires_at:
        return None
    return entry.get("value")


def set(cache_root: str, key: str, value: Any, ttl_seconds: float) -> None:
    """Write value under key with a TTL. Overwrites any existing entry.

    Atomic via tmp-file + os.replace so a crash during write leaves the
    previous entry (or no entry) intact. Mode 0600 on the final file.
    Raises if value is not JSON-serializable or if the cache_root cannot
    be created.
    """
    if ttl_seconds <= 0:
        raise ValueError(
            f"ttl_seconds must be positive, got {ttl_seconds!r}"
        )
    path = _key_path(cache_root, key)
    os.makedirs(cache_root, exist_ok=True)
    entry = {"value": value, "expires_at": time.time() + ttl_seconds}
    # Use NamedTemporaryFile so cleanup happens on exception, but in the
    # same directory as the target so os.replace stays atomic on the
    # same filesystem.
    fd, tmp_path = tempfile.mkstemp(
        prefix=key + ".", suffix=".tmp", dir=cache_root,
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(entry, f)
        try:
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            # Permission set is best-effort on filesystems that don't
            # support it (e.g. some network mounts on Windows). The
            # replace still happens.
            pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def clear(cache_root: str, key: str | None = None) -> None:
    """Remove one entry (key=...) or every entry under cache_root (key=None).

    Silent on missing files. Does not remove cache_root itself when
    wiping all entries — only the *.json files inside it.
    """
    if key is not None:
        path = _key_path(cache_root, key)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    if not os.path.isdir(cache_root):
        return
    for name in os.listdir(cache_root):
        if not name.endswith(_SUFFIX):
            continue
        try:
            os.remove(os.path.join(cache_root, name))
        except FileNotFoundError:
            pass
