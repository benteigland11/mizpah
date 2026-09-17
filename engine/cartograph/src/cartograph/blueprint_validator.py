"""
Blueprint validator pipeline.

A blueprint validates by materializing its declared dep widgets into a
temp sandbox at `<sandbox>/cg/<dep_dir>/`, then running the blueprint's
own tests and example against that populated sandbox.

Sandbox layout:
    <tmp>/<blueprint_basename>/
        blueprint.json
        src/...
        tests/...
        examples/...
        cg/<dep_dir>/...        (copied from local library)
        .venv/                   (created by PythonEngine.install_deps)

Single sandbox: one install, one verdict. If any step fails, the whole
blueprint validation fails.

Engine-driven: each language engine supplies blueprint_scaffold,
src_import_pattern, file_ext, install_deps, run_tests, and
run_blueprint_example so any installed engine can host a blueprint.
"""

import json
import logging
import os
import re
import shutil
import tempfile

log = logging.getLogger("cartograph")


def validate_blueprint(carto, path: str) -> dict:
    """Validate a blueprint directory. Returns {status, message, ...}."""
    from .manifest import detect_kind, KIND_BLUEPRINT, ManifestError, load_manifest

    if not os.path.exists(path):
        return {"status": "error", "message": f"Path not found: {path}"}

    try:
        kind = detect_kind(path)
    except ManifestError as e:
        return {"status": "error", "message": str(e)}
    if kind != KIND_BLUEPRINT:
        return {
            "status": "error",
            "message": f"Not a blueprint (no blueprint.json): {path}",
        }

    try:
        manifest = load_manifest(path)
    except ManifestError as e:
        return {"status": "error", "message": f"blueprint.json: {e}"}

    raw = manifest.raw

    # 1. Schema validation.
    from .blueprints import validate_blueprint_manifest, derive_domains
    from .languages.registry import supported_languages
    from .validator import VALID_DOMAINS

    project_widgets = _scan_project_cg(path)
    schema = validate_blueprint_manifest(
        raw,
        supported_languages=set(supported_languages()),
        valid_domains=VALID_DOMAINS,
        library_widget_ids=set(project_widgets.keys()),
    )
    if not schema["ok"]:
        return {
            "status": "error",
            "message": "blueprint.json schema errors:\n  - "
                       + "\n  - ".join(schema["errors"]),
            "errors": schema["errors"],
        }

    language = manifest.language.lower()
    from .languages import get_engine
    engine = get_engine(language)
    if engine is None or not engine.supported:
        return {
            "status": "error",
            "message": (
                f"No language engine available for blueprint validation: "
                f"language='{language}'. Run `cartograph doctor`."
            ),
        }
    ok, msg = engine.check_available()
    if not ok:
        return {
            "status": "error",
            "message": (
                f"Language engine '{language}' is not ready: {msg}"
            ),
        }
    ext = engine.file_ext or "py"
    src_import_re = engine.src_import_pattern() or r''

    # 2. Structure.
    for folder in ("src", "tests", "examples"):
        folder_path = os.path.join(path, folder)
        if not os.path.isdir(folder_path) or not os.listdir(folder_path):
            return {
                "status": "error",
                "message": f"{folder}/ is missing or empty",
            }

    # 3. Example: exists, no [TODO], imports from src/.
    example_dir = os.path.join(path, "examples")
    examples_in_lang = [
        f for f in os.listdir(example_dir) if f.endswith(f".{ext}")
    ]
    if not examples_in_lang:
        return {"status": "error",
                "message": f"examples/ contains no .{ext} files"}
    example_file = examples_in_lang[0]
    example_path = os.path.join(example_dir, example_file)
    example_content = open(example_path, encoding="utf-8").read()
    if "[TODO]" in example_content:
        return {
            "status": "error",
            "message": f"examples/{example_file} still contains [TODO] placeholders",
        }
    if src_import_re and not re.search(src_import_re, example_content, re.MULTILINE):
        return {
            "status": "error",
            "message": (
                f"examples/{example_file} does not import from src/. "
                "The example must exercise the blueprint."
            ),
        }

    # 4. Tests exist. Defer to the engine so language-specific naming
    # conventions (PHPUnit's *Test.php, etc.) are respected.
    tests_dir = os.path.join(path, "tests")
    test_files = engine.find_test_files(path)
    if not test_files:
        return {
            "status": "error",
            "message": f"tests/ contains no test files (extension .{ext})",
        }

    # 5. Contamination (kind-aware: blueprint sealed-API rules).
    from .contamination import scan_contamination
    contam = scan_contamination(path)
    if contam["blocks"]:
        return {
            "status": "error",
            "message": "Contamination scan failed - hard blocks found.",
            "blocks": contam["blocks"],
        }
    contam_warnings = list(contam.get("warnings", []))

    # 6. Resolve deps to local source paths (project cg/, then library).
    deps = raw.get("dependencies", [])
    resolved_deps = _resolve_dep_sources(carto, deps, project_widgets)
    if resolved_deps.get("error"):
        return {"status": "error", "message": resolved_deps["error"]}

    # 7. Build sandbox: temp dir + copy of blueprint + each dep into cg/.
    from .engine import python_dir_name
    # realpath resolves Windows 8.3 short-names (e.g. RUNNER~1 -> runneradmin)
    # to their long form. Without this, vitest+v8 coverage records hits under
    # the long path while the include-glob enumerates under the short path,
    # producing a phantom 0% report on Windows. No-op on POSIX.
    sandbox_root = os.path.realpath(
        tempfile.mkdtemp(prefix="cartograph_bp_validate_")
    )
    sandbox = os.path.join(sandbox_root, os.path.basename(path.rstrip(os.sep)))
    try:
        # Copy the blueprint itself, but skip any pre-existing .venv / cg / __pycache__.
        shutil.copytree(
            path,
            sandbox,
            ignore=shutil.ignore_patterns(".venv", "cg", "__pycache__",
                                          ".pytest_cache", "*.pyc"),
        )

        cg_root = os.path.join(sandbox, "cg")
        os.makedirs(cg_root, exist_ok=True)
        for dep_id, src_path in resolved_deps["sources"].items():
            dep_dir = os.path.join(cg_root, python_dir_name(dep_id))
            shutil.copytree(
                src_path,
                dep_dir,
                ignore=shutil.ignore_patterns(".venv", "__pycache__",
                                              ".pytest_cache", "history"),
            )

        # 8. Install runtime deps (union from dep widgets) via the engine.
        runtime_deps = _collect_runtime_deps(resolved_deps["sources"])
        try:
            engine.install_deps(sandbox, runtime_deps)
        except Exception as e:
            return {
                "status": "error",
                "message": f"Dependency install failed: {e}",
            }

        # 9. Run the example.
        try:
            ex_result = engine.run_blueprint_example(sandbox, example_file)
        except Exception as e:
            return {
                "status": "error",
                "message": f"Example runner crashed: {e}",
            }
        if not ex_result["passed"]:
            return {
                "status": "error",
                "message": f"examples/{example_file} failed to run.",
                "test_output": (ex_result.get("error") or "")[:3000],
            }

        # 10. Run tests with --cov=src (the blueprint's own src/, not deps).
        try:
            test_result = engine.run_tests(sandbox)
        except Exception as e:
            return {
                "status": "error",
                "message": f"Test runner crashed: {e}",
            }
        if not test_result["passed"]:
            err = test_result.get("error", "")
            if ("does not meet global threshold" in err
                or "Required test coverage of" in err
                or ("coverage" in err.lower() and "threshold" in err.lower())):
                msg = "Coverage below threshold - add tests to reach 80%."
            else:
                msg = "Tests failed. Fix before checkin."
            return {
                "status": "error",
                "message": msg,
                "test_output": err[:3000],
            }

        # 11. Domains: derived from dep widgets (informational; checkin will
        #     write them. Schema accepts current value as-is.)
        derived = derive_domains(deps, lambda i: _read_widget_meta(
            project_widgets.get(i, {}).get("path")))

        result = {
            "status": "success",
            "message": "Blueprint is valid",
            "kind": "blueprint",
            "id": raw["id"],
            "language": language,
            "deps": [{"id": d["id"], "version": d["version"]} for d in deps],
            "derived_domains": derived,
        }
        if contam_warnings:
            result["warnings"] = contam_warnings
        return result
    finally:
        try:
            shutil.rmtree(sandbox_root, ignore_errors=True)
        except Exception as e:
            log.error("Sandbox cleanup failed for %s: %s", sandbox_root, e)
        try:
            engine.cleanup(sandbox)
        except Exception:
            pass


def _read_widget_meta(widget_dir: str | None) -> dict | None:
    """Return the widget.json dict for a widget directory, or None."""
    if not widget_dir:
        return None
    wjson = os.path.join(widget_dir, "widget.json")
    if not os.path.isfile(wjson):
        return None
    try:
        with open(wjson, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _scan_project_cg(blueprint_path: str) -> dict:
    """Map widget_id -> {"path": ..., "version": ...} for installs in the
    project's `cg/`.

    The blueprint scaffold places blueprints at `<project>/cg/<py_dir>/`,
    so the project's cg/ is the blueprint's parent directory. If the
    blueprint is not under a `cg/`, returns {} — validation falls back
    to the local library.
    """
    bp_abs = os.path.abspath(blueprint_path)
    parent = os.path.dirname(bp_abs)
    if os.path.basename(parent) != "cg":
        return {}
    out: dict[str, dict] = {}
    for entry in os.listdir(parent):
        if entry == os.path.basename(bp_abs):
            continue
        wdir = os.path.join(parent, entry)
        wjson = os.path.join(wdir, "widget.json")
        if not os.path.isfile(wjson):
            continue
        try:
            with open(wjson, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        meta = data.get("meta") or data
        wid = (meta.get("id") or "").strip()
        ver = (meta.get("version") or "").strip()
        if wid:
            out[wid] = {"path": wdir, "version": ver}
    return out


def _resolve_dep_sources(carto, deps: list, project_widgets: dict | None = None) -> dict:
    """Map each dep {id, version} to a local source dir.

    Returns {"sources": {dep_id: path}} on success, or {"error": str}.

    Strict: deps must be installed in the project's `cg/` at the pinned
    version. There is no library fallback — the same widgets the author
    runs with locally are the ones the validator uses.

    `carto` is unused but kept for signature stability with older callers.
    """
    _ = carto
    project_widgets = project_widgets or {}
    sources: dict[str, str] = {}
    for dep in deps:
        dep_id = dep.get("id")
        pinned = dep.get("version")
        proj = project_widgets.get(dep_id)
        if proj is None:
            return {"error": (
                f"Dependency '{dep_id}' is not installed in this project's "
                "cg/. Run `cartograph install <id>` from the project root, "
                "then re-validate."
            )}
        if proj.get("version") != pinned:
            return {"error": (
                f"Dependency '{dep_id}' pinned to '{pinned}', but the "
                f"project's cg/ has '{proj.get('version')}'. Reinstall the "
                "widget at the pinned version, or re-run `blueprint add-dep` "
                "to re-pin to what's installed."
            )}
        sources[dep_id] = proj["path"]
    return {"sources": sources}


def _collect_runtime_deps(dep_sources: dict[str, str]) -> list[str]:
    """Union of tech_stack.dependencies across all dep widgets.

    Reads each dep's widget.json directly from its resolved source path.
    Returns a deduplicated list of runtime dependency strings (pip / npm /
    nimble / etc. - whatever the engine's install_deps consumes).
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for dep_id, src_path in dep_sources.items():
        widget_json = os.path.join(src_path, "widget.json")
        if not os.path.isfile(widget_json):
            continue
        try:
            with open(widget_json, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log.debug("Could not read %s for pip deps: %s", widget_json, e)
            continue
        for d in data.get("tech_stack", {}).get("dependencies", []) or []:
            name = d if isinstance(d, str) else (d.get("name") or "")
            name = (name or "").strip()
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
    return ordered


