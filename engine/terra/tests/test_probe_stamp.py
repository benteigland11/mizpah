"""Validation stamps: a probe measures only after passing for its exact source."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.probe_stamp import (
    STAMP_MISSING,
    STAMP_STALE,
    STAMP_VALID,
    check_probe_stamp,
    read_probe_stamp,
)
from terra.probe_validate import validate_probe_dir

GOOD = (
    "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
    "KIND = 'watch'\n"
    "DURATION_S = 0\n"
    "def run(ctx=None):\n"
    "    ctx = ctx or {}\n"
    "    to = ctx.get('to') or {'kind': 'default'}\n"
    "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
    "            'measures': [{'quantity': 'q', 'value': %s}]}\n"
)

BAD = "def run(ctx=None):\n    return {'to': {'kind': 'x'}, 'status': 'ok', 'artifacts': []}\n"


def _pdir(root: Path, pid: str = "p") -> Path:
    return root / ".terra" / "map" / "probes" / pid


def _mk(root: Path, source: str, pid: str = "p") -> Path:
    init_probe(root, pid, purpose="p")
    pdir = _pdir(root, pid)
    (pdir / "probe.py").write_text(source, encoding="utf-8")
    return pdir


def test_validate_writes_stamp_and_run_reuses_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdir = _mk(tmp_path, GOOD % 1)
    assert check_probe_stamp(pdir)["state"] == STAMP_MISSING

    result = validate_probe_dir(pdir)
    assert result["ok"]
    assert result["stamp"]["source_sha256"]
    assert check_probe_stamp(pdir)["state"] == STAMP_VALID

    stamp = run_probe(tmp_path, "p", to={"kind": "region"})
    assert stamp["validation"]["state"] == STAMP_VALID
    assert stamp["validation"]["revalidated"] is False
    assert stamp["validation"]["source_sha256"] == result["stamp"]["source_sha256"]


def test_edit_stales_stamp_and_run_revalidates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdir = _mk(tmp_path, GOOD % 1)
    validate_probe_dir(pdir)
    before = read_probe_stamp(pdir)["source_sha256"]

    (pdir / "probe.py").write_text(GOOD % 2, encoding="utf-8")
    check = check_probe_stamp(pdir)
    assert check["state"] == STAMP_STALE

    stamp = run_probe(tmp_path, "p", to={"kind": "region"})
    assert stamp["validation"]["revalidated"] is True
    after = read_probe_stamp(pdir)["source_sha256"]
    assert after != before
    assert check_probe_stamp(pdir)["state"] == STAMP_VALID


def test_probe_json_change_stales_stamp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdir = _mk(tmp_path, GOOD % 1)
    validate_probe_dir(pdir)
    meta = pdir / "probe.json"
    meta.write_text(meta.read_text().replace('"purpose": "p"', '"purpose": "p2"'))
    assert check_probe_stamp(pdir)["state"] == STAMP_STALE


def test_run_errors_out_when_validation_fails_and_stamps_no_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk(tmp_path, BAD)
    with pytest.raises(ValueError) as e:
        run_probe(tmp_path, "p", to={"kind": "region"})
    assert "not validated" in str(e.value)
    runs = tmp_path / ".terra" / "map" / "runs"
    assert not runs.is_dir() or not [d for d in runs.iterdir() if d.is_dir()]


def test_failed_validation_stamp_is_not_reusable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdir = _mk(tmp_path, BAD)
    assert validate_probe_dir(pdir)["ok"] is False
    check = check_probe_stamp(pdir)
    assert check["state"] == "failed"
    with pytest.raises(ValueError):
        run_probe(tmp_path, "p", to={"kind": "region"})


def test_stamp_file_is_not_part_of_the_hash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdir = _mk(tmp_path, GOOD % 1)
    validate_probe_dir(pdir)
    validate_probe_dir(pdir)  # rewriting the stamp must not stale it
    assert check_probe_stamp(pdir)["state"] == STAMP_VALID
