"""Assumptions: consumable provisional values with loud conditionality."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.assumptions import (
    create_assumption,
    list_assumptions,
    read_assumption,
    set_assumption,
)
from terra.gate import check_gate
from terra.knowns import graduate_unknown
from terra.map_status import collect_status_board
from terra.paths import create_session_map, scoped_map, set_active_map_id
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run
from terra.unknowns import validate_all_unknowns


@pytest.fixture(autouse=True)
def _reset_active_map():
    set_active_map_id(None)
    yield
    set_active_map_id(None)


def _write_measure_probe(
    root: Path, probe_id: str, *, quantity: str, value: float
) -> None:
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        f"Q = {quantity!r}\n"
        f"V = {value!r}\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [], "
        "'measures': [{'quantity': Q, 'value': V}]}\n",
        encoding="utf-8",
    )


def test_create_read_and_revise_number_assumption(tmp_path: Path):
    create_assumption(
        tmp_path,
        "emissivity",
        claim="Radiator coating emissivity?",
        map_type="number",
        quantity="radiator_emissivity",
        value=0.85,
        reason="working BOL coating value",
        evidence_needed="vendor test over operating temperature",
        unit="1",
    )

    reading = read_assumption(tmp_path, "emissivity")
    assert reading["value"] == 0.85
    assert reading["conditional"] is True
    assert reading["assumptions"] == ["emissivity"]
    assert reading["evidence_n"] == 0

    rec = set_assumption(
        tmp_path,
        "emissivity",
        value=0.78,
        reason="switched to conservative EOL basis",
    )
    assert rec["assumed_value"] == 0.78
    assert [r["value"] for r in rec["assumption_revisions"]] == [0.85, 0.78]
    with pytest.raises(ValueError, match="reason"):
        set_assumption(tmp_path, "emissivity", value=0.75, reason="")


def test_unknown_blocks_gate_assumption_is_notice(tmp_path: Path):
    create_assumption(
        tmp_path,
        "efficiency",
        claim="Converter efficiency?",
        map_type="number",
        quantity="efficiency",
        value=0.9,
        reason="working vendor-class value",
        evidence_needed="bench measurement",
    )
    verdict = check_gate(tmp_path)
    assert verdict["ok"] is True
    assert any(n["kind"] == "assumption_active" for n in verdict["notices"])

    create_unknown(
        tmp_path,
        "material",
        claim="Which coating material?",
        evidence_needed="selection review",
        blocks_build=False,  # legacy softness is intentionally ignored
    )
    verdict = check_gate(tmp_path)
    assert verdict["ok"] is False
    assert any(v["kind"] == "unknown_blocking" for v in verdict["violations"])
    validation = validate_all_unknowns(tmp_path)
    assert validation["blocking_count"] == 1
    assert validation["assumption_count"] == 1


def test_evidence_does_not_replace_assumption_and_graduation_uses_run(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    create_assumption(
        tmp_path,
        "efficiency",
        claim="Converter efficiency?",
        map_type="number",
        quantity="efficiency",
        value=0.9,
        reason="working vendor-class value",
        evidence_needed="bench measurement",
    )
    init_probe(tmp_path, "bench", purpose="measure efficiency")
    _write_measure_probe(tmp_path, "bench", quantity="efficiency", value=0.82)
    rid = run_probe(tmp_path, "bench", to={"kind": "default"})["id"]
    rec = link_run(tmp_path, "efficiency", rid)
    assert rec["assumed_value"] == 0.9
    assert rec["stats"]["mean"] == 0.82
    assert read_assumption(tmp_path, "efficiency")["observed_value"] == 0.82

    known = graduate_unknown(tmp_path, "efficiency")
    assert known["stats"]["mean"] == 0.82
    assert known["stats"]["mean"] != rec["assumed_value"]


def test_status_separates_assumptions_from_unknowns(tmp_path: Path):
    create_assumption(
        tmp_path,
        "density",
        claim="Working density?",
        map_type="number",
        quantity="density",
        value=1000,
        reason="water-like placeholder",
        evidence_needed="material selection",
    )
    board = collect_status_board(tmp_path)
    scope = board["scopes"][0]
    assert scope["counts"]["assumptions"] == 1
    assert scope["counts"]["unknowns"] == 0
    assert scope["unknowns_open"] == []
    assert scope["assumptions"][0]["id"] == "density"
    assert any(a["kind"] == "assumption_active" for a in board["attention"])


def test_assumption_read_falls_through_parent_map(tmp_path: Path):
    create_assumption(
        tmp_path,
        "density",
        claim="Working density?",
        map_type="number",
        quantity="density",
        value=1000,
        reason="global working basis",
        evidence_needed="material selection",
    )
    create_session_map(tmp_path, "trial")
    with scoped_map("trial"):
        reading = read_assumption(tmp_path, "density")
        from terra.assumptions import describe_assumption

        description = describe_assumption(tmp_path, "density")
    assert reading["value"] == 1000
    assert reading["map"] == "global"
    assert description["reading"]["map"] == "global"


def test_list_assumptions_excludes_plain_unknowns(tmp_path: Path):
    create_unknown(tmp_path, "gap", claim="What?", evidence_needed="answer")
    create_assumption(
        tmp_path,
        "guess",
        claim="How much?",
        map_type="number",
        quantity="q",
        value=3,
        reason="working value",
        evidence_needed="measurement",
    )
    rows = list_assumptions(tmp_path)
    assert [row["id"] for row in rows] == ["guess"]
