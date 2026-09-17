"""TERRA_MAP env pinning: per-shell active map for concurrent sessions."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.map_status import collect_status_board
from terra.paths import (
    GLOBAL_MAP_ID,
    create_session_map,
    get_active_map_id,
    resolve_active_map,
    set_active_map_id,
    write_active_map,
)


@pytest.fixture(autouse=True)
def _reset_active_map():
    set_active_map_id(None)
    yield
    set_active_map_id(None)


def test_env_overrides_active_map_file(tmp_path: Path, monkeypatch):
    create_session_map(tmp_path, "exp_a", purpose="A")
    create_session_map(tmp_path, "exp_b", purpose="B")
    write_active_map(tmp_path, "exp_a")
    set_active_map_id(None)  # fresh shell: no CLI --map context
    assert resolve_active_map(tmp_path) == ("exp_a", "file")

    monkeypatch.setenv("TERRA_MAP", "exp_b")
    assert resolve_active_map(tmp_path) == ("exp_b", "env")
    assert get_active_map_id(tmp_path) == "exp_b"

    # a peer session flipping the pointer must not affect this shell
    write_active_map(tmp_path, "exp_a")
    set_active_map_id(None)
    assert get_active_map_id(tmp_path) == "exp_b"


def test_cli_context_beats_env(tmp_path: Path, monkeypatch):
    create_session_map(tmp_path, "exp_a", purpose="A")
    create_session_map(tmp_path, "exp_b", purpose="B")
    monkeypatch.setenv("TERRA_MAP", "exp_a")
    set_active_map_id("exp_b")
    assert resolve_active_map(tmp_path) == ("exp_b", "cli")


def test_default_and_file_sources(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TERRA_MAP", raising=False)
    assert resolve_active_map(tmp_path) == (GLOBAL_MAP_ID, "default")
    create_session_map(tmp_path, "exp", purpose="E", use=True)
    set_active_map_id(None)  # fresh shell reads the pointer file
    assert resolve_active_map(tmp_path) == ("exp", "file")


def test_env_invalid_slug_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TERRA_MAP", "Not A Slug!")
    with pytest.raises(ValueError):
        get_active_map_id(tmp_path)


def test_map_use_notes_env_override(tmp_path: Path, monkeypatch, capsys):
    import argparse

    from terra.cli import cmd_map_use

    monkeypatch.chdir(tmp_path)
    create_session_map(tmp_path, "exp", purpose="E")
    monkeypatch.setenv("TERRA_MAP", "exp")
    rc = cmd_map_use(argparse.Namespace(id="global"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "TERRA_MAP" in out and "NOTE:" in out
    assert "terra --map global" in out


def test_status_board_reports_source_and_missing_map(tmp_path: Path, monkeypatch):
    create_session_map(tmp_path, "exp", purpose="E")
    monkeypatch.setenv("TERRA_MAP", "ghost")
    board = collect_status_board(tmp_path)
    assert board["active_map"] == "ghost"
    assert board["active_map_source"] == "env"
    kinds = [a.get("kind") for a in board["attention"]]
    assert "active_map_missing" in kinds

    monkeypatch.setenv("TERRA_MAP", "exp")
    board = collect_status_board(tmp_path)
    kinds = [a.get("kind") for a in board["attention"]]
    assert "active_map_missing" not in kinds
