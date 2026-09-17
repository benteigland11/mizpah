"""
Widget validation against Gold Standards.

validate_item() checks directory structure, manifest schema, tests, examples,
language-specific rules, and implementation uniqueness before checkin.
"""

import glob
import json
import logging
import os
import re
import subprocess
import sys

log = logging.getLogger("cartograph")


def _load_domains() -> frozenset:
    """Load valid domains from library_config.json - single source of truth."""
    config_path = os.path.join(os.path.dirname(__file__), "library_config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            return frozenset(json.load(f)["domains"].keys())
    except Exception:
        # Fallback if config is missing/broken - should never happen in production
        return frozenset(["backend", "data", "ml", "security", "infra",
                          "frontend", "universal", "modeling"])

VALID_DOMAINS = _load_domains()


def validate_item(carto, path):
    """Validate a widget directory before checkin."""
    checklist = []
    errors = []

    def check(description, passed, error_detail=None):
        checklist.append(f"{'✅' if passed else '❌'} {description}")
        if not passed and error_detail:
            errors.append(error_detail)
        return passed

    # 1. Path exists
    if not check("Path exists", os.path.exists(path), f"Path not found: {path}"):
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error", "message": f"Path not found: {path}"}

    # 2. widget.json exists
    manifest_path = os.path.join(path, "widget.json")
    if not os.path.exists(manifest_path):
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error", "message": "Missing widget.json"}

    check("widget.json exists", True)

    # 3. Valid JSON, no TODOs
    try:
        content = open(manifest_path, encoding="utf-8").read()
        data = json.loads(content)
        check("widget.json is valid JSON", True)
    except (OSError, json.JSONDecodeError) as e:
        check("widget.json is valid JSON", False, str(e))
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error", "message": f"Invalid JSON: {e}"}

    todo_count = content.count("[TODO]")
    if not check("No [TODO] placeholders", todo_count == 0,
                 f"Found {todo_count} [TODO] placeholder(s) — fill them in"):
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error", "message": "Replace all [TODO] placeholders in widget.json"}

    # 4. Required meta fields
    meta = data.get("meta", {})
    for field in ("id", "name", "domain"):
        if not check(f"meta.{field} present", bool(meta.get(field)),
                     f"Missing required field meta.{field}"):
            _print_checklist(checklist, errors, failed=True)
            return {"status": "error", "message": f"meta.{field} is required"}

    # 5. Domain is a known value
    domain = meta.get("domain", "").lower()
    valid_domains = sorted(VALID_DOMAINS)
    if not check(f"meta.domain is valid ({domain})",
                 domain in VALID_DOMAINS,
                 f"'{domain}' is not a valid domain. Choose one of: {', '.join(valid_domains)}"):
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error",
                "message": f"Invalid domain '{domain}'. Valid domains: {', '.join(valid_domains)}"}

    # 6. tech_stack
    tech_stack = data.get("tech_stack", {})
    if not check("tech_stack.language present", bool(tech_stack.get("language")),
                 "Missing tech_stack.language"):
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error", "message": "Missing tech_stack.language"}

    if not check("tech_stack.dependencies present", "dependencies" in tech_stack,
                 "Missing tech_stack.dependencies (use [] if none)"):
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error", "message": "Missing tech_stack.dependencies (use [] if none)"}

    # 7. Required structure: src/, tests/, examples/
    for folder in ("src", "tests", "examples"):
        folder_path = os.path.join(path, folder)
        ok = os.path.isdir(folder_path) and bool(os.listdir(folder_path))
        if not check(f"{folder}/ exists and has files", ok,
                     f"{folder}/ is missing or empty"):
            _print_checklist(checklist, errors, failed=True)
            return {"status": "error", "message": f"{folder}/ is missing or empty"}

    # -- Resolve engine early so steps 7c/8 can use language-specific behaviour
    from .languages import get_engine
    language = tech_stack.get("language", "python").lower()
    dependencies = tech_stack.get("dependencies", [])
    engine = get_engine(language)

    # 6b. No widget-on-widget dependencies
    library_ids = {w["id"] for w in carto.widgets}
    widget_deps = [
        d["name"] if isinstance(d, dict) else str(d)
        for d in dependencies
        if (d["name"] if isinstance(d, dict) else str(d)) in library_ids
    ]
    if not check("No widget-on-widget dependencies", not widget_deps,
                 f"Dependencies cannot be other widgets: {', '.join(widget_deps)}. "
                 "Copy the needed logic into src/ instead."):
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error",
                "message": f"Widget depends on other widgets ({', '.join(widget_deps)}). "
                           "Widgets must be self-contained — copy the logic into src/ instead."}

    if engine is None or not engine.supported:
        from .languages.registry import supported_languages
        langs = ", ".join(supported_languages())
        if engine is None:
            msg = f"Unknown language '{language}'. Supported: {langs}."
            check(f"Language '{language}' recognised", False, msg)
        else:
            msg = f"'{language}' is not supported for validation. Supported: {langs}."
            check(f"Language '{language}' supported", False, msg)
        _print_checklist(checklist, errors, failed=True)
        return {"status": "error", "message": msg}

    contam_warnings = []

    # 6c. Hydrate canonical library_notes (warn on drift, restore in place).
    # library_notes is managed by Cartograph - agents should not edit it. We
    # restore here at validate time so drift is surfaced during the dev loop,
    # not silently fixed at checkin.
    try:
        from .scaffolding import _library_notes as _canonical_library_notes
        canonical_notes = _canonical_library_notes(language, domain)
        current_notes = data.get("library_notes")
        if current_notes != canonical_notes:
            data["library_notes"] = canonical_notes
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            contam_warnings.append(
                "library_notes drifted from canonical and was restored. "
                "Agents should not edit it."
            )
            check("library_notes hydrated from canonical", True)
    except Exception as e:
        log.debug("library_notes hydration skipped: %s", e)

    try:
        # 7b. src/__init__.py imports cleanly (Python only)
        init_path = os.path.join(path, "src", "__init__.py")
        if language == "python" and os.path.exists(init_path):
            init_result = subprocess.run(
                [sys.executable, "-c", "import src"],
                cwd=path, capture_output=True, text=True, timeout=10
            )
            init_ok = init_result.returncode == 0
            init_err = (init_result.stderr or "").strip()
            if not check("src/__init__.py imports cleanly", init_ok, init_err):
                _print_checklist(checklist, errors, failed=True)
                return {"status": "error",
                        "message": "src/__init__.py has import errors.",
                        "test_output": init_err}

        # 7c. Example file exists and has no TODOs (execution deferred until after install)
        example_file = engine.example_filename(path)
        example_path = os.path.join(path, "examples", example_file)
        if not check(f"examples/{example_file} exists", os.path.exists(example_path),
                     f"Missing examples/{example_file}"):
            _print_checklist(checklist, errors, failed=True)
            return {"status": "error", "message": f"Missing examples/{example_file}"}

        example_content = open(example_path, encoding="utf-8").read()
        example_todos = example_content.count("[TODO]")
        if not check(f"No [TODO] in {example_file}", example_todos == 0,
                     f"Found {example_todos} [TODO] placeholder(s) in examples/{example_file} — write real example code"):
            _print_checklist(checklist, errors, failed=True)
            return {"status": "error", "message": f"Replace [TODO] placeholders in examples/{example_file}"}

        # 7d. Example imports at least one thing from src/
        import_pattern = engine.src_import_pattern()
        if import_pattern is not None:
            imports_src = bool(re.search(import_pattern, example_content, re.MULTILINE))
            if not check(f"{example_file} imports from src/", imports_src,
                         f"examples/{example_file} does not import anything from src/ — the example must use the widget"):
                _print_checklist(checklist, errors, failed=True)
                return {"status": "error", "message": (
                    f"examples/{example_file} must import from src/"
                )}

        # 8. Test files follow naming convention
        test_files = engine.find_test_files(path)
        if not check(f"Test files found ({len(test_files)})", len(test_files) > 0,
                     f"No test files found in tests/ for language '{language}'"):
            _print_checklist(checklist, errors, failed=True)
            return {"status": "error", "message": f"No test files found in tests/ matching the expected pattern for '{language}'"}

        # 8b. Language-specific required files (e.g. .nimble for Nim)
        required = engine.required_files(path)
        for rel_path, hint in required:
            full = os.path.join(path, rel_path)
            if not check(f"{rel_path} exists", os.path.exists(full), hint):
                _print_checklist(checklist, errors, failed=True)
                return {"status": "error", "message": hint}

        # 9. Language checks, install deps, run example, run tests
        from .contamination import scan_contamination
        contam = scan_contamination(path)
        contam_warnings.extend(contam.get("warnings", []))
        if contam["blocks"]:
            check("Contamination scan clean", False,
                  "Contamination blocked:\n" + "\n".join(contam["blocks"]))
            _print_checklist(checklist, errors + contam["blocks"], failed=True)
            return {"status": "error",
                    "message": "Contamination scan failed - hard blocks found.",
                    "blocks": contam["blocks"]}
        check("Contamination scan clean", True)

        log.debug("Running language checks...")
        lang_check = engine.validate_widget(path, dependencies)
        if not check("Language checks pass", lang_check["passed"],
                     lang_check.get("error", "")):
            _print_checklist(checklist, errors, failed=True,
                              test_output=lang_check.get("error"))
            return {"status": "error", "message": lang_check.get("error", "Language checks failed"),
                    "test_output": lang_check.get("error", "")}

        log.debug("Installing dependencies...")
        try:
            engine.install_deps(path, dependencies)
            check("Dependencies installed", True)
        except Exception as e:
            check("Dependencies installed", False, str(e))
            _print_checklist(checklist, errors, failed=True)
            return {"status": "error", "message": f"Dependency install failed: {e}"}

        log.debug("Running example...")
        example_result = engine.run_example(path)
        example_err = example_result.get("error", "")
        if not check(f"{example_file} runs cleanly", example_result["passed"], example_err):
            _print_checklist(checklist, errors, failed=True, test_output=example_err)
            return {"status": "error", "message": f"{example_file} failed to run.",
                    "test_output": example_err[:500]}

        log.debug("Running tests...")
        result = engine.run_tests(path)
        test_error = result.get("error", "")
        if not check("All tests pass", result["passed"], test_error):
            _print_checklist(checklist, errors, failed=True, test_output=test_error)
            if ("does not meet global threshold" in test_error
                    or "Required test coverage of" in test_error
                    or ("coverage" in test_error.lower() and "threshold" in test_error.lower())):
                fail_msg = "Coverage below threshold - add tests to reach 80%."
            else:
                fail_msg = "Tests failed. Fix before checkin."
            return {"status": "error", "message": fail_msg,
                    "test_output": test_error[:3000]}

        # 9b. Sidecar (e.g. python/ inside an openscad widget)
        sidecar_spec = engine.sidecar()
        if sidecar_spec is not None:
            sc_lang, sc_dir, sc_cov = sidecar_spec
            sc_path = os.path.join(path, sc_dir)
            if os.path.isdir(sc_path) and any(
                f.endswith(f".{get_engine(sc_lang).file_ext}")
                for f in os.listdir(sc_path)
            ):
                sc_engine = get_engine(sc_lang)
                if sc_engine is None:
                    check(f"{sc_dir}/ sidecar engine available", False,
                          f"sidecar language '{sc_lang}' is not registered")
                    _print_checklist(checklist, errors, failed=True)
                    return {"status": "error",
                            "message": f"Sidecar engine '{sc_lang}' not available"}
                sc_result = sc_engine.validate_subtree(path, sc_dir, coverage=sc_cov)
                sc_err = sc_result.get("error", "")
                if not check(f"{sc_dir}/ sidecar passes ({sc_cov}% coverage)",
                             sc_result["passed"], sc_err):
                    _print_checklist(checklist, errors, failed=True, test_output=sc_err)
                    return {"status": "error",
                            "message": f"{sc_dir}/ sidecar failed",
                            "test_output": sc_err[:3000]}

        # 10. Custom validation rules
        from .rules import run_all_rules
        custom = run_all_rules(path, language)
        if custom["rules_run"] > 0:
            if custom["blocks"]:
                check("Custom rules pass", False,
                      "Custom rule blocks:\n" + "\n".join(custom["blocks"]))
                _print_checklist(checklist, errors + custom["blocks"], failed=True)
                return {"status": "error",
                        "message": "Custom validation rules failed.",
                        "blocks": custom["blocks"]}
            contam_warnings.extend(custom["warnings"])
            check(f"Custom rules pass ({custom['rules_run']} rules)", True)

        # 11. Uniqueness check
        current_hash = carto._calculate_implementation_hash(path)
        duplicate = next((w for w in carto.widgets
                          if w.get("implementation_hash") == current_hash
                          and w["id"] != meta.get("id")), None)
        if not check("Implementation is unique",
                     duplicate is None,
                     f"Identical code already exists: {duplicate['id']}" if duplicate else None):
            _print_checklist(checklist, errors, failed=True)
            return {
                "status": "error",
                "message": f"Identical code already exists: {duplicate['id']}",
            }

        _print_checklist(checklist, errors, failed=False, warnings=contam_warnings)

        # Write a stamp so checkin can skip re-validation if nothing changes
        from .validation_stamp import write_stamp
        try:
            write_stamp(path, language, engine)
        except Exception as e:
            return {"status": "error", "message": f"Could not write validation stamp: {e}"}

        result = {"status": "success", "message": "Widget is valid"}
        if contam_warnings:
            result["warnings"] = contam_warnings
        return result
    finally:
        try:
            engine.cleanup(path)
        except Exception as e:
            log.error("Validation cleanup failed for %s: %s", path, e)


def _print_checklist(checklist, errors, failed, test_output=None, warnings=None):
    """Log validation results."""
    status = "FAILED" if failed else "PASSED"
    log.info("Validation %s (%d checks)", status, len(checklist))
    for item in checklist:
        log.debug("  %s", item)
    for error in errors:
        log.debug("  Error: %s", error)
    if test_output:
        log.debug("  Test output: %s", test_output[:500])
    if warnings:
        log.info("Warnings (%d):", len(warnings))
        for w in warnings:
            log.info("  %s", w)
