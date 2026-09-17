"""Number type: stats + confidence ladder + knowns."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import create_known, link_run_known, promote_known, load_known
from terra.number_type import compute_number_stats, derive_confidence
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run, load_unknown


def test_stats_n1_no_std():
    s = compute_number_stats([3.0])
    assert s["n"] == 1
    assert s["mean"] == 3.0
    assert s["std"] is None
    assert derive_confidence(s) == "low"


def test_stats_n2_med():
    s = compute_number_stats([2.0, 4.0])
    assert s["n"] == 2
    assert s["std"] is not None
    assert derive_confidence(s) == "med"


def test_known_n1_cannot_promote_high(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=5)
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    create_known(
        tmp_path,
        "est",
        claim="q is about 5",
        quantity="q",
        run_id=rid,
    )
    rec = load_known(tmp_path, "est")
    assert rec["stats"]["n"] == 1
    assert rec["confidence_derived"] == "low"
    with pytest.raises(ValueError, match="confidence"):
        promote_known(tmp_path, "est", "high")


def test_known_multi_sample_promote_med(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=2)
    r1 = run_probe(tmp_path, "p", to={"kind": "region", "id": "a"}).get("id")
    # second sample different value via rewrite
    _write_measure_probe(tmp_path, "p", quantity="q", value=6)
    r2 = run_probe(tmp_path, "p", to={"kind": "region", "id": "b"}).get("id")
    create_known(tmp_path, "est", claim="q", quantity="q", run_id=r1)
    link_run_known(tmp_path, "est", r2)
    rec = promote_known(tmp_path, "est", "med")
    assert rec["confidence"] == "med"
    assert rec["stats"]["n"] == 2
    assert rec["status"] == "active"


def test_unknown_number_link_run_stats(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_unknown(
        tmp_path,
        "u",
        claim="what is q?",
        evidence_needed="measures of q",
        map_type="number",
        quantity="q",
    )
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=10)
    rid = run_probe(tmp_path, "p", to={"kind": "default"}).get("id")
    rec = link_run(tmp_path, "u", rid)
    assert rec["type"] == "number"
    assert rec["stats"]["n"] == 1
    assert rec["stats"]["mean"] == 10.0


def _write_measure_probe(tmp_path: Path, probe_id: str, *, quantity: str, value: float):
    pdir = tmp_path / ".terra" / "map" / "probes" / probe_id
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
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
