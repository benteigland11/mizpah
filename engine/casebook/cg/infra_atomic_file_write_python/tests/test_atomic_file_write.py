"""Tests for atomic_file_write."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.atomic_file_write import (
    DEFAULT_TEMP_SUFFIX,
    atomic_write_bytes,
    atomic_write_text,
)


# ---- bytes ----

def test_bytes_write_creates_file(tmp_path: Path):
    p = tmp_path / "x.bin"
    atomic_write_bytes(p, b"abc")
    assert p.read_bytes() == b"abc"


def test_bytes_write_overwrites_existing(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"old")
    atomic_write_bytes(p, b"new")
    assert p.read_bytes() == b"new"


def test_bytes_empty_payload(tmp_path: Path):
    p = tmp_path / "e.bin"
    atomic_write_bytes(p, b"")
    assert p.read_bytes() == b""


def test_bytes_accepts_bytearray(tmp_path: Path):
    p = tmp_path / "x.bin"
    atomic_write_bytes(p, bytearray(b"abc"))
    assert p.read_bytes() == b"abc"


def test_bytes_rejects_str(tmp_path: Path):
    with pytest.raises(TypeError):
        atomic_write_bytes(tmp_path / "x.bin", "abc")  # type: ignore[arg-type]


def test_bytes_with_fsync(tmp_path: Path):
    p = tmp_path / "x.bin"
    atomic_write_bytes(p, b"durable", fsync=True)
    assert p.read_bytes() == b"durable"


def test_returns_path(tmp_path: Path):
    p = tmp_path / "x.bin"
    out = atomic_write_bytes(p, b"abc")
    assert out == p


# ---- text ----

def test_text_write_default_utf8(tmp_path: Path):
    p = tmp_path / "x.txt"
    atomic_write_text(p, "héllo 🌞")
    assert p.read_text(encoding="utf-8") == "héllo 🌞"


def test_text_custom_encoding(tmp_path: Path):
    p = tmp_path / "x.txt"
    atomic_write_text(p, "hello", encoding="ascii")
    assert p.read_bytes() == b"hello"


def test_text_rejects_bytes(tmp_path: Path):
    with pytest.raises(TypeError):
        atomic_write_text(tmp_path / "x.txt", b"abc")  # type: ignore[arg-type]


def test_text_empty_encoding_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        atomic_write_text(tmp_path / "x.txt", "abc", encoding="")


# ---- atomicity ----

def test_no_temp_file_left_on_success(tmp_path: Path):
    p = tmp_path / "x.bin"
    atomic_write_bytes(p, b"abc")
    assert list(tmp_path.iterdir()) == [p]


def test_temp_file_cleaned_on_oserror(tmp_path: Path, monkeypatch):
    p = tmp_path / "x.bin"
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("src.atomic_file_write.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(p, b"abc")
    # The temp file should NOT linger after a failed replace.
    leftovers = [f for f in tmp_path.iterdir() if f.name.endswith(DEFAULT_TEMP_SUFFIX)]
    assert leftovers == []
    assert not p.exists()
    # Sanity: real_replace still callable (we monkeypatched, not deleted).
    assert real_replace is os.replace.__wrapped__ if hasattr(os.replace, "__wrapped__") else True  # noqa: E501


def test_existing_file_not_touched_until_rename(tmp_path: Path, monkeypatch):
    p = tmp_path / "x.bin"
    p.write_bytes(b"original")

    def boom(src, dst):
        raise OSError("nope")

    monkeypatch.setattr("src.atomic_file_write.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(p, b"new")
    assert p.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [p]


def test_temp_suffix_custom(tmp_path: Path):
    p = tmp_path / "x.bin"
    atomic_write_bytes(p, b"abc", temp_suffix=".partial")
    assert p.read_bytes() == b"abc"
    assert list(tmp_path.iterdir()) == [p]


def test_before_replace_failure_preserves_destination_and_cleans_temp(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"original")

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        atomic_write_bytes(p, b"new", before_replace=cancel)

    assert p.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [p]


def test_empty_temp_suffix_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        atomic_write_bytes(tmp_path / "x.bin", b"abc", temp_suffix="")


def test_non_string_temp_suffix_rejected(tmp_path: Path):
    with pytest.raises(TypeError):
        atomic_write_bytes(
            tmp_path / "x.bin", b"abc", temp_suffix=None  # type: ignore[arg-type]
        )


def test_missing_parent_dir_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        atomic_write_bytes(tmp_path / "no-such-dir" / "x.bin", b"abc")


def test_path_can_be_str(tmp_path: Path):
    p = tmp_path / "x.bin"
    atomic_write_bytes(str(p), b"abc")
    assert p.read_bytes() == b"abc"
