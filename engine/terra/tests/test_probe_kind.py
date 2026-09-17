"""Probe kind (run | watch) + duration_s (snapshot = watch @ 0)."""

from __future__ import annotations

import json
from pathlib import Path

from terra.probe_init import init_probe
from terra.probe_validate import validate_probe_dir, validate_probe_script


def test_init_watch_snapshot_default(tmp_path: Path):
    pdir = init_probe(tmp_path, "snap", purpose="list state once")
    meta = json.loads((pdir / "probe.json").read_text())
    assert meta["kind"] == "watch"
    assert meta["duration_s"] == 0.0
    script = (pdir / "probe.py").read_text()
    assert "KIND = 'watch'" in script or 'KIND = "watch"' in script
    assert "DURATION_S" in script
    result = validate_probe_dir(pdir)
    assert result["ok"] is True, result["blocks"]


def test_init_run_no_duration(tmp_path: Path):
    pdir = init_probe(tmp_path, "drive", purpose="exercise api", kind="run")
    meta = json.loads((pdir / "probe.json").read_text())
    assert meta["kind"] == "run"
    assert "duration_s" not in meta
    result = validate_probe_dir(pdir)
    assert result["ok"] is True, result["blocks"]


def test_kind_mismatch_script_vs_meta(tmp_path: Path):
    pdir = init_probe(tmp_path, "mismatch", purpose="x", kind="watch")
    script = (pdir / "probe.py").read_text()
    (pdir / "probe.py").write_text(
        script.replace("KIND = 'watch'", "KIND = 'run'").replace(
            "DURATION_S = 0.0  # 0 = snapshot\n", ""
        ),
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("does not match" in b or "KIND" in b for b in result["blocks"])


def test_watch_missing_duration_in_script(tmp_path: Path):
    pdir = init_probe(tmp_path, "nodur", purpose="x", kind="watch")
    script = (pdir / "probe.py").read_text()
    lines = [ln for ln in script.splitlines() if not ln.startswith("DURATION_S")]
    (pdir / "probe.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("DURATION_S" in b for b in result["blocks"])


def test_run_with_duration_in_meta_fails(tmp_path: Path):
    pdir = init_probe(tmp_path, "baddur", purpose="x", kind="run")
    meta = json.loads((pdir / "probe.json").read_text())
    meta["duration_s"] = 5
    (pdir / "probe.json").write_text(json.dumps(meta), encoding="utf-8")
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("duration_s" in b for b in result["blocks"])


def test_duration_mismatch(tmp_path: Path):
    pdir = init_probe(
        tmp_path, "stream", purpose="listen", kind="watch", duration_s=5.0
    )
    script = (pdir / "probe.py").read_text()
    (pdir / "probe.py").write_text(
        script.replace("DURATION_S = 5.0", "DURATION_S = 1.0"),
        encoding="utf-8",
    )
    result = validate_probe_dir(pdir)
    assert result["ok"] is False
    assert any("DURATION_S" in b and "match" in b for b in result["blocks"])


def test_bare_script_requires_kind(tmp_path: Path):
    script = tmp_path / "peek.py"
    script.write_text(
        "PURPOSE = 'peek'\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    to = (ctx or {}).get('to') or {'k': 1}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': []}\n",
        encoding="utf-8",
    )
    result = validate_probe_script(script)
    assert result["ok"] is False
    assert any("KIND" in b for b in result["blocks"])


def test_bare_script_with_kind_ok(tmp_path: Path):
    script = tmp_path / "peek.py"
    script.write_text(
        "PURPOSE = 'peek'\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    to = (ctx or {}).get('to') or {'k': 1}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': []}\n",
        encoding="utf-8",
    )
    result = validate_probe_script(script)
    assert result["ok"] is True, result["blocks"]
