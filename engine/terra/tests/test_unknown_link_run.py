"""First-class unknown ↔ run links."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import (
    create_unknown,
    describe_unknown,
    link_run,
    load_unknown,
    set_status,
    unlink_run,
)


def test_link_run_and_resolve_via_run_ids(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_unknown(
        tmp_path,
        "home_job_split",
        claim="how do jobs split?",
        evidence_needed="anomaly + layout runs",
    )
    init_probe(tmp_path, "anomalies", purpose="anomalies")
    stamp = run_probe(tmp_path, "anomalies", to={"kind": "region", "id": "home"})
    run_id = stamp["id"]

    rec = link_run(tmp_path, "home_job_split", run_id)
    assert rec["status"] == "probing"
    assert run_id in rec["run_ids"]
    assert rec["primary_run_id"] == run_id
    assert "anomalies" in (rec.get("probe_ids") or [])

    # resolve with only run_ids — no prose
    rec2 = set_status(tmp_path, "home_job_split", "resolved")
    assert rec2["status"] == "resolved"


def test_resolve_still_blocks_without_trail(tmp_path: Path):
    create_unknown(tmp_path, "gap", claim="x?", evidence_needed="y")
    with pytest.raises(ValueError, match="resolved|trail"):
        set_status(tmp_path, "gap", "resolved")


def test_link_run_missing_run_fails(tmp_path: Path):
    create_unknown(tmp_path, "gap", claim="x?", evidence_needed="y")
    with pytest.raises(FileNotFoundError):
        link_run(tmp_path, "gap", "no_such_run")


def test_describe_and_unlink(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_unknown(tmp_path, "gap", claim="x?", evidence_needed="y")
    init_probe(tmp_path, "p", purpose="p")
    rid = run_probe(tmp_path, "p", to={"kind": "default"}).get("id")
    link_run(tmp_path, "gap", rid)
    desc = describe_unknown(tmp_path, "gap")
    assert len(desc["linked_runs"]) == 1
    assert desc["linked_runs"][0]["exists"] is True
    unlink_run(tmp_path, "gap", rid)
    rec = load_unknown(tmp_path, "gap")
    assert rec["run_ids"] == []
    assert rec["primary_run_id"] is None


def test_link_run_to_resolved_unknown_warns(tmp_path, monkeypatch, capsys):
    """CLI notes to the agent: runs on a resolved unknown feed nothing."""
    import argparse

    from terra.cli import cmd_unknown_link_run
    from terra.knowns import graduate_unknown
    from terra.probe_init import init_probe
    from terra.probe_run import run_probe
    from terra.unknowns import create_unknown, link_run

    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    pdir = tmp_path / ".terra" / "map" / "probes" / "p"
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
        "            'measures': [{'quantity': 'q', 'value': 1}]}\n",
        encoding="utf-8",
    )
    create_unknown(
        tmp_path, "u", claim="q?", evidence_needed="e",
        map_type="number", quantity="q",
    )
    r1 = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    link_run(tmp_path, "u", r1)
    graduate_unknown(tmp_path, "u", known_id="fact")

    r2 = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    rc = cmd_unknown_link_run(
        argparse.Namespace(id="u", run_id=r2, primary=False)
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    note = payload["meta"]["note"]
    assert "this unknown is resolved by known:fact" in note
    assert f"terra known link-run fact {r2}" in note
