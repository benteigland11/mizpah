"""Declared probe inputs stamp and propagate map provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.assumptions import create_assumption, set_assumption
from terra.calculations import create_calculation, run_calculation
from terra.gate import check_gate
from terra.knowns import graduate_unknown
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.readings import read_known
from terra.map_status import collect_status_board
from terra.run_inputs import run_input_state
from terra.unknowns import create_unknown, link_run


def test_assumption_conditioned_probe_stamps_and_stales(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_assumption(
        tmp_path,
        "scale",
        claim="Working calibration scale?",
        map_type="number",
        quantity="scale",
        value=2.0,
        reason="working calibration",
        evidence_needed="calibration test",
    )
    init_probe(
        tmp_path,
        "sensor",
        purpose="read a scaled sensor",
        inputs={"scale": "assumption:scale"},
    )
    pdir = tmp_path / ".terra" / "map" / "probes" / "sensor"
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': ctx.get('to'), 'status': 'ok', 'artifacts': []}\n"
        "    value = ctx['inputs']['scale']\n"
        "    return {'to': ctx.get('to'), 'status': 'ok', 'artifacts': [], "
        "'measures': [{'quantity': 'reading', 'value': value}]}\n",
        encoding="utf-8",
    )
    run = run_probe(tmp_path, "sensor", to={"kind": "default"})
    assert run["inputs"]["scale"]["value"] == 2.0
    assert run["conditional"] is True
    assert run["assumptions"] == ["scale"]
    assert run_input_state(tmp_path, run)["stale"] is False

    create_unknown(
        tmp_path,
        "reading",
        claim="Scaled reading?",
        evidence_needed="sensor run",
        map_type="number",
        quantity="reading",
    )
    link_run(tmp_path, "reading", run["id"])
    known = graduate_unknown(tmp_path, "reading")
    assert known["conditional"] is True
    assert known["assumptions"] == ["scale"]
    reading = read_known(tmp_path, "reading")
    assert reading["value"] == 2.0
    assert reading["conditional"] is True
    assert reading["assumptions"] == ["scale"]
    create_calculation(
        tmp_path,
        "derived",
        inputs={"reading": "known:reading"},
        output_type="number",
        quantity="derived",
    )
    calc = run_calculation(tmp_path, "derived")
    assert calc["conditional"] is True
    assert calc["assumptions"] == ["scale"]

    set_assumption(tmp_path, "scale", value=3.0, reason="new calibration basis")
    assert run_input_state(tmp_path, run)["stale"] is True
    with pytest.raises(ValueError, match="stale probe inputs"):
        read_known(tmp_path, "reading")
    gate = check_gate(tmp_path)
    assert gate["ok"] is False
    assert any(v["kind"] == "evidence_input_stale" for v in gate["violations"])
    board = collect_status_board(tmp_path)
    assert any(
        a["kind"] == "evidence_input_stale" for a in board["attention"]
    )
