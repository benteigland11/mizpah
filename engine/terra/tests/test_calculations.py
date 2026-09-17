"""Map-native calculations compose only knowns and assumptions."""

from __future__ import annotations

from pathlib import Path

import pytest

from terra.assumptions import create_assumption, set_assumption
from terra.calculations import (
    calculation_staleness,
    create_calculation,
    get_calculation,
    parse_output_specs,
    parse_bindings,
    run_calculation,
    validate_calculation,
)
from terra.paths import calculation_dir
from terra.gate import check_gate
from terra.knowns import graduate_unknown
from terra.knowns import load_known
from terra.map_status import collect_status_board
from terra.probe_init import init_probe
from terra.probe_run import run_probe
from terra.readings import read_known
from terra.run_validate import validate_run_id
from terra.unknowns import create_unknown, link_calculation, link_run


def _assumption(root: Path, assumption_id: str, value: float) -> None:
    create_assumption(
        root,
        assumption_id,
        claim=f"Working {assumption_id}?",
        map_type="number",
        quantity=assumption_id,
        value=value,
        reason="test basis",
        evidence_needed="measurement",
    )


def _known(root: Path, known_id: str, value: float) -> None:
    probe_id = f"p_{known_id}"
    init_probe(root, probe_id, purpose="calculation input")
    pdir = root / ".terra" / "map" / "probes" / probe_id
    (pdir / "probe.py").write_text(
        "KIND = 'watch'\nDURATION_S = 0\n"
        "REQUIRED_EXPORTS = ['to', 'status', 'artifacts']\n"
        f"def run(ctx=None):\n    return {{'to': {{'kind': 'default'}}, "
        f"'status': 'ok', 'artifacts': [], 'measures': "
        f"[{{'quantity': {known_id!r}, 'value': {value!r}}}]}}\n",
        encoding="utf-8",
    )
    run_id = run_probe(root, probe_id, to={"kind": "default"})["id"]
    create_unknown(
        root,
        f"u_{known_id}",
        claim=f"{known_id}?",
        evidence_needed="measurement",
        map_type="number",
        quantity=known_id,
    )
    link_run(root, f"u_{known_id}", run_id)
    graduate_unknown(root, f"u_{known_id}", known_id=known_id)


def test_calculation_propagates_assumption_and_stales_on_change(tmp_path: Path):
    _assumption(tmp_path, "x", 3.0)
    _assumption(tmp_path, "y", 4.0)
    create_calculation(
        tmp_path,
        "area",
        inputs={"x": "assumption:x", "y": "assumption:y"},
        output_type="number",
        quantity="area",
    )
    (calculation_dir(tmp_path, "area") / "calc.py").write_text(
        'def calculate(inputs):\n    return {"value": inputs["x"] * inputs["y"]}\n',
        encoding="utf-8",
    )

    result = run_calculation(tmp_path, "area")
    assert result["value"] == 12.0
    assert result["conditional"] is True
    assert result["assumptions"] == ["x", "y"]
    assert get_calculation(tmp_path, "area")["value"] == 12.0

    set_assumption(tmp_path, "x", value=5.0, reason="new basis")
    stale = calculation_staleness(tmp_path, "area")
    assert stale["stale"] is True
    with pytest.raises(ValueError, match="stale"):
        get_calculation(tmp_path, "area")


def test_formula_literals_are_allowed_and_audited(tmp_path: Path):
    _assumption(tmp_path, "x", 3.0)
    create_calculation(
        tmp_path,
        "scaled",
        inputs={"x": "assumption:x"},
        output_type="number",
        quantity="scaled",
    )
    (calculation_dir(tmp_path, "scaled") / "calc.py").write_text(
        'def calculate(inputs):\n    return {"value": inputs["x"] * 0.85}\n',
        encoding="utf-8",
    )
    result = validate_calculation(tmp_path, "scaled")
    assert result["ok"] is True
    assert result["literals"] == [
        {"value": 0.85, "line": 2, "column": 35, "context": "mult"}
    ]
    run = run_calculation(tmp_path, "scaled")
    assert run["value"] == pytest.approx(2.55)
    assert run["literals"] == result["literals"]


def test_kinetic_energy_formula_can_use_half_and_square(tmp_path: Path):
    _assumption(tmp_path, "mass", 4.0)
    _assumption(tmp_path, "velocity", 3.0)
    create_calculation(
        tmp_path,
        "kinetic_energy",
        inputs={"mass": "assumption:mass", "velocity": "assumption:velocity"},
        output_type="number",
        quantity="kinetic_energy",
        unit="J",
    )
    (calculation_dir(tmp_path, "kinetic_energy") / "calc.py").write_text(
        'def calculate(inputs):\n    return {"value": (1 / 2) * inputs["mass"] * inputs["velocity"] ** 2}\n',
        encoding="utf-8",
    )
    result = run_calculation(tmp_path, "kinetic_energy")
    assert result["value"] == 18.0
    assert [row["value"] for row in result["literals"]] == [1, 2, 2]


def test_display_decimals_preserve_raw_value(tmp_path: Path):
    _assumption(tmp_path, "numerator", 1.0)
    _assumption(tmp_path, "denominator", 3.0)
    create_calculation(
        tmp_path,
        "ratio",
        inputs={
            "numerator": "assumption:numerator",
            "denominator": "assumption:denominator",
        },
        output_type="number",
        quantity="ratio",
        decimal_places=2,
    )
    (calculation_dir(tmp_path, "ratio") / "calc.py").write_text(
        'def calculate(inputs):\n    return {"value": inputs["numerator"] / inputs["denominator"]}\n',
        encoding="utf-8",
    )
    result = run_calculation(tmp_path, "ratio")
    assert result["value"] == pytest.approx(1 / 3)
    assert result["display"] == {
        "value": 0.33,
        "decimal_places": 2,
        "formatted": "0.33",
    }


def test_display_decimals_reject_boolean_output(tmp_path: Path):
    _assumption(tmp_path, "flag", 1.0)
    with pytest.raises(ValueError, match="only apply to number"):
        create_calculation(
            tmp_path,
            "flag_calc",
            inputs={"flag": "assumption:flag"},
            output_type="boolean",
            quantity="flag_calc",
            decimal_places=2,
        )


def test_model_profile_multiple_outputs_artifacts_and_runtime(tmp_path: Path):
    _assumption(tmp_path, "mass", 4.0)
    _assumption(tmp_path, "velocity", 3.0)
    create_calculation(
        tmp_path,
        "motion_model",
        inputs={"mass": "assumption:mass", "velocity": "assumption:velocity"},
        output_type=None,
        quantity=None,
        profile="model",
        outputs={
            "energy": {"type": "number", "quantity": "energy", "unit": "J"},
            "moving": {"type": "boolean", "quantity": "moving", "unit": ""},
        },
    )
    cdir = calculation_dir(tmp_path, "motion_model")
    (cdir / "helper.py").write_text(
        "def kinetic_energy(mass, velocity):\n    return 0.5 * mass * velocity ** 2\n",
        encoding="utf-8",
    )
    (cdir / "calc.py").write_text(
        "from pathlib import Path\n"
        "from helper import kinetic_energy\n\n"
        "def calculate(inputs, ctx):\n"
        "    energy = kinetic_energy(inputs['mass'], inputs['velocity'])\n"
        "    Path(ctx['calculation_dir'], 'summary.txt').write_text(str(energy))\n"
        "    return {\n"
        "        'outputs': {\n"
        "            'energy': {'value': energy},\n"
        "            'moving': {'value': inputs['velocity'] > 0},\n"
        "        },\n"
        "        'health': {'ok': True},\n"
        "        'diagnostics': {'method': 'closed_form', 'finite': True},\n"
        "        'artifacts': [{'path': 'summary.txt', 'role': 'summary'}],\n"
        "    }\n",
        encoding="utf-8",
    )
    validation = validate_calculation(tmp_path, "motion_model")
    assert validation["ok"] is True
    result = run_calculation(tmp_path, "motion_model")
    assert result["outputs"]["energy"]["value"] == 18.0
    assert result["outputs"]["moving"]["value"] is True
    assert result["diagnostics"]["method"] == "closed_form"
    assert result["artifacts"][0]["sha256"]
    assert result["runtime"]["python"]
    assert result["conditional"] is True

    (cdir / "helper.py").write_text(
        "def kinetic_energy(mass, velocity):\n    return mass * velocity ** 2\n",
        encoding="utf-8",
    )
    assert calculation_staleness(tmp_path, "motion_model")["stale"] is True


def _simple_model(tmp_path: Path, calculation_id: str = "model") -> Path:
    _assumption(tmp_path, "x", 3.0)
    create_calculation(
        tmp_path,
        calculation_id,
        inputs={"x": "assumption:x"},
        output_type=None,
        quantity=None,
        profile="model",
        outputs={"y": {"type": "number", "quantity": "y", "unit": ""}},
    )
    return calculation_dir(tmp_path, calculation_id)


def test_model_requires_explicit_health_verdict(tmp_path: Path):
    cdir = _simple_model(tmp_path)
    (cdir / "calc.py").write_text(
        "def calculate(inputs, ctx):\n"
        "    return {'outputs': {'y': {'value': inputs['x']}}, "
        "'diagnostics': {}, 'artifacts': []}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="model health"):
        run_calculation(tmp_path, "model")


def test_failed_model_health_is_recorded_stale_and_gates(tmp_path: Path):
    cdir = _simple_model(tmp_path)
    (cdir / "calc.py").write_text(
        "def calculate(inputs, ctx):\n"
        "    return {'outputs': {'y': {'value': inputs['x']}}, "
        "'health': {'ok': False, 'summary': 'solver did not converge'}, "
        "'diagnostics': {'residual': 2.0}, 'artifacts': []}\n",
        encoding="utf-8",
    )
    result = run_calculation(tmp_path, "model")
    assert result["health"]["ok"] is False
    stale = calculation_staleness(tmp_path, "model")
    assert stale["stale"] is True
    assert "solver did not converge" in "; ".join(stale["reasons"])
    assert any(
        row["kind"] == "calculation_stale" for row in check_gate(tmp_path)["violations"]
    )


def test_model_artifact_drift_makes_result_stale(tmp_path: Path):
    cdir = _simple_model(tmp_path)
    (cdir / "calc.py").write_text(
        "from pathlib import Path\n"
        "def calculate(inputs, ctx):\n"
        "    Path(ctx['calculation_dir'], 'result.txt').write_text('original')\n"
        "    return {'outputs': {'y': {'value': inputs['x']}}, "
        "'health': {'ok': True}, 'diagnostics': {}, "
        "'artifacts': [{'path': 'result.txt'}]}\n",
        encoding="utf-8",
    )
    run_calculation(tmp_path, "model")
    (cdir / "result.txt").write_text("changed", encoding="utf-8")
    reasons = calculation_staleness(tmp_path, "model")["reasons"]
    assert "model artifact changed: result.txt" in reasons


def test_model_runtime_drift_makes_result_stale(tmp_path: Path, monkeypatch):
    cdir = _simple_model(tmp_path)
    (cdir / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    run_calculation(tmp_path, "model")
    import importlib.metadata

    real_version = importlib.metadata.version

    def changed_version(name):
        return "999.0" if name == "pytest" else real_version(name)

    monkeypatch.setattr(importlib.metadata, "version", changed_version)
    reasons = calculation_staleness(tmp_path, "model")["reasons"]
    assert "model runtime installed changed" in reasons


def test_calculation_rejects_non_finite_numbers(tmp_path: Path):
    cdir = _simple_model(tmp_path)
    (cdir / "calc.py").write_text(
        "def calculate(inputs, ctx):\n"
        "    return {'outputs': {'y': {'value': float('nan')}}, "
        "'health': {'ok': True}, 'diagnostics': {}, 'artifacts': []}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-finite"):
        run_calculation(tmp_path, "model")


def test_known_only_calculation_is_clean(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _known(tmp_path, "width", 4.0)
    create_calculation(
        tmp_path,
        "copy_width",
        inputs={"width": "known:width"},
        output_type="number",
        quantity="copy_width",
    )
    result = run_calculation(tmp_path, "copy_width")
    assert result["value"] == 4.0
    assert result["conditional"] is False
    assert result["assumptions"] == []


def test_calculation_result_is_evidence_that_graduates_to_derived_known(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    _known(tmp_path, "width", 4.0)
    create_calculation(
        tmp_path,
        "double_width",
        inputs={"width": "known:width"},
        output_type="number",
        quantity="derived_width",
        unit="m",
    )
    cpath = calculation_dir(tmp_path, "double_width") / "calc.py"
    cpath.write_text(
        'def calculate(inputs):\n    return {"value": inputs["width"] * 2}\n',
        encoding="utf-8",
    )
    result = run_calculation(tmp_path, "double_width")
    run_id = result["evidence_run_id"]
    validation = validate_run_id(tmp_path, run_id)
    assert validation["ok"] is True
    assert validation["record"]["source_type"] == "calculation"
    assert validation["record"]["calculation_id"] == "double_width"

    create_unknown(
        tmp_path,
        "derived_width",
        claim="What is derived width?",
        evidence_needed="validated composition",
        map_type="number",
        quantity="derived_width",
        unit="m",
    )
    unknown = link_calculation(tmp_path, "derived_width", "double_width")
    assert unknown["run_ids"] == [run_id]
    assert unknown["stats"]["mean"] == 8.0
    known = graduate_unknown(tmp_path, "derived_width")
    assert known["stats"]["mean"] == 8.0
    assert known["conditional"] is False
    assert [d["id"] for d in known["deps"]["knowns"]] == ["width"]
    assert read_known(tmp_path, "derived_width")["value"] == 8.0

    cpath.write_text(cpath.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale probe inputs"):
        read_known(tmp_path, "derived_width")
    assert any(
        v["kind"] == "evidence_input_stale" and v["id"] == "derived_width"
        for v in check_gate(tmp_path)["violations"]
    )


def test_assumption_conditioned_calculation_evidence_stays_conditional(tmp_path: Path):
    _assumption(tmp_path, "working_width", 3.0)
    create_calculation(
        tmp_path,
        "conditional_width",
        inputs={"width": "assumption:working_width"},
        output_type="number",
        quantity="derived_width",
    )
    run_calculation(tmp_path, "conditional_width")
    create_unknown(
        tmp_path,
        "derived_width",
        claim="What is derived width?",
        evidence_needed="validated composition",
        map_type="number",
        quantity="derived_width",
    )
    link_calculation(tmp_path, "derived_width", "conditional_width")
    known = graduate_unknown(tmp_path, "derived_width")
    assert known["conditional"] is True
    assert known["assumptions"] == ["working_width"]
    reading = read_known(tmp_path, "derived_width")
    assert reading["conditional"] is True
    assert reading["assumptions"] == ["working_width"]


def test_binding_contract_rejects_unknowns_and_literals():
    with pytest.raises(ValueError, match="only"):
        parse_bindings(["x=unknown:x"])
    with pytest.raises(ValueError, match="only"):
        parse_bindings(["x=literal:3"])


def test_model_relation_output_can_evidence_and_graduate_relation(tmp_path: Path):
    _assumption(tmp_path, "slope", 0.1)
    outputs = parse_output_specs(["polar=relation:cl:alpha_deg::deg"])
    assert outputs["polar"] == {
        "type": "relation",
        "quantity": "cl",
        "x_quantity": "alpha_deg",
        "unit": "",
        "x_unit": "deg",
    }
    create_calculation(
        tmp_path,
        "polar_model",
        inputs={"slope": "assumption:slope"},
        output_type=None,
        quantity=None,
        profile="model",
        outputs=outputs,
    )
    (calculation_dir(tmp_path, "polar_model") / "calc.py").write_text(
        "def calculate(inputs, ctx):\n"
        "    s = inputs['slope']\n"
        "    return {'outputs': {'polar': {'points': [\n"
        "        {'x': -2, 'value': -2*s},\n"
        "        {'x': 0, 'value': 0},\n"
        "        {'x': 4, 'value': 4*s},\n"
        "    ]}}, 'health': {'ok': True}, 'diagnostics': {}, 'artifacts': []}\n",
        encoding="utf-8",
    )
    result = run_calculation(tmp_path, "polar_model")
    assert result["outputs"]["polar"]["points"][2] == {"x": 4.0, "value": 0.4}

    create_unknown(
        tmp_path,
        "cl_curve",
        claim="What is CL(alpha)?",
        evidence_needed="derived polar sweep",
        map_type="relation",
        quantity="cl",
        x_quantity="alpha_deg",
        x_unit="deg",
    )
    unknown = link_calculation(
        tmp_path, "cl_curve", "polar_model", output="polar"
    )
    assert unknown["stats"]["n"] == 1
    assert unknown["stats"]["station_count"] == 3
    known = graduate_unknown(tmp_path, "cl_curve")
    assert known["type"] == "relation"
    assert known["conditional"] is True
    assert read_known(tmp_path, "cl_curve", at=2)["value"] == pytest.approx(0.2)


def test_model_relation_output_rejects_unordered_or_nonfinite_points(tmp_path: Path):
    _assumption(tmp_path, "slope", 0.1)
    create_calculation(
        tmp_path,
        "bad_curve",
        inputs={"slope": "assumption:slope"},
        output_type=None,
        quantity=None,
        profile="model",
        outputs=parse_output_specs(["curve=relation:y:x"]),
    )
    (calculation_dir(tmp_path, "bad_curve") / "calc.py").write_text(
        "def calculate(inputs, ctx):\n"
        "    return {'outputs': {'curve': {'points': ["
        "{'x': 1, 'value': 2}, {'x': 0, 'value': 3}]}}, "
        "'health': {'ok': True}, 'diagnostics': {}, 'artifacts': []}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strictly ordered"):
        run_calculation(tmp_path, "bad_curve")


def test_source_change_makes_result_stale(tmp_path: Path):
    _assumption(tmp_path, "x", 3.0)
    create_calculation(
        tmp_path,
        "copy_x",
        inputs={"x": "assumption:x"},
        output_type="number",
        quantity="copy_x",
    )
    run_calculation(tmp_path, "copy_x")
    path = calculation_dir(tmp_path, "copy_x") / "calc.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert calculation_staleness(tmp_path, "copy_x")["stale"] is True


def test_status_and_gate_surface_calculation_state(tmp_path: Path):
    _assumption(tmp_path, "x", 3.0)
    create_calculation(
        tmp_path,
        "copy_x",
        inputs={"x": "assumption:x"},
        output_type="number",
        quantity="copy_x",
    )
    assert check_gate(tmp_path)["ok"] is False
    assert any(
        v["kind"] == "calculation_stale" for v in check_gate(tmp_path)["violations"]
    )

    run_calculation(tmp_path, "copy_x")
    gate = check_gate(tmp_path)
    assert gate["ok"] is True
    assert any(n["kind"] == "calculation_conditional" for n in gate["notices"])
    scope = collect_status_board(tmp_path)["scopes"][0]
    assert scope["counts"]["calculations"] == 1
    assert scope["calculations"][0]["conditional"] is True
