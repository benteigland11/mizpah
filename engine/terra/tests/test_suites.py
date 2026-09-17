"""Suites: ordered probe recipes with shared to."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.probe_init import init_probe
from terra.suites import create_suite, list_suites, run_suite


def test_suite_create_and_run(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "a", purpose="a")
    init_probe(tmp_path, "b", purpose="b")
    create_suite(tmp_path, "pair", probes=["a", "b"], default_to={"kind": "town"})
    rows = list_suites(tmp_path)
    assert len(rows) == 1
    assert rows[0]["ok"] is True

    summary = run_suite(tmp_path, "pair", to={"kind": "town", "id": "x"})
    assert summary["ok"] is True
    assert len(summary["run_ids"]) == 2
    assert summary["results"][0]["probe_id"] == "a"
    assert summary["results"][1]["probe_id"] == "b"


def test_suite_missing_probe_at_create(tmp_path: Path):
    init_probe(tmp_path, "only", purpose="o")
    with pytest.raises(FileNotFoundError):
        create_suite(tmp_path, "bad", probes=["only", "missing"])


def test_suite_uses_default_to(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    create_suite(
        tmp_path, "s", probes=["p"], default_to={"kind": "server", "id": "local"}
    )
    summary = run_suite(tmp_path, "s")  # no override
    assert summary["ok"] is True
    assert summary["to"]["kind"] == "server"
