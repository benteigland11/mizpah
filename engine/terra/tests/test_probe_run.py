"""Stamped probe runs — evidence pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.paths import ensure_map_lib, map_lib_root
from terra.probe_init import init_probe
from terra.probe_run import list_runs, run_probe
from terra.probe_validate import validate_probe_dir
from terra.unknowns import create_unknown, load_unknown


def test_run_stamps_time_from(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "env_snap", purpose="env?", kind="watch")
    script = tmp_path / ".terra" / "map" / "probes" / "env_snap" / "probe.py"
    script.write_text(script.read_text().replace(
        '    raise NotImplementedError("TODO: implement measure()")  # scaffold stub',
        '    return {"host_up": bool(ctx.get("to"))}'))
    stamp = run_probe(tmp_path, "env_snap", to={"kind": "host"}, dry_run=False)
    assert stamp["id"]
    assert stamp["probe_id"] == "env_snap"
    assert stamp["time"]["started_at"]
    assert stamp["time"]["finished_at"]
    assert stamp["from"]["probe_id"] == "env_snap"
    assert stamp["from"]["runner"] == "python"
    assert stamp["to"]
    assert stamp["status"]
    assert Path(stamp["_run_dir"]).is_dir()
    assert Path(stamp["_path"]).is_file()
    assert len(stamp.get("artifacts") or []) >= 1


def test_run_rejects_empty_to(tmp_path: Path):
    init_probe(tmp_path, "x", purpose="x")
    with pytest.raises(ValueError, match="input|to"):
        run_probe(tmp_path, "x", to={})


def test_list_runs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    run_probe(tmp_path, "p", to={"k": 1})
    rows = list_runs(tmp_path, probe_id="p")
    assert len(rows) == 1
    assert rows[0]["ok"] is True


def test_create_unknown_with_probe_is_probing(tmp_path: Path):
    create_unknown(
        tmp_path,
        "gap",
        claim="what?",
        evidence_needed="reading",
        probe_id="p1",
    )
    rec = load_unknown(tmp_path, "gap")
    assert rec["status"] == "probing"
    assert rec["probe_id"] == "p1"
    assert "p1" in rec["probe_ids"]


def test_map_lib_import(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ensure_map_lib(tmp_path)
    (map_lib_root(tmp_path) / "helper_mod.py").write_text(
        "VALUE = 42\n", encoding="utf-8"
    )
    pdir = init_probe(tmp_path, "uses_lib", purpose="lib")
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "import helper_mod\n"
        "from pathlib import Path\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'k': 1}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    p = Path(__file__).parent / 'out.txt'\n"
        "    p.write_text(str(helper_mod.VALUE))\n"
        "    return {'to': to, 'status': 'ok', "
        "'artifacts': [{'path': str(p), 'role': 'out'}]}\n",
        encoding="utf-8",
    )
    v = validate_probe_dir(pdir)
    assert v["ok"] is True, v["blocks"]
    stamp = run_probe(tmp_path, "uses_lib", to={"k": 1})
    assert stamp["status"] == "ok"
    assert any(
        str(a.get("path", "")).endswith("out.txt") or a.get("exists")
        for a in stamp["artifacts"]
    )


def test_run_persists_probe_reported_error_text(tmp_path: Path, monkeypatch):
    """E4 (KDP-A) regression: a probe that self-reports status='error' with
    an `error` string must have that LITERAL string survive into the
    stamped run doc via the real stamp_doc construction inside run_probe
    (src/terra/probe_run.py). Before the fix, 390 errored runs across the
    program had `error == null` -- 100% loss of every failure diagnostic --
    because stamp_doc's dict literal never copied `raw.get("error")`
    forward. This exercises the REAL run_probe, not a reimplementation, and
    asserts on the literal message, not `error is not None`.
    """
    monkeypatch.chdir(tmp_path)
    pdir = init_probe(tmp_path, "self_reports_error", purpose="canfail", kind="run")
    nonce_message = "TERRA_TEST_NONCE_e4_regression: distinctive instrument failure text"
    (pdir / "probe.py").write_text(
        "KIND = 'run'\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'k': 1}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {'to': to, 'status': 'error', "
        f"'error': {nonce_message!r}, 'artifacts': []}}\n",
        encoding="utf-8",
    )
    v = validate_probe_dir(pdir)
    assert v["ok"] is True, v["blocks"]

    stamp = run_probe(tmp_path, "self_reports_error", to={"k": 1})

    # literal string, not a truthy check -- a truncated/renamed/class-name
    # error field would still pass `error is not None` and still be a
    # lost diagnostic.
    assert stamp["status"] == "error"
    assert stamp["error"] == nonce_message

    # and it must actually be PERSISTED, not just present on the returned
    # dict -- read the stamped doc back off disk, same as the map would.
    import json

    on_disk = json.loads(Path(stamp["_path"]).read_text(encoding="utf-8"))
    assert on_disk["error"] == nonce_message


def test_scaffold_is_one_stub_and_an_unimplemented_run_is_an_error(tmp_path: Path, monkeypatch):
    """The worker implements measure() and nothing else; run() carries the contract."""
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "mean_probe", purpose="mean?", kind="run")
    script = tmp_path / ".terra" / "map" / "probes" / "mean_probe" / "probe.py"
    source = script.read_text()
    assert source.count('raise NotImplementedError("TODO: implement measure()")') == 1 and "scaffold stub" in source
    stub = run_probe(tmp_path, "mean_probe", to={"kind": "cli"}, dry_run=False)
    assert stub["status"] == "error" and not stub.get("measures")
    script.write_text(source.replace(
        '    raise NotImplementedError("TODO: implement measure()")  # scaffold stub',
        '    values = [3.0, 5.0, 7.0]\n    return {"sample_mean": sum(values) / len(values), "all_positive": bool(ctx)}'))
    stamp = run_probe(tmp_path, "mean_probe", to={"kind": "cli"}, dry_run=False)
    assert stamp["status"] == "ok"
    assert stamp["measures"] == [{"quantity": "sample_mean", "value": 5.0}, {"quantity": "all_positive", "value": True}]
    assert len(stamp["artifacts"]) == 1 and stamp["artifacts"][0]["path"].endswith("_last_reading.json")


def test_measure_py_beside_the_probe_takes_precedence(tmp_path: Path, monkeypatch):
    """The worker writes one new small file instead of editing the scaffold in place."""
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "sib", purpose="sibling?", kind="run")
    pdir = tmp_path / ".terra" / "map" / "probes" / "sib"
    (tmp_path / "data.txt").write_text("2\n4\n")
    (pdir / "measure.py").write_text(
        "from pathlib import Path\n\n\ndef measure(ctx):\n"
        "    values = [float(x) for x in Path('data.txt').read_text().split()]\n"
        "    return {'sample_mean': sum(values) / len(values)}\n")
    stamp = run_probe(tmp_path, "sib", to={"kind": "cli"}, dry_run=False)
    assert stamp["status"] == "ok" and stamp["measures"] == [{"quantity": "sample_mean", "value": 3.0}]
    from terra.probe_validate import validate_probe_dir
    assert validate_probe_dir(pdir)["ok"] is True
    (pdir / "measure.py").write_text("def measure(ctx):\n    return {'sample_mean': 3.0}\n")
    result = validate_probe_dir(pdir)
    assert result["ok"] is False and any("reads nothing" in b for b in result["blocks"])
    (pdir / "measure.py").write_text("def other(ctx):\n    return open('data.txt').read()\n")
    result = validate_probe_dir(pdir)
    assert result["ok"] is False and any("must define measure(ctx)" in b for b in result["blocks"])


def test_declared_measures_bound_what_a_probe_may_report(tmp_path: Path, monkeypatch):
    """One probe, its declared quantities; a run reporting others is refused and nothing is stamped."""
    import pytest
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "one", purpose="one quantity", kind="run", measures=["sample_mean"])
    script = tmp_path / ".terra" / "map" / "probes" / "one" / "probe.py"
    stub = '    raise NotImplementedError("TODO: implement measure()")  # scaffold stub'
    script.write_text(script.read_text().replace(stub, '    return {"sample_mean": 3.0 if ctx else 0, "sample_max": 5.0}'))
    with pytest.raises(ValueError, match="extra: \\['sample_max'\\]"):
        run_probe(tmp_path, "one", to={"kind": "cli"}, dry_run=False)
    assert not list((tmp_path / ".terra" / "map" / "runs").glob("*_one_*"))
    script.write_text(script.read_text().replace('    return {"sample_mean": 3.0 if ctx else 0, "sample_max": 5.0}',
                                                 '    return {"sample_mean": 3.0 if ctx else 0}'))
    stamp = run_probe(tmp_path, "one", to={"kind": "cli"}, dry_run=False)
    assert stamp["measures"] == [{"quantity": "sample_mean", "value": 3.0}]
    meta = __import__("json").loads((tmp_path / ".terra" / "map" / "probes" / "one" / "probe.json").read_text())
    assert meta["measures"] == ["sample_mean"]
