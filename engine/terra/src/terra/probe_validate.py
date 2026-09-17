"""Level-1 probe validation (design-time + contract exercise).

Level 1 bare minimum:
  - package/script is a real Python probe (id, purpose, entry, import)
  - run() accepts a target (`to`) and returns {to, status, artifacts}
  - `to` non-empty; status string; artifacts list
  - time/from are substrate concerns (not required from probe)

Does not stamp runs or survey the real world beyond calling run() once
with a synthetic fixture target (dry_run=True).
"""

from __future__ import annotations

import ast
import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .paths import find_project_root
from .probe_load import load_probe_module, probe_sys_path

from .probe_contract import (
    LEVEL1_CTX,
    LEVEL1_RUN_TIMEOUT_S,
    PROBE_ENTRY_DEFAULT,
    PROBE_LANGUAGE,
    PROBE_META_NAME,
    PROBE_RESULT_KEYS,
    PROBE_SCHEMA_VERSION,
    PROBE_SCRIPT_NAME,
    REQUIRED_EXPORT_KEYS,
    STEP_EXECUTE,
    STEP_INPUT,
    STEP_OUTPUT,
    SUBSTRATE_KEYS,
    VALIDATION_LEVEL,
    validate_kind_in_script,
    validate_kind_meta,
    validate_probe_input_level1,
    validate_probe_output_level1,
)
from .watch_ctx import build_watch_ctx

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _result(
    *,
    ok: bool,
    probe_id: str | None,
    path: Path | None,
    blocks: list[str],
    warnings: list[str],
    meta: dict[str, Any] | None = None,
    level: int = VALIDATION_LEVEL,
    exercise: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "level": level,
        "id": probe_id,
        "path": str(path) if path else None,
        "blocks": blocks,
        "warnings": warnings,
        "meta": meta,
        "exercise": exercise,
    }


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"probe.json is not valid JSON: {e}"
    except OSError as e:
        return None, f"cannot read probe.json: {e}"
    if not isinstance(data, dict):
        return None, "probe.json must be a JSON object"
    return data, None


def _parse_entry(entry: str) -> tuple[str, str] | None:
    if not isinstance(entry, str) or ":" not in entry:
        return None
    script, _, attr = entry.partition(":")
    script, attr = script.strip(), attr.strip()
    if not script or not attr or not attr.isidentifier():
        return None
    return script, attr


def _ast_has_function(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return True
    return False


def _import_probe_module(
    script_path: Path, module_name: str, *, project_root: Path | None = None
) -> tuple[ModuleType | None, str | None]:
    root = project_root or find_project_root(script_path) or script_path.parent
    return load_probe_module(root, script_path, module_name=module_name)


def _check_required_exports(mod: ModuleType) -> list[str]:
    blocks: list[str] = []
    declared = getattr(mod, "REQUIRED_EXPORTS", None)
    if declared is None:
        blocks.append(
            "level1: probe module must define REQUIRED_EXPORTS "
            f"(iterable including {sorted(REQUIRED_EXPORT_KEYS)})"
        )
        return blocks
    try:
        keys = frozenset(declared)
    except TypeError:
        blocks.append("level1: REQUIRED_EXPORTS must be an iterable of strings")
        return blocks
    missing = REQUIRED_EXPORT_KEYS - keys
    if missing:
        blocks.append(
            f"level1: REQUIRED_EXPORTS missing {sorted(missing)} "
            f"(need at least {sorted(REQUIRED_EXPORT_KEYS)})"
        )
    return blocks


def _call_run(
    fn: Callable[..., Any],
    ctx: dict[str, Any],
    *,
    project_root: Path | None = None,
    probe_package: Path | None = None,
) -> tuple[Any, str | None]:
    """Call run(ctx) with timeout. Input must not be silently dropped.

    If run() cannot accept ctx, that is an **input-path** failure (non-silent):
    we do not fall back to run() with no args.
    """

    def _invoke() -> Any:
        if project_root is not None:
            with probe_sys_path(project_root, probe_package):
                return fn(ctx)
        return fn(ctx)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_invoke)
            return fut.result(timeout=LEVEL1_RUN_TIMEOUT_S), None
    except FuturesTimeout:
        return None, (
            f"level1/{STEP_EXECUTE}: run() exceeded {LEVEL1_RUN_TIMEOUT_S}s timeout "
            "(probes must be fast under dry_run=True)"
        )
    except TypeError as e:
        # Common: def run(): ... ignores ctx — would silently drop `to`
        return None, (
            f"level1/{STEP_INPUT}: run() must accept ctx so input 'to' is not dropped "
            f"({type(e).__name__}: {e})"
        )
    except Exception as e:  # noqa: BLE001
        return None, (
            f"level1/{STEP_EXECUTE}: run() raised {type(e).__name__}: {e}"
        )


def exercise_run_level1(
    fn: Callable[..., Any],
    *,
    ctx: dict[str, Any] | None = None,
    project_root: Path | None = None,
    probe_package: Path | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Level-1 I/O validation: input → execute → output. Failures never silent.

    Returns (blocks, warnings, exercise_report).
    exercise_report always includes per-step ok flags for CLI loudness.
    """
    blocks: list[str] = []
    warnings: list[str] = []
    ctx = dict(ctx if ctx is not None else LEVEL1_CTX)
    # Level-1 is always snapshot-style; still expose watch_mode for probes that branch
    ctx.setdefault("dry_run", True)
    ctx.setdefault("_terra_validation", "level1")
    if project_root is not None and probe_package is not None:
        try:
            import json as _json

            meta_path = probe_package / PROBE_META_NAME
            if meta_path.is_file():
                meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                for k, v in build_watch_ctx(meta, dry_run=True).items():
                    ctx.setdefault(k, v)
        except (OSError, ValueError, TypeError):
            pass

    input_blocks = validate_probe_input_level1(ctx)
    steps: dict[str, Any] = {
        STEP_INPUT: {"ok": len(input_blocks) == 0, "blocks": list(input_blocks)},
        STEP_EXECUTE: {"ok": None, "blocks": []},  # None = skipped
        STEP_OUTPUT: {"ok": None, "blocks": []},
    }
    blocks.extend(input_blocks)

    exercise: dict[str, Any] = {
        "steps": steps,
        "ctx_to": ctx.get("to") if isinstance(ctx, dict) else None,
        "result_keys": None,
        "status": None,
        "to": None,
        "artifact_count": None,
    }

    # Non-silent: do not call run() with invalid input
    if input_blocks:
        steps[STEP_EXECUTE]["ok"] = False
        steps[STEP_EXECUTE]["blocks"] = [
            f"level1/{STEP_EXECUTE}: skipped because input validation failed"
        ]
        steps[STEP_OUTPUT]["ok"] = False
        steps[STEP_OUTPUT]["blocks"] = [
            f"level1/{STEP_OUTPUT}: skipped because input validation failed"
        ]
        blocks.extend(steps[STEP_EXECUTE]["blocks"])
        blocks.extend(steps[STEP_OUTPUT]["blocks"])
        return blocks, warnings, exercise

    raw, err = _call_run(
        fn, ctx, project_root=project_root, probe_package=probe_package
    )
    if err:
        steps[STEP_EXECUTE]["ok"] = False
        steps[STEP_EXECUTE]["blocks"] = [err]
        blocks.append(err)
        # Input-path TypeError already labeled input; still mark output skipped
        if f"/{STEP_INPUT}:" in err:
            steps[STEP_INPUT]["ok"] = False
            steps[STEP_INPUT]["blocks"].append(err)
        steps[STEP_OUTPUT]["ok"] = False
        steps[STEP_OUTPUT]["blocks"] = [
            f"level1/{STEP_OUTPUT}: skipped because execute failed"
        ]
        blocks.extend(steps[STEP_OUTPUT]["blocks"])
        return blocks, warnings, exercise

    steps[STEP_EXECUTE]["ok"] = True

    output_blocks = validate_probe_output_level1(raw)
    steps[STEP_OUTPUT]["ok"] = len(output_blocks) == 0
    steps[STEP_OUTPUT]["blocks"] = list(output_blocks)
    blocks.extend(output_blocks)

    if isinstance(raw, dict):
        exercise["result_keys"] = list(raw.keys())
        exercise["status"] = raw.get("status")
        exercise["to"] = raw.get("to")
        exercise["artifact_count"] = (
            len(raw["artifacts"]) if isinstance(raw.get("artifacts"), list) else None
        )
        leaked = sorted(SUBSTRATE_KEYS & frozenset(raw.keys()))
        if leaked:
            warnings.append(
                f"level1/{STEP_OUTPUT}: result includes substrate fields {leaked} — "
                "time/from will be stamped by Terra on real runs; prefer omitting"
            )

    return blocks, warnings, exercise


def _validate_module_level1(
    mod: ModuleType,
    attr: str,
    *,
    purpose_from_meta: str | None = None,
    meta_kind: str | None = None,
    meta_duration_s: float | None = None,
    project_root: Path | None = None,
    probe_package: Path | None = None,
) -> tuple[list[str], list[str], dict[str, Any] | None]:
    blocks: list[str] = []
    warnings: list[str] = []
    exercise = None

    fn = getattr(mod, attr, None)
    if not callable(fn):
        blocks.append(f"level1: entry attribute {attr!r} is not callable")
        return blocks, warnings, None

    blocks.extend(_check_required_exports(mod))
    blocks.extend(
        validate_kind_in_script(
            mod, meta_kind=meta_kind, meta_duration_s=meta_duration_s
        )
    )

    if not getattr(mod, "PURPOSE", None) and purpose_from_meta:
        warnings.append(
            "optional: set PURPOSE = \"…\" in probe.py to mirror probe.json"
        )

    # Only exercise run if export/kind declaration ok enough
    if not blocks:
        ex_blocks, ex_warns, exercise = exercise_run_level1(
            fn, project_root=project_root, probe_package=probe_package
        )
        blocks.extend(ex_blocks)
        warnings.extend(ex_warns)

    return blocks, warnings, exercise


def validate_probe_dir(probe_path: Path) -> dict[str, Any]:
    """Level-1 validate a probe package directory."""
    blocks: list[str] = []
    warnings: list[str] = []
    exercise = None
    probe_path = probe_path.resolve()
    dir_name = probe_path.name

    if not probe_path.is_dir():
        return _result(
            ok=False,
            probe_id=dir_name,
            path=probe_path,
            blocks=[f"not a directory: {probe_path}"],
            warnings=[],
        )

    meta_path = probe_path / PROBE_META_NAME
    if not meta_path.is_file():
        return _result(
            ok=False,
            probe_id=dir_name,
            path=probe_path,
            blocks=[f"missing {PROBE_META_NAME}"],
            warnings=[],
        )

    meta, err = _load_json(meta_path)
    if err:
        return _result(
            ok=False,
            probe_id=dir_name,
            path=probe_path,
            blocks=[err],
            warnings=[],
        )
    assert meta is not None

    if meta.get("schema_version") != PROBE_SCHEMA_VERSION:
        blocks.append(
            f"schema_version must be {PROBE_SCHEMA_VERSION}, "
            f"got {meta.get('schema_version')!r}"
        )

    pid = meta.get("id")
    if not isinstance(pid, str) or not pid.strip():
        blocks.append("id must be a non-empty string")
        pid = dir_name
    elif pid != dir_name:
        blocks.append(f"id {pid!r} does not match directory name {dir_name!r}")
    elif not _SLUG_RE.match(pid):
        blocks.append(
            f"id {pid!r} must be a slug: start with a-z, then a-z0-9_ only"
        )

    purpose = meta.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        blocks.append(
            "purpose must be a non-empty string (what mystery this reduces)"
        )

    lang = meta.get("language", PROBE_LANGUAGE)
    if lang != PROBE_LANGUAGE:
        blocks.append(f"language must be {PROBE_LANGUAGE!r} in v0, got {lang!r}")

    kind_blocks, kind_info = validate_kind_meta(meta)
    blocks.extend(kind_blocks)
    from .map_inputs import validate_map_bindings

    blocks.extend(validate_map_bindings(meta.get("inputs")))

    entry = meta.get("entry", PROBE_ENTRY_DEFAULT)
    parsed = _parse_entry(entry) if isinstance(entry, str) else None
    if parsed is None:
        blocks.append(f"entry must look like 'probe.py:run', got {entry!r}")
        script_name, attr = PROBE_SCRIPT_NAME, "run"
    else:
        script_name, attr = parsed

    script_path = probe_path / script_name
    if not script_path.is_file():
        blocks.append(f"entry script missing: {script_name}")
        return _result(
            ok=False,
            probe_id=pid if isinstance(pid, str) else dir_name,
            path=probe_path,
            blocks=blocks,
            warnings=warnings,
            meta=meta,
        )

    try:
        source = script_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(script_path))
    except SyntaxError as e:
        blocks.append(f"probe script syntax error: {e}")
        return _result(
            ok=False,
            probe_id=pid if isinstance(pid, str) else dir_name,
            path=probe_path,
            blocks=blocks,
            warnings=warnings,
            meta=meta,
        )

    if not _ast_has_function(tree, attr):
        blocks.append(
            f"probe script must define function {attr!r} (entry {entry!r})"
        )

    if not any("syntax error" in b for b in blocks) and _ast_has_function(tree, attr):
        mod_name = f"terra_probe_{dir_name}_{attr}"
        mod, import_err = _import_probe_module(script_path, mod_name)
        if import_err:
            blocks.append(import_err)
        else:
            assert mod is not None
            root = find_project_root(probe_path) or probe_path.parent
            m_blocks, m_warns, exercise = _validate_module_level1(
                mod,
                attr,
                purpose_from_meta=purpose if isinstance(purpose, str) else None,
                meta_kind=kind_info.get("kind"),
                meta_duration_s=kind_info.get("duration_s"),
                project_root=root,
                probe_package=probe_path,
            )
            blocks.extend(m_blocks)
            warnings.extend(m_warns)

    # Attach kind summary onto meta copy for list/show
    if isinstance(meta, dict) and kind_info.get("kind"):
        meta = dict(meta)
        meta["_kind_info"] = kind_info

    # Stamp the outcome against the current package hash so `probe run` can
    # tell a validated instrument from an edited one.
    from .probe_stamp import write_probe_stamp

    stamp = write_probe_stamp(
        probe_path,
        ok=len(blocks) == 0,
        probe_id=pid if isinstance(pid, str) else dir_name,
        level=VALIDATION_LEVEL,
        blocks=blocks,
        warnings=warnings,
    )

    result = _result(
        ok=len(blocks) == 0,
        probe_id=pid if isinstance(pid, str) else dir_name,
        path=probe_path,
        blocks=blocks,
        warnings=warnings,
        meta=meta,
        exercise=exercise,
    )
    result["stamp"] = {
        "source_sha256": stamp.get("source_sha256"),
        "validated_at": stamp.get("validated_at"),
        "ok": stamp.get("ok"),
    }
    return result


def validate_probe_script(
    script_path: Path,
    *,
    purpose: str | None = None,
    probe_id: str | None = None,
) -> dict[str, Any]:
    """Level-1 validate a bare .py probe script."""
    blocks: list[str] = []
    warnings: list[str] = []
    exercise = None
    script_path = script_path.resolve()

    if not script_path.is_file():
        return _result(
            ok=False,
            probe_id=probe_id,
            path=script_path,
            blocks=[f"not a file: {script_path}"],
            warnings=[],
        )
    if script_path.suffix != ".py":
        blocks.append(
            f"v0 only supports Python probes (.py), got {script_path.suffix!r}"
        )

    try:
        source = script_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(script_path))
    except SyntaxError as e:
        return _result(
            ok=False,
            probe_id=probe_id or script_path.stem,
            path=script_path,
            blocks=[f"syntax error: {e}"],
            warnings=warnings,
        )

    if not _ast_has_function(tree, "run"):
        blocks.append("level1: probe script must define function run(ctx) or run()")

    mod_name = f"terra_probe_file_{script_path.stem}"
    mod, import_err = _import_probe_module(script_path, mod_name)
    if import_err:
        blocks.append(import_err)
    elif mod is not None:
        mod_purpose = getattr(mod, "PURPOSE", None)
        if isinstance(mod_purpose, str) and mod_purpose.strip():
            purpose = purpose or mod_purpose
        elif not (purpose and purpose.strip()):
            warnings.append(
                "no purpose yet — set PURPOSE in the script or pass --purpose "
                "(required once packaged as probe.json)"
            )
        # Bare script: kind comes only from the module (no probe.json yet)
        script_kind = getattr(mod, "KIND", None)
        script_dur = None
        if script_kind == "watch" and hasattr(mod, "DURATION_S"):
            from .probe_contract import normalize_duration_s

            script_dur, _ = normalize_duration_s(getattr(mod, "DURATION_S"))
        m_blocks, m_warns, exercise = _validate_module_level1(
            mod,
            "run",
            meta_kind=script_kind if script_kind in ("run", "watch") else None,
            meta_duration_s=script_dur,
        )
        blocks.extend(m_blocks)
        warnings.extend(m_warns)

    if probe_id is None:
        probe_id = script_path.stem
        if not _SLUG_RE.match(probe_id):
            warnings.append(
                f"script stem {probe_id!r} is not a valid probe id slug; "
                "rename or set id when packaging"
            )

    return _result(
        ok=len(blocks) == 0,
        probe_id=probe_id,
        path=script_path,
        blocks=blocks,
        warnings=warnings,
        meta={
            "purpose": purpose,
            "language": PROBE_LANGUAGE,
            "entry": f"{script_path.name}:run",
            "schema_version": PROBE_SCHEMA_VERSION,
            "ingested": "script",
            "result_keys": sorted(PROBE_RESULT_KEYS),
        },
        exercise=exercise,
    )


def validate_all_probes(probes_root: Path) -> dict[str, Any]:
    """Level-1 validate every probe package under probes_root."""
    probes_root = probes_root.resolve()
    if not probes_root.is_dir():
        return {
            "ok": False,
            "level": VALIDATION_LEVEL,
            "blocks": [f"probes directory missing: {probes_root}"],
            "probes": [],
        }

    results = []
    for child in sorted(p for p in probes_root.iterdir() if p.is_dir()):
        if child.name.startswith(".") or child.name in ("__pycache__", "_lib"):
            continue
        if not (child / PROBE_META_NAME).is_file() and not (child / "probe.py").is_file():
            continue
        results.append(validate_probe_dir(child))

    store_blocks: list[str] = []
    if not results:
        store_blocks.append("no probes found under " + str(probes_root))

    any_fail = any(not r["ok"] for r in results)
    return {
        "ok": (not any_fail) and len(store_blocks) == 0,
        "level": VALIDATION_LEVEL,
        "blocks": store_blocks,
        "probes": results,
    }
