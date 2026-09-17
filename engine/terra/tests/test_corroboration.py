"""Corroboration: independent methods agreeing is the second evidence axis."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.corroboration import compute_corroboration, parse_tolerance
from terra.gate import check_gate
from terra.knowns import (
    graduate_unknown,
    link_run_known,
    load_known,
    promote_known,
    set_tolerance,
)
from terra.map_status import collect_status_board
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.readings import read_known
from terra.unknowns import create_unknown, link_run


def _write_measure_probe(root: Path, probe_id: str, *, quantity: str, value: float) -> None:
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
        f"            'measures': [{{'quantity': {quantity!r}, 'value': {value!r}}}]}}\n",
        encoding="utf-8",
    )


def _probe_runs(tmp_path: Path, probe_id: str, values: list[float], quantity="q") -> list[str]:
    init_probe(tmp_path, probe_id, purpose="p")
    rids = []
    for v in values:
        _write_measure_probe(tmp_path, probe_id, quantity=quantity, value=v)
        rids.append(run_probe(tmp_path, probe_id, to={"kind": "region"}).get("id"))
    return rids


def _known_from(tmp_path: Path, kid: str, rids: list[str], *, tolerance=None) -> None:
    create_unknown(
        tmp_path, f"u_{kid}", claim=f"{kid}?", evidence_needed="e",
        map_type="number", quantity="q", tolerance=tolerance,
    )
    for r in rids:
        link_run(tmp_path, f"u_{kid}", r)
    graduate_unknown(tmp_path, f"u_{kid}", known_id=kid)


def test_parse_tolerance_forms():
    assert parse_tolerance("5%") == ("rel", 0.05)
    assert parse_tolerance("0.5") == ("abs", 0.5)
    assert parse_tolerance(2) == ("abs", 2.0)
    assert parse_tolerance(None) is None
    assert parse_tolerance("") is None
    with pytest.raises(ValueError):
        parse_tolerance("lots")
    with pytest.raises(ValueError):
        parse_tolerance("-3%")


def test_corroboration_number_agree_and_disagree():
    a = {"n": 3, "mean": 100.0}
    b = {"n": 2, "mean": 103.0}
    c = compute_corroboration({"a": a, "b": b}, map_type="number", tolerance="5%")
    assert c["methods"] == 2 and c["agree"] is True
    c2 = compute_corroboration({"a": a, "b": {"n": 2, "mean": 120.0}},
                               map_type="number", tolerance="5%")
    assert c2["agree"] is False
    # no tolerance declared → spread surfaced, not judged
    c3 = compute_corroboration({"a": a, "b": b}, map_type="number")
    assert c3["agree"] is None and c3["spread"] == 3.0


def test_single_method_caps_at_med(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rids = _probe_runs(tmp_path, "cad", [100, 101, 99, 100, 100])
    _known_from(tmp_path, "mtow", rids, tolerance="5%")
    rec = load_known(tmp_path, "mtow")
    assert rec["stats"]["n"] == 5
    assert rec["confidence_derived"] == "med"
    promote_known(tmp_path, "mtow", "med")
    with pytest.raises(ValueError, match="second independent probe"):
        promote_known(tmp_path, "mtow", "high")


def test_two_agreeing_methods_reach_high(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rids = _probe_runs(tmp_path, "cad", [100, 101, 99])
    rids += _probe_runs(tmp_path, "sheet", [102, 100])
    _known_from(tmp_path, "mtow", rids, tolerance="5%")
    rec = load_known(tmp_path, "mtow")
    corr = rec["stats"]["corroboration"]
    assert corr["methods"] == 2 and corr["agree"] is True
    assert rec["confidence_derived"] == "high"
    promote_known(tmp_path, "mtow", "high")


def test_disagreement_is_louder_than_absence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rids = _probe_runs(tmp_path, "cad", [100, 101, 100])
    rids += _probe_runs(tmp_path, "sheet", [130, 131])
    _known_from(tmp_path, "cg_pos", rids, tolerance="5%")
    rec = load_known(tmp_path, "cg_pos")
    assert rec["stats"]["corroboration"]["agree"] is False
    # derived collapses to low despite n=5
    assert rec["confidence_derived"] == "low"
    with pytest.raises(ValueError, match="DISAGREE"):
        promote_known(tmp_path, "cg_pos", "med")
    # read refuses; escape hatch works
    with pytest.raises(ValueError, match="DISAGREE"):
        read_known(tmp_path, "cg_pos")
    r = read_known(tmp_path, "cg_pos", allow_disagree=True)
    assert r["corroboration"]["agree"] is False
    # gate + attention
    verdict = check_gate(tmp_path)
    assert any(v["kind"] == "methods_disagree" for v in verdict["violations"])
    board = collect_status_board(tmp_path)
    kinds = {a["kind"] for a in board["attention"]}
    assert "methods_disagree" in kinds


def test_tolerance_via_command_rejudges(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rids = _probe_runs(tmp_path, "cad", [100, 101, 100])
    rids += _probe_runs(tmp_path, "sheet", [103, 104])
    _known_from(tmp_path, "mtow", rids)  # no tolerance yet
    rec = load_known(tmp_path, "mtow")
    assert rec["stats"]["corroboration"]["agree"] is None
    rec = set_tolerance(tmp_path, "mtow", within="5%")
    assert rec["stats"]["corroboration"]["agree"] is True
    rec = set_tolerance(tmp_path, "mtow", within="1%")
    assert rec["stats"]["corroboration"]["agree"] is False


def test_boolean_methods_verdicts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = {"n": 3, "rate": 1.0}
    b = {"n": 4, "rate": 0.75}
    c = compute_corroboration({"a": a, "b": b}, map_type="boolean")
    assert c["agree"] is True  # both majority-true
    c2 = compute_corroboration(
        {"a": a, "b": {"n": 4, "rate": 0.25}}, map_type="boolean"
    )
    assert c2["agree"] is False
