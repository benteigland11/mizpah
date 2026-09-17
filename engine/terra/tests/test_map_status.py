"""Map status board — agent-first (Cartograph-aligned)."""

from __future__ import annotations

from pathlib import Path

from terra.agent_io import emit, success
from terra.knowns import create_known
from terra.map_status import (
    agent_status_response,
    collect_status_board,
    format_status_text,
    write_status_html,
)
from terra.paths import create_session_map, write_active_map
from terra.plans import create_plan
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown


def test_status_board_active_map(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="peek")
    create_unknown(
        tmp_path,
        "gap",
        claim="how does X work?",
        evidence_needed="a probe reading",
        blocks_build=True,
    )
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    create_known(
        tmp_path,
        "fact",
        claim="q is about 1",
        quantity="q",
        map_type="number",
        run_id=rid,
    )
    create_plan(
        tmp_path,
        "gate",
        claim="prove a then b",
        mode="sequence",
        legs=["a:boolean:a_ok", "b:number:b_count"],
    )

    board = collect_status_board(tmp_path)
    assert board["active_map"] == "global"
    assert len(board["scopes"]) == 1
    scope = board["scopes"][0]
    c = scope["counts"]
    assert c["probes"] == 1
    assert c["unknowns_open"] == 1
    assert c["unknowns_blocking"] == 1
    assert c["knowns"] == 1
    assert c["plans"] == 1
    assert c["plans_open"] == 1

    # Agent guidance is first-class, not prose-only
    kinds = {a["kind"] for a in board["attention"]}
    assert "unknown_open" in kinds
    assert "plan_incomplete" in kinds
    assert any(a.get("severity") == "block" for a in board["attention"])
    assert board["next_actions"]
    assert board["next_actions"][0]["argv"][0] == "terra"

    env = agent_status_response(board)
    assert env["status"] == "success"
    assert env["data"]["command"] == "map.status"
    assert "attention" in env["data"]

    text = format_status_text(board)
    assert "OPEN UNKNOWNS" in text
    assert "gap" in text


def test_status_all_maps_and_html(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    create_unknown(tmp_path, "g_u", claim="global gap?", evidence_needed="e")
    create_session_map(tmp_path, "exp", purpose="trial", use=True)
    create_unknown(tmp_path, "e_u", claim="exp gap?", evidence_needed="e")

    board = collect_status_board(tmp_path, all_maps=True)
    assert len(board["scopes"]) == 2
    ids = {s["map_id"] for s in board["scopes"]}
    assert ids == {"global", "exp"}

    # isolation: exp unknown only on exp scope
    by_id = {s["map_id"]: s for s in board["scopes"]}
    g_unk = {u["id"] for u in by_id["global"]["unknowns_open"]}
    e_unk = {u["id"] for u in by_id["exp"]["unknowns_open"]}
    assert "g_u" in g_unk
    assert "e_u" not in g_unk
    assert "e_u" in e_unk

    path = write_status_html(tmp_path, board)
    assert path.is_file()
    html = path.read_text(encoding="utf-8")
    assert "Terra map status" in html
    assert "g_u" in html
    assert "e_u" in html


def test_status_id_scope(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    create_session_map(tmp_path, "only", use=False)
    write_active_map(tmp_path, "global")
    create_unknown(tmp_path, "on_global", claim="g?", evidence_needed="e")
    board = collect_status_board(tmp_path, map_id="only")
    assert board["scopes"][0]["map_id"] == "only"
    assert board["scopes"][0]["counts"]["unknowns"] == 0
    # guidance still sees global unknown
    assert any(
        a.get("id") == "on_global" and a.get("map_id") == "global"
        for a in board["attention"]
    )


def test_status_cross_map_attention_when_active_quiet(tmp_path: Path, monkeypatch):
    """Active global must not hide session unknowns (agent DX)."""
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    create_session_map(tmp_path, "night", purpose="trial", use=True)
    create_unknown(
        tmp_path,
        "hostiles",
        claim="how many?",
        evidence_needed="e",
        blocks_build=True,
    )
    write_active_map(tmp_path, "global")

    board = collect_status_board(tmp_path, all_maps=False)
    assert board["active_map"] == "global"
    assert len(board["scopes"]) == 1
    assert board["scopes"][0]["map_id"] == "global"
    assert board["scopes"][0]["counts"]["unknowns_open"] == 0

    kinds = {a["kind"] for a in board["attention"]}
    assert "unknown_open" in kinds
    assert "other_map_work" in kinds
    assert "night" in board["maps_with_attention"]

    # next_actions must pin --map night (not usable without it on global)
    show = [
        a
        for a in board["next_actions"]
        if a.get("op") == "unknown.show" and a.get("map_id") == "night"
    ]
    assert show
    assert "--map" in show[0]["argv"]
    assert "night" in show[0]["argv"]

    # no useless quiet self-loop
    assert not any(
        a.get("op") == "map.status" and a.get("priority") == 90
        for a in board["next_actions"]
    )
