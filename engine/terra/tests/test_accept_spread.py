"""Accept-spread: irreducible cross-method disagreement as honest uncertainty."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.gate import check_gate
from terra.knowns import (
    accept_spread,
    graduate_unknown,
    link_run_known,
    load_known,
    promote_known,
)
from terra.map_status import collect_status_board
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.readings import read_known
from terra.unknowns import create_unknown, link_run


def _write_measure_probe(root: Path, probe_id: str, *, quantity="cg", value=1.0):
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


def _runs(tmp_path: Path, probe: str, values: list[float]) -> list[str]:
    rids = []
    for v in values:
        _write_measure_probe(tmp_path, probe, value=v)
        rids.append(run_probe(tmp_path, probe, to={"kind": "region"}).get("id"))
    return rids


@pytest.fixture
def contested(tmp_path, monkeypatch):
    """cg known: mass_props says ~31, weight_balance says ~33.8 (tol 5%)."""
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "mass_props", purpose="p")
    init_probe(tmp_path, "weight_balance", purpose="p")
    create_unknown(
        tmp_path, "u_cg", claim="cg?", evidence_needed="e",
        map_type="number", quantity="cg", tolerance="5%",
    )
    for rid in _runs(tmp_path, "mass_props", [31.0, 31.1, 30.9]):
        link_run(tmp_path, "u_cg", rid)
    for rid in _runs(tmp_path, "weight_balance", [33.8, 33.9]):
        link_run(tmp_path, "u_cg", rid)
    graduate_unknown(tmp_path, "u_cg", known_id="cg")
    return tmp_path


def test_accept_unblocks_with_band(contested):
    rec = load_known(contested, "cg")
    assert rec["stats"]["corroboration"]["agree"] is False
    assert rec["confidence_derived"] == "low"
    with pytest.raises(ValueError, match="DISAGREE"):
        read_known(contested, "cg")

    rec = accept_spread(contested, "cg", reason="irreducible at phase 0")
    corr = rec["stats"]["corroboration"]
    assert corr["accepted"] is True
    # confidence recovers to med (reproducible-but-contested), high blocked
    assert rec["confidence_derived"] == "med"
    promote_known(contested, "cg", "med")
    with pytest.raises(ValueError, match="accepted band|actual agreement"):
        promote_known(contested, "cg", "high")

    r = read_known(contested, "cg")
    assert r["uncertainty"] == pytest.approx(corr["spread"])
    assert r["band"] and r["band"][0] < 31.5 < 33.0 < r["band"][1]

    # gate clean but the accepted band is surfaced as a notice
    verdict = check_gate(contested)
    assert not [
        v for v in verdict["violations"] if v["kind"] == "methods_disagree"
    ]
    notes = [n for n in verdict["notices"] if n["kind"] == "spread_accepted"]
    assert [n["id"] for n in notes] == ["cg"]
    assert "irreducible at phase 0" in notes[0]["why"]
    board = collect_status_board(contested)
    kinds = {a["kind"]: a["severity"] for a in board["attention"]}
    assert "methods_disagree" not in kinds
    assert kinds.get("spread_accepted") == "info"


def test_accept_refuses_when_nothing_to_accept(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p1", purpose="p")
    create_unknown(
        tmp_path, "u", claim="q?", evidence_needed="e",
        map_type="number", quantity="cg",
    )
    for rid in _runs(tmp_path, "p1", [1.0]):
        link_run(tmp_path, "u", rid)
    graduate_unknown(tmp_path, "u", known_id="k")
    with pytest.raises(ValueError, match="not\\s+judgeable|nothing to accept"):
        accept_spread(tmp_path, "k", reason="x")


def test_widening_spread_retrips_alarm(contested):
    accept_spread(contested, "cg", reason="phase 0 band")
    # a new, further-out weight_balance reading widens the spread
    rid = _runs(contested, "weight_balance", [36.0])[0]
    link_run_known(contested, "cg", rid)
    rec = load_known(contested, "cg")
    corr = rec["stats"]["corroboration"]
    assert corr["agree"] is False
    assert corr["accepted"] is False
    assert "grew" in corr["accepted_reason"]
    assert rec["confidence_derived"] == "low"
    with pytest.raises(ValueError, match="DISAGREE"):
        read_known(contested, "cg")
    verdict = check_gate(contested)
    assert any(
        v["kind"] == "methods_disagree" for v in verdict["violations"]
    )
    # revoked acceptance is a violation again, not a quiet notice
    assert not [
        n for n in verdict["notices"] if n["kind"] == "spread_accepted"
    ]


def test_agreement_clears_acceptance(contested):
    accept_spread(contested, "cg", reason="phase 0 band")
    # weight_balance corrected: new readings converge on mass_props
    for rid in _runs(contested, "weight_balance", [31.2, 31.0, 31.1, 31.0]):
        link_run_known(contested, "cg", rid)
    rec = load_known(contested, "cg")
    corr = rec["stats"]["corroboration"]
    assert corr["agree"] is True
    assert "accepted_spread" not in rec
    assert not [
        n for n in check_gate(contested)["notices"]
        if n["kind"] == "spread_accepted"
    ]
    # road to high reopens (n>=5, tight, 2 agreeing methods)
    assert rec["confidence_derived"] == "high"
