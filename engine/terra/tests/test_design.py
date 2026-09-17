"""Design layer: knowns graduate into the stable design; files link to it."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.design import (
    add_param,
    attach_artifact,
    check_design,
    detach_artifact,
    get_param,
    refresh_param,
    remove_param,
)
from terra.gate import check_gate
from terra.knowns import graduate_unknown, link_run_known, promote_known
from terra.paths import create_session_map, write_active_map
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run


def _write_measure_probe(root: Path, probe_id: str, *, quantity="q", value=1.0):
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
        f"            'measures': [{{'quantity': {quantity!r}, 'value': {value!r}}}]}}\n",
        encoding="utf-8",
    )


def _mk_known(tmp_path: Path, kid: str, *, n: int = 3, value: float = 42.0) -> None:
    pid = f"p_{kid}"
    init_probe(tmp_path, pid, purpose="p")
    _write_measure_probe(tmp_path, pid, value=value)
    rids = [
        run_probe(tmp_path, pid, to={"kind": "region"}).get("id")
        for _ in range(n)
    ]
    create_unknown(
        tmp_path, f"u_{kid}", claim=f"{kid}?", evidence_needed="e",
        map_type="number", quantity="q",
    )
    for r in rids:
        link_run(tmp_path, f"u_{kid}", r)
    graduate_unknown(tmp_path, f"u_{kid}", known_id=kid)
    if n >= 3:
        promote_known(tmp_path, kid, "med")


def test_admission_bar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow", n=1)  # low confidence
    with pytest.raises(ValueError, match="needs >= med"):
        add_param(tmp_path, "mtow")
    with pytest.raises(ValueError, match="not found on the GLOBAL map"):
        add_param(tmp_path, "ghost")


def test_session_knowns_do_not_become_design(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_session_map(tmp_path, "trial", purpose="t", use=True)
    _mk_known(tmp_path, "exp_val", n=3)
    write_active_map(tmp_path, "global")
    with pytest.raises(ValueError, match="GLOBAL map"):
        add_param(tmp_path, "exp_val")


def test_add_get_and_live_link(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow", n=3, value=42.0)
    entry = add_param(tmp_path, "mtow")
    assert entry["value_at_admission"] == 42.0
    r = get_param(tmp_path, "mtow", consumer="tool:sheet.py")
    assert r["value"] == 42.0 and r["param"] == "mtow"
    assert check_design(tmp_path)["ok"] is True
    # duplicate refused without force
    with pytest.raises(FileExistsError, match="exists"):
        add_param(tmp_path, "mtow")


def test_param_goes_red_when_known_moves(tmp_path, monkeypatch):
    import time

    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow", n=3)
    add_param(tmp_path, "mtow")
    time.sleep(1.1)
    rid = run_probe(tmp_path, "p_mtow", to={"kind": "region"}).get("id")
    link_run_known(tmp_path, "mtow", rid)
    result = check_design(tmp_path)
    assert result["ok"] is False
    assert any("moved since admission" in v["why"] for v in result["violations"])
    # gate picks it up
    verdict = check_gate(tmp_path)
    assert any(v["kind"] == "design_param" for v in verdict["violations"])
    # refresh re-pins
    refresh_param(tmp_path, "mtow")
    assert check_design(tmp_path)["ok"] is True


def test_artifact_lifecycle(tmp_path, monkeypatch):
    import time

    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow", n=3)
    add_param(tmp_path, "mtow")
    f = tmp_path / "prints"
    f.mkdir()
    sheet = f / "three_view.pdf"
    sheet.write_text("render v1")
    with pytest.raises(ValueError, match="not design params"):
        attach_artifact(tmp_path, "prints/three_view.pdf", uses=["nope"])
    attach_artifact(tmp_path, "prints/three_view.pdf", uses=["mtow"])
    assert check_design(tmp_path)["ok"] is True

    # unregistered edit
    sheet.write_text("render v2")
    result = check_design(tmp_path)
    assert any("file changed" in v["why"] for v in result["violations"])
    attach_artifact(tmp_path, "prints/three_view.pdf", uses=["mtow"])  # re-stamp
    assert check_design(tmp_path)["ok"] is True

    # param moves after stamp → REGENERATE
    time.sleep(1.1)
    rid = run_probe(tmp_path, "p_mtow", to={"kind": "region"}).get("id")
    link_run_known(tmp_path, "mtow", rid)
    refresh_param(tmp_path, "mtow")
    result = check_design(tmp_path)
    assert any("REGENERATE" in v["why"] for v in result["violations"])
    assert any(v["kind"] == "design_artifact" for v in result["violations"])

    # regenerate + re-attach clears
    sheet.write_text("render v3 from new mtow")
    attach_artifact(tmp_path, "prints/three_view.pdf", uses=["mtow"])
    assert check_design(tmp_path)["ok"] is True

    # missing file
    sheet.unlink()
    assert any(
        "file missing" in v["why"]
        for v in check_design(tmp_path)["violations"]
    )
    detach_artifact(tmp_path, "prints/three_view.pdf")
    assert check_design(tmp_path)["ok"] is True


def test_remove_param_refuses_while_used(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mk_known(tmp_path, "mtow", n=3)
    add_param(tmp_path, "mtow")
    (tmp_path / "a.stl").write_text("x")
    attach_artifact(tmp_path, "a.stl", uses=["mtow"])
    with pytest.raises(ValueError, match="used by artifacts"):
        remove_param(tmp_path, "mtow")
    detach_artifact(tmp_path, "a.stl")
    remove_param(tmp_path, "mtow")
    assert check_design(tmp_path)["counts"]["params"] == 0
