"""Agent-DX fixes from the 2026-07-27 lead handbacks.

Each test encodes a failure an agent actually hit in production, not a
hypothetical. The common thread is that all of them let an agent believe
something false: a failure that reads as success, a run whose inputs are
undisclosed, a reclaim that reports the wrong reason.
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
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.route import (
    HEARTBEAT_STALE_HOURS,
    add_task,
    complete_task,
    heartbeat_task,
    init_route,
    load_route,
    route_log,
    save_route,
    start_task,
)


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


# --- #4: a failure must not read as a success -----------------------------


def test_error_envelope_screams_on_stderr(project: Path) -> None:
    """`| tail` on a failure showed `"code": "route_complete"` — reads as OK.

    A lead marked three routes done this way. stdout must stay pure JSON;
    the loud marker goes to stderr.
    """
    r = _run(project, "route", "complete", "nope_not_a_task", "--evidence", "x")

    assert r.returncode == 1
    assert "TERRA ERROR" in r.stderr, "failure must be unmissable on stderr"
    assert "route_complete" in r.stderr, "marker should name the operation"
    payload = json.loads(r.stdout)
    assert payload["status"] == "error", "stdout must stay parseable JSON"


def test_success_writes_nothing_to_the_error_channel(project: Path) -> None:
    add_task(project, "a", title="A", bucket="low")
    r = _run(project, "route", "status")
    assert r.returncode == 0
    assert "TERRA ERROR" not in r.stderr


# --- #9: reclaiming a stranded lead ---------------------------------------


def _age_heartbeat(root: Path, task_id: str, hours: float) -> None:
    from datetime import datetime, timedelta, timezone

    rec = load_route(root)
    stamp = (
        (datetime.now(timezone.utc) - timedelta(hours=hours))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    for t in rec["tasks"]:
        if t["id"] == task_id:
            t["last_heartbeat_at"] = stamp
    save_route(root, rec)


def test_reclaim_stranded_task_instead_of_waiting_on_none(project: Path) -> None:
    """The message used to be `task X waiting on None` — meaningless."""
    add_task(project, "a", title="A", bucket="low")
    start_task(project, "a", agent="dead-lead")
    _age_heartbeat(project, "a", HEARTBEAT_STALE_HOURS + 1)

    t = start_task(project, "a", agent="cg-01-sim-lead")
    assert t["owner_agent"] == "cg-01-sim-lead"
    assert t["reclaimed_from"] == "dead-lead"
    assert t["status"] == "in_progress"


def test_reclaim_works_when_owner_was_never_stamped(project: Path) -> None:
    """The real case: owner_agent None, so nothing to compare against."""
    add_task(project, "a", title="A", bucket="low")
    start_task(project, "a")  # no --agent
    t = start_task(project, "a", agent="cg-01-sim-lead")
    assert t["owner_agent"] == "cg-01-sim-lead"


def test_refuses_to_steal_a_LIVE_task(project: Path) -> None:
    """Two writers on one task corrupted the master model. Must refuse."""
    add_task(project, "a", title="A", bucket="low")
    start_task(project, "a", agent="alive-lead")
    heartbeat_task(project, "a", agent="alive-lead")

    with pytest.raises(ValueError, match="ALIVE"):
        start_task(project, "a", agent="other-lead")


def test_same_agent_restart_is_just_a_heartbeat(project: Path) -> None:
    add_task(project, "a", title="A", bucket="low")
    start_task(project, "a", agent="me")
    t = start_task(project, "a", agent="me")
    assert t["owner_agent"] == "me"
    assert "reclaimed_from" not in t


# --- #7: route log --task -------------------------------------------------


def test_route_log_filters_to_one_task(project: Path) -> None:
    add_task(project, "a", title="A", bucket="low")
    add_task(project, "b", title="B", bucket="low")
    complete_task(project, "a", evidence="did A", freehand="n/a")
    complete_task(project, "b", evidence="did B", freehand="n/a")

    everything = route_log(project)
    just_a = route_log(project, task_id="a")

    assert {e["task"] for e in just_a["events"]} == {"a"}
    assert len(just_a["events"]) < len(everything["events"])

    with pytest.raises(ValueError, match="task not found"):
        route_log(project, task_id="ghost")


def test_route_log_task_flag_wired_in_cli(project: Path) -> None:
    """An advertised flag that isn't wired is worse than none."""
    add_task(project, "a", title="A", bucket="low")
    add_task(project, "b", title="B", bucket="low")
    complete_task(project, "a", evidence="did A", freehand="n/a")
    complete_task(project, "b", evidence="did B", freehand="n/a")

    r = _run(project, "route", "log", "--task", "a")
    assert r.returncode == 0
    ids = {e["task"] for e in json.loads(r.stdout)["data"]["events"]}
    assert ids == {"a"}


# --- #8: unknown get ------------------------------------------------------


def test_unknown_get_is_an_alias_for_show(project: Path) -> None:
    """`known get` exists, so agents reach for `unknown get` and got nothing;
    `unknown status` looks like the reader but is a SETTER."""
    _run(project, "unknown", "create", "u", "--type", "number",
         "--quantity", "q", "--claim", "c?", "--evidence", "e")

    got = _run(project, "unknown", "get", "u", "--json")
    shown = _run(project, "unknown", "show", "u", "--json")

    assert got.returncode == 0, got.stderr[:300]
    assert shown.returncode == 0
    assert json.loads(got.stdout) == json.loads(shown.stdout)


# --- #3: env reads are disclosed in the run record ------------------------


def _write_env_reading_probe(root: Path, probe_id: str) -> None:
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "import os\n"
        "from pathlib import Path\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    pin = os.environ.get('COMBAT_KG_OVERRIDE')\n"
        "    secret = os.environ.get('MY_API_TOKEN')\n"
        "    p = Path(__file__).parent / 'm.txt'\n"
        "    p.write_text(str(pin))\n"
        "    return {\n"
        "        'to': to,\n"
        "        'status': 'ok',\n"
        "        'artifacts': [{'path': str(p), 'role': 'out'}],\n"
        "        'measures': [{'quantity': 'mass', 'value': float(pin or 0)}],\n"
        "    }\n",
        encoding="utf-8",
    )


def test_run_record_discloses_env_overrides(project: Path, monkeypatch) -> None:
    """A hand-pinned run must be distinguishable from a bare one.

    Declared bindings only cover known:/assumption:. A probe reading
    os.environ consumed an input the ledger never mentioned, so a forced run
    could graduate a belief and look identical to an honest one.
    """
    init_probe(project, "p", purpose="p")
    _write_env_reading_probe(project, "p")

    monkeypatch.setenv("COMBAT_KG_OVERRIDE", "10437.6")
    monkeypatch.setenv("MY_API_TOKEN", "hunter2")
    stamp = run_probe(project, "p", to={"kind": "t", "id": "1"})

    env = stamp["env_reads"]
    assert "COMBAT_KG_OVERRIDE" in env["read"], "the override must be visible"
    assert env["read"]["COMBAT_KG_OVERRIDE"] == "10437.6"
    assert env["complete"] is False, "must not overclaim subprocess coverage"


def test_env_read_values_are_redacted_for_secrets(project: Path, monkeypatch) -> None:
    init_probe(project, "p", purpose="p")
    _write_env_reading_probe(project, "p")
    monkeypatch.setenv("COMBAT_KG_OVERRIDE", "1")
    monkeypatch.setenv("MY_API_TOKEN", "hunter2")

    env = run_probe(project, "p", to={"kind": "t", "id": "1"})["env_reads"]
    assert env["read"]["MY_API_TOKEN"] == "<redacted>"
    assert "hunter2" not in json.dumps(env)


def test_bare_run_records_no_override(project: Path, monkeypatch) -> None:
    """The discriminator only works if a clean run stays clean."""
    init_probe(project, "p", purpose="p")
    _write_env_reading_probe(project, "p")
    monkeypatch.delenv("COMBAT_KG_OVERRIDE", raising=False)
    monkeypatch.delenv("MY_API_TOKEN", raising=False)

    env = run_probe(project, "p", to={"kind": "t", "id": "1"})["env_reads"]
    assert env["read"].get("COMBAT_KG_OVERRIDE") is None
    assert env["count"] <= 2


def test_os_environ_is_restored_after_a_run(project: Path) -> None:
    """A recording proxy that leaks would corrupt every later command."""
    init_probe(project, "p", purpose="p")
    _write_env_reading_probe(project, "p")
    before = type(os.environ)
    run_probe(project, "p", to={"kind": "t", "id": "1"})
    assert type(os.environ) is before
    assert os.environ.get("PATH"), "real environment must still work"


# --- run-level read provenance (the orphaned-run screening enabler) -------


def _write_ssot_reading_probe(root: Path, probe_id: str, known_id: str) -> None:
    """A probe that reads SSOT from inside its own code — the common shape."""
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "from pathlib import Path\n"
        f"KID = {known_id!r}\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    from terra.readings import read_known\n"
        "    import os\n"
        "    r = read_known(Path(os.getcwd()), KID)\n"
        "    v = r.get('value') or r.get('mean') or 0\n"
        "    p = Path(__file__).parent / 'm.txt'\n"
        "    p.write_text(str(v))\n"
        "    return {\n"
        "        'to': to,\n"
        "        'status': 'ok',\n"
        "        'artifacts': [{'path': str(p), 'role': 'out'}],\n"
        "        'measures': [{'quantity': 'derived', 'value': float(v) * 2}],\n"
        "    }\n",
        encoding="utf-8",
    )


def _make_known(root: Path, kid: str, quantity: str, value: float) -> None:
    from terra.knowns import graduate_unknown
    from terra.unknowns import create_unknown, link_run
    from test_formula_type import _write_measure_probe

    init_probe(root, f"src_{kid}", purpose="p")
    _write_measure_probe(root, f"src_{kid}", quantity=quantity, value=value)
    rid = run_probe(root, f"src_{kid}", to={"kind": "t", "id": "1"})["id"]
    create_unknown(
        root, kid, map_type="number", quantity=quantity,
        claim="c?", evidence_needed="e",
    )
    link_run(root, kid, rid)
    graduate_unknown(root, kid)


def test_run_records_which_knowns_it_was_computed_against(
    project: Path, monkeypatch
) -> None:
    """Screening 6,000 orphaned runs by DoR basis was only possible by
    date-bucketing, because the record never said what the run consumed."""
    monkeypatch.chdir(project)
    _make_known(project, "mtow_basis", "mtow", 11986.5)
    init_probe(project, "consumer_probe", purpose="p")
    _write_ssot_reading_probe(project, "consumer_probe", "mtow_basis")

    stamp = run_probe(project, "consumer_probe", to={"kind": "t", "id": "1"})

    reads = stamp["known_reads"]
    assert reads, "a probe that read SSOT must disclose it"
    row = next(r for r in reads if r["known_id"] == "mtow_basis")
    assert row["value"] == 11986.5, "the BASIS value, not just the id"
    assert row["as_of"], "must carry the belief's as-of stamp for screening"


def test_run_with_no_ssot_reads_records_none(project: Path, monkeypatch) -> None:
    """The discriminator only works if a non-consuming run stays empty."""
    monkeypatch.chdir(project)
    from test_formula_type import _write_measure_probe

    init_probe(project, "standalone", purpose="p")
    _write_measure_probe(project, "standalone", quantity="q", value=1)
    stamp = run_probe(project, "standalone", to={"kind": "t", "id": "1"})
    assert stamp["known_reads"] == []


def test_read_sink_does_not_leak_between_runs(project: Path, monkeypatch) -> None:
    monkeypatch.chdir(project)
    _make_known(project, "mtow_basis", "mtow", 11986.5)
    init_probe(project, "consumer_probe", purpose="p")
    _write_ssot_reading_probe(project, "consumer_probe", "mtow_basis")
    from test_formula_type import _write_measure_probe

    init_probe(project, "standalone", purpose="p")
    _write_measure_probe(project, "standalone", quantity="q", value=1)

    run_probe(project, "consumer_probe", to={"kind": "t", "id": "1"})
    after = run_probe(project, "standalone", to={"kind": "t", "id": "2"})
    assert after["known_reads"] == [], "sink must not carry over"


def test_known_get_reports_deps_not_silent_none(project: Path, monkeypatch) -> None:
    """`known get` omitted deps entirely, so every belief read as unwired.

    A wiring audit driven off this path would have reported 100% of beliefs
    blind. Silent omission of the field being audited is a lying instrument.
    """
    monkeypatch.chdir(project)
    _make_known(project, "wired_known", "q", 1.0)
    dep_file = project / "some_generator.py"
    dep_file.write_text("# generator\n", encoding="utf-8")

    from terra.knowns import add_dependency
    from terra.readings import read_known

    add_dependency(project, "wired_known", ["file:some_generator.py"])

    reading = read_known(project, "wired_known")
    assert reading.get("deps"), "deps must survive into the consumption view"
    files = (reading["deps"].get("files") or [])
    assert any(f.get("path") == "some_generator.py" for f in files)


def test_known_get_deps_is_empty_dict_not_none_when_unwired(
    project: Path, monkeypatch
) -> None:
    """The discriminator: unwired must be distinguishable from omitted."""
    monkeypatch.chdir(project)
    _make_known(project, "bare_known", "q", 1.0)
    from terra.readings import read_known

    assert read_known(project, "bare_known")["deps"] == {}


# --- route cancel: dispose dead-premise work honestly ---------------------


def test_cancel_retires_without_asserting_an_outcome(project: Path) -> None:
    """`cancelled` was in TASK_STATUSES but had no verb, so dead-premise
    routes could only be left blocked (reads as pending) or completed
    (asserts an outcome that never happened)."""
    from terra.route import cancel_task, route_status

    add_task(project, "dead", title="Dead premise", bucket="low")
    t = cancel_task(project, "dead", reason="targets a retired canon mesh")

    assert t["status"] == "cancelled"
    assert t["cancelled_reason"] == "targets a retired canon mesh"
    assert t["pickable"] is False
    assert "blocked_reason" not in t

    counts = route_status(project)["counts"]["by_status"]
    assert counts.get("cancelled") == 1
    assert counts.get("done", 0) == 0, "cancelled must NOT read as done"


def test_cancel_requires_a_reason(project: Path) -> None:
    from terra.route import cancel_task

    add_task(project, "dead", title="D", bucket="low")
    with pytest.raises(ValueError, match="reason required"):
        cancel_task(project, "dead", reason="   ")


def test_cannot_cancel_a_completed_task(project: Path) -> None:
    """Cancelling a done task would erase its evidence."""
    from terra.route import cancel_task

    add_task(project, "finished", title="F", bucket="low")
    complete_task(project, "finished", evidence="did it", freehand="n/a")
    # message generalised when the terminal-state guard was made systematic
    # (route.py _refuse_if_terminal); the REFUSAL is what matters here
    with pytest.raises(ValueError, match="terminal state"):
        cancel_task(project, "finished", reason="oops")


def test_cancel_clears_blocked_debt(project: Path) -> None:
    from terra.route import block_task, cancel_task, route_status

    add_task(project, "dead", title="D", bucket="low")
    block_task(project, "dead", reason="dead premise")
    assert route_status(project)["counts"]["blocked"] == 1
    cancel_task(project, "dead", reason="wrong object entirely")
    assert route_status(project)["counts"]["blocked"] == 0


# --- provenance must never destroy a completed measurement ----------------


def test_bytes_env_key_does_not_kill_the_run(project: Path, monkeypatch) -> None:
    """`os.environ.get(b'PATH')` is legal. A bytes key slipped past the
    str-keyed filter and later exploded `sorted()` comparing str<bytes — in
    `summarize()`, which runs AFTER run() returns. Full compute paid, nothing
    stamped, reads as "never ran". Cost a 12-flight sim run (2026-07-28).
    """
    from terra.env_reads import record_env_reads, summarize

    sink: dict = {}
    with record_env_reads(sink):
        os.environ.get(b"PATH")       # boring → must be filtered, not stored raw
        os.environ.get(b"MY_PIN")     # real read via bytes key
        os.environ.get("STR_PIN")

    assert all(isinstance(k, str) for k in sink), "keys must be normalized"
    assert "PATH" not in sink, "bytes PATH must still hit the boring filter"
    assert "MY_PIN" in sink and "STR_PIN" in sink
    out = summarize(sink)
    assert isinstance(out["read"], dict)


def test_summarize_never_raises_even_on_garbage(project: Path) -> None:
    """Provenance is never worth a measurement."""
    from terra.env_reads import summarize

    class Unsortable:
        def __str__(self):
            raise RuntimeError("boom")

    out = summarize({Unsortable(): "x"})
    assert "degraded" in out["note"] or out["read"] == {}


def test_probe_run_survives_a_broken_provenance_block(
    project: Path, monkeypatch
) -> None:
    """The durable invariant: no provenance failure, present or future, may
    destroy a completed run."""
    monkeypatch.chdir(project)
    from test_formula_type import _write_measure_probe

    init_probe(project, "p", purpose="p")
    _write_measure_probe(project, "p", quantity="q", value=1)

    monkeypatch.setattr(
        "terra.env_reads.summarize",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("provenance boom")),
    )
    stamp = run_probe(project, "p", to={"kind": "t", "id": "1"})

    assert stamp["status"] == "ok", "the measurement must survive"
    assert stamp["measures"], "measures must still be stamped"
    assert "degraded" in stamp["env_reads"], "and the failure must be VISIBLE"


# --- every JSON-default read verb must accept --json ----------------------


_VOLATILE_KEYS = frozenset({
    "at", "as_of", "updated_at", "created_at", "generated_at", "started_at",
    "last_heartbeat_at", "hours_since_heartbeat", "closed_at", "cancelled_at",
    "validated_at", "linked_at", "elapsed_s", "duration_s",
})


def _stable(obj):
    """Recursively drop clock-derived fields so two invocations compare."""
    if isinstance(obj, dict):
        return {
            k: _stable(v) for k, v in obj.items() if k not in _VOLATILE_KEYS
        }
    if isinstance(obj, list):
        return [_stable(v) for v in obj]
    return obj


@pytest.mark.parametrize(
    "argv",
    [
        ("route", "status"),
        ("route", "budget"),
        ("route", "next"),
        ("route", "log"),
        ("gate"),
        ("brief", "show"),
        ("map", "status"),
        ("sitrep"),
    ],
)
def test_json_flag_accepted_on_every_read_verb(project: Path, argv) -> None:
    """Refusing --json on a JSON-default verb makes argparse exit 2 with an
    EMPTY stdout, which reads downstream as "corrupt JSON output" rather than
    "unrecognized flag". Found on sitrep, then on FIVE sibling verbs.
    """
    argv = (argv,) if isinstance(argv, str) else argv
    add_task(project, "a", title="A", bucket="low")

    plain = _run(project, *argv)
    flagged = _run(project, *argv, "--json")

    assert flagged.returncode != 2, (
        f"terra {' '.join(argv)} --json exited 2: {flagged.stderr[:200]}"
    )
    assert flagged.stdout, "must not produce EMPTY stdout"
    # gate exits 1 as a verdict; compare payloads, not exit codes.
    # These are TWO separate invocations at different wall-clock times, so
    # timestamps and age-derived fields legitimately differ — comparing raw
    # payloads made this test flaky (it failed whenever the pair straddled a
    # second boundary, on a rotating cast of argv ids). A flaky test is its
    # own lying instrument: it trains everyone to discount real failures.
    # Strip volatile fields; the claim under test is that --json is an
    # accepted no-op, not that the clock stood still.
    assert _stable(json.loads(flagged.stdout)) == _stable(json.loads(plain.stdout))


# --- subject binding must travel as an ARGUMENT, not env ------------------


def _write_spec_reading_probe(root: Path, probe_id: str) -> None:
    """Probe that selects its subject from ctx['spec'] — the real shape."""
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "from pathlib import Path\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    spec = ctx.get('spec') or {}\n"
        "    parts = spec.get('parts') or ['DEFAULT']\n"
        "    is_default = 1.0 if parts == ['DEFAULT'] else 0.0\n"
        "    p = Path(__file__).parent / 'm.txt'\n"
        "    p.write_text(str(parts))\n"
        "    return {\n"
        "        'to': to, 'status': 'ok',\n"
        "        'artifacts': [{'path': str(p), 'role': 'out'}],\n"
        "        'measures': [{'quantity': 'subject_is_default_list',\n"
        "                      'value': is_default}],\n"
        "    }\n",
        encoding="utf-8",
    )


def test_spec_file_selects_the_subject(project: Path, monkeypatch) -> None:
    """Env-based overrides die at a fresh-shell boundary; an argument does not.

    A dropped override let a probe measure the DEFAULT body and still report
    status=ok — that is how a gate certified canon while claiming to test a
    different artifact (2026-07-28).
    """
    monkeypatch.chdir(project)
    init_probe(project, "subj", purpose="p")
    _write_spec_reading_probe(project, "subj")

    spec = project / "spec.json"
    spec.write_text(json.dumps({"parts": ["vfin"]}), encoding="utf-8")

    bare = _run(project, "probe", "run", "subj", "--to", "kind=t", "--json")
    assert bare.returncode == 0
    with_spec = _run(
        project, "probe", "run", "subj", "--to", "kind=t",
        "--spec-file", str(spec), "--json",
    )
    assert with_spec.returncode == 0, with_spec.stderr[:300]

    def measure(out):
        # `probe run --json` emits the raw stamp at top level (no envelope).
        d = json.loads(out)
        d = d.get("data", d)
        return {m["quantity"]: m["value"] for m in d["measures"]}

    assert measure(bare.stdout)["subject_is_default_list"] == 1.0
    assert measure(with_spec.stdout)["subject_is_default_list"] == 0.0


def test_assert_measure_turns_a_silent_miss_into_a_hard_error(
    project: Path, monkeypatch
) -> None:
    """A binding that can be silently ignored is not a binding."""
    monkeypatch.chdir(project)
    init_probe(project, "subj", purpose="p")
    _write_spec_reading_probe(project, "subj")

    # No spec passed → probe measures the DEFAULT subject.
    r = _run(
        project, "probe", "run", "subj", "--to", "kind=t", "--json",
        "--assert-measure", "subject_is_default_list=0",
    )
    assert r.returncode == 1, "a dropped subject must be a HARD error"
    assert "assert" in r.stderr.lower()
    payload = json.loads(r.stdout)
    assert payload["status"] == "error"
    # the run must still be stamped for audit even though the command failed
    assert "run stamped as" in payload["error"]["message"]


def test_assert_measure_passes_when_satisfied(project: Path, monkeypatch) -> None:
    """The discriminator: a satisfied assertion must not fail the run."""
    monkeypatch.chdir(project)
    init_probe(project, "subj", purpose="p")
    _write_spec_reading_probe(project, "subj")
    spec = project / "spec.json"
    spec.write_text(json.dumps({"parts": ["vfin"]}), encoding="utf-8")

    r = _run(
        project, "probe", "run", "subj", "--to", "kind=t",
        "--spec-file", str(spec), "--json",
        "--assert-measure", "subject_is_default_list=0",
    )
    assert r.returncode == 0, r.stderr[:300]


def test_assert_measure_catches_a_never_emitted_measure(
    project: Path, monkeypatch
) -> None:
    """`tetgen_meshability` had NO subject measure at all — ungraded the whole
    time. Asserting a measure the probe never emits must fail, not pass."""
    monkeypatch.chdir(project)
    init_probe(project, "subj", purpose="p")
    _write_spec_reading_probe(project, "subj")

    r = _run(
        project, "probe", "run", "subj", "--to", "kind=t",
        "--json", "--assert-measure", "a_measure_this_probe_never_emits",
    )
    assert r.returncode == 1
    assert "NOT EMITTED" in r.stdout


# --- a link that adds no sample must SAY SO -------------------------------


def test_link_run_REFUSES_when_it_adds_no_sample(project: Path) -> None:
    """Escalated from a stderr NOTE to a refusal on 2026-08-08.

    The note shipped 2026-07-28 and the SAME defect silently voided evidence
    at least three more times in a single day — each link "succeeded", n
    stayed 0, and a real finding could not graduate despite good evidence
    behind it. A warning missed that often is decoration.
    """
    from terra.unknowns import LinkAddedNoSample, create_unknown, link_run, load_unknown
    from test_formula_type import _write_measure_probe

    init_probe(project, "emitter", purpose="p")
    _write_measure_probe(project, "emitter", quantity="s_wing", value=38.0)
    rid = run_probe(project, "emitter", to={"kind": "t", "id": "1"})["id"]

    # unknown declares a DIFFERENT quantity than the probe emits
    create_unknown(
        project, "p_req", map_type="number", quantity="p_req",
        claim="P?", evidence_needed="e",
    )
    with pytest.raises(LinkAddedNoSample) as exc:
        link_run(project, "p_req", rid)
    msg = str(exc.value)
    assert "NAME MISMATCH" in msg          # names what actually went wrong
    assert "'p_req'" in msg                # what was declared
    assert "'s_wing'" in msg               # what was actually emitted
    assert "--allow-no-sample" in msg      # the way forward

    # the refusal must leave the record untouched, not half-linked
    assert (load_unknown(project, "p_req").get("run_ids") or []) == []

    # ...and the deliberate override still works
    rec = link_run(project, "p_req", rid, allow_no_sample=True)
    assert rid in rec["run_ids"]


def test_link_run_still_succeeds_when_it_DOES_add_a_sample(project: Path) -> None:
    """CAN-FAIL: the refusal must not fire on a correct link."""
    from terra.unknowns import create_unknown, link_run
    from test_formula_type import _write_measure_probe

    init_probe(project, "emitter2", purpose="p")
    _write_measure_probe(project, "emitter2", quantity="s_wing", value=38.0)
    rid = run_probe(project, "emitter2", to={"kind": "t", "id": "1"})["id"]
    create_unknown(
        project, "s_wing", map_type="number", quantity="s_wing",
        claim="S?", evidence_needed="e",
    )
    rec = link_run(project, "s_wing", rid)
    assert (rec["stats"] or {}).get("n") == 1


def test_link_run_is_quiet_when_the_sample_lands(project: Path, monkeypatch, capsys):
    """The discriminator: a good link must not emit a spurious warning."""
    monkeypatch.chdir(project)
    from terra.unknowns import create_unknown, link_run
    from test_formula_type import _write_measure_probe

    init_probe(project, "p", purpose="p")
    _write_measure_probe(project, "p", quantity="q", value=1.0)
    rid = run_probe(project, "p", to={"kind": "t", "id": "1"})["id"]
    create_unknown(
        project, "u", claim="c?", evidence_needed="e",
        map_type="number", quantity="q",
    )
    rec = link_run(project, "u", rid)

    assert (rec.get("stats") or {}).get("n", 0) == 1
    assert "added NO sample" not in capsys.readouterr().err
