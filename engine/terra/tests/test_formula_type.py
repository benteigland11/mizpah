"""Formula type: observation as checkable expression + vars."""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from terra.formula_type import evaluate_expression, parse_vars_arg
from terra.gate import check_gate
from terra.knowns import create_known, link_run_known, load_known, promote_known
from terra.map_status import collect_status_board
from terra.paths import create_session_map, scoped_map
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run
from terra.knowns import graduate_unknown


def _write_measure_probe(root: Path, probe_id: str, *, quantity: str, value) -> None:
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


def test_eval_mean_le():
    val, binds = evaluate_expression("mean(h) <= 10", {"h": [3.0, 5.0, 7.0]})
    assert val is True
    assert binds["mean(h)"] == 5.0


def test_eval_disallows_call():
    with pytest.raises(ValueError, match="simple function|unknown function|disallowed"):
        evaluate_expression("__import__('os').system('x')", {})


def test_parse_vars():
    v = parse_vars_arg(["h=hostile_count", "b=rcon_up:boolean"])
    assert v["h"]["quantity"] == "hostile_count"
    assert v["b"]["kind"] == "boolean"
    assert parse_vars_arg("limit=known:spec_limit")["limit"] == {
        "known_id": "spec_limit"
    }


def test_known_formula_holds_and_promote(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="hostile_count", value=4)
    r1 = run_probe(tmp_path, "p", to={"kind": "region", "id": "a"}).get("id")
    _write_measure_probe(tmp_path, "p", quantity="hostile_count", value=6)
    r2 = run_probe(tmp_path, "p", to={"kind": "region", "id": "b"}).get("id")
    _write_measure_probe(tmp_path, "p", quantity="hostile_count", value=5)
    r3 = run_probe(tmp_path, "p", to={"kind": "region", "id": "c"}).get("id")

    create_known(
        tmp_path,
        "sparse",
        claim="Hostiles stay sparse",
        map_type="formula",
        expression="mean(h) <= 10 and n(h) >= 3",
        vars=["h=hostile_count"],
        run_id=r1,
    )
    link_run_known(tmp_path, "sparse", r2)
    rec = link_run_known(tmp_path, "sparse", r3)
    assert rec["stats"]["holds"] is True
    assert rec["stats"]["n"] == 3
    assert rec["confidence_derived"] == "med"
    rec2 = promote_known(tmp_path, "sparse", "med")
    assert rec2["confidence"] == "med"


def test_known_formula_failure_can_be_confident_and_blocks_gate(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="hostile_count", value=50)
    run_ids = [
        run_probe(tmp_path, "p", to={"kind": "region", "id": str(i)}).get("id")
        for i in range(5)
    ]
    create_known(
        tmp_path,
        "sparse",
        claim="under 10",
        map_type="formula",
        expression="mean(h) <= 10",
        vars=["h=hostile_count"],
        run_id=run_ids[0],
    )
    for rid in run_ids[1:]:
        link_run_known(tmp_path, "sparse", rid)
    rec = load_known(tmp_path, "sparse")
    assert rec["stats"]["holds"] is False
    assert rec["confidence_derived"] == "high"
    assert promote_known(tmp_path, "sparse", "high")["confidence"] == "high"

    verdict = check_gate(tmp_path)
    assert verdict["ok"] is False
    assert any(
        v["kind"] == "known_formula_failed" and v["id"] == "sparse"
        for v in verdict["violations"]
    )
    failure = next(
        v for v in verdict["violations"] if v["kind"] == "known_formula_failed"
    )
    assert "claimed confidence=high" in failure["why"]
    assert "derived=high" in failure["why"]

    board = collect_status_board(tmp_path)
    row = next(k for k in board["scopes"][0]["knowns"] if k["id"] == "sparse")
    assert row["holds"] is False
    assert row["verdict"] == "fail"
    attention = next(
        a for a in board["attention"] if a["kind"] == "known_formula_failed"
    )
    assert attention["id"] == "sparse"
    assert attention["verdict"] == "fail"
    action = next(a for a in board["next_actions"] if a["op"] == "known.show")
    assert action["argv"] == ["terra", "known", "show", "sparse", "--json"]


def test_formula_binds_live_known_and_tracks_dependency(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "measured", purpose="measured")
    init_probe(tmp_path, "spec", purpose="spec")
    _write_measure_probe(tmp_path, "measured", quantity="mtow", value=90)
    measured_run = run_probe(tmp_path, "measured", to={"kind": "design"})["id"]
    _write_measure_probe(tmp_path, "spec", quantity="mtow_limit", value=100)
    spec_run = run_probe(tmp_path, "spec", to={"kind": "requirement"})["id"]
    create_known(
        tmp_path, "spec_mtow", claim="MTOW limit", quantity="mtow_limit",
        run_id=spec_run,
    )
    create_known(
        tmp_path,
        "closes_mtow",
        claim="MTOW closes",
        map_type="formula",
        expression="measured <= limit",
        vars=["measured=mtow", "limit=known:spec_mtow"],
        run_id=measured_run,
    )
    rec = load_known(tmp_path, "closes_mtow")
    assert rec["stats"]["holds"] is True
    assert rec["stats"]["bindings"]["limit"] == 100.0
    assert rec["deps"]["knowns"][0]["id"] == "spec_mtow"

    time.sleep(1.1)  # dependency stamps use second-resolution timestamps
    _write_measure_probe(tmp_path, "spec", quantity="mtow_limit", value=80)
    newer_spec = run_probe(tmp_path, "spec", to={"kind": "requirement-v2"})["id"]
    link_run_known(tmp_path, "spec_mtow", newer_spec)
    from terra.staleness import compute_staleness

    stale = compute_staleness(tmp_path)["closes_mtow"]
    assert stale["stale"] is True
    assert any("spec_mtow" in reason for reason in stale["reasons"])


def test_session_formula_can_use_parent_run(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="ceiling", value=16500)
    global_run = run_probe(tmp_path, "p", to={"kind": "baseline"})["id"]
    create_session_map(tmp_path, "trial", parent="global")
    with scoped_map("trial"):
        create_unknown(
            tmp_path,
            "closes_ceiling",
            claim="Ceiling closes",
            map_type="formula",
            expression="measured >= 15500",
            vars=["measured=ceiling"],
        )
        link_run(tmp_path, "closes_ceiling", global_run)
        rec = graduate_unknown(tmp_path, "closes_ceiling")
        assert rec["stats"]["holds"] is True
        assert rec["run_ids"] == [global_run]
