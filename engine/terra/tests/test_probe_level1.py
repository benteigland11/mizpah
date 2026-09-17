"""Level-1 bare-minimum validation: to in, {to,status,artifacts} out."""

from __future__ import annotations

from pathlib import Path

from terra.probe_contract import validate_probe_result_level1
from terra.probe_init import init_probe
from terra.probe_validate import validate_probe_dir, validate_probe_script


def test_result_helper_empty_to_blocks():
    blocks = validate_probe_result_level1(
        {"to": {}, "status": "ok", "artifacts": []}
    )
    assert any("to" in b for b in blocks)


def test_result_helper_missing_keys():
    blocks = validate_probe_result_level1({"to": {"a": 1}})
    assert any("missing" in b for b in blocks)


def test_result_helper_ok():
    blocks = validate_probe_result_level1(
        {"to": {"kind": "path", "path": "/tmp"}, "status": "ok", "artifacts": []}
    )
    assert blocks == []


def test_scaffold_passes_level1(tmp_path: Path):
    pdir = init_probe(tmp_path, "env_check", purpose="runtime?")
    result = validate_probe_dir(pdir)
    assert result["ok"] is True, result["blocks"]
    assert result["level"] == 1
    assert result["exercise"] is not None
    assert result["exercise"]["status"] == "ok"


def test_empty_to_from_run_fails(tmp_path: Path):
    pdir = init_probe(tmp_path, "empty_to", purpose="x")
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    return {'to': {}, 'status': 'ok', 'artifacts': []}\n",
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("to" in b for b in result["blocks"])


def test_missing_artifacts_key_fails(tmp_path: Path):
    pdir = init_probe(tmp_path, "no_arts", purpose="x")
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    return {'to': {'k': 1}, 'status': 'ok'}\n",
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("artifacts" in b or "missing" in b for b in result["blocks"])


def test_run_raises_fails(tmp_path: Path):
    pdir = init_probe(tmp_path, "boom", purpose="x")
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    raise RuntimeError('nope')\n",
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("raised" in b for b in result["blocks"])


def test_bare_script_level1(tmp_path: Path):
    script = tmp_path / "peek.py"
    script.write_text(
        "PURPOSE = 'peek'\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': []}\n",
        encoding="utf-8",
    )
    result = validate_probe_script(script)
    assert result["ok"] is True, result["blocks"]
    assert result["level"] == 1
