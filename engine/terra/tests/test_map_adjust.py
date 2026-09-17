"""Retract / adjust: bad runs must leave the map clean for the next agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import create_known, link_run_known, load_known, unlink_run_known
from terra.probe_init import init_probe
from terra.probe_run import delete_run, run_probe, void_run
from terra.unknowns import create_unknown, link_run, load_unknown


def _write_measure_probe(root: Path, probe_id: str, *, quantity: str, value: float) -> None:
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "from pathlib import Path\n"
        f"Q = {quantity!r}\n"
        f"V = {value!r}\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    p = Path(__file__).parent / 'm.txt'\n"
        "    p.write_text(str(V))\n"
        "    return {\n"
        "        'to': to,\n"
        "        'status': 'ok',\n"
        "        'artifacts': [{'path': str(p), 'role': 'out'}],\n"
        "        'measures': [{'quantity': Q, 'value': V}],\n"
        "    }\n",
        encoding="utf-8",
    )


def test_known_unlink_run_drops_sample(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=2)
    r1 = run_probe(tmp_path, "p", to={"kind": "region", "id": "a"}).get("id")
    _write_measure_probe(tmp_path, "p", quantity="q", value=100)
    r2 = run_probe(tmp_path, "p", to={"kind": "region", "id": "b"}).get("id")
    create_known(tmp_path, "est", claim="q", quantity="q", run_id=r1)
    link_run_known(tmp_path, "est", r2)
    rec = load_known(tmp_path, "est")
    assert rec["stats"]["n"] == 2
    assert rec["stats"]["mean"] == 51.0

    rec2 = unlink_run_known(tmp_path, "est", r2)
    assert r2 not in rec2["run_ids"]
    assert rec2["stats"]["n"] == 1
    assert rec2["stats"]["mean"] == 2.0


def test_void_run_cascades_and_cleans_stats(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=3)
    r_good = run_probe(tmp_path, "p", to={"kind": "region", "id": "g"}).get("id")
    _write_measure_probe(tmp_path, "p", quantity="q", value=999)
    r_bad = run_probe(tmp_path, "p", to={"kind": "region", "id": "bad"}).get("id")

    create_known(tmp_path, "est", claim="q", quantity="q", run_id=r_good)
    link_run_known(tmp_path, "est", r_bad)
    create_unknown(
        tmp_path,
        "u",
        claim="q?",
        evidence_needed="measures",
        map_type="number",
        quantity="q",
    )
    link_run(tmp_path, "u", r_good)
    link_run(tmp_path, "u", r_bad)

    result = void_run(tmp_path, r_bad, reason="probe bug / outlier")
    assert result["run"]["voided"] is True
    assert "est" in result["unlinked"]["knowns"]
    assert "u" in result["unlinked"]["unknowns"]

    known = load_known(tmp_path, "est")
    assert r_bad not in known["run_ids"]
    assert known["stats"]["n"] == 1
    assert known["stats"]["mean"] == 3.0

    unk = load_unknown(tmp_path, "u")
    assert r_bad not in unk["run_ids"]
    assert unk["stats"]["n"] == 1


def test_voided_run_cannot_relink(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=1)
    r_good = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    void_run(tmp_path, rid, reason="bad", cascade=False)
    create_known(tmp_path, "est", claim="q", quantity="q", run_id=r_good)
    with pytest.raises(ValueError, match="voided"):
        link_run_known(tmp_path, "est", rid)


def test_delete_run_removes_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=1)
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    create_known(tmp_path, "est", claim="q", quantity="q", run_id=rid)
    delete_run(tmp_path, rid)
    assert not (tmp_path / ".terra" / "map" / "runs" / rid).exists()
    known = load_known(tmp_path, "est")
    assert known["run_ids"] == []
    assert known["stats"]["n"] == 0
