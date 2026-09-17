"""Non-blocking agent notes: likely mistakes get a copy-pasteable correction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from terra.cli import (
    cmd_design_refresh,
    cmd_known_unlink_run,
    cmd_probe_run,
    cmd_probe_validate,
    cmd_unknown_status,
)
from terra.knowns import graduate_unknown, link_run_known, promote_known
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_probe, link_run


def _write_measure_probe(root: Path, probe_id: str, *, quantity="q", value=1.0):
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


def test_probe_validate_scaffold_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "x", purpose="p")
    rc = cmd_probe_validate(
        argparse.Namespace(target="x", json=False, purpose=None, id=None)
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "scaffold stub" in out
    assert "validate PASS ≠ surveyed" in out


def test_probe_run_prints_link_hint(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "mass", purpose="p")
    _write_measure_probe(tmp_path, "mass")
    create_unknown(
        tmp_path, "mtow", claim="?", evidence_needed="e",
        map_type="number", quantity="q",
    )
    link_probe(tmp_path, "mtow", "mass")
    rc = cmd_probe_run(
        argparse.Namespace(
            id="mass", to='{"kind":"server"}', to_file=None, timeout=None,
            dry_run=False, json=False, strict_to=False, strict_status=False,
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "next: terra unknown link-run mtow " in out


def test_prose_resolve_suggests_graduate(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p")
    create_unknown(
        tmp_path, "u", claim="?", evidence_needed="e",
        map_type="number", quantity="q",
    )
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    link_run(tmp_path, "u", rid)
    rc = cmd_unknown_status(
        argparse.Namespace(
            id="u", status="resolved", resolved_by="prose note", notes=None
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "terra unknown graduate u" in out


def test_unlink_to_unbacked_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p")
    create_unknown(
        tmp_path, "u", claim="?", evidence_needed="e",
        map_type="number", quantity="q",
    )
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    link_run(tmp_path, "u", rid)
    graduate_unknown(tmp_path, "u", known_id="fact")
    rc = cmd_known_unlink_run(argparse.Namespace(id="fact", run_id=rid))
    out = capsys.readouterr().out
    assert rc == 0
    assert "unbacked (n=0)" in out


def test_design_refresh_lists_stale_artifacts(tmp_path, monkeypatch, capsys):
    import time

    from terra.design import add_param, attach_artifact

    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p")
    create_unknown(
        tmp_path, "u", claim="?", evidence_needed="e",
        map_type="number", quantity="q",
    )
    for _ in range(3):
        rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
        link_run(tmp_path, "u", rid)
    graduate_unknown(tmp_path, "u", known_id="mtow")
    promote_known(tmp_path, "mtow", "med")
    add_param(tmp_path, "mtow")
    (tmp_path / "sheet.pdf").write_text("v1")
    attach_artifact(tmp_path, "sheet.pdf", uses=["mtow"])
    time.sleep(1.1)
    rid = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    link_run_known(tmp_path, "mtow", rid)
    rc = cmd_design_refresh(argparse.Namespace(name="mtow"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "sheet.pdf" in out and "REGENERATE" in out
