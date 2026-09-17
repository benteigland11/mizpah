"""Map type: formula — observation as a checkable predicate with vars.

Probes stay open. A formula known/unknown is:

  claim + expression + vars{name → quantity} + run_ids
  → substrate evaluates → holds / holds_rate / bindings / by_run

Allowlisted AST only (no eval of arbitrary Python).
"""

from __future__ import annotations

import ast
import operator
import statistics
from typing import Any, Callable

from .number_type import (
    CONFIDENCE_SET,
    coerce_bool,
    confidence_rank,
    extract_boolean_measures_from_run_meta,
    extract_number_measures_from_run_meta,
)

FORMULA_FUNCS = frozenset({"mean", "min", "max", "std", "n", "rate", "sum"})
_COMP_OPS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def empty_formula_stats() -> dict[str, Any]:
    return {
        "kind": "formula",
        "n": 0,
        "holds": None,
        "holds_rate": None,
        "k_hold": 0,
        "k_fail": 0,
        "k_skip": 0,
        "bindings": {},
        "value": None,
        "by_run": [],
        "error": None,
    }


def parse_vars_arg(raw: str | list[str] | dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """Parse vars from CLI or dict.

    CLI forms (repeatable ``--var``):
      h=hostile_count
      limit=known:spec_hostile_limit
      h:hostile_count
      h=hostile_count:number
      b=rcon_up:boolean
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: dict[str, dict[str, str]] = {}
        for name, spec in raw.items():
            if isinstance(spec, str):
                out[str(name)] = {"quantity": spec}
            elif isinstance(spec, dict):
                q = spec.get("quantity")
                known_id = spec.get("known_id") or spec.get("known")
                if isinstance(known_id, str) and known_id.strip():
                    if isinstance(q, str) and q.strip():
                        raise ValueError(f"var {name!r} cannot bind quantity and known")
                    entry = {"known_id": known_id.strip()}
                elif isinstance(q, str) and q.strip():
                    entry = {"quantity": q.strip()}
                else:
                    raise ValueError(f"var {name!r} needs quantity or known_id")
                kind = spec.get("kind") or spec.get("type")
                if isinstance(kind, str) and kind.strip():
                    entry["kind"] = kind.strip()
                out[str(name)] = entry
            else:
                raise ValueError(f"var {name!r} must be str or object")
        return out
    items = raw if isinstance(raw, list) else [raw]
    out = {}
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"bad --var {item!r}")
        s = item.strip()
        if "=" in s:
            name, rest = s.split("=", 1)
        elif ":" in s:
            name, rest = s.split(":", 1)
        else:
            raise ValueError(
                f"var must be name=quantity or name=quantity:kind, got {item!r}"
            )
        name = name.strip()
        rest = rest.strip()
        if not name.isidentifier():
            raise ValueError(f"var name must be identifier, got {name!r}")
        kind = None
        if rest.startswith("known:"):
            known_id = rest.removeprefix("known:").strip()
            if not known_id:
                raise ValueError(f"var {name!r} missing known id")
            out[name] = {"known_id": known_id}
            continue
        if ":" in rest:
            quantity, kind = rest.rsplit(":", 1)
            quantity, kind = quantity.strip(), kind.strip()
        else:
            quantity = rest
        if not quantity:
            raise ValueError(f"var {name!r} missing quantity")
        entry = {"quantity": quantity}
        if kind:
            if kind not in ("number", "boolean"):
                raise ValueError(f"var kind must be number|boolean, got {kind!r}")
            entry["kind"] = kind
        out[name] = entry
    return out


def validate_formula_fields(
    expression: Any, vars_spec: Any
) -> list[str]:
    blocks: list[str] = []
    if not isinstance(expression, str) or not expression.strip():
        blocks.append("type=formula requires expression (non-empty string)")
    else:
        try:
            validate_expression_ast(expression.strip())
        except ValueError as e:
            blocks.append(f"expression: {e}")
    if not isinstance(vars_spec, dict) or not vars_spec:
        blocks.append(
            "type=formula requires vars object "
            "(name → {quantity, optional kind number|boolean})"
        )
    else:
        for name, spec in vars_spec.items():
            if not isinstance(name, str) or not name.isidentifier():
                blocks.append(f"vars key {name!r} must be an identifier")
                continue
            if not isinstance(spec, dict):
                blocks.append(f"vars[{name}] must be an object")
                continue
            q = spec.get("quantity")
            known_id = spec.get("known_id")
            has_q = isinstance(q, str) and bool(q.strip())
            has_known = isinstance(known_id, str) and bool(known_id.strip())
            if has_q == has_known:
                blocks.append(
                    f"vars[{name}] needs exactly one of quantity or known_id"
                )
            kind = spec.get("kind") or spec.get("type")
            if has_known and kind is not None:
                blocks.append(f"vars[{name}].kind is inferred from its known")
            elif kind is not None and kind not in ("number", "boolean"):
                blocks.append(
                    f"vars[{name}].kind must be number|boolean when set"
                )
    return blocks


# Op symbol + its mirror (for flipping when the var is on the right side).
_OP_SYMBOL: dict[type, str] = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=",
}
_OP_MIRROR: dict[type, type] = {
    ast.Lt: ast.Gt, ast.Gt: ast.Lt, ast.LtE: ast.GtE, ast.GtE: ast.LtE,
    ast.Eq: ast.Eq, ast.NotEq: ast.NotEq,
}


def _threshold_operand_var(node: ast.AST) -> str | None:
    """A bare var, or a value-aggregate over one var (mean/min/max/sum(var)).

    Returns the var name if this operand refers to a single variable's value.
    n()/rate()/std() are NOT value thresholds (count/spread), so return None.
    """
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("mean", "min", "max", "sum")
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    ):
        return node.args[0].id
    return None


def extract_thresholds(expression: str) -> list[dict[str, Any]]:
    """Pull value-threshold comparisons (var OP constant) out of a formula.

    Used by the design self-check to compare a gate's bar against the accepted
    baseline. Only comparisons where one side refers to a single var's value
    and the other is a numeric constant are returned, normalized so the var is
    on the left: {var, op (ast type), op_symbol, bound}. n()/rate()/std()
    guards and var-to-var comparisons are ignored (not value bars).
    """
    tree = validate_expression_ast(expression)
    out: list[dict[str, Any]] = []

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, op, right = node.left, node.ops[0], node.comparators[0]
            lvar = _threshold_operand_var(left)
            rvar = _threshold_operand_var(right)
            lconst = isinstance(left, ast.Constant) and isinstance(
                left.value, (int, float)
            ) and not isinstance(left.value, bool)
            rconst = isinstance(right, ast.Constant) and isinstance(
                right.value, (int, float)
            ) and not isinstance(right.value, bool)
            if lvar and rconst:
                out.append({
                    "var": lvar, "op": type(op),
                    "op_symbol": _OP_SYMBOL[type(op)], "bound": right.value,
                })
            elif rvar and lconst and type(op) in _OP_MIRROR:
                mop = _OP_MIRROR[type(op)]
                out.append({
                    "var": rvar, "op": mop,
                    "op_symbol": _OP_SYMBOL[mop], "bound": left.value,
                })
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(tree.body)
    return out


def satisfies_threshold(value: float, op: type, bound: float) -> bool:
    """Does `value OP bound` hold? (op is an ast comparison type.)"""
    return bool(_COMP_OPS[op](value, bound))


def validate_expression_ast(expression: str) -> ast.Expression:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e}") from e
    if not isinstance(tree, ast.Expression):
        raise ValueError("must be a single expression")
    _assert_allowed(tree.body)
    return tree


def _assert_allowed(node: ast.AST) -> None:
    allowed = (
        ast.Expression,
        ast.BoolOp,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.And,
        ast.Or,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    )
    if not isinstance(node, allowed):
        raise ValueError(f"disallowed syntax: {type(node).__name__}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("only simple function calls allowed")
        fname = node.func.id
        if fname not in FORMULA_FUNCS:
            raise ValueError(
                f"unknown function {fname!r}; allow: {sorted(FORMULA_FUNCS)}"
            )
        if len(node.args) != 1 or node.keywords:
            raise ValueError(f"{fname}() takes exactly one argument (a var name)")
        if not isinstance(node.args[0], ast.Name):
            raise ValueError(f"{fname}() argument must be a var name")
    if isinstance(node, ast.Name):
        # bare names only ok as call args; bare name alone is ok as bool-ish if we allow
        pass
    for child in ast.iter_child_nodes(node):
        _assert_allowed(child)


def _agg(fn: str, values: list[Any]) -> Any:
    if fn == "n":
        return float(len(values))
    if fn == "rate":
        bools = [v for v in values if isinstance(v, bool)]
        if not bools:
            return None
        return float(sum(1 for v in bools if v)) / float(len(bools))
    nums = [
        float(v)
        for v in values
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if not nums:
        return None
    if fn == "mean":
        return float(statistics.fmean(nums))
    if fn == "min":
        return float(min(nums))
    if fn == "max":
        return float(max(nums))
    if fn == "sum":
        return float(sum(nums))
    if fn == "std":
        if len(nums) < 2:
            return None
        return float(statistics.stdev(nums))
    raise ValueError(f"unknown func {fn}")


def evaluate_expression(
    expression: str,
    env: dict[str, list[Any]],
) -> tuple[Any, dict[str, Any]]:
    """Evaluate allowlisted expression. Returns (value, bindings snapshot)."""
    tree = validate_expression_ast(expression)
    bindings: dict[str, Any] = {}

    def eval_node(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool)):
                return node.value
            raise ValueError(f"constant type not allowed: {type(node.value)}")
        if isinstance(node, ast.Name):
            # bare var → rate-like if bools else mean of numbers if present
            vals = env.get(node.id)
            if vals is None:
                raise ValueError(f"unknown var {node.id!r}")
            if vals and all(isinstance(v, bool) for v in vals):
                b = _agg("rate", vals)
            else:
                b = _agg("mean", vals)
            bindings[node.id] = b
            return b
        if isinstance(node, ast.UnaryOp):
            v = eval_node(node.operand)
            if v is None:
                return None
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.UAdd):
                return v
            if isinstance(node.op, ast.Not):
                return not bool(v)
            raise ValueError("unsupported unary op")
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ValueError("unsupported binary op")
            a, b = eval_node(node.left), eval_node(node.right)
            if a is None or b is None:
                return None
            return op(a, b)
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                for v in node.values:
                    r = eval_node(v)
                    if r is None:
                        return None
                    if not r:
                        return False
                return True
            if isinstance(node.op, ast.Or):
                for v in node.values:
                    r = eval_node(v)
                    if r is None:
                        continue
                    if r:
                        return True
                return False
            raise ValueError("unsupported bool op")
        if isinstance(node, ast.Compare):
            left = eval_node(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = eval_node(comp)
                if left is None or right is None:
                    return None
                fn = _COMP_OPS.get(type(op))
                if fn is None:
                    raise ValueError("unsupported comparison")
                if not fn(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)
            assert isinstance(node.args[0], ast.Name)
            fname = node.func.id
            vname = node.args[0].id
            vals = env.get(vname)
            if vals is None:
                raise ValueError(f"unknown var {vname!r} in {fname}()")
            result = _agg(fname, vals)
            bindings[f"{fname}({vname})"] = result
            return result
        raise ValueError(f"cannot evaluate {type(node).__name__}")

    value = eval_node(tree.body)
    return value, bindings


def recompute_formula_node(
    record: dict[str, Any],
    *,
    project_root,
    run_dir_fn,
) -> dict[str, Any]:
    """Rebuild formula stats from linked runs."""
    import json

    from .probe_run import RUN_META_NAME

    expression = (record.get("expression") or "").strip()
    vars_spec = record.get("vars") or {}
    if not isinstance(vars_spec, dict):
        vars_spec = {}

    env: dict[str, list[Any]] = {str(k): [] for k in vars_spec}
    by_run: list[dict[str, Any]] = []
    k_hold = 0
    k_fail = 0
    k_skip = 0
    n_data_runs = 0

    known_values: dict[str, Any] = {}
    input_assumptions: set[str] = set()
    for name, spec in vars_spec.items():
        if not isinstance(spec, dict) or not spec.get("known_id"):
            continue
        from .readings import read_known

        reading = read_known(
            project_root,
            str(spec["known_id"]),
            min_conf="low",
            consumer=f"formula:{record.get('id') or 'unknown'}",
            record=False,
        )
        known_values[str(name)] = reading.get("value")
        input_assumptions.update(reading.get("assumptions") or [])

    for rid in record.get("run_ids") or []:
        if not isinstance(rid, str):
            continue
        rdir = run_dir_fn(project_root, rid)
        if not rdir.is_dir():
            from .paths import find_run_dir

            visible = find_run_dir(project_root, rid)
            if visible is not None:
                rdir = visible[0]
        meta_path = rdir / RUN_META_NAME
        entry: dict[str, Any] = {"run_id": rid}
        if not meta_path.is_file():
            entry["missing"] = True
            k_skip += 1
            by_run.append(entry)
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entry["missing"] = True
            k_skip += 1
            by_run.append(entry)
            continue
        if meta.get("voided"):
            entry["voided"] = True
            k_skip += 1
            by_run.append(entry)
            continue
        from .run_inputs import run_input_state

        input_state = run_input_state(project_root, meta)
        if input_state["stale"]:
            entry["stale_inputs"] = True
            entry["stale_reasons"] = input_state["reasons"]
            k_skip += 1
            by_run.append(entry)
            continue
        input_assumptions.update(input_state["assumptions"])

        run_env: dict[str, list[Any]] = {}
        any_vals = False
        for name, spec in vars_spec.items():
            if not isinstance(spec, dict):
                continue
            q = str(spec.get("quantity") or "").strip()
            kind = (spec.get("kind") or spec.get("type") or "").strip() or None
            name_s = str(name)
            if name_s in known_values:
                value = known_values[name_s]
                vals = [] if value is None else [value]
            elif kind == "boolean":
                vals: list[Any] = extract_boolean_measures_from_run_meta(
                    meta, quantity=q
                )
            elif kind == "number":
                vals = extract_number_measures_from_run_meta(meta, quantity=q)
            else:
                nums = extract_number_measures_from_run_meta(meta, quantity=q)
                vals = (
                    nums
                    if nums
                    else extract_boolean_measures_from_run_meta(meta, quantity=q)
                )
            run_env[name_s] = vals
            if name_s in env:
                env[name_s].extend(vals)
            if vals:
                any_vals = True

        entry["vars"] = {k: list(v) for k, v in run_env.items()}
        if not any_vals or not expression:
            entry["holds"] = None
            k_skip += 1
            by_run.append(entry)
            continue

        n_data_runs += 1
        try:
            # Per-run: treat each var's samples in this run as the vector
            val, binds = evaluate_expression(expression, run_env)
            holds = bool(val) if val is not None else None
            entry["value"] = val
            entry["bindings"] = binds
            entry["holds"] = holds
            if holds is True:
                k_hold += 1
            elif holds is False:
                k_fail += 1
            else:
                k_skip += 1
        except ValueError as e:
            entry["error"] = str(e)
            entry["holds"] = None
            k_skip += 1
        by_run.append(entry)

    stats = empty_formula_stats()
    stats["n"] = n_data_runs
    stats["k_hold"] = k_hold
    stats["k_fail"] = k_fail
    stats["k_skip"] = k_skip
    stats["by_run"] = by_run
    judged = k_hold + k_fail
    if judged > 0:
        stats["holds_rate"] = float(k_hold) / float(judged)
    else:
        stats["holds_rate"] = None

    error = None
    holds_agg = None
    value_agg = None
    bindings_agg: dict[str, Any] = {}
    for name, value in known_values.items():
        if value is not None:
            env[name] = [value]
    if expression and any(env.values()):
        try:
            value_agg, bindings_agg = evaluate_expression(expression, env)
            holds_agg = bool(value_agg) if value_agg is not None else None
        except ValueError as e:
            error = str(e)
    stats["value"] = value_agg
    stats["bindings"] = bindings_agg
    stats["holds"] = holds_agg
    stats["error"] = error

    record = dict(record)
    record["stats"] = stats
    record["conditional"] = bool(input_assumptions)
    record["assumptions"] = sorted(input_assumptions)
    record["confidence_derived"] = derive_confidence_formula(stats)
    claimed = record.get("confidence") or "low"
    if claimed not in CONFIDENCE_SET:
        claimed = "low"
    if confidence_rank(claimed) > confidence_rank(record["confidence_derived"]):
        record["confidence"] = record["confidence_derived"]
    else:
        record["confidence"] = claimed
    return record


def derive_confidence_formula(stats: dict[str, Any]) -> str:
    """Derive confidence in the verdict, independently of pass/fail."""
    n = int(stats.get("n") or 0)
    if n < 1 or stats.get("error"):
        return "low"
    if n >= 5:
        return "high"
    if n >= 3:
        return "med"
    return "low"


def can_claim_formula_confidence(
    stats: dict[str, Any], want: str
) -> tuple[bool, str]:
    if want not in CONFIDENCE_SET:
        return False, f"confidence must be one of {sorted(CONFIDENCE_SET)}"
    derived = derive_confidence_formula(stats)
    if confidence_rank(want) <= confidence_rank(derived):
        return True, derived
    return (
        False,
        f"cannot claim confidence={want!r} for formula with n={stats.get('n')}, "
        f"holds={stats.get('holds')}, holds_rate={stats.get('holds_rate')} "
        f"(derived max is {derived!r})",
    )
