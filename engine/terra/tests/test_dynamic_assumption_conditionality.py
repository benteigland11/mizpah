"""Conditionality must propagate however the assumption was obtained.

A probe that reads an assumption from inside its own code used to stamp
`conditional: false, assumptions: []` because the top-level fields came only
from the DECLARED probe.json inputs block — which almost no probe fills in.
`terra gate` was therefore blind to the conditionality of every consumer of a
frozen planning assumption.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from terra.assumptions import create_assumption
from terra.brief import init_brief
from terra.paths import ensure_map_store
from terra.probe_init import init_probe
from terra.probe_run import run_probe


@pytest.fixture()
def proj(tmp_path):
    init_brief(tmp_path, title="t", mission="m")
    ensure_map_store(tmp_path)
    return tmp_path


def _write_probe(root: Path, pid: str, *, read_assumption_id: str | None):
    body = (
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "from pathlib import Path\n"
        f"AID = {read_assumption_id!r}\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    v = 1.0\n"
        "    if AID:\n"
        "        import os\n"
        "        from terra.assumptions import read_assumption\n"
        "        v = float(read_assumption(Path(os.getcwd()), AID)['value'])\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
        "            'measures': [{'quantity': 'q', 'value': v}]}\n"
    )
    (root / ".terra" / "map" / "probes" / pid / "probe.py").write_text(
        body, encoding="utf-8"
    )


def test_dynamic_assumption_read_marks_the_run_conditional(proj, monkeypatch):
    monkeypatch.chdir(proj)
    create_assumption(
        proj, "frozen_cd0", map_type="number", quantity="cd0", value=0.041,
        claim="frozen basis?", reason="PM freeze", evidence_needed="cfd",
    )
    init_probe(proj, "consumer", purpose="p")
    _write_probe(proj, "consumer", read_assumption_id="frozen_cd0")

    stamp = run_probe(proj, "consumer", to={"kind": "t", "id": "1"})
    assert stamp["conditional"] is True
    assert "frozen_cd0" in stamp["assumptions"]
    # and the read is still visible in the per-read provenance
    assert any(
        r.get("assumption_id") == "frozen_cd0" for r in stamp["known_reads"]
    )


def test_probe_reading_no_assumption_is_NOT_conditional(proj, monkeypatch):
    """CAN-FAIL: marking everything conditional would be useless."""
    monkeypatch.chdir(proj)
    init_probe(proj, "plain", purpose="p")
    _write_probe(proj, "plain", read_assumption_id=None)

    stamp = run_probe(proj, "plain", to={"kind": "t", "id": "1"})
    assert stamp["conditional"] is False
    assert stamp["assumptions"] == []
