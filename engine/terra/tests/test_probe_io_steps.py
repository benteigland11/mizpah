"""Input/output validation must fail loud — never silent."""

from __future__ import annotations

from pathlib import Path

from terra.probe_contract import (
    validate_probe_input_level1,
    validate_probe_output_level1,
)
from terra.probe_init import init_probe
from terra.probe_validate import exercise_run_level1, validate_probe_dir


def test_input_missing_to_blocks():
    blocks = validate_probe_input_level1({"dry_run": True})
    assert blocks
    assert any("input" in b and "to" in b for b in blocks)


def test_input_empty_to_blocks():
    blocks = validate_probe_input_level1({"to": {}})
    assert any("input" in b for b in blocks)


def test_output_none_blocks():
    blocks = validate_probe_output_level1(None)
    assert any("output" in b for b in blocks)


def test_exercise_skips_run_on_bad_input():
    called = {"n": 0}

    def run(ctx=None):
        called["n"] += 1
        return {"to": {"k": 1}, "status": "ok", "artifacts": []}

    blocks, _, exercise = exercise_run_level1(run, ctx={"to": {}})
    assert called["n"] == 0
    assert any("input" in b for b in blocks)
    assert exercise["steps"]["input"]["ok"] is False
    assert exercise["steps"]["output"]["ok"] is False


def test_run_without_ctx_is_input_error_not_silent():
    def run():
        return {"to": {"k": 1}, "status": "ok", "artifacts": []}

    blocks, _, exercise = exercise_run_level1(run)
    assert blocks
    assert exercise["steps"]["input"]["ok"] is False or any(
        "input" in b for b in blocks
    )
    assert any("ctx" in b or "input" in b for b in blocks)


def test_bad_output_labeled(tmp_path: Path):
    pdir = init_probe(tmp_path, "bad_out", purpose="x")
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    return {'to': {}, 'status': 'ok', 'artifacts': []}\n",
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert result["exercise"] is not None
    assert result["exercise"]["steps"]["input"]["ok"] is True
    assert result["exercise"]["steps"]["output"]["ok"] is False
    assert any("output" in b for b in result["blocks"])


def test_good_probe_all_steps_ok(tmp_path: Path):
    pdir = init_probe(tmp_path, "good", purpose="x")
    result = validate_probe_dir(pdir)
    assert result["ok"] is True
    steps = result["exercise"]["steps"]
    assert steps["input"]["ok"] is True
    assert steps["execute"]["ok"] is True
    assert steps["output"]["ok"] is True
