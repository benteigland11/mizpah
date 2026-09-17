"""Design-time probe validation."""

from __future__ import annotations

from pathlib import Path

from terra.probe_init import init_probe
from terra.probe_validate import (
    validate_all_probes,
    validate_probe_dir,
    validate_probe_script,
)
from terra.paths import probes_root


def test_init_and_validate_ok(tmp_path: Path):
    pdir = init_probe(
        tmp_path,
        "env_check",
        purpose="What Python/platform are we on?",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is True, result["blocks"]
    assert result["id"] == "env_check"


def test_missing_required_exports_fails(tmp_path: Path):
    pdir = init_probe(tmp_path, "bad_exports", purpose="x")
    script = pdir / "probe.py"
    script.write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['status']\n\ndef run(ctx=None):\n    return {}\n",
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("REQUIRED_EXPORTS" in b for b in result["blocks"])


def test_missing_run_fails(tmp_path: Path):
    pdir = init_probe(tmp_path, "no_run", purpose="x")
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n",
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("run" in b for b in result["blocks"])


def test_id_mismatch_fails(tmp_path: Path):
    pdir = init_probe(tmp_path, "good_id", purpose="x")
    meta = (pdir / "probe.json").read_text(encoding="utf-8")
    (pdir / "probe.json").write_text(
        meta.replace('"good_id"', '"other_id"'),
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("does not match" in b for b in result["blocks"])


def test_validate_bare_script(tmp_path: Path):
    script = tmp_path / "quick_look.py"
    script.write_text(
        "PURPOSE = 'peek'\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    return {'to': {'k': 1}, 'status': 'ok', 'artifacts': []}\n",
        encoding="utf-8",
    )
    result = validate_probe_script(script)
    assert result["ok"] is True, result["blocks"]


def test_validate_all(tmp_path: Path):
    init_probe(tmp_path, "one", purpose="first")
    init_probe(tmp_path, "two", purpose="second")
    result = validate_all_probes(probes_root(tmp_path))
    assert result["ok"] is True
    assert len(result["probes"]) == 2
