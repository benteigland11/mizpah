"""A formula node binding a SUPERSEDED known must stay DESCRIBABLE.

The read refusal is correct — a retired belief must not be consumed — but it
used to escape `unknown show/get` as an uncaught traceback, making the node
unreadable. An unreadable node is invisible to CLI-based audits, so retiring
a belief could silently hide every node that still depends on it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from terra.brief import init_brief
from terra.knowns import graduate_unknown, supersede_known
from terra.paths import ensure_map_store
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run


def _run(root: Path, *argv):
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    source_paths = [str(repo), str(repo / "src")]
    if inherited := env.get("PYTHONPATH"):
        source_paths.append(inherited)
    env["PYTHONPATH"] = os.pathsep.join(source_paths)
    return subprocess.run(
        [sys.executable, "-m", "terra.cli", *argv],
        cwd=root, capture_output=True, text=True, env=env,
    )


@pytest.fixture()
def proj(tmp_path):
    init_brief(tmp_path, title="t", mission="m")
    ensure_map_store(tmp_path)
    return tmp_path


def test_formula_var_on_superseded_known_does_not_traceback(proj, monkeypatch):
    monkeypatch.chdir(proj)
    from test_formula_type import _write_measure_probe

    init_probe(proj, "src", purpose="p")
    _write_measure_probe(proj, "src", quantity="floor", value=900.0)
    rid = run_probe(proj, "src", to={"kind": "t", "id": "1"})["id"]
    create_unknown(proj, "floor_k", map_type="number", quantity="floor",
                   claim="floor?", evidence_needed="e")
    link_run(proj, "floor_k", rid)
    graduate_unknown(proj, "floor_k")

    create_unknown(
        proj, "closes", map_type="formula", quantity="closes",
        claim="closes?", evidence_needed="e",
        expression="measured >= limit",
        vars=["measured=floor", "limit=known:floor_k"],
    )
    supersede_known(proj, "floor_k", reason="owner relaxed the floor")

    r = _run(proj, "unknown", "show", "closes")
    assert "Traceback" not in r.stderr, r.stderr[:400]
    assert r.returncode == 1
    assert "cannot be described" in r.stderr
    assert "SUPERSEDED" in r.stderr or "superseded" in r.stderr
    assert "--allow-superseded" in r.stderr


def test_healthy_formula_node_still_describes(proj, monkeypatch):
    """CAN-FAIL: the guard must not break ordinary description."""
    monkeypatch.chdir(proj)
    from test_formula_type import _write_measure_probe

    init_probe(proj, "src2", purpose="p")
    _write_measure_probe(proj, "src2", quantity="floor", value=900.0)
    rid = run_probe(proj, "src2", to={"kind": "t", "id": "1"})["id"]
    create_unknown(proj, "floor_k2", map_type="number", quantity="floor",
                   claim="floor?", evidence_needed="e")
    link_run(proj, "floor_k2", rid)
    graduate_unknown(proj, "floor_k2")
    create_unknown(
        proj, "closes2", map_type="formula", quantity="closes",
        claim="closes?", evidence_needed="e",
        expression="measured >= limit",
        vars=["measured=floor", "limit=known:floor_k2"],
    )
    r = _run(proj, "unknown", "show", "closes2")
    assert r.returncode == 0, r.stderr[:400]
    assert "Traceback" not in r.stderr
