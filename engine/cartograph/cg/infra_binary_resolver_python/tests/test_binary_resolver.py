import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.binary_resolver import ResolveError, ResolvedBinary, resolve


def _make_executable(path, body="#!/bin/sh\nexit 0\n"):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def test_override_wins_over_path(tmp_path, monkeypatch):
    override = _make_executable(tmp_path / "tool")
    monkeypatch.setenv("PATH", "")  # PATH would have been empty anyway
    result = resolve("tool", override=override)
    assert isinstance(result, ResolvedBinary)
    assert result.path == override
    assert result.source == "override"
    assert result.name == "tool"


def test_path_lookup_when_no_override(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_executable(bindir / "toolx")
    monkeypatch.setenv("PATH", str(bindir))
    result = resolve("toolx")
    assert result.source == "path"
    assert result.path.endswith("toolx")


def test_missing_binary_raises(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ResolveError) as ei:
        resolve("definitely-not-a-real-binary-xyz")
    assert "definitely-not-a-real-binary-xyz" in str(ei.value)


def test_missing_binary_error_names_config_key(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ResolveError) as ei:
        resolve("nim", override_key="paths.nim")
    assert "paths.nim" in str(ei.value)


def test_override_must_exist(tmp_path):
    with pytest.raises(ResolveError) as ei:
        resolve("tool", override=str(tmp_path / "does-not-exist"))
    assert "does not exist" in str(ei.value)


def test_override_must_be_executable(tmp_path):
    not_executable = tmp_path / "not-exec"
    not_executable.write_text("plain file, no +x")
    with pytest.raises(ResolveError) as ei:
        resolve("tool", override=str(not_executable))
    assert "not an executable" in str(ei.value)


def test_override_error_mentions_config_key(tmp_path):
    with pytest.raises(ResolveError) as ei:
        resolve("nim", override=str(tmp_path / "missing"),
                override_key="paths.nim")
    assert "paths.nim" in str(ei.value)


def test_relative_override_is_resolved(tmp_path, monkeypatch):
    override = _make_executable(tmp_path / "tool")
    monkeypatch.chdir(tmp_path)
    result = resolve("tool", override="./tool")
    assert os.path.isabs(result.path)
    assert result.source == "override"


def test_empty_name_rejected():
    with pytest.raises(ResolveError):
        resolve("")


def test_resolved_binary_is_immutable(tmp_path, monkeypatch):
    override = _make_executable(tmp_path / "tool")
    result = resolve("tool", override=override)
    with pytest.raises(Exception):
        result.path = "/tampered"  # frozen dataclass
