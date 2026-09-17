"""Global vs session map scopes."""

from __future__ import annotations

from pathlib import Path

from terra.knowns import create_known, list_knowns
from terra.paths import (
    GLOBAL_MAP_ID,
    create_session_map,
    get_active_map_id,
    knowns_root,
    map_root,
    probes_root,
    set_active_map_id,
    write_active_map,
)
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, list_unknowns


def test_session_isolates_knowns(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_active_map_id(GLOBAL_MAP_ID)
    init_probe(tmp_path, "p", purpose="p")  # global probes

    create_session_map(tmp_path, "exp_a", purpose="A", use=True)
    assert get_active_map_id(tmp_path) == "exp_a"
    create_unknown(tmp_path, "u_a", claim="a?", evidence_needed="e")
    assert len(list_unknowns(tmp_path)) == 1

    write_active_map(tmp_path, GLOBAL_MAP_ID)
    assert get_active_map_id(tmp_path) == GLOBAL_MAP_ID
    assert list_unknowns(tmp_path) == []  # global empty

    write_active_map(tmp_path, "exp_a")
    assert len(list_unknowns(tmp_path)) == 1

    # probes still global
    assert (probes_root(tmp_path) / "p").is_dir()
    assert "sessions" not in str(probes_root(tmp_path))


def test_runs_land_on_active_map(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "p", purpose="p")
    create_session_map(tmp_path, "exp", use=True)
    from pathlib import Path as P

    pdir = tmp_path / ".terra" / "map" / "probes" / "p"
    (pdir / "probe.py").write_text(
        "KIND='watch'\nDURATION_S=0\n"
        "REQUIRED_EXPORTS=['to','status','artifacts']\n"
        "from pathlib import Path\n"
        "def run(ctx=None):\n"
        "    ctx=ctx or {}\n"
        "    to=ctx.get('to') or {'kind':'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to':to,'status':'ok','artifacts':[]}\n"
        "    f=Path(__file__).parent/'o.txt'; f.write_text('1')\n"
        "    return {'to':to,'status':'ok','artifacts':[{'path':str(f),'role':'out'}],"
        "'measures':[{'quantity':'q','value':1}]}\n",
        encoding="utf-8",
    )
    stamp = run_probe(tmp_path, "p", to={"kind": "region"})
    assert stamp.get("map_id") == "exp"
    assert "sessions/exp" in stamp["_run_dir"].replace("\\", "/")


def test_cli_map_override_context(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_active_map_id(GLOBAL_MAP_ID)
    create_session_map(tmp_path, "exp", use=False)
    set_active_map_id("exp")
    assert "sessions/exp" in str(map_root(tmp_path)).replace("\\", "/")
    set_active_map_id(GLOBAL_MAP_ID)
    assert "sessions" not in str(map_root(tmp_path))


def test_global_and_session_knowns_do_not_mix(tmp_path: Path, monkeypatch):
    """Session experiments must not muddy global durable beliefs."""
    monkeypatch.chdir(tmp_path)
    set_active_map_id(GLOBAL_MAP_ID)
    init_probe(tmp_path, "p", purpose="p")

    r_global = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    create_known(
        tmp_path,
        "k_global",
        claim="stable fact",
        map_type="number",
        quantity="q",
        run_id=r_global,
    )
    assert [k["id"] for k in list_knowns(tmp_path)] == ["k_global"]

    create_session_map(tmp_path, "trial", purpose="messy trial", use=True)
    r_trial = run_probe(tmp_path, "p", to={"kind": "region"}).get("id")
    create_known(
        tmp_path,
        "k_trial",
        claim="experimental",
        map_type="number",
        quantity="q",
        run_id=r_trial,
    )
    assert [k["id"] for k in list_knowns(tmp_path)] == ["k_trial"]
    assert knowns_root(tmp_path).as_posix().endswith("sessions/trial/knowns")

    write_active_map(tmp_path, GLOBAL_MAP_ID)
    ids = [k["id"] for k in list_knowns(tmp_path)]
    assert ids == ["k_global"]
    assert "k_trial" not in ids
