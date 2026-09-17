"""Knowns are born only by graduating an evidence-bearing unknown."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import (
    create_known,
    graduate_unknown,
    load_known,
    set_known,
)
from terra.map_status import collect_status_board
from terra.probe_init import init_probe
from terra.probe_run import run_probe, void_run
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


def _typed_unknown_with_run(tmp_path: Path, uid: str = "gap") -> str:
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="q", value=4)
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    create_unknown(
        tmp_path,
        uid,
        claim="how big is q?",
        evidence_needed="a reading",
        map_type="number",
        quantity="q",
    )
    link_run(tmp_path, uid, rid)
    return rid


def test_create_known_requires_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="evidence at birth"):
        create_known(tmp_path, "naked", claim="q", quantity="q")


def test_known_set_cannot_birth(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        set_known(tmp_path, "ghost", claim="q", quantity="q")


def test_graduate_carries_evidence_and_resolves(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rid = _typed_unknown_with_run(tmp_path)
    rec = graduate_unknown(tmp_path, "gap")
    assert rec["id"] == "gap"
    assert rec["run_ids"] == [rid]
    assert rec["origin_unknown_id"] == "gap"
    assert rec["stats"]["n"] == 1
    assert rec["confidence"] == "low"
    unk = load_unknown(tmp_path, "gap")
    assert unk["status"] == "resolved"
    assert unk["resolved_by"] == "known:gap"


def test_graduate_as_slug(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _typed_unknown_with_run(tmp_path)
    rec = graduate_unknown(tmp_path, "gap", known_id="q_est")
    assert rec["id"] == "q_est"
    assert load_unknown(tmp_path, "gap")["resolved_by"] == "known:q_est"
    assert load_known(tmp_path, "q_est")["origin_unknown_id"] == "gap"


def test_graduate_blocks_untyped(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    create_unknown(tmp_path, "vague", claim="hm?", evidence_needed="e")
    with pytest.raises(ValueError, match="untyped"):
        graduate_unknown(tmp_path, "vague")


def test_graduate_blocks_without_live_runs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_unknown(
        tmp_path,
        "dry",
        claim="q?",
        evidence_needed="e",
        map_type="number",
        quantity="q",
    )
    with pytest.raises(ValueError, match="no live"):
        graduate_unknown(tmp_path, "dry")


def test_graduate_excludes_voided_runs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rid = _typed_unknown_with_run(tmp_path)
    void_run(tmp_path, rid, reason="bad", cascade=False)
    with pytest.raises(ValueError, match="no live"):
        graduate_unknown(tmp_path, "gap")


def test_unbacked_known_surfaces_in_attention(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rid = _typed_unknown_with_run(tmp_path)
    graduate_unknown(tmp_path, "gap")
    board = collect_status_board(tmp_path)
    kinds = {a["kind"] for a in board["attention"]}
    assert "known_unbacked" not in kinds

    # evidence voided away later → belief flagged, re-back action offered
    void_run(tmp_path, rid, reason="probe bug", cascade=True)
    board = collect_status_board(tmp_path)
    unbacked = [
        a for a in board["attention"] if a["kind"] == "known_unbacked"
    ]
    assert unbacked and unbacked[0]["id"] == "gap"
    assert any(
        a.get("op") == "known.link-run" for a in board["next_actions"]
    )


def test_known_set_cli_edits_claim(tmp_path, monkeypatch, capsys):
    """`terra known set` is wired: metadata edits work, freehand value refused."""
    monkeypatch.chdir(tmp_path)
    _typed_unknown_with_run(tmp_path)
    kid = "gap"
    graduate_unknown(tmp_path, kid)

    from terra.cli import main

    assert main(["known", "set", kid, "--claim", "sharper claim?"]) == 0
    assert load_known(tmp_path, kid)["claim"] == "sharper claim?"

    assert main(["known", "set", kid, "--value", "42"]) == 1
    err = capsys.readouterr().err
    assert "freehand --value is not supported" in err

    assert main(["known", "set", kid]) == 1
    assert "nothing to set" in capsys.readouterr().err
