"""Boolean map type: trials → rate + confidence ladder."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import create_known, link_run_known, promote_known, load_known
from terra.number_type import compute_boolean_stats, derive_confidence
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run


def test_boolean_stats():
    s = compute_boolean_stats([True, True, False])
    assert s["n"] == 3
    assert s["k_true"] == 2
    assert s["k_false"] == 1
    assert abs(s["rate"] - 2 / 3) < 1e-9
    assert derive_confidence(s, map_type="boolean") == "med"


def test_boolean_n1_low():
    s = compute_boolean_stats([True])
    assert derive_confidence(s, map_type="boolean") == "low"


def test_boolean_high_needs_unanimity_and_corroboration():
    # unanimous n=5 but single method → capped at med (repetition ≠ truth)
    s = compute_boolean_stats([True] * 5)
    assert derive_confidence(s, map_type="boolean") == "med"
    # two agreeing methods → high
    s["by_probe"] = {
        "a": compute_boolean_stats([True] * 3),
        "b": compute_boolean_stats([True] * 2),
    }
    from terra.corroboration import compute_corroboration

    s["corroboration"] = compute_corroboration(s["by_probe"], map_type="boolean")
    assert s["corroboration"]["agree"] is True
    assert derive_confidence(s, map_type="boolean") == "high"
    s2 = compute_boolean_stats([True] * 4 + [False])
    assert derive_confidence(s2, map_type="boolean") == "med"


def test_known_boolean_promote(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_bool_probe(tmp_path, "p", quantity="up", value=True)
    r1 = run_probe(tmp_path, "p", to={"kind": "server"}).get("id")
    create_known(
        tmp_path,
        "rcon",
        claim="RCON up",
        quantity="up",
        map_type="boolean",
        run_id=r1,
    )
    rec = load_known(tmp_path, "rcon")
    assert rec["type"] == "boolean"
    assert rec["stats"]["n"] == 1
    assert rec["stats"]["rate"] == 1.0
    with pytest.raises(ValueError, match="confidence|trials"):
        promote_known(tmp_path, "rcon", "high")

    for _ in range(2):
        rid = run_probe(tmp_path, "p", to={"kind": "server"}).get("id")
        link_run_known(tmp_path, "rcon", rid)
    rec = promote_known(tmp_path, "rcon", "med")
    assert rec["confidence"] == "med"
    assert rec["stats"]["n"] == 3


def test_unknown_boolean(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_unknown(
        tmp_path,
        "u",
        claim="is up?",
        evidence_needed="trials",
        map_type="boolean",
        quantity="up",
    )
    init_probe(tmp_path, "p", purpose="p")
    _write_bool_probe(tmp_path, "p", quantity="up", value=False)
    rid = run_probe(tmp_path, "p", to={"kind": "server"}).get("id")
    rec = link_run(tmp_path, "u", rid)
    assert rec["stats"]["n"] == 1
    assert rec["stats"]["k_false"] == 1
    assert rec["stats"]["rate"] == 0.0


def _write_bool_probe(tmp_path: Path, probe_id: str, *, quantity: str, value: bool):
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
        "    to = ctx.get('to') or {'kind': 'server'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    p = Path(__file__).parent / 'b.txt'\n"
        "    p.write_text(str(V))\n"
        "    return {\n"
        "        'to': to,\n"
        "        'status': 'ok',\n"
        "        'artifacts': [{'path': str(p), 'role': 'out'}],\n"
        "        'measures': [{'quantity': Q, 'value': V}],\n"
        "    }\n",
        encoding="utf-8",
    )
