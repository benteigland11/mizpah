"""known supersede: soft tombstone — keep as history, refuse as current.

Between `known set` (metadata only, can't touch a bug-derived value) and
`known delete` (destructive, dangling refs). A retired belief is refused by
the read path unless --allow-superseded, and surfaces as info in map status.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.knowns import load_known, supersede_known
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.readings import read_known
from terra.unknowns import create_unknown, link_run
from terra.knowns import graduate_unknown


def _write_measure_probe(root: Path, probe_id: str, *, quantity: str, value: float):
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        f"Q = {quantity!r}\nV = {value!r}\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {'to': to, 'status': 'ok', 'artifacts': [],\n"
        "            'measures': [{'quantity': Q, 'value': V}]}\n",
        encoding="utf-8",
    )


def _mk_known(tmp_path: Path, kid: str, *, quantity="q", value=4.0) -> str:
    pid = f"p_{kid}"
    init_probe(tmp_path, pid, purpose="p")
    _write_measure_probe(tmp_path, pid, quantity=quantity, value=value)
    rid = run_probe(tmp_path, pid, to={"kind": "region"}).get("id")
    create_unknown(
        tmp_path, f"u_{kid}", claim=f"{kid}?", evidence_needed="e",
        map_type="number", quantity=quantity,
    )
    link_run(tmp_path, f"u_{kid}", rid)
    graduate_unknown(tmp_path, f"u_{kid}", known_id=kid)
    return rid


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_supersede_stamps_tombstone(proj):
    _mk_known(proj, "sm", value=-5.27)
    rec = supersede_known(proj, "sm", reason="bug-derived sign error")
    assert rec["status"] == "superseded"
    assert rec["superseded"]["reason"] == "bug-derived sign error"
    assert rec["superseded"]["refuted"] is False


def test_refuted_flag_sets_refuted_status(proj):
    _mk_known(proj, "ballast", value=99)
    rec = supersede_known(proj, "ballast", reason="never real", refuted=True)
    assert rec["status"] == "refuted"
    assert rec["superseded"]["refuted"] is True


def test_supersede_requires_reason(proj):
    _mk_known(proj, "sm", value=1)
    with pytest.raises(ValueError, match="requires --reason"):
        supersede_known(proj, "sm", reason="  ")


def test_read_refuses_superseded_by_default(proj):
    _mk_known(proj, "sm", value=-5.27)
    supersede_known(proj, "sm", reason="wrong")
    with pytest.raises(ValueError, match="SUPERSEDED"):
        read_known(proj, "sm")


def test_read_allows_superseded_with_flag_but_flags_it(proj):
    _mk_known(proj, "sm", value=-5.27)
    supersede_known(proj, "sm", reason="wrong")
    r = read_known(proj, "sm", allow_superseded=True)
    assert r["value"] == -5.27  # historical value still readable
    assert r["superseded"] is True
    assert r["superseded_info"]["reason"] == "wrong"


def test_refuted_also_refused(proj):
    _mk_known(proj, "ballast", value=99)
    supersede_known(proj, "ballast", reason="bug", refuted=True)
    with pytest.raises(ValueError, match="REFUTED"):
        read_known(proj, "ballast")


def test_by_must_exist_and_not_self(proj):
    _mk_known(proj, "sm", value=1)
    with pytest.raises(FileNotFoundError):
        supersede_known(proj, "sm", reason="x", superseded_by="ghost")
    with pytest.raises(ValueError, match="cannot supersede itself"):
        supersede_known(proj, "sm", reason="x", superseded_by="sm")


def test_by_points_at_replacement(proj):
    _mk_known(proj, "sm_old", value=-5.27)
    _mk_known(proj, "sm_new", value=5.27)
    rec = supersede_known(
        proj, "sm_old", reason="sign fixed", superseded_by="sm_new"
    )
    assert rec["superseded"]["by"] == "sm_new"
    # the refusal message should point at the replacement
    with pytest.raises(ValueError, match="sm_new"):
        read_known(proj, "sm_old")


def test_supersede_is_not_delete(proj):
    _mk_known(proj, "sm", value=1)
    supersede_known(proj, "sm", reason="wrong")
    # record still on disk (history preserved), just refused as current
    assert load_known(proj, "sm")["status"] == "superseded"


def test_map_status_flags_retired_as_info_not_debt(proj):
    from terra.map_status import agent_status_response, collect_status_board

    _mk_known(proj, "sm", value=1)
    supersede_known(proj, "sm", reason="wrong")
    board = agent_status_response(collect_status_board(proj, all_maps=True))["data"]
    retired = [
        a for a in board.get("attention") or []
        if a.get("kind") == "known_retired" and a.get("id") == "sm"
    ]
    assert len(retired) == 1
    assert retired[0]["severity"] == "info"
    # a retired known must NOT also fire high-severity unbacked/stale noise
    noisy = [
        a for a in board.get("attention") or []
        if a.get("id") == "sm" and a.get("kind") != "known_retired"
    ]
    assert not noisy


def test_gate_excludes_retired_knowns_from_debt(tmp_path: Path, monkeypatch):
    """Retiring a mis-wired gate must CLEAR its violation.

    Otherwise the only way to green the gate is `known delete`, which destroys
    the audit trail — the gate would reward erasing a mistake over recording
    it. Real case 2026-07-27: a tautologically-false flutter formula was
    correctly superseded with a full reason and `terra gate` kept counting it.
    """
    monkeypatch.chdir(tmp_path)
    from terra.gate import check_gate
    from terra.knowns import create_known, link_run_known
    from terra.probe_run import run_probe
    from test_formula_type import _write_measure_probe

    init_probe(tmp_path, "p", purpose="p")
    _write_measure_probe(tmp_path, "p", quantity="hostile_count", value=50)
    run_ids = [
        run_probe(tmp_path, "p", to={"kind": "region", "id": str(i)}).get("id")
        for i in range(5)
    ]
    create_known(
        tmp_path,
        "sparse",
        claim="under 10",
        map_type="formula",
        expression="mean(h) <= 10",
        vars=["h=hostile_count"],
        run_id=run_ids[0],
    )
    for rid in run_ids[1:]:
        link_run_known(tmp_path, "sparse", rid)

    before = check_gate(tmp_path)
    assert any(
        v["kind"] == "known_formula_failed" and v["id"] == "sparse"
        for v in before["violations"]
    ), "precondition: the failing formula must be a violation"

    supersede_known(tmp_path, "sparse", reason="mis-wired gate, tautological")

    after = check_gate(tmp_path)
    assert not any(
        v["id"] == "sparse" for v in after["violations"]
    ), "a retired belief must not remain gate debt"
    assert any(
        n["kind"] == "known_retired" and n["id"] == "sparse"
        for n in after["notices"]
    ), "it must still be VISIBLE as a notice — retired, not vanished"


def test_supersede_warns_about_active_copies_on_other_maps(
    tmp_path: Path, monkeypatch, capsys
):
    """Supersession is a LOCAL write and Terra gave no signal.

    A child map retiring its copy left `global` serving a TAILLESS airframe as
    the certified master OML for days (2026-07-28), and did the same to
    `mtow_final`. The warning must NAME the maps still serving it.
    """
    monkeypatch.chdir(tmp_path)
    from terra.paths import create_session_map, scoped_map
    from terra.knowns import supersede_known

    root = tmp_path
    _mk_known(root, "shared_belief")
    create_session_map(root, "childmap")
    # Probes are GLOBAL, so the child copy needs its own probe/unknown ids but
    # graduates to the SAME known id — which is exactly the real shape.
    init_probe(root, "p_child", purpose="p")
    _write_measure_probe(root, "p_child", quantity="q", value=4.0)
    with scoped_map("childmap"):
        rid = run_probe(root, "p_child", to={"kind": "region"}).get("id")
        create_unknown(
            root, "u_child", claim="c?", evidence_needed="e",
            map_type="number", quantity="q",
        )
        link_run(root, "u_child", rid)
        graduate_unknown(root, "u_child", known_id="shared_belief")
        supersede_known(root, "shared_belief", reason="wrong on this map")

    err = capsys.readouterr().err
    assert "still ACTIVE" in err, f"no sibling warning; stderr={err!r}"
    assert "global" in err, "must NAME the map still serving it"
    assert "does NOT propagate" in err
    assert "terra --map global known supersede" in err, "must give the fix"


def test_no_sibling_warning_when_no_other_copy(tmp_path: Path, monkeypatch, capsys):
    """The discriminator: a lone belief must not emit a spurious warning."""
    monkeypatch.chdir(tmp_path)
    from terra.knowns import supersede_known

    _mk_known(tmp_path, "lonely")
    supersede_known(tmp_path, "lonely", reason="just wrong")
    assert "still ACTIVE" not in capsys.readouterr().err


def test_unretiring_moves_the_tombstone_to_history(proj):
    """A stale `superseded` block on an ACTIVE known is a self-contradicting
    record — it reads as "retired for <reason>" while being served as current.

    Hit for real 2026-07-28: an accidental supersede was reverted with
    `known status … active` and the tombstone stayed put.
    """
    from terra.knowns import set_known_status, supersede_known

    _mk_known(proj, "oops")
    supersede_known(proj, "oops", reason="retired by mistake")
    assert load_known(proj, "oops").get("superseded")

    rec = set_known_status(proj, "oops", "active")

    assert rec["status"] == "active"
    assert "superseded" not in rec, "tombstone must not sit on a live belief"
    hist = rec.get("superseded_history") or []
    assert len(hist) == 1, "audit trail must survive"
    assert hist[0]["reason"] == "retired by mistake"
    assert hist[0]["reverted_at"], "must record WHEN it was un-retired"


def test_status_change_between_live_states_keeps_no_history(proj):
    """The discriminator: an ordinary status edit must not fabricate history."""
    from terra.knowns import set_known_status

    _mk_known(proj, "plain")
    rec = set_known_status(proj, "plain", "provisional")
    assert "superseded_history" not in rec


def test_stale_tombstone_self_heals_on_any_status_write(proj):
    """Records already sitting in the contradictory state must heal too —
    transition-keying missed them, so they stayed broken forever."""
    import json as _json
    from terra.knowns import set_known_status
    from terra.paths import known_path

    _mk_known(proj, "already_broken")
    # Simulate the pre-existing bad state: active known carrying a tombstone.
    p = known_path(proj, "already_broken")
    rec = _json.loads(p.read_text())
    rec["status"] = "active"
    rec["superseded"] = {"at": "2026-07-28T00:00:00Z", "reason": "legacy residue"}
    p.write_text(_json.dumps(rec, indent=2, sort_keys=True) + "\n")

    healed = set_known_status(proj, "already_broken", "active")
    assert "superseded" not in healed, "must heal without a status transition"
    assert (healed.get("superseded_history") or [])[0]["reason"] == "legacy residue"


def test_duplicate_sample_runs_are_reported_not_silently_counted(proj):
    """`n` counts RUNS, not distinct samples — a deterministic re-run doubles
    the evidence count the confidence ladder rests on. 196 knowns on CG-01
    carry duplicates, 146 at med. Report it; do NOT re-rate silently.
    """
    from terra.knowns import link_run_known
    from terra.probe_run import run_probe

    _mk_known(proj, "det", value=7.0)
    # Same probe, same deterministic value → byte-identical reading.
    rid2 = run_probe(proj, "p_det", to={"kind": "region", "id": "again"}).get("id")
    link_run_known(proj, "det", rid2)

    st = load_known(proj, "det")["stats"]
    assert st["n"] == 2, "n still counts runs — behaviour deliberately unchanged"
    assert st["distinct_sample_signatures"] == 1, "only ONE real sample exists"
    assert st["duplicate_sample_runs"] == 1, "the inflation must be visible"


def test_genuinely_distinct_samples_report_zero_duplicates(proj):
    """The discriminator: real repeat measurements must not be flagged."""
    from terra.knowns import link_run_known
    from terra.probe_run import run_probe

    _mk_known(proj, "varied", value=1.0)
    _write_measure_probe(proj, "p_varied", quantity="q", value=2.0)
    rid2 = run_probe(proj, "p_varied", to={"kind": "region", "id": "b"}).get("id")
    link_run_known(proj, "varied", rid2)

    st = load_known(proj, "varied")["stats"]
    assert st["distinct_sample_signatures"] == 2
    assert st["duplicate_sample_runs"] == 0
