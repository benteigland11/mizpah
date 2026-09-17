"""Plans sit above types: multi (all) and sequential evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.plans import create_plan, link_run_plan, load_plan, promote_plan
from terra.probe_init import init_probe
from terra.probe_run import run_probe


def _write_measure_probe(root: Path, probe_id: str, *, quantity: str, value) -> None:
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


def test_plan_all_any_order(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "rcon", purpose="r")
    init_probe(tmp_path, "mobs", purpose="m")
    _write_measure_probe(tmp_path, "rcon", quantity="rcon_up", value=True)
    _write_measure_probe(tmp_path, "mobs", quantity="hostile_count", value=3)

    create_plan(
        tmp_path,
        "night_ok",
        claim="night path is safe enough to build",
        mode="all",
        legs=[
            "rcon:boolean:rcon_up",
            "hostiles:number:hostile_count",
        ],
    )
    assert (tmp_path / ".terra" / "map" / "plans" / "night_ok.json").is_file()
    # second leg first — ok in mode=all
    r_m = run_probe(tmp_path, "mobs", to={"kind": "region"}).get("id")
    rec = link_run_plan(tmp_path, "night_ok", r_m, leg_id="hostiles")
    assert rec["plan"]["satisfied_count"] == 1

    r_r = run_probe(tmp_path, "rcon", to={"kind": "server"}).get("id")
    rec2 = link_run_plan(tmp_path, "night_ok", r_r, leg_id="rcon")
    assert rec2["plan"]["all_satisfied"] is True
    assert rec2["role"] == "plan"
    assert "type" not in rec2 or rec2.get("type") is None


def test_plan_sequence_blocks_ahead(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "rcon", purpose="r")
    init_probe(tmp_path, "mobs", purpose="m")
    _write_measure_probe(tmp_path, "rcon", quantity="rcon_up", value=True)
    _write_measure_probe(tmp_path, "mobs", quantity="hostile_count", value=4)

    create_plan(
        tmp_path,
        "gate",
        claim="prove rcon then count",
        mode="sequence",
        legs=[
            "rcon:boolean:rcon_up",
            "hostiles:number:hostile_count:n=1:conf=low",
        ],
    )
    r_m = run_probe(tmp_path, "mobs", to={"kind": "region"}).get("id")
    with pytest.raises(ValueError, match="sequence.*rcon"):
        link_run_plan(tmp_path, "gate", r_m, leg_id="hostiles")

    r_r = run_probe(tmp_path, "rcon", to={"kind": "server"}).get("id")
    link_run_plan(tmp_path, "gate", r_r, leg_id="rcon")
    rec = link_run_plan(tmp_path, "gate", r_m, leg_id="hostiles")
    assert rec["plan"]["all_satisfied"] is True
    assert rec["plan"]["next_leg"] is None


def test_plan_promote_needs_all_legs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "rcon", purpose="r")
    _write_measure_probe(tmp_path, "rcon", quantity="rcon_up", value=True)
    create_plan(
        tmp_path,
        "partial",
        claim="two pieces",
        mode="all",
        legs=[
            "rcon:boolean:rcon_up:n=1:conf=low",
            "other:boolean:other_up:n=1:conf=low",
        ],
    )
    rid = run_probe(tmp_path, "rcon", to={"kind": "server"}).get("id")
    link_run_plan(tmp_path, "partial", rid, leg_id="rcon")
    with pytest.raises(ValueError, match="cannot promote plan|waiting"):
        promote_plan(tmp_path, "partial", "med")


def test_plan_requires_leg_on_link(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=1)
    create_plan(
        tmp_path,
        "dossier",
        claim="multi",
        mode="all",
        legs=["a:number:q"],
    )
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    with pytest.raises(TypeError):
        # leg_id is required keyword
        link_run_plan(tmp_path, "dossier", rid)  # type: ignore[call-arg]
