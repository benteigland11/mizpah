"""`probe_source_sha256` must describe the source that EXECUTED.

It used to be read at the END of the run, so a long solve stamped whatever
was on disk when it finished. Observed live on CG-01: an arm that started
under one sha and executed that code recorded a peer's later edit, because
the probe was edited mid-solve. That is not a lie about the FILE, it is a
lie about the RUN — two arms look like different code when they were not.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from terra.brief import init_brief
from terra.paths import ensure_map_store
from terra.probe_init import init_probe
from terra.probe_run import run_probe


@pytest.fixture()
def proj(tmp_path):
    init_brief(tmp_path, title="t", mission="m")
    ensure_map_store(tmp_path)
    return tmp_path


def _probe(root: Path, pid: str, marker: str):
    (root / ".terra" / "map" / "probes" / pid / "probe.py").write_text(
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        f"MARKER = {marker!r}\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
        "            'measures': [{'quantity': 'q', 'value': 1.0}]}\n",
        encoding="utf-8",
    )


def test_sha_records_the_source_that_ran_not_a_later_edit(proj, monkeypatch):
    """Edit the probe DURING the run; the stamp must be the executed source."""
    monkeypatch.chdir(proj)
    init_probe(proj, "p", purpose="x")
    _probe(proj, "p", "ORIGINAL")
    script = proj / ".terra" / "map" / "probes" / "p" / "probe.py"
    executed = hashlib.sha256(script.read_bytes()).hexdigest()

    import terra.probe_run as pr

    real = pr._call_with_timeout

    def edit_midrun(fn, ctx, timeout):
        out = real(fn, ctx, timeout)
        _probe(proj, "p", "EDITED_BY_A_PEER_MIDRUN")   # concurrent writer
        return out

    monkeypatch.setattr(pr, "_call_with_timeout", edit_midrun)
    stamp = run_probe(proj, "p", to={"kind": "t", "id": "1"})

    after = hashlib.sha256(script.read_bytes()).hexdigest()
    assert after != executed, "fixture must actually change the file"
    assert stamp["probe_source_sha256"] == executed, (
        "stamp must describe the source that RAN, not the later edit"
    )


def test_uncontended_run_still_stamps_its_own_source(proj, monkeypatch):
    """CAN-FAIL: ordinary runs must be unaffected."""
    monkeypatch.chdir(proj)
    init_probe(proj, "q", purpose="x")
    _probe(proj, "q", "ONLY")
    script = proj / ".terra" / "map" / "probes" / "q" / "probe.py"
    stamp = run_probe(proj, "q", to={"kind": "t", "id": "1"})
    assert stamp["probe_source_sha256"] == hashlib.sha256(
        script.read_bytes()
    ).hexdigest()
