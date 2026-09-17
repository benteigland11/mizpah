"""Coupled solves: convergence stamps + cohorts (knowns valid only as a set)."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.cohorts import (
    add_member,
    check_cohort,
    cohort_violations,
    create_cohort,
    delete_cohort,
    find_cohort_for,
    link_run_cohort,
    load_cohort,
    set_cohort,
)
from terra.gate import check_gate
from terra.knowns import graduate_unknown, link_run_known, load_known
from terra.map_status import collect_status_board
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.readings import read_known
from terra.unknowns import create_unknown, link_run


def _write_solver_probe(
    root: Path,
    probe_id: str,
    *,
    values: dict[str, float],
    converged: bool = True,
) -> None:
    """A sizing-loop style probe: runs to settle, emits coupled quantities."""
    pdir = root / ".terra" / "map" / "probes" / probe_id
    measures = [
        {"quantity": q, "value": v} for q, v in sorted(values.items())
    ]
    (pdir / "probe.py").write_text(
        "from pathlib import Path\n"
        f"MEASURES = {measures!r}\n"
        f"CONVERGED = {converged!r}\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    p = Path(__file__).parent / 'residuals.txt'\n"
        "    p.write_text('0.1 0.01 0.0001')\n"
        "    return {\n"
        "        'to': to,\n"
        "        'status': 'ok' if CONVERGED else 'failed',\n"
        "        'artifacts': [{'path': str(p), 'role': 'residual_history'}],\n"
        "        'measures': MEASURES if CONVERGED else [],\n"
        "        'convergence': {\n"
        "            'converged': CONVERGED,\n"
        "            'iterations': 12,\n"
        "            'residual': 0.0001 if CONVERGED else 0.9,\n"
        "            'tol': 0.001,\n"
        "            'criterion': 'max|dx|/x < tol',\n"
        "        },\n"
        "    }\n",
        encoding="utf-8",
    )


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_probe(tmp_path, "sizing", purpose="run sizing loop to settle")
    _write_solver_probe(tmp_path, "sizing", values={"we": 900.0, "s_wing": 12.0})
    return tmp_path


def _solve(proj: Path, *, start: str = "a") -> str:
    return run_probe(proj, "sizing", to={"kind": "solve", "start": start})["id"]


def _birth_pair(proj: Path, rid: str) -> None:
    for uid, q in (("we", "we"), ("s_wing", "s_wing")):
        create_unknown(
            proj, uid, claim=f"{q}?", map_type="number", quantity=q
        )
        link_run(proj, uid, rid)
        graduate_unknown(proj, uid, cohort_id="sizing_set")


def test_convergence_stamped_on_run(proj):
    stamp = run_probe(proj, "sizing", to={"kind": "solve", "start": "a"})
    conv = stamp["convergence"]
    assert conv["converged"] is True
    assert conv["iterations"] == 12
    assert conv["criterion"]


def test_unconverged_run_warns_and_cannot_link(proj):
    init_probe(proj, "bad", purpose="diverging solve")
    _write_solver_probe(proj, "bad", values={}, converged=False)
    stamp = run_probe(proj, "bad", to={"kind": "solve"})
    assert any("did NOT converge" in w for w in stamp["warnings"])
    create_unknown(proj, "u", claim="q?", map_type="number", quantity="we")
    with pytest.raises(ValueError, match="did not converge"):
        link_run(proj, "u", stamp["id"])
    # known path guarded too
    rid = _solve(proj)
    link_run(proj, "u", rid)
    graduate_unknown(proj, "u")
    with pytest.raises(ValueError, match="did not converge"):
        link_run_known(proj, "u", stamp["id"])


def test_graduate_cohort_and_consistency(proj):
    rid = _solve(proj)
    _birth_pair(proj, rid)
    cohort = load_cohort(proj, "sizing_set")
    assert cohort["members"] == ["we", "s_wing"]
    assert find_cohort_for(proj, "s_wing")["id"] == "sizing_set"
    chk = check_cohort(proj, "sizing_set")
    assert chk["consistent"] is True
    assert chk["common_runs"] == [rid]
    assert cohort_violations(proj) == []


def test_mixed_cohort_blocks_everywhere(proj):
    rid = _solve(proj)
    _birth_pair(proj, rid)
    # a second solve linked to only ONE member → mixed set
    rid2 = _solve(proj, start="b")
    link_run_known(proj, "we", rid2)

    chk = check_cohort(proj, "sizing_set")
    assert chk["consistent"] is False
    assert any("s_wing" in p for p in chk["problems"])

    gate = check_gate(proj)
    kinds = {v["kind"] for v in gate["violations"]}
    assert "cohort_inconsistent" in kinds

    board = collect_status_board(proj, all_maps=True)
    att = {a["kind"] for a in board.get("attention") or []}
    assert "cohort_inconsistent" in att

    with pytest.raises(ValueError, match="NOT backed by the same solve"):
        read_known(proj, "we")
    reading = read_known(proj, "we", allow_cohort_mismatch=True)
    assert reading["cohort"]["consistent"] is False

    # one fan-out restores the whole family
    out = link_run_cohort(proj, "sizing_set", rid2)
    assert set(out["linked"]) == {"we", "s_wing"}
    assert out["check"]["consistent"] is True
    assert check_gate(proj)["ok"] or "cohort_inconsistent" not in {
        v["kind"] for v in check_gate(proj)["violations"]
    }
    reading = read_known(proj, "we")
    assert reading["cohort"]["consistent"] is True
    assert set(reading["cohort"]["common_runs"]) == {rid, rid2}


def test_known_has_one_coupling_context(proj):
    rid = _solve(proj)
    _birth_pair(proj, rid)
    with pytest.raises(ValueError, match="one coupling context"):
        create_cohort(proj, "other", members=["we"])


def test_multistart_advances_n_for_all_members(proj):
    rid = _solve(proj)
    _birth_pair(proj, rid)
    rid2 = _solve(proj, start="b")
    link_run_cohort(proj, "sizing_set", rid2)
    for kid in ("we", "s_wing"):
        rec = load_known(proj, kid)
        assert (rec.get("stats") or {}).get("n") == 2


def test_add_member(proj):
    rid = _solve(proj)
    _birth_pair(proj, rid)
    create_unknown(proj, "p_req", claim="P?", map_type="number", quantity="p_req")
    # p_req's quantity is not emitted by this solve. Deliberate: this test
    # exercises cohort RUN-SET consistency, not evidence weight, so the
    # zero-sample link must be stated on purpose.
    link_run(proj, "p_req", rid, allow_no_sample=True)
    graduate_unknown(proj, "p_req")
    add_member(proj, "sizing_set", "p_req")
    assert "p_req" in load_cohort(proj, "sizing_set")["members"]
    # p_req has no measures for its quantity? It shares the same run set,
    # so the cohort stays consistent even though its stats come from the
    # same solve.
    assert check_cohort(proj, "sizing_set")["consistent"] is True


def test_set_cohort_replaces_members_and_title(proj):
    rid = _solve(proj)
    _birth_pair(proj, rid)
    rec = set_cohort(
        proj, "sizing_set", members=["s_wing", "s_wing"], title="Wing only"
    )
    assert rec["members"] == ["s_wing"]
    assert rec["title"] == "Wing only"
    assert find_cohort_for(proj, "we") is None


def test_set_cohort_refuses_empty_or_foreign_member(proj):
    rid = _solve(proj)
    _birth_pair(proj, rid)
    with pytest.raises(ValueError, match="at least one member"):
        set_cohort(proj, "sizing_set", members=[])
    create_unknown(proj, "p_req", claim="P?", map_type="number", quantity="p_req")
    # p_req's quantity is not emitted by this solve. Deliberate: this test
    # exercises cohort RUN-SET consistency, not evidence weight, so the
    # zero-sample link must be stated on purpose.
    link_run(proj, "p_req", rid, allow_no_sample=True)
    graduate_unknown(proj, "p_req")
    create_cohort(proj, "other", members=["p_req"])
    with pytest.raises(ValueError, match="already belongs"):
        set_cohort(proj, "sizing_set", members=["s_wing", "p_req"])


def test_delete_cohort_preserves_knowns(proj):
    rid = _solve(proj)
    _birth_pair(proj, rid)
    path = delete_cohort(proj, "sizing_set")
    assert not path.exists()
    assert load_known(proj, "we")["id"] == "we"
    assert find_cohort_for(proj, "s_wing") is None
