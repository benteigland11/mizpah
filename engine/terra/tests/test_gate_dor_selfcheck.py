"""Gate self-check: warn when a gate's bar is stricter than the DoR baseline.

A gate (formula known) that the certified-good design-of-record itself fails
is a bug in the gate, not a wall. check_design emits a non-blocking
`gate_stricter_than_baseline` notice; the gate folds it as a notice, never a
violation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.design import add_param, check_design
from terra.formula_type import extract_thresholds, satisfies_threshold
from terra.gate import check_gate
from terra.knowns import graduate_unknown, link_run_known, promote_known
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.unknowns import create_unknown, link_run


def _probe(root: Path, pid: str, *, quantity: str, value: float):
    init_probe(root, pid, purpose="p")
    pdir = root / ".terra" / "map" / "probes" / pid
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


def _mk_number_param(root: Path, kid: str, *, quantity: str, value: float):
    """Number known on global, promoted to med, admitted as a design param."""
    pid = f"p_{kid}"
    _probe(root, pid, quantity=quantity, value=value)
    for _ in range(3):
        rid = run_probe(root, pid, to={"kind": "region"}).get("id")
        # link each run to the unknown as we go
        if _ == 0:
            create_unknown(
                root, f"u_{kid}", claim="?", evidence_needed="e",
                map_type="number", quantity=quantity,
            )
        link_run(root, f"u_{kid}", rid)
    graduate_unknown(root, f"u_{kid}", known_id=kid)
    promote_known(root, kid, "med")
    add_param(root, kid)


def _mk_formula_gate(root: Path, kid: str, *, expression: str, var: str, quantity: str):
    """Formula gate known on global referencing `quantity` via `var`."""
    pid = f"pg_{kid}"
    _probe(root, pid, quantity=quantity, value=0.0)
    rid = run_probe(root, pid, to={"kind": "region"}).get("id")
    create_unknown(
        root, f"ug_{kid}", claim="gate?", evidence_needed="e",
        map_type="formula", expression=expression, vars=[f"{var}={quantity}"],
    )
    link_run(root, f"ug_{kid}", rid)
    graduate_unknown(root, f"ug_{kid}", known_id=kid)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_extract_and_satisfy_unit():
    th = extract_thresholds("sm <= 0.01 and n(sm) >= 3")
    assert [(t["var"], t["op_symbol"], t["bound"]) for t in th] == [("sm", "<=", 0.01)]
    assert satisfies_threshold(0.005, th[0]["op"], th[0]["bound"]) is True
    assert satisfies_threshold(0.015, th[0]["op"], th[0]["bound"]) is False


def test_notice_when_gate_stricter_than_baseline(proj):
    _mk_number_param(proj, "symmetry_max", quantity="sm", value=0.015)
    _mk_formula_gate(proj, "sym_gate", expression="s <= 0.01", var="s", quantity="sm")
    result = check_design(proj)
    notices = result["notices"]
    assert any(n["kind"] == "gate_stricter_than_baseline" for n in notices)
    n = next(n for n in notices if n["kind"] == "gate_stricter_than_baseline")
    assert n["id"] == "sym_gate"
    assert "stricter than the design of record" in n["why"]


def test_no_notice_when_baseline_passes_gate(proj):
    _mk_number_param(proj, "symmetry_max", quantity="sm", value=0.015)
    _mk_formula_gate(proj, "sym_gate", expression="s <= 0.02", var="s", quantity="sm")
    result = check_design(proj)
    assert not [
        n for n in result["notices"]
        if n["kind"] == "gate_stricter_than_baseline"
    ]


def test_gate_folds_notice_but_does_not_fail(proj):
    _mk_number_param(proj, "symmetry_max", quantity="sm", value=0.015)
    _mk_formula_gate(proj, "sym_gate", expression="s <= 0.01", var="s", quantity="sm")
    verdict = check_gate(proj)
    kinds = {n["kind"] for n in verdict.get("notices") or []}
    assert "gate_stricter_than_baseline" in kinds
    # notice is non-blocking: the gate must not carry it as a violation
    assert not any(
        v["kind"] == "gate_stricter_than_baseline" for v in verdict["violations"]
    )


def test_no_baseline_no_notice(proj):
    # a gate whose quantity is not a design param yields no baseline to check
    _mk_formula_gate(proj, "sym_gate", expression="s <= 0.01", var="s", quantity="sm")
    result = check_design(proj)
    assert not [
        n for n in result["notices"]
        if n["kind"] == "gate_stricter_than_baseline"
    ]
