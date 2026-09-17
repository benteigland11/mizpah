"""Read path, dependency staleness, and the mechanical gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terra.gate import check_gate
from terra.knowns import (
    add_dependency,
    graduate_unknown,
    link_run_known,
    load_known,
    reaffirm_known,
)
from terra.probe_init import init_probe
from terra.probe_run import run_probe, void_run
from terra.readings import list_consumers, read_known
from terra.staleness import compute_staleness
from terra.unknowns import create_unknown, link_run


def _write_measure_probe(root: Path, probe_id: str, *, quantity: str, value: float) -> None:
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "from pathlib import Path\n"
        f"Q = {quantity!r}\n"
        f"V = {value!r}\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
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


def _mk_known(
    tmp_path: Path, kid: str, *, quantity: str = "q", value: float = 4
) -> str:
    """probe → run → typed unknown → graduate. Returns run id."""
    pid = f"p_{kid}"
    init_probe(tmp_path, pid, purpose="p")
    _write_measure_probe(tmp_path, pid, quantity=quantity, value=value)
    rid = run_probe(tmp_path, pid, to={"kind": "region"}).get("id")
    create_unknown(
        tmp_path,
        f"u_{kid}",
        claim=f"{kid}?",
        evidence_needed="e",
        map_type="number",
        quantity=quantity,
    )
    link_run(tmp_path, f"u_{kid}", rid)
    graduate_unknown(tmp_path, f"u_{kid}", known_id=kid)
    return rid


# ---------- read path ----------


def test_read_known_returns_value_and_stamps_consumer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow", value=42)
    r = read_known(tmp_path, "mtow", consumer="tool:sheet_gen.py")
    assert r["value"] == 42.0
    assert r["n"] == 1
    assert r["stale"] is False
    consumers = list_consumers(tmp_path, "mtow")
    assert consumers[0]["consumer"] == "tool:sheet_gen.py"
    assert consumers[0]["reads"] == 1
    read_known(tmp_path, "mtow", consumer="tool:sheet_gen.py")
    assert list_consumers(tmp_path, "mtow")[0]["reads"] == 2


def test_read_known_missing_and_unbacked_are_loud(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="survey it first"):
        read_known(tmp_path, "ghost")
    rid = _mk_known(tmp_path, "mtow", value=42)
    void_run(tmp_path, rid, reason="bad", cascade=True)
    with pytest.raises(ValueError, match="unbacked"):
        read_known(tmp_path, "mtow")


def test_read_known_min_conf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow", value=42)
    with pytest.raises(ValueError, match="below required"):
        read_known(tmp_path, "mtow", min_conf="med")


def test_probe_reading_known_records_probe_consumer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow", value=42)
    init_probe(tmp_path, "reader", purpose="derives from mtow")
    pdir = tmp_path / ".terra" / "map" / "probes" / "reader"
    (pdir / "probe.py").write_text(
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    from terra.readings import known\n"
        "    v = known('mtow')['value']\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
        "            'measures': [{'quantity': 'derived', 'value': v * 2}]}\n",
        encoding="utf-8",
    )
    run_probe(tmp_path, "reader", to={"kind": "region"})
    consumers = {c["consumer"] for c in list_consumers(tmp_path, "mtow")}
    assert "probe:reader" in consumers


# ---------- staleness ----------


def test_file_dep_staleness_and_reaffirm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "cg_pos")
    stl = tmp_path / "airframe.stl"
    stl.write_text("v1")
    add_dependency(tmp_path, "cg_pos", ["file:airframe.stl"])
    assert compute_staleness(tmp_path)["cg_pos"]["stale"] is False

    stl.write_text("v2 — relofted wing")
    info = compute_staleness(tmp_path)["cg_pos"]
    assert info["stale"] is True
    assert any("file dep changed" in r for r in info["reasons"])
    with pytest.raises(ValueError, match="STALE"):
        read_known(tmp_path, "cg_pos")

    reaffirm_known(tmp_path, "cg_pos", reason="checked: CG unaffected")
    assert compute_staleness(tmp_path)["cg_pos"]["stale"] is False
    assert load_known(tmp_path, "cg_pos")["reaffirmed"][0]["reason"]


def test_known_dep_staleness_cascades(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "wing_area", quantity="a", value=10)
    _mk_known(tmp_path, "stall_kt", quantity="s", value=55)
    _mk_known(tmp_path, "mission_ok", quantity="m", value=1)
    add_dependency(tmp_path, "stall_kt", ["known:wing_area"])
    add_dependency(tmp_path, "mission_ok", ["known:stall_kt"])
    assert not compute_staleness(tmp_path)["mission_ok"]["stale"]

    # upstream moves (new evidence on wing_area)
    pid = "p_wing_area"
    rid2 = run_probe(tmp_path, pid, to={"kind": "region"}).get("id")
    import time

    time.sleep(1.1)  # updated_at is second-resolution
    link_run_known(tmp_path, "wing_area", rid2)

    stale = compute_staleness(tmp_path)
    assert stale["stall_kt"]["stale"] is True
    assert stale["mission_ok"]["stale"] is True
    assert any("upstream stale" in r for r in stale["mission_ok"]["reasons"])
    # wing_area itself is fresh — it IS the new truth
    assert stale["wing_area"]["stale"] is False


def test_link_run_refreshes_dep_stamps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "cg_pos")
    stl = tmp_path / "airframe.stl"
    stl.write_text("v1")
    add_dependency(tmp_path, "cg_pos", ["file:airframe.stl"])
    stl.write_text("v2")
    assert compute_staleness(tmp_path)["cg_pos"]["stale"] is True
    # honest re-derivation: new run linked → stamps refresh → fresh again
    rid = run_probe(tmp_path, "p_cg_pos", to={"kind": "region"}).get("id")
    link_run_known(tmp_path, "cg_pos", rid)
    assert compute_staleness(tmp_path)["cg_pos"]["stale"] is False


def test_dependency_cycle_is_stale(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "a")
    _mk_known(tmp_path, "b")
    add_dependency(tmp_path, "a", ["known:b"])
    add_dependency(tmp_path, "b", ["known:a"])
    stale = compute_staleness(tmp_path)
    assert stale["a"]["stale"] and stale["b"]["stale"]


def test_self_dep_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "a")
    with pytest.raises(ValueError, match="depend on itself"):
        add_dependency(tmp_path, "a", ["known:a"])


# ---------- gate ----------


def test_gate_passes_clean_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow")
    verdict = check_gate(tmp_path)
    assert verdict["ok"] is True
    assert verdict["violations"] == []


def test_gate_fails_on_blocking_unknown_and_stale_known(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "cg_pos")
    stl = tmp_path / "airframe.stl"
    stl.write_text("v1")
    add_dependency(tmp_path, "cg_pos", ["file:airframe.stl"])
    create_unknown(
        tmp_path, "debt", claim="?", evidence_needed="e", blocks_build=True
    )
    stl.write_text("v2")
    verdict = check_gate(tmp_path)
    assert verdict["ok"] is False
    kinds = {v["kind"] for v in verdict["violations"]}
    assert kinds == {"unknown_blocking", "known_stale"}


def test_gate_sees_session_map_debt(tmp_path, monkeypatch):
    from terra.paths import create_session_map, write_active_map

    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow")
    create_session_map(tmp_path, "trial", purpose="t", use=True)
    create_unknown(
        tmp_path, "hidden", claim="?", evidence_needed="e", blocks_build=True
    )
    write_active_map(tmp_path, "global")
    verdict = check_gate(tmp_path)
    assert verdict["ok"] is False
    assert verdict["violations"][0]["map_id"] == "trial"
    # single-map scope still available
    assert check_gate(tmp_path, map_id="global")["ok"] is True
