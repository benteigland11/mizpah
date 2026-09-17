"""Known graph rendering: chain readable in one look."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import add_dependency, graduate_unknown
from terra.known_graph import (
    build_graph,
    build_tree,
    render_graph_text,
    render_tree_text,
)
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


def _mk_known(tmp_path: Path, kid: str, *, quantity: str = "q", value: float = 4) -> str:
    pid = f"p_{kid}"
    init_probe(tmp_path, pid, purpose="p")
    _write_measure_probe(tmp_path, pid, quantity=quantity, value=value)
    rid = run_probe(tmp_path, pid, to={"kind": "region"}).get("id")
    create_unknown(
        tmp_path, f"u_{kid}", claim=f"{kid}?", evidence_needed="e",
        map_type="number", quantity=quantity,
    )
    link_run(tmp_path, f"u_{kid}", rid)
    graduate_unknown(tmp_path, f"u_{kid}", known_id=kid)
    return rid


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """file:airframe.stl → cg_pos → mission_ok; mtow isolated."""
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "cg_pos")
    _mk_known(tmp_path, "mission_ok")
    _mk_known(tmp_path, "mtow")
    (tmp_path / "airframe.stl").write_text("v1")
    add_dependency(tmp_path, "cg_pos", ["file:airframe.stl"])
    add_dependency(tmp_path, "mission_ok", ["known:cg_pos"])
    read_known(tmp_path, "mission_ok", consumer="tool:mission_card.py")
    return tmp_path


def test_graph_structure(chain):
    g = build_graph(chain)
    ids = {n["id"] for n in g["nodes"]}
    assert {"known:cg_pos", "known:mission_ok", "known:mtow", "file:airframe.stl"} <= ids
    assert {"from": "file:airframe.stl", "to": "known:cg_pos", "kind": "file"} in g["edges"]
    assert {"from": "known:cg_pos", "to": "known:mission_ok", "kind": "known"} in g["edges"]
    assert g["roots"] == ["file:airframe.stl"]
    assert g["isolated"] == ["known:mtow"]
    assert g["counts"]["stale"] == 0


def test_graph_shows_stale_chain_and_changed_file(chain):
    (chain / "airframe.stl").write_text("v2")
    g = build_graph(chain)
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id["file:airframe.stl"]["changed"] is True
    assert by_id["known:cg_pos"]["stale"] is True
    assert by_id["known:mission_ok"]["stale"] is True
    assert "tool:mission_card.py" in by_id["known:mission_ok"]["consumers"]

    text = render_graph_text(g)
    assert "CHANGED" in text
    assert text.index("file:airframe.stl") < text.index("cg_pos")
    assert text.index("cg_pos") < text.index("mission_ok")
    assert "consumers: tool:mission_card.py" in text
    assert "unwired" in text and "mtow" in text


def test_tree_focus(chain):
    (chain / "airframe.stl").write_text("v2")
    t = build_tree(build_graph(chain), "mission_ok")
    up = {n["id"] for n in t["upstream"]}
    assert up == {"known:cg_pos", "file:airframe.stl"}
    assert t["downstream"] == []
    text = render_tree_text(t)
    assert "upstream:" in text and "airframe.stl" in text

    t2 = build_tree(build_graph(chain), "cg_pos")
    assert {n["id"] for n in t2["downstream"]} == {"known:mission_ok"}


def test_tree_unknown_id_is_loud(chain):
    with pytest.raises(FileNotFoundError, match="not in graph"):
        build_tree(build_graph(chain), "ghost")


def test_graph_cycle_renders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "a")
    _mk_known(tmp_path, "b")
    add_dependency(tmp_path, "a", ["known:b"])
    add_dependency(tmp_path, "b", ["known:a"])
    g = build_graph(tmp_path)
    # cycle: no roots among a/b, but render must terminate
    text = render_graph_text(g)
    assert isinstance(text, str)
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id["known:a"]["stale"] and by_id["known:b"]["stale"]
