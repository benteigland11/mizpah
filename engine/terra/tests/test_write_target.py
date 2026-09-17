"""Active-map loudness: name the write target; scream on inherited/stale reads.

The active map is invisible until a write (or delete) hits the wrong copy.
State-changing commands announce their target map on stderr; `known get`
screams when the value is inherited via the map chain or stale.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from terra.cli import _announce_write_target, cmd_known_get, cmd_unknown_create
from terra.knowns import graduate_unknown
from terra.paths import create_session_map, write_active_map
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run


def _write_measure_probe(root: Path, probe_id: str, *, quantity="q", value=7.0):
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


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p")
    return tmp_path


def _mk_known_on(root: Path, mid: str, kid: str, *, value=7.0) -> None:
    from terra.paths import scoped_map

    _write_measure_probe(root, "p", value=value)
    with scoped_map(mid):
        rid = run_probe(root, "p", to={"kind": "region"}).get("id")
        create_unknown(
            root, f"u_{kid}", claim="?", evidence_needed="e",
            map_type="number", quantity="q",
        )
        link_run(root, f"u_{kid}", rid)
        graduate_unknown(root, f"u_{kid}", known_id=kid)


def test_announce_global_is_arrow(proj, capsys):
    write_active_map(proj, "global")
    _announce_write_target(proj, "unknown create")
    err = capsys.readouterr().err
    assert "→ unknown create writes to map 'global'" in err
    assert "NOT global" not in err


def test_announce_session_is_warning(proj, capsys):
    create_session_map(proj, "sim_vv")
    write_active_map(proj, "sim_vv")
    _announce_write_target(proj, "known delete")
    err = capsys.readouterr().err
    assert "⚠ known delete writes to map 'sim_vv'" in err
    assert "NOT global" in err


def test_unknown_create_announces_target(proj, capsys):
    rc = cmd_unknown_create(
        argparse.Namespace(
            id="u1", claim="q?", evidence="e", no_blocks_build=False,
            probe=None, notes="", force=False, type="number", quantity="q",
            unit="", expression=None, vars=None, within=None,
            x_quantity=None, x_unit="",
        )
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "unknown create writes to map" in err


def _get_ns(kid: str, **over):
    base = dict(
        id=kid, min_conf="low", allow_stale=False, allow_disagree=False,
        allow_cohort_mismatch=False, consumer=None, at=None, raw=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_known_get_screams_when_inherited(proj, capsys):
    _mk_known_on(proj, "global", "k", value=7.0)
    create_session_map(proj, "child", parent="global")
    write_active_map(proj, "child")
    rc = cmd_known_get(_get_ns("k"))
    assert rc == 0
    err = capsys.readouterr().err
    assert "INHERITED from map 'global'" in err


def test_known_get_quiet_when_local(proj, capsys):
    _mk_known_on(proj, "global", "k", value=7.0)
    write_active_map(proj, "global")
    rc = cmd_known_get(_get_ns("k"))
    assert rc == 0
    err = capsys.readouterr().err
    assert "INHERITED" not in err
