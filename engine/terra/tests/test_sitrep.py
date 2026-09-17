"""sitrep — one-call orientation digest.

The point of these tests is the ECONOMICS invariants, not just the shape:
sitrep exists to replace five turns with one, so a regression that makes it
big, or that makes it exit nonzero (aborting `&&` chains), defeats it even
if the payload is still correct.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from terra.brief import init_brief
from terra.paths import ensure_map_store, set_active_map_id
from terra.route import add_task, block_task, init_route, start_task
from terra.sitrep import collect_sitrep, format_sitrep_text


def _env() -> dict[str, str]:
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{repo}{os.pathsep}{repo / 'src'}"
    env.pop("TERRA_MAP", None)
    return env


def _run(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "terra.cli", *argv],
        cwd=root,
        capture_output=True,
        text=True,
        env=_env(),
    )


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    set_active_map_id(None)
    root = tmp_path / "proj"
    root.mkdir()
    ensure_map_store(root)
    init_brief(root, title="dx", mission="m")
    init_route(root)
    return root


def test_sitrep_composes_brief_route_map_gate(project: Path) -> None:
    add_task(project, "a", title="A", bucket="low")
    rep = collect_sitrep(project)

    assert rep["command"] == "terra.sitrep"
    assert rep["brief"]["title"] == "dx"
    assert rep["route"]["counts"]["tasks"] == 1
    assert "ok" in rep["gate"]
    assert isinstance(rep["attention_summary"], list)
    assert rep["maps"], "must report at least the global map"


def test_sitrep_exits_zero_even_when_gate_fails(project: Path) -> None:
    """The chaining invariant: a failing gate must NOT abort an `&&` chain."""
    _run(project, "unknown", "create", "u", "--type", "number",
         "--quantity", "q", "--claim", "c?", "--evidence", "e")

    gate = _run(project, "gate")
    assert gate.returncode == 1, "precondition: gate fails on an open unknown"

    sit = _run(project, "sitrep")
    assert sit.returncode == 0, "sitrep must never abort a chained call"
    payload = json.loads(sit.stdout)
    assert payload["status"] == "success"
    assert payload["data"]["gate"]["ok"] is False, (
        "the verdict must survive in the payload, not the exit code"
    )


def test_sitrep_chains_without_losing_later_commands(project: Path) -> None:
    """`terra gate && X` drops X; `terra sitrep && X` must not."""
    _run(project, "unknown", "create", "u", "--type", "number",
         "--quantity", "q", "--claim", "c?", "--evidence", "e")
    env = _env()
    py = sys.executable
    chained = subprocess.run(
        f"{py} -m terra.cli sitrep >/dev/null && echo REACHED",
        shell=True, cwd=project, capture_output=True, text=True, env=env,
    )
    assert "REACHED" in chained.stdout

    # and the documented footgun still behaves as documented
    broken = subprocess.run(
        f"{py} -m terra.cli gate >/dev/null && echo REACHED",
        shell=True, cwd=project, capture_output=True, text=True, env=env,
    )
    assert "REACHED" not in broken.stdout


def test_attention_rollup_survives_a_flood(project: Path) -> None:
    """A flood of one kind must not bury the other kinds."""
    for i in range(25):
        _run(project, "unknown", "create", f"u{i}", "--type", "number",
             "--quantity", "q", "--claim", "c?", "--evidence", "e")
    add_task(project, "t1", title="T1", bucket="low")
    add_task(project, "t2", title="T2", bucket="low")
    block_task(project, "t2", reason="waiting")

    rep = collect_sitrep(project)
    kinds = {(s["plane"], s["kind"]) for s in rep["attention_summary"]}
    assert ("map", "unknown_open") in kinds
    assert ("route", "task_blocked") in kinds, (
        "route debt must not be buried under 25 map unknowns"
    )
    flood = next(
        s for s in rep["attention_summary"]
        if s["kind"] == "unknown_open"
    )
    assert flood["count"] == 25, "rollup must count ALL of them, not the sample"

    sampled_kinds = {(a.get("plane"), a.get("kind")) for a in rep["attention"]}
    assert ("route", "task_blocked") in sampled_kinds, (
        "diverse sample must reach a rare kind before repeating a common one"
    )


def test_truncation_is_declared_never_silent(project: Path) -> None:
    for i in range(40):
        _run(project, "unknown", "create", f"u{i}", "--type", "number",
             "--quantity", "q", "--claim", "c?", "--evidence", "e")
    rep = collect_sitrep(project)
    assert rep["truncated"]["attention"] > 0
    assert rep["attention_total"] > len(rep["attention"])
    assert rep["truncated"]["full"] is False

    full = collect_sitrep(project, full=True)
    assert full["truncated"]["attention"] == 0
    assert full["truncated"]["full"] is True
    assert len(full["attention"]) == full["attention_total"]


def test_sitrep_is_smaller_than_the_calls_it_replaces(project: Path) -> None:
    """The whole reason it exists. If this fails, sitrep is pointless."""
    for i in range(30):
        add_task(project, f"t{i}", title=f"T{i}", bucket="low")
    for i in range(10):
        _run(project, "unknown", "create", f"u{i}", "--type", "number",
             "--quantity", "q", "--claim", "c?", "--evidence", "e")

    sequence = sum(
        len(_run(project, *argv).stdout)
        for argv in (
            ("route", "status"),
            ("route", "budget"),
            ("map", "status"),
            ("known", "list"),
            ("gate"),
        )
        if True
    )
    sit = len(_run(project, "sitrep").stdout)
    assert sit < sequence, (
        f"sitrep ({sit}b) must be smaller than the 5-call sequence ({sequence}b)"
    )


def test_sitrep_is_a_pure_view(project: Path) -> None:
    """No new state: nothing under .terra may change."""
    add_task(project, "a", title="A", bucket="low")

    def snapshot() -> dict[str, float]:
        return {
            str(p): p.stat().st_mtime_ns
            for p in sorted((project / ".terra").rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    collect_sitrep(project)
    collect_sitrep(project, full=True)
    assert snapshot() == before, "sitrep must not write anything"


def test_human_format_renders_without_route_or_brief(tmp_path: Path) -> None:
    """Map-only project: sitrep must degrade, not crash."""
    set_active_map_id(None)
    root = tmp_path / "bare"
    root.mkdir()
    ensure_map_store(root)
    rep = collect_sitrep(root)
    assert rep["route"] is None
    assert rep["brief"] is None
    text = format_sitrep_text(rep)
    assert "not initialized" in text


def test_in_progress_task_surfaces_owner(project: Path) -> None:
    add_task(project, "a", title="A", bucket="low")
    start_task(project, "a", agent="cg-01-tech-lead")
    rep = collect_sitrep(project)
    assert rep["route"]["counts"]["in_progress"] == 1


def test_json_flag_is_accepted_as_noop(project: Path) -> None:
    """Every other read verb takes --json; refusing it here cost a lead turns.

    The failure mode is nasty: argparse exits 2 with an EMPTY stdout, so the
    caller's json.load fails at char 0 and the symptom reads as "corrupt
    JSON output", not "unrecognized flag".
    """
    plain = _run(project, "sitrep")
    flagged = _run(project, "sitrep", "--json")

    assert flagged.returncode == 0, (
        f"--json must be accepted; got rc={flagged.returncode} "
        f"stderr={flagged.stderr[:200]}"
    )
    assert flagged.stdout, "--json must not produce empty stdout"
    a, b = json.loads(plain.stdout), json.loads(flagged.stdout)
    assert a["data"]["command"] == b["data"]["command"] == "terra.sitrep"


def test_stdout_is_strictly_json_no_preamble(project: Path) -> None:
    """Advisory NOTE/banner text must never land on stdout and break parsing."""
    _run(project, "unknown", "create", "u", "--type", "number",
         "--quantity", "q", "--claim", "c?", "--evidence", "e")
    out = _run(project, "sitrep").stdout
    assert out.lstrip().startswith("{"), f"stdout preamble: {out[:120]!r}"
    json.loads(out)


def test_one_stale_belief_does_not_take_down_orientation(
    project: Path, monkeypatch
) -> None:
    """A stale known anywhere used to raise out of check_gate/status_board and
    kill the WHOLE digest — `terra sitrep` was unusable program-wide on
    2026-07-28. Orientation must DEGRADE, never die.
    """
    monkeypatch.chdir(project)
    add_task(project, "a", title="A", bucket="low")

    import terra.sitrep as sr

    def boom(*a, **k):
        raise ValueError("known x is STALE (known dep moved) — re-derive")

    monkeypatch.setattr(sr, "collect_sitrep", sr.collect_sitrep)
    monkeypatch.setattr("terra.gate.check_gate", boom)

    rep = sr.collect_sitrep(project)

    assert rep["route"] is not None, "route must still answer"
    assert rep["brief"] is not None, "brief must still answer"
    assert any(d["section"] == "gate" for d in rep["degraded"])
    assert rep["gate"]["ok"] is None, "unknown verdict, not a false PASS"
    text = sr.format_sitrep_text(rep)
    assert "DEGRADED" in text, "the human view must SAY it is degraded"


def test_degraded_is_empty_when_healthy(project: Path) -> None:
    """The discriminator: a healthy program must not report degradation."""
    add_task(project, "a", title="A", bucket="low")
    assert collect_sitrep(project)["degraded"] == []


def test_degraded_gate_renders_unknown_not_fail(project: Path, monkeypatch) -> None:
    """A verdict we could not compute must not print as FAIL (or PASS)."""
    monkeypatch.chdir(project)
    add_task(project, "a", title="A", bucket="low")
    import terra.sitrep as sr

    monkeypatch.setattr(
        "terra.gate.check_gate",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("stale")),
    )
    text = sr.format_sitrep_text(sr.collect_sitrep(project))
    assert "gate: UNKNOWN" in text
    assert "gate: FAIL" not in text
