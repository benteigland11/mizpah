"""Relation type: F(x) measured as a curve, stored as a known."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terra.knowns import graduate_unknown, load_known, promote_known
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.readings import read_known
from terra.relation_type import (
    compute_relation_stats,
    evaluate_relation,
    relation_corroboration,
)
from terra.unknowns import create_unknown, link_run


def _write_sweep_probe(
    root: Path, probe_id: str, points: list[tuple[float, float]], quantity="cl"
) -> None:
    pdir = root / ".terra" / "map" / "probes" / probe_id
    rows = json.dumps(
        [{"quantity": quantity, "x": x, "value": y} for x, y in points]
    )
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
        f"            'measures': {rows}}}\n".replace("}}", "}").replace(
            "'measures': ", "'measures': "
        )
        + "",
        encoding="utf-8",
    )


def _sweep(tmp_path: Path, probe: str, points, uid="u_cl") -> str:
    _write_sweep_probe(tmp_path, probe, points)
    rid = run_probe(tmp_path, probe, to={"kind": "region"}).get("id")
    link_run(tmp_path, uid, rid)
    return rid


GRID = [(0.0, 0.10), (4.0, 0.60), (8.0, 1.05)]


@pytest.fixture
def curve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "vlm", purpose="p")
    create_unknown(
        tmp_path, "u_cl", claim="CL(alpha)?", evidence_needed="sweeps",
        map_type="relation", quantity="cl", x_quantity="alpha_deg",
        tolerance="10%",
    )
    return tmp_path


def test_stats_and_ladder_unit_is_sweeps(curve):
    for _ in range(2):
        _sweep(curve, "vlm", GRID)
    graduate_unknown(curve, "u_cl", known_id="cl_vs_alpha")
    rec = load_known(curve, "cl_vs_alpha")
    st = rec["stats"]
    assert st["kind"] == "relation"
    assert st["n"] == 2  # sweeps, not points
    assert st["points"] == 6
    assert st["station_count"] == 3
    assert st["x_range"] == [0.0, 8.0]
    assert rec["confidence_derived"] == "low"  # 2 sweeps < 3
    # third sweep → med
    from terra.knowns import link_run_known

    _write_sweep_probe(curve, "vlm", GRID)
    rid = run_probe(curve, "vlm", to={"kind": "region"}).get("id")
    link_run_known(curve, "cl_vs_alpha", rid)
    rec = load_known(curve, "cl_vs_alpha")
    assert rec["stats"]["n"] == 3
    assert rec["confidence_derived"] == "med"
    promote_known(curve, "cl_vs_alpha", "med")
    with pytest.raises(ValueError, match="second independent probe"):
        promote_known(curve, "cl_vs_alpha", "high")


def test_evaluate_interp_and_no_extrapolation(curve):
    for _ in range(3):
        _sweep(curve, "vlm", GRID)
    graduate_unknown(curve, "u_cl", known_id="cl_vs_alpha")
    r = read_known(curve, "cl_vs_alpha", at=4.0)
    assert r["value"] == pytest.approx(0.60)
    r = read_known(curve, "cl_vs_alpha", at=2.0)
    assert r["value"] == pytest.approx(0.35)  # midpoint 0.10..0.60
    assert r["at"] == 2.0
    with pytest.raises(ValueError, match="outside the measured x_range"):
        read_known(curve, "cl_vs_alpha", at=12.0)
    # full table without --at
    r = read_known(curve, "cl_vs_alpha")
    assert isinstance(r["value"], list) and len(r["value"]) == 3
    with pytest.raises(ValueError, match="no measured stations"):
        evaluate_relation(compute_relation_stats([], sweeps=0), 1.0)


def test_relation_corroboration_shared_stations(curve):
    init_probe(curve, "panel", purpose="p")
    for _ in range(3):
        _sweep(curve, "vlm", GRID)
    # second method, same grid, within 10% everywhere
    _sweep(curve, "panel", [(0.0, 0.105), (4.0, 0.63), (8.0, 1.02)])
    graduate_unknown(curve, "u_cl", known_id="cl_vs_alpha")
    rec = load_known(curve, "cl_vs_alpha")
    corr = rec["stats"]["corroboration"]
    assert corr["methods"] == 2
    assert corr["shared_stations"] == 3
    assert corr["agree"] is True


def test_relation_disagreement_at_shared_station(curve):
    init_probe(curve, "panel", purpose="p")
    for _ in range(3):
        _sweep(curve, "vlm", GRID)
    # panel disagrees badly at 8deg (post-stall behavior)
    _sweep(curve, "panel", [(0.0, 0.10), (4.0, 0.61), (8.0, 1.45)])
    graduate_unknown(curve, "u_cl", known_id="cl_vs_alpha")
    rec = load_known(curve, "cl_vs_alpha")
    corr = rec["stats"]["corroboration"]
    assert corr["agree"] is False
    assert rec["confidence_derived"] == "low"
    with pytest.raises(ValueError, match="DISAGREE"):
        read_known(curve, "cl_vs_alpha", at=4.0)


def test_disjoint_grids_not_judgeable():
    a = compute_relation_stats([(0.0, 1.0), (2.0, 2.0)], sweeps=1)
    b = compute_relation_stats([(1.0, 1.5), (3.0, 2.5)], sweeps=1)
    corr = relation_corroboration({"a": a, "b": b}, tolerance="5%")
    assert corr["agree"] is None
    assert corr["shared_stations"] == 0


def test_relation_requires_x_quantity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    with pytest.raises(ValueError, match="x-quantity"):
        create_unknown(
            tmp_path, "u", claim="?", evidence_needed="e",
            map_type="relation", quantity="cl",
        )
