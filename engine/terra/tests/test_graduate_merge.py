"""Funnel: multiple unknowns converge into one known (--with / --into)."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import graduate_unknown, load_known
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run, load_unknown


def _write_measure_probe(root: Path, probe_id: str, *, quantity="mass_kg", value=1.0):
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


def _unknown_with_runs(
    tmp_path: Path, uid: str, probe: str, values: list[float],
    *, quantity="mass_kg", tolerance=None,
) -> None:
    init_probe(tmp_path, probe, purpose="p")
    create_unknown(
        tmp_path, uid, claim=f"{uid}?", evidence_needed="e",
        map_type="number", quantity=quantity, tolerance=tolerance,
    )
    for v in values:
        _write_measure_probe(tmp_path, probe, quantity=quantity, value=v)
        rid = run_probe(tmp_path, probe, to={"kind": "region"}).get("id")
        link_run(tmp_path, uid, rid)


def test_merge_at_birth_unions_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _unknown_with_runs(tmp_path, "mass_cad", "cad", [100, 101], tolerance="5%")
    _unknown_with_runs(tmp_path, "mass_sheet", "sheet", [102])
    rec = graduate_unknown(
        tmp_path, "mass_cad", with_ids=["mass_sheet"], known_id="mtow"
    )
    assert rec["stats"]["n"] == 3
    assert set(rec["origin_unknown_ids"]) == {"mass_cad", "mass_sheet"}
    assert rec["tolerance"] == "5%"
    # two probes → corroboration judged (multi-method at birth)
    corr = rec["stats"]["corroboration"]
    assert corr["methods"] == 2 and corr["agree"] is True
    for uid in ("mass_cad", "mass_sheet"):
        u = load_unknown(tmp_path, uid)
        assert u["status"] == "resolved"
        assert u["resolved_by"] == "known:mtow"


def test_merge_rejects_different_quantity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _unknown_with_runs(tmp_path, "a", "pa", [1])
    _unknown_with_runs(tmp_path, "b", "pb", [1], quantity="other_q")
    with pytest.raises(ValueError, match="same quantity"):
        graduate_unknown(tmp_path, "a", with_ids=["b"])


def test_merge_rejects_conflicting_tolerance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _unknown_with_runs(tmp_path, "a", "pa", [1], tolerance="5%")
    _unknown_with_runs(tmp_path, "b", "pb", [1], tolerance="1%")
    with pytest.raises(ValueError, match="conflicting tolerances"):
        graduate_unknown(tmp_path, "a", with_ids=["b"])


def test_merge_rejects_untyped_contributor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _unknown_with_runs(tmp_path, "a", "pa", [1])
    create_unknown(tmp_path, "vague", claim="hm?", evidence_needed="e")
    with pytest.raises(ValueError, match="untyped"):
        graduate_unknown(tmp_path, "a", with_ids=["vague"])


def test_into_merges_into_existing_known(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _unknown_with_runs(tmp_path, "mass_cad", "cad", [100, 101])
    graduate_unknown(tmp_path, "mass_cad", known_id="mtow")
    # late-arriving question funnels into the existing known
    _unknown_with_runs(tmp_path, "mass_fuel_check", "fuelsheet", [102])
    rec = graduate_unknown(tmp_path, "mass_fuel_check", into="mtow")
    assert rec["id"] == "mtow"
    assert rec["stats"]["n"] == 3
    assert rec["stats"]["corroboration"]["methods"] == 2
    assert "mass_fuel_check" in rec["origin_unknown_ids"]
    assert "mass_cad" in rec["origin_unknown_ids"]
    assert load_unknown(tmp_path, "mass_fuel_check")["resolved_by"] == "known:mtow"


def test_into_rejects_mismatch_and_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _unknown_with_runs(tmp_path, "a", "pa", [1])
    with pytest.raises(FileNotFoundError):
        graduate_unknown(tmp_path, "a", into="ghost")
    graduate_unknown(tmp_path, "a", known_id="mtow")
    _unknown_with_runs(tmp_path, "b", "pb", [1], quantity="other_q")
    with pytest.raises(ValueError, match="measures"):
        graduate_unknown(tmp_path, "b", into="mtow")
    _unknown_with_runs(tmp_path, "c", "pc", [1])
    with pytest.raises(ValueError, match="mutually exclusive"):
        graduate_unknown(tmp_path, "c", into="mtow", known_id="x")
