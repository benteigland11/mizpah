"""Map parent chain: read-through, shadowing, and adoption up the tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from terra.cohorts import adopt_cohort, create_cohort
from terra.knowns import (
    adopt_known,
    find_known_map,
    graduate_unknown,
    load_known,
    promote_known,
    shadowed_ancestor,
)
from terra.paths import (
    create_session_map,
    known_path,
    map_chain,
    map_parent,
    run_dir,
    scoped_map,
    set_active_map_id,
)
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.readings import read_known
from terra.unknowns import create_unknown, link_run


@pytest.fixture(autouse=True)
def _reset_active_map():
    set_active_map_id(None)
    yield
    set_active_map_id(None)


def _write_measure_probe(
    root: Path, probe_id: str, *, quantity: str, value: float
) -> None:
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\n"
        "DURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        f"Q = {quantity!r}\n"
        f"V = {value!r}\n"
        "def run(ctx=None):\n"
        "    ctx = ctx or {}\n"
        "    to = ctx.get('to') or {'kind': 'default'}\n"
        "    if ctx.get('dry_run'):\n"
        "        return {'to': to, 'status': 'ok', 'artifacts': []}\n"
        "    return {\n"
        "        'to': to,\n"
        "        'status': 'ok',\n"
        "        'artifacts': [],\n"
        "        'measures': [{'quantity': Q, 'value': V}],\n"
        "    }\n",
        encoding="utf-8",
    )


def _grow_known(
    root: Path,
    kid: str,
    *,
    map_id: str,
    quantity: str = "q",
    value: float = 4,
    samples: int = 3,
    conf: str | None = "med",
) -> None:
    """Graduate a known on map_id with enough samples for med."""
    probe_id = f"p_{map_id}_{kid}"
    init_probe(root, probe_id, purpose="p")
    _write_measure_probe(root, probe_id, quantity=quantity, value=value)
    with scoped_map(map_id):
        rids = [
            run_probe(root, probe_id, to={"kind": "region", "i": i}).get("id")
            for i in range(samples)
        ]
        create_unknown(
            root,
            kid,
            claim=f"how big is {quantity}?",
            evidence_needed="a reading",
            map_type="number",
            quantity=quantity,
        )
        for rid in rids:
            link_run(root, kid, rid)
        graduate_unknown(root, kid)
        if conf:
            promote_known(root, kid, conf)


def test_map_chain_and_parent(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_session_map(tmp_path, "exp")
    create_session_map(tmp_path, "trial", parent="exp")
    assert map_parent(tmp_path, "trial") == "exp"
    assert map_parent(tmp_path, "exp") == "global"
    assert map_parent(tmp_path, "global") is None
    assert map_chain(tmp_path, "trial") == ["trial", "exp", "global"]
    assert map_chain(tmp_path, "global") == ["global"]


def test_parent_must_exist_and_not_self(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="parent map"):
        create_session_map(tmp_path, "kid", parent="ghost")
    with pytest.raises(ValueError, match="own parent"):
        create_session_map(tmp_path, "loop", parent="loop")


def test_read_through_chain(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _grow_known(tmp_path, "mtow", map_id="global")
    create_session_map(tmp_path, "exp")
    create_session_map(tmp_path, "trial", parent="exp")
    with scoped_map("trial"):
        reading = read_known(tmp_path, "mtow")
    assert reading["value"] == 4
    assert reading["map"] == "global"
    assert reading["inherited"] is True
    # consumer edge lands on the OWNING map, not the reader's
    with scoped_map("global"):
        from terra.paths import consumer_path

        assert consumer_path(tmp_path, "mtow").is_file()


def test_child_shadows_ancestor(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _grow_known(tmp_path, "mtow", map_id="global", value=4)
    create_session_map(tmp_path, "exp")
    _grow_known(tmp_path, "mtow", map_id="exp", value=9)
    with scoped_map("exp"):
        reading = read_known(tmp_path, "mtow")
        assert reading["value"] == 9
        assert reading["map"] == "exp"
        assert shadowed_ancestor(tmp_path, "mtow") == "global"
    assert find_known_map(tmp_path, "mtow", "exp") == "exp"
    assert find_known_map(tmp_path, "mtow", "global") == "global"


def test_adopt_copies_runs_and_stamps_provenance(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_session_map(tmp_path, "exp")
    _grow_known(tmp_path, "span", map_id="exp")
    rec = adopt_known(tmp_path, "span", from_map="exp")
    assert rec["adopted_from"]["map"] == "exp"
    assert rec["stats"]["n"] == 3
    with scoped_map("global"):
        assert known_path(tmp_path, "span").is_file()
        for rid in rec["run_ids"]:
            assert run_dir(tmp_path, rid).is_dir()
    with scoped_map("exp"):
        assert load_known(tmp_path, "span")["adopted_to"]["map"] == "global"


def test_adopt_refuses_low_confidence(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_session_map(tmp_path, "exp")
    _grow_known(tmp_path, "weak", map_id="exp", samples=1, conf=None)
    with pytest.raises(ValueError, match="adoption needs"):
        adopt_known(tmp_path, "weak", from_map="exp")


def test_adopt_refuses_from_global(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _grow_known(tmp_path, "mtow", map_id="global")
    with pytest.raises(ValueError, match="no parent"):
        adopt_known(tmp_path, "mtow", from_map="global")


def test_adopt_refuses_destination_collision(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _grow_known(tmp_path, "mtow", map_id="global", value=4)
    create_session_map(tmp_path, "exp")
    _grow_known(tmp_path, "mtow", map_id="exp", value=9)
    with pytest.raises(FileExistsError, match="already exists on 'global'"):
        adopt_known(tmp_path, "mtow", from_map="exp")


def test_adopt_requires_deps_at_destination(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_session_map(tmp_path, "exp")
    _grow_known(tmp_path, "base", map_id="exp", quantity="b")
    _grow_known(tmp_path, "derived", map_id="exp", quantity="d")
    from terra.knowns import add_dependency

    with scoped_map("exp"):
        add_dependency(tmp_path, "derived", ["known:base"])
    with pytest.raises(ValueError, match="deps not resolvable"):
        adopt_known(tmp_path, "derived", from_map="exp")
    # bottom-up works
    adopt_known(tmp_path, "base", from_map="exp")
    rec = adopt_known(tmp_path, "derived", from_map="exp")
    assert rec["adopted_from"]["map"] == "exp"


def test_cohort_member_refuses_solo_adopt(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_session_map(tmp_path, "exp")
    _grow_known(tmp_path, "cg_x", map_id="exp", quantity="x")
    with scoped_map("exp"):
        create_cohort(tmp_path, "balance", members=["cg_x"])
    with pytest.raises(ValueError, match="cohort adopt"):
        adopt_known(tmp_path, "cg_x", from_map="exp")


def test_cohort_adopt_moves_the_set(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_session_map(tmp_path, "exp")
    # one solve backs both members: same runs on each
    probe_id = "solver"
    init_probe(tmp_path, probe_id, purpose="p")
    _write_measure_probe(tmp_path, probe_id, quantity="x", value=1)
    with scoped_map("exp"):
        rids = [
            run_probe(tmp_path, probe_id, to={"kind": "region", "i": i}).get("id")
            for i in range(3)
        ]
        for kid in ("cg_x", "cg_y"):
            create_unknown(
                tmp_path,
                kid,
                claim=f"{kid}?",
                evidence_needed="solve",
                map_type="number",
                quantity="x",
            )
            for rid in rids:
                link_run(tmp_path, kid, rid)
            graduate_unknown(tmp_path, kid)
            promote_known(tmp_path, kid, "med")
        create_cohort(tmp_path, "balance", members=["cg_x", "cg_y"])
    rec = adopt_cohort(tmp_path, "balance", from_map="exp")
    assert rec["members"] == ["cg_x", "cg_y"]
    with scoped_map("global"):
        assert known_path(tmp_path, "cg_x").is_file()
        assert known_path(tmp_path, "cg_y").is_file()


def test_stale_cascades_from_ancestor_dep(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _grow_known(tmp_path, "base", map_id="global", quantity="b")
    create_session_map(tmp_path, "exp")
    _grow_known(tmp_path, "derived", map_id="exp", quantity="d")
    from terra.knowns import add_dependency
    from terra.staleness import compute_staleness

    with scoped_map("exp"):
        add_dependency(tmp_path, "derived", ["known:base"])
        assert not compute_staleness(tmp_path)["derived"]["stale"]
    # ancestor moves → child goes stale through the chain
    # (bump updated_at directly: second-resolution timestamps make an
    # in-process reaffirm race the depend stamp)
    with scoped_map("global"):
        p = known_path(tmp_path, "base")
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["updated_at"] = "2999-01-01T00:00:00Z"
        p.write_text(json.dumps(rec), encoding="utf-8")
    with scoped_map("exp"):
        info = compute_staleness(tmp_path)["derived"]
        assert info["stale"]
        assert any("base" in r for r in info["reasons"])


def test_map_list_carries_parent(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from terra.paths import list_maps

    create_session_map(tmp_path, "exp")
    create_session_map(tmp_path, "trial", parent="exp")
    rows = {r["id"]: r for r in list_maps(tmp_path)}
    assert rows["trial"]["parent"] == "exp"
    assert rows["exp"]["parent"] == "global"
    assert "parent" not in rows["global"]
