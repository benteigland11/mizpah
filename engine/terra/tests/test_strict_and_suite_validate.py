"""suite validate + --strict-to / --strict-status on run."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.suites import create_suite, validate_all_suites, validate_suite


def test_suite_validate_ok(tmp_path: Path):
    init_probe(tmp_path, "a", purpose="a")
    init_probe(tmp_path, "b", purpose="b")
    create_suite(tmp_path, "pair", probes=["a", "b"])
    result = validate_suite(tmp_path, "pair")
    assert result["ok"] is True, result["blocks"]
    assert len(result["probes"]) == 2
    assert validate_all_suites(tmp_path)["ok"] is True


def test_suite_validate_bad_probe(tmp_path: Path):
    init_probe(tmp_path, "a", purpose="a")
    create_suite(tmp_path, "pair", probes=["a"])
    # break probe script
    p = tmp_path / ".terra" / "map" / "probes" / "a" / "probe.py"
    p.write_text("KIND='watch'\nDURATION_S=0\n", encoding="utf-8")
    result = validate_suite(tmp_path, "pair")
    assert result["ok"] is False
    assert any("probe a" in b or "run" in b for b in result["blocks"])


def test_strict_to_fails_missing_kind(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    with pytest.raises(ValueError, match="strict"):
        run_probe(
            tmp_path,
            "p",
            to={"uuid": "no-kind"},
            dry_run=False,
            strict_to=True,
        )


def test_strict_status_fails_freeform(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    pdir = tmp_path / ".terra" / "map" / "probes" / "p"
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "from pathlib import Path\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    f = Path(__file__).parent / 'o.txt'\n"
        "    f.write_text('x')\n"
        "    return {'to': to, 'status': 'missing_layout', "
        "'artifacts': [{'path': str(f), 'role': 'out'}]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strict-status"):
        run_probe(
            tmp_path,
            "p",
            to={"kind": "region"},
            dry_run=False,
            strict_status=True,
        )


def test_default_still_soft(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    stamp = run_probe(tmp_path, "p", to={"uuid": "x"}, dry_run=False)
    assert stamp.get("id")
    assert any("kind" in w for w in (stamp.get("warnings") or []))
