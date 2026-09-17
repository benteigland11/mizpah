"""Tests for ttl_disk_cache: hit/miss, expiry, corruption, atomicity, clear."""
from __future__ import annotations

import json
import os
import stat
import sys
import time

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.ttl_disk_cache import get, set, clear  # noqa: E402, A001


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_set_then_get_returns_value(tmp_path):
    set(str(tmp_path), "profile", {"name": "ada"}, ttl_seconds=60)
    assert get(str(tmp_path), "profile") == {"name": "ada"}


def test_get_missing_returns_none(tmp_path):
    assert get(str(tmp_path), "absent") is None


def test_set_creates_cache_root_if_missing(tmp_path):
    target = tmp_path / "does-not-exist-yet"
    set(str(target), "thing", 42, ttl_seconds=60)
    assert (target / "thing.json").is_file()
    assert get(str(target), "thing") == 42


def test_overwrite_replaces_value(tmp_path):
    set(str(tmp_path), "k", "first", ttl_seconds=60)
    set(str(tmp_path), "k", "second", ttl_seconds=60)
    assert get(str(tmp_path), "k") == "second"


def test_value_types_round_trip(tmp_path):
    cases = {
        "string": "hi",
        "number": 3.14,
        "list": [1, 2, 3],
        "dict": {"a": 1, "b": [True, None]},
        "null": None,
    }
    for k, v in cases.items():
        set(str(tmp_path), k, v, ttl_seconds=60)
        assert get(str(tmp_path), k) == v


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

def test_expired_entry_returns_none(tmp_path):
    set(str(tmp_path), "stale", "x", ttl_seconds=60)
    # Rewrite the entry's expires_at to be in the past.
    path = tmp_path / "stale.json"
    entry = json.loads(path.read_text())
    entry["expires_at"] = time.time() - 1
    path.write_text(json.dumps(entry))
    assert get(str(tmp_path), "stale") is None


def test_nonpositive_ttl_rejected(tmp_path):
    with pytest.raises(ValueError):
        set(str(tmp_path), "k", "v", ttl_seconds=0)
    with pytest.raises(ValueError):
        set(str(tmp_path), "k", "v", ttl_seconds=-5)


# ---------------------------------------------------------------------------
# Corruption tolerance — bad entries should miss, not raise
# ---------------------------------------------------------------------------

def test_corrupt_json_returns_none(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json")
    assert get(str(tmp_path), "broken") is None


def test_missing_expires_at_returns_none(tmp_path):
    path = tmp_path / "noexp.json"
    path.write_text(json.dumps({"value": "x"}))
    assert get(str(tmp_path), "noexp") is None


def test_non_numeric_expires_at_returns_none(tmp_path):
    path = tmp_path / "weird.json"
    path.write_text(json.dumps({"value": "x", "expires_at": "soon"}))
    assert get(str(tmp_path), "weird") is None


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_key", [
    "with/slash", "with space", "with.dot", "../escape", "",
    "questionmark?", "uniçode",
])
def test_invalid_key_rejected(tmp_path, bad_key):
    with pytest.raises(ValueError):
        set(str(tmp_path), bad_key, "v", ttl_seconds=60)
    with pytest.raises(ValueError):
        get(str(tmp_path), bad_key)


@pytest.mark.parametrize("good_key", [
    "profile", "abc123", "with-dash", "with_under",
    "MixedCase", "0", "X",
])
def test_valid_keys_accepted(tmp_path, good_key):
    set(str(tmp_path), good_key, "v", ttl_seconds=60)
    assert get(str(tmp_path), good_key) == "v"


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------

def test_clear_one_key(tmp_path):
    set(str(tmp_path), "a", 1, ttl_seconds=60)
    set(str(tmp_path), "b", 2, ttl_seconds=60)
    clear(str(tmp_path), "a")
    assert get(str(tmp_path), "a") is None
    assert get(str(tmp_path), "b") == 2


def test_clear_missing_key_is_silent(tmp_path):
    clear(str(tmp_path), "never-existed")  # should not raise


def test_clear_all(tmp_path):
    set(str(tmp_path), "a", 1, ttl_seconds=60)
    set(str(tmp_path), "b", 2, ttl_seconds=60)
    (tmp_path / "not-ours.txt").write_text("leave me alone")
    clear(str(tmp_path))
    assert get(str(tmp_path), "a") is None
    assert get(str(tmp_path), "b") is None
    assert (tmp_path / "not-ours.txt").is_file()  # non-json files preserved


def test_clear_missing_root_is_silent(tmp_path):
    clear(str(tmp_path / "never-created"))  # should not raise


def test_clear_invalid_key_rejected(tmp_path):
    with pytest.raises(ValueError):
        clear(str(tmp_path), "../escape")


# ---------------------------------------------------------------------------
# Atomicity — no temp leaks, no partial writes
# ---------------------------------------------------------------------------

def test_set_leaves_no_temp_files(tmp_path):
    set(str(tmp_path), "k", "v", ttl_seconds=60)
    leftovers = [n for n in os.listdir(tmp_path) if ".tmp" in n]
    assert leftovers == []


def test_set_with_unserializable_value_cleans_up(tmp_path):
    class NotJsonable:
        pass
    with pytest.raises(TypeError):
        set(str(tmp_path), "bad", NotJsonable(), ttl_seconds=60)
    leftovers = list(os.listdir(tmp_path))
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_concurrent_overwrite_keeps_consistent_entry(tmp_path):
    """Interleaved writes must each produce a complete entry, never a
    partial one. We can't easily reproduce a real race, but we can assert
    that after a sequence of overwrites the final file parses cleanly."""
    for i in range(20):
        set(str(tmp_path), "k", i, ttl_seconds=60)
    entry = json.loads((tmp_path / "k.json").read_text())
    assert entry["value"] == 19


# ---------------------------------------------------------------------------
# Permissions (best-effort; skip on platforms without POSIX modes)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not hasattr(os, "geteuid"), reason="POSIX-only permission check",
)
def test_set_writes_user_only_permissions(tmp_path):
    set(str(tmp_path), "secret", "shh", ttl_seconds=60)
    mode = stat.S_IMODE(os.stat(tmp_path / "secret.json").st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR  # 0o600
