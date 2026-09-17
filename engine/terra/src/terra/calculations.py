"""Map-native calculations over declared knowns and assumptions only."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import platform
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .map_inputs import parse_map_bindings, resolve_map_bindings
from .paths import calculation_dir, calculations_root, ensure_calculations_store

_ALLOWED_CALLS = {"abs", "all", "any", "len", "max", "min", "round", "sum"}
_SCALAR_OUTPUT_TYPES = {"number", "boolean"}
_MODEL_OUTPUT_TYPES = {*_SCALAR_OUTPUT_TYPES, "relation"}
_PROFILES = {"expression", "model"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_hash(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _package_hash(cdir: Path, profile: str) -> str:
    paths = [cdir / "calc.py"]
    if profile == "model":
        paths = sorted(cdir.glob("*.py"))
        if (cdir / "requirements.txt").is_file():
            paths.append(cdir / "requirements.txt")
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file():
            continue
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def calculation_source_hash(project_root: Path, calculation_id: str) -> str:
    """Current package hash for evidence freshness checks."""
    rec = load_calculation(project_root, calculation_id)
    return _package_hash(
        calculation_dir(project_root, calculation_id),
        rec.get("profile") or "expression",
    )


def _validate_slug(value: str, label: str) -> None:
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_]*", value or ""):
        raise ValueError(f"{label} must be a slug (a-z, then a-z0-9_)")


def parse_bindings(rows: list[str]) -> dict[str, str]:
    """Calculation compatibility wrapper over the shared map binding contract."""
    bindings = parse_map_bindings(rows)
    if not bindings:
        raise ValueError("calculation requires at least one declared input")
    return bindings


def parse_output_specs(rows: list[str]) -> dict[str, dict[str, str]]:
    outputs: dict[str, dict[str, str]] = {}
    for raw in rows:
        name, sep, spec = raw.partition("=")
        _validate_slug(name.strip(), "output name")
        parts = spec.split(":") if sep else []
        if not parts or parts[0] not in _MODEL_OUTPUT_TYPES:
            raise ValueError(
                "outputs must be NAME=number|boolean:QUANTITY[:UNIT] or "
                "NAME=relation:Y_QUANTITY:X_QUANTITY[:Y_UNIT[:X_UNIT]]"
            )
        if parts[0] == "relation":
            if len(parts) not in (3, 4, 5):
                raise ValueError(
                    "relation output must be "
                    "NAME=relation:Y_QUANTITY:X_QUANTITY[:Y_UNIT[:X_UNIT]]"
                )
            _validate_slug(parts[1], "output quantity")
            _validate_slug(parts[2], "output x quantity")
            outputs[name.strip()] = {
                "type": "relation",
                "quantity": parts[1],
                "x_quantity": parts[2],
                "unit": parts[3] if len(parts) >= 4 else "",
                "x_unit": parts[4] if len(parts) == 5 else "",
            }
        else:
            if len(parts) not in (2, 3):
                raise ValueError(
                    "scalar output must be NAME=number|boolean:QUANTITY[:UNIT]"
                )
            _validate_slug(parts[1], "output quantity")
            outputs[name.strip()] = {
                "type": parts[0],
                "quantity": parts[1],
                "unit": parts[2] if len(parts) == 3 else "",
            }
    return outputs


def create_calculation(
    project_root: Path,
    calculation_id: str,
    *,
    inputs: dict[str, str],
    output_type: str,
    quantity: str,
    unit: str = "",
    purpose: str = "",
    force: bool = False,
    decimal_places: int | None = None,
    profile: str = "expression",
    outputs: dict[str, dict[str, str]] | None = None,
) -> Path:
    _validate_slug(calculation_id, "calculation id")
    if profile not in _PROFILES:
        raise ValueError(f"calculation profile must be one of {sorted(_PROFILES)}")
    if profile == "expression":
        if output_type is None or quantity is None:
            raise ValueError("expression calculations require output type and quantity")
        _validate_slug(quantity, "output quantity")
        if output_type not in _SCALAR_OUTPUT_TYPES:
            raise ValueError("calculation output type must be number or boolean")
    elif not outputs:
        raise ValueError("model calculations require at least one declared output")
    if decimal_places is not None:
        if profile != "expression" or output_type != "number":
            raise ValueError("decimal places only apply to number outputs")
        if isinstance(decimal_places, bool) or not isinstance(decimal_places, int):
            raise ValueError("decimal places must be an integer")
        if not 0 <= decimal_places <= 15:
            raise ValueError("decimal places must be between 0 and 15")
    parse_bindings([f"{k}={v}" for k, v in inputs.items()])
    ensure_calculations_store(project_root)
    cdir = calculation_dir(project_root, calculation_id)
    if cdir.exists() and not force:
        raise FileExistsError(f"calculation already exists: {calculation_id}")
    cdir.mkdir(parents=True, exist_ok=True)
    now = _now()
    record = {
        "id": calculation_id,
        "profile": profile,
        "purpose": purpose,
        "inputs": inputs,
        **(
            {
                "output": {
                    "type": output_type,
                    "quantity": quantity,
                    "unit": unit,
                    **(
                        {"display": {"decimal_places": decimal_places}}
                        if decimal_places is not None
                        else {}
                    ),
                }
            }
            if profile == "expression"
            else {}
        ),
        **({"outputs": outputs} if outputs else {}),
        "created_at": now,
        "updated_at": now,
        "runs": [],
    }
    (cdir / "calc.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    first_input = next(iter(inputs))
    if profile == "model":
        first_output = next(iter(outputs or {}))
        first_spec = (outputs or {})[first_output]
        first_payload = (
            f'{{"points": [{{"x": 0, "value": inputs[{first_input!r}]}}]}}'
            if first_spec["type"] == "relation"
            else f'{{"value": inputs[{first_input!r}]}}'
        )
        source = (
            '"""Model calculation. Package-local imports are allowed."""\n\n'
            "def calculate(inputs, ctx):\n"
            "    return {\n"
            f"        \"outputs\": {{{first_output!r}: {first_payload}}},\n"
            "        \"health\": {\"ok\": True},\n"
            "        \"diagnostics\": {},\n"
            "        \"artifacts\": [],\n"
            "    }\n"
        )
        (cdir / "requirements.txt").write_text("", encoding="utf-8")
    else:
        source = (
            '"""Calculation logic. Domain values must come from inputs."""\n\n'
            "def calculate(inputs):\n"
            "    return {\"value\": inputs[" + repr(first_input) + "]}\n"
        )
    (cdir / "calc.py").write_text(source, encoding="utf-8")
    return cdir


def load_calculation(project_root: Path, calculation_id: str) -> dict[str, Any]:
    path = calculation_dir(project_root, calculation_id) / "calc.json"
    if not path.is_file():
        raise FileNotFoundError(f"calculation not found: {calculation_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_source(source: str, *, profile: str = "expression") -> list[str]:
    blocks: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"calc.py syntax error: {exc}"]
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    calculate = [n for n in funcs if n.name == "calculate"]
    if len(calculate) != 1:
        blocks.append("calc.py must define exactly one calculate(inputs) function")
    elif profile == "expression" and (
        len(calculate[0].args.args) != 1 or calculate[0].args.args[0].arg != "inputs"
    ):
        blocks.append("expression calculate must have exactly one argument named inputs")
    elif profile == "model" and (
        [a.arg for a in calculate[0].args.args] != ["inputs", "ctx"]
    ):
        blocks.append("model calculate must have arguments (inputs, ctx)")
    for node in ast.walk(tree):
        if profile == "expression" and isinstance(
            node,
            (
                ast.Import,
                ast.ImportFrom,
                ast.Attribute,
                ast.Global,
                ast.Nonlocal,
                ast.While,
            ),
        ):
            blocks.append(f"forbidden Python construct: {type(node).__name__}")
        elif profile == "expression" and isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
                blocks.append(
                    f"function call at line {getattr(node, 'lineno', '?')} is not allowed"
                )
    return list(dict.fromkeys(blocks))


def _literal_inventory(source: str) -> list[dict[str, Any]]:
    """Expose numeric/boolean formula literals without treating logic as data."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if not isinstance(value, (int, float, bool)):
            continue
        parent_role = "literal"
        for candidate in ast.walk(tree):
            if isinstance(candidate, ast.BinOp) and (
                candidate.left is node or candidate.right is node
            ):
                parent_role = type(candidate.op).__name__.lower()
                break
            if isinstance(candidate, ast.Subscript) and candidate.slice is node:
                parent_role = "index"
                break
            if isinstance(candidate, ast.Compare) and node in candidate.comparators:
                parent_role = "comparison"
                break
        rows.append(
            {
                "value": value,
                "line": getattr(node, "lineno", None),
                "column": getattr(node, "col_offset", None),
                "context": parent_role,
            }
        )
    return sorted(rows, key=lambda row: (row.get("line") or 0, row.get("column") or 0))


def validate_calculation(project_root: Path, calculation_id: str) -> dict[str, Any]:
    blocks: list[str] = []
    try:
        rec = load_calculation(project_root, calculation_id)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return {"id": calculation_id, "ok": False, "blocks": [str(exc)]}
    source_path = calculation_dir(project_root, calculation_id) / "calc.py"
    profile = rec.get("profile") or "expression"
    if profile not in _PROFILES:
        blocks.append(f"profile must be one of {sorted(_PROFILES)}")
    if not source_path.is_file():
        blocks.append("calc.py missing")
        source = ""
    else:
        source = source_path.read_text(encoding="utf-8")
        blocks.extend(_validate_source(source, profile=profile))
    try:
        parse_bindings([f"{k}={v}" for k, v in (rec.get("inputs") or {}).items()])
    except ValueError as exc:
        blocks.append(str(exc))
    output = rec.get("output") or {}
    if profile == "expression" and output.get("type") not in _SCALAR_OUTPUT_TYPES:
        blocks.append("output.type must be number or boolean")
    if profile == "model":
        outputs = rec.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            blocks.append("model outputs must be a non-empty object")
        else:
            for name, spec in outputs.items():
                if not isinstance(spec, dict) or spec.get("type") not in _MODEL_OUTPUT_TYPES:
                    blocks.append(
                        f"output {name!r} type must be number, boolean, or relation"
                    )
                if not isinstance(spec, dict) or not spec.get("quantity"):
                    blocks.append(f"output {name!r} requires quantity")
                if (
                    isinstance(spec, dict)
                    and spec.get("type") == "relation"
                    and not spec.get("x_quantity")
                ):
                    blocks.append(f"relation output {name!r} requires x_quantity")
        runtime = _runtime_manifest(calculation_dir(project_root, calculation_id))
        missing = [name for name, version in runtime["installed"].items() if version is None]
        if missing:
            blocks.append("model requirements are not installed: " + ", ".join(missing))
    display = output.get("display")
    if display is not None:
        decimals = display.get("decimal_places") if isinstance(display, dict) else None
        if output.get("type") != "number":
            blocks.append("output.display only applies to number outputs")
        if (
            isinstance(decimals, bool)
            or not isinstance(decimals, int)
            or not 0 <= decimals <= 15
        ):
            blocks.append("output.display.decimal_places must be an integer from 0 to 15")
    return {
        "id": calculation_id,
        "ok": not blocks,
        "blocks": blocks,
        "literals": _literal_inventory(source),
        "record": rec,
    }


@contextmanager
def _model_sys_path(cdir: Path):
    old = list(sys.path)
    before = set(sys.modules)
    for name, module in list(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if module_file:
            try:
                Path(module_file).resolve().relative_to(cdir.resolve())
            except ValueError:
                continue
            sys.modules.pop(name, None)
    sys.path.insert(0, str(cdir))
    try:
        yield
    finally:
        sys.path[:] = old
        for name in set(sys.modules) - before:
            module_file = getattr(sys.modules.get(name), "__file__", None)
            if module_file:
                try:
                    Path(module_file).resolve().relative_to(cdir.resolve())
                except ValueError:
                    continue
                sys.modules.pop(name, None)


def _runtime_manifest(cdir: Path) -> dict[str, Any]:
    import importlib.metadata

    req_path = cdir / "requirements.txt"
    requirements = []
    if req_path.is_file():
        requirements = [
            line.strip()
            for line in req_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    installed: dict[str, str | None] = {}
    for requirement in requirements:
        name = requirement.split(";", 1)[0].strip()
        for marker in ("==", ">=", "<=", "~=", ">", "<", "["):
            name = name.split(marker, 1)[0].strip()
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed[name] = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "requirements": requirements,
        "installed": installed,
        "requirements_sha256": (
            _sha256(req_path.read_bytes()) if req_path.is_file() else None
        ),
    }


def _normalize_model_artifacts(cdir: Path, rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("model artifacts must be a list")
    artifacts: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("each model artifact must contain a string path")
        path = (cdir / item["path"]).resolve()
        try:
            path.relative_to(cdir.resolve())
        except ValueError as exc:
            raise ValueError("model artifacts must stay inside calculation package") from exc
        if not path.is_file():
            raise ValueError(f"model artifact missing: {item['path']}")
        artifacts.append(
            {
                **item,
                "path": str(path.relative_to(cdir)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path.read_bytes()),
            }
        )
    return artifacts


def _normalize_model_health(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
        raise ValueError("model health must be an object containing boolean ok")
    summary = value.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("model health summary must be a string")
    return value


def _model_integrity_reasons(cdir: Path, latest: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    health = latest.get("health")
    if not isinstance(health, dict) or not isinstance(health.get("ok"), bool):
        reasons.append("model health verdict missing or invalid")
    elif health["ok"] is False:
        detail = health.get("summary")
        reasons.append("model health failed" + (f": {detail}" if detail else ""))

    for artifact in latest.get("artifacts") or []:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            reasons.append("stored model artifact record is invalid")
            continue
        path = (cdir / artifact["path"]).resolve()
        try:
            path.relative_to(cdir.resolve())
        except ValueError:
            reasons.append(f"model artifact escaped package: {artifact['path']}")
            continue
        if not path.is_file():
            reasons.append(f"model artifact missing: {artifact['path']}")
        elif (
            path.stat().st_size != artifact.get("bytes")
            or _sha256(path.read_bytes()) != artifact.get("sha256")
        ):
            reasons.append(f"model artifact changed: {artifact['path']}")

    stamped_runtime = latest.get("runtime")
    current_runtime = _runtime_manifest(cdir)
    if not isinstance(stamped_runtime, dict):
        reasons.append("model runtime manifest missing or invalid")
    else:
        for field in ("python", "platform", "installed"):
            if stamped_runtime.get(field) != current_runtime.get(field):
                reasons.append(f"model runtime {field} changed")
    return reasons


def run_calculation(project_root: Path, calculation_id: str) -> dict[str, Any]:
    validation = validate_calculation(project_root, calculation_id)
    if not validation["ok"]:
        raise ValueError("invalid calculation: " + "; ".join(validation["blocks"]))
    rec = validation["record"]
    profile = rec.get("profile") or "expression"
    values, provenance, assumptions = resolve_map_bindings(
        project_root,
        rec.get("inputs") or {},
        consumer=f"calculation:{rec['id']}",
    )
    cdir = calculation_dir(project_root, calculation_id)
    source_path = cdir / "calc.py"
    source_bytes = source_path.read_bytes()
    if profile == "expression":
        import builtins

        safe_builtins = {name: getattr(builtins, name) for name in _ALLOWED_CALLS}
        namespace: dict[str, Any] = {"__builtins__": safe_builtins}
        exec(compile(source_bytes, str(source_path), "exec"), namespace)
        raw = namespace["calculate"](dict(values))
    else:
        namespace = {"__builtins__": __builtins__}
        with _model_sys_path(cdir):
            exec(compile(source_bytes, str(source_path), "exec"), namespace)
            raw = namespace["calculate"](
                dict(values), {"calculation_dir": str(cdir), "profile": profile}
            )
    if not isinstance(raw, dict):
        raise ValueError("calculate must return an object")
    base_result = {
        "id": calculation_id,
        "profile": profile,
        "conditional": bool(assumptions),
        "assumptions": assumptions,
        "inputs": provenance,
        "input_hash": _json_hash(provenance),
        "source_sha256": _package_hash(cdir, profile),
        "literals": validation.get("literals") or [],
        "calculated_at": _now(),
    }
    if profile == "expression":
        if set(raw) != {"value"}:
            raise ValueError("expression calculate(inputs) must return exactly {'value': ...}")
        value = raw["value"]
        output_type = rec["output"]["type"]
        _validate_output_value("value", value, output_type)
        result = {
            **base_result,
            "value": value,
            "type": output_type,
            "quantity": rec["output"]["quantity"],
            "unit": rec["output"].get("unit") or "",
        }
        display = rec["output"].get("display") or {}
        if output_type == "number" and "decimal_places" in display:
            decimals = int(display["decimal_places"])
            result["display"] = {
                "value": round(value, decimals),
                "decimal_places": decimals,
                "formatted": f"{value:.{decimals}f}",
            }
    else:
        if not isinstance(raw.get("outputs"), dict):
            raise ValueError("model calculate must return an outputs object")
        declared = rec.get("outputs") or {}
        if set(raw["outputs"]) != set(declared):
            raise ValueError(
                f"model output names must exactly match declared outputs {sorted(declared)}"
            )
        output_rows: dict[str, Any] = {}
        for name, spec in declared.items():
            row = raw["outputs"][name]
            expected_key = "points" if spec["type"] == "relation" else "value"
            if not isinstance(row, dict) or set(row) != {expected_key}:
                raise ValueError(
                    f"model output {name!r} must be exactly "
                    f"{{{expected_key!r}: ...}}"
                )
            if spec["type"] == "relation":
                points = _validate_relation_points(name, row["points"])
                output_rows[name] = {**spec, "points": points}
            else:
                _validate_output_value(name, row["value"], spec["type"])
                output_rows[name] = {**spec, "value": row["value"]}
        diagnostics = raw.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            raise ValueError("model diagnostics must be an object")
        result = {
            **base_result,
            "outputs": output_rows,
            "health": _normalize_model_health(raw.get("health")),
            "diagnostics": diagnostics,
            "artifacts": _normalize_model_artifacts(cdir, raw.get("artifacts") or []),
            "runtime": _runtime_manifest(cdir),
        }
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"calculation result must be JSON-serializable: {exc}") from exc
    # Calculations are instruments over admitted map beliefs. Stamp their
    # outputs into the shared evidence-run store so unknowns can consume them
    # through the same link-run → graduate lifecycle as probe observations.
    from .paths import ensure_runs_store, get_active_map_id, run_dir
    from .probe_run import RUN_META_NAME, RUN_SCHEMA_VERSION

    ensure_runs_store(project_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_run_id = f"{stamp}_calc_{calculation_id}_{uuid.uuid4().hex[:6]}"
    evidence_dir = run_dir(project_root, evidence_run_id)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    if profile == "expression":
        measures = [
            {
                "quantity": result["quantity"],
                "value": result["value"],
                **({"unit": result["unit"]} if result.get("unit") else {}),
            }
        ]
    else:
        measures = []
        for name, row in result["outputs"].items():
            if row["type"] == "relation":
                measures.extend(
                    {
                        "quantity": row["quantity"],
                        "x_quantity": row["x_quantity"],
                        "x": point["x"],
                        "value": point["value"],
                        "output": name,
                        **({"unit": row["unit"]} if row.get("unit") else {}),
                        **(
                            {"x_unit": row["x_unit"]}
                            if row.get("x_unit")
                            else {}
                        ),
                    }
                    for point in row["points"]
                )
            else:
                measures.append(
                    {
                        "quantity": row["quantity"],
                        "value": row["value"],
                        "output": name,
                        **({"unit": row["unit"]} if row.get("unit") else {}),
                    }
                )
    evidence_meta = {
        "schema_version": RUN_SCHEMA_VERSION,
        "id": evidence_run_id,
        "source_type": "calculation",
        "calculation_id": calculation_id,
        "time": {
            "started_at": result["calculated_at"],
            "finished_at": result["calculated_at"],
            "captured_at": result["calculated_at"],
            "duration_s": 0.0,
        },
        "from": {
            "calculation_id": calculation_id,
            "runner": "terra.calculation.run",
            "profile": profile,
        },
        "to": {"kind": "map_composition", "calculation": calculation_id},
        "status": "ok",
        "artifacts": result.get("artifacts") or [],
        "measures": measures,
        "map_id": get_active_map_id(project_root),
        "input_bindings": dict(rec.get("inputs") or {}),
        "inputs": provenance,
        "conditional": bool(assumptions),
        "assumptions": assumptions,
        "calculation_source_sha256": result["source_sha256"],
    }
    (evidence_dir / RUN_META_NAME).write_text(
        json.dumps(evidence_meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result["evidence_run_id"] = evidence_run_id
    result["evidence_path"] = str((evidence_dir / RUN_META_NAME).relative_to(project_root))
    rec["latest"] = result
    rec.setdefault("runs", []).append(result)
    rec["updated_at"] = result["calculated_at"]
    (calculation_dir(project_root, calculation_id) / "calc.json").write_text(
        json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _validate_output_value(name: str, value: Any, output_type: str) -> None:
    if output_type == "number" and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        raise ValueError(f"output {name!r} declared number but returned non-number")
    if output_type == "number" and not math.isfinite(value):
        raise ValueError(f"output {name!r} declared number but returned non-finite value")
    if output_type == "boolean" and not isinstance(value, bool):
        raise ValueError(f"output {name!r} declared boolean but returned non-boolean")


def _validate_relation_points(name: str, value: Any) -> list[dict[str, float]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"relation output {name!r} points must be a non-empty list")
    points: list[dict[str, float]] = []
    previous_x: float | None = None
    for index, point in enumerate(value):
        if not isinstance(point, dict) or set(point) != {"x", "value"}:
            raise ValueError(
                f"relation output {name!r} point {index} must be exactly "
                "{'x': number, 'value': number}"
            )
        x = point["x"]
        y = point["value"]
        if any(
            not isinstance(v, (int, float))
            or isinstance(v, bool)
            or not math.isfinite(v)
            for v in (x, y)
        ):
            raise ValueError(
                f"relation output {name!r} point {index} must contain finite numbers"
            )
        x_float = float(x)
        if previous_x is not None and x_float <= previous_x:
            raise ValueError(
                f"relation output {name!r} points must be strictly ordered by x"
            )
        previous_x = x_float
        points.append({"x": x_float, "value": float(y)})
    return points


def calculation_staleness(project_root: Path, calculation_id: str) -> dict[str, Any]:
    rec = load_calculation(project_root, calculation_id)
    latest = rec.get("latest")
    if not latest:
        return {"stale": True, "reasons": ["never run"]}
    reasons: list[str] = []
    cdir = calculation_dir(project_root, calculation_id)
    profile = rec.get("profile") or "expression"
    source_path = cdir / "calc.py"
    if not source_path.is_file() or _package_hash(cdir, profile) != latest.get("source_sha256"):
        reasons.append("calculation source or requirements changed")
    if profile == "model":
        reasons.extend(_model_integrity_reasons(cdir, latest))
    try:
        _, provenance, _ = resolve_map_bindings(
            project_root,
            rec.get("inputs") or {},
            consumer=f"calculation:{rec['id']}",
            record=False,
        )
        if _json_hash(provenance) != latest.get("input_hash"):
            reasons.append("input value or provenance changed")
    except (ValueError, FileNotFoundError) as exc:
        reasons.append(f"input unavailable: {exc}")
    return {"stale": bool(reasons), "reasons": reasons}


def get_calculation(project_root: Path, calculation_id: str) -> dict[str, Any]:
    rec = load_calculation(project_root, calculation_id)
    if not rec.get("latest"):
        raise ValueError(f"calculation {calculation_id} has never run")
    stale = calculation_staleness(project_root, calculation_id)
    if stale["stale"]:
        raise ValueError(f"calculation {calculation_id} is stale: {'; '.join(stale['reasons'])}")
    return {**rec["latest"], "stale": False}


def list_calculations(project_root: Path) -> list[dict[str, Any]]:
    root = calculations_root(project_root)
    if not root.is_dir():
        return []
    rows = []
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        validation = validate_calculation(project_root, child.name)
        stale = calculation_staleness(project_root, child.name) if validation["ok"] else {"stale": True, "reasons": ["invalid"]}
        rows.append({**validation, "staleness": stale})
    return rows


def delete_calculation(project_root: Path, calculation_id: str) -> Path:
    import shutil

    cdir = calculation_dir(project_root, calculation_id)
    if not cdir.is_dir():
        raise FileNotFoundError(f"calculation not found: {calculation_id}")
    shutil.rmtree(cdir)
    return cdir
