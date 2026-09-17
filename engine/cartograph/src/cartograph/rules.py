"""
Custom validation rules - user-defined checks that run during validation.

A rules file is a single script per language that receives the widget path
as its first argument and prints JSON to stdout:

    {"blocks": ["hard failure", ...], "warnings": ["soft warning", ...]}

Empty arrays or no output means all checks passed. Non-zero exit or
invalid JSON is treated as a block with the error details.

File locations (all run if present, project first):
    .cartograph/rules/rules.py       per-project rules
    <data_dir>/rules/rules.py        global rules (per-user)
    $CARTOGRAPH_ORG_RULES            org-pushed rules (colon-separated paths;
                                     each path may be a single rules file or
                                     a directory containing rules.<lang>.* files)

Fixed filenames per language:
    python        rules.py
    javascript    rules.js
    typescript    rules.typescript.js
    nim           rules.nim
    angular       rules.angular.js
    php           rules.php
    openscad      rules.openscad.py
    systemverilog rules.sv.py
    css           rules.css.js
"""

import json
import logging
import os
import subprocess
import sys

log = logging.getLogger("cartograph")

# Maps widget language -> (filename, runner command)
# Each language gets its own rules file so team conventions stay isolated.
_LANGUAGE_RULES = {
    "python":        ("rules.py",            [sys.executable]),
    "javascript":    ("rules.js",            ["node"]),
    "typescript":    ("rules.typescript.js", ["node"]),
    "nim":           ("rules.nim",           ["nim", "r", "--hints:off"]),
    "angular":       ("rules.angular.js",    ["node"]),
    "php":           ("rules.php",           ["php"]),
    "openscad":      ("rules.openscad.py",   [sys.executable]),
    "systemverilog": ("rules.sv.py",         [sys.executable]),
    "css":           ("rules.css.js",        ["node"]),
    "terraform":     ("rules.terraform.py",  [sys.executable]),
    "go":            ("rules.go.py",         [sys.executable]),
    "spice":         ("rules.spice.py",      [sys.executable]),
    "rust":          ("rules.rust.py",       [sys.executable]),
    "gdscript":      ("rules.gdscript.py",   [sys.executable]),
    "java":          ("rules.java.py",       [sys.executable]),
    "lean":          ("rules.lean.py",       [sys.executable]),
    "csharp":        ("rules.csharp.py",     [sys.executable]),
    "flutter":       ("rules.flutter.py",    [sys.executable]),
}


def _org_rules_paths(filename: str) -> list[str]:
    """Resolve CARTOGRAPH_ORG_RULES into a list of existing rules files.

    The env var holds os.pathsep-separated entries. Each entry may be:
      - a path to a rules file -> used directly
      - a path to a directory  -> joined with the language-specific filename
    Missing entries are silently skipped.
    """
    raw = os.environ.get("CARTOGRAPH_ORG_RULES", "").strip()
    if not raw:
        return []

    found = []
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        if os.path.isdir(entry):
            candidate = os.path.join(entry, filename)
            if os.path.isfile(candidate):
                found.append(candidate)
        elif os.path.isfile(entry):
            # Only count file entries that match this language's filename.
            if os.path.basename(entry) == filename:
                found.append(entry)
    return found


def _rules_file_paths(language: str) -> list[dict]:
    """Return rules files that exist for this language.

    Returns list of {"path": str, "scope": "project"|"global"|"org"}.
    Order: project, global, org (so project rules report first).
    """
    info = _LANGUAGE_RULES.get(language)
    if not info:
        return []

    filename, _ = info
    results = []

    # Per-project
    project_path = os.path.join(os.getcwd(), ".cartograph", "rules", filename)
    if os.path.isfile(project_path):
        results.append({"path": project_path, "scope": "project"})

    # Global (per-user)
    from .engine import _user_data_dir
    global_path = os.path.join(_user_data_dir(), "rules", filename)
    if os.path.isfile(global_path):
        results.append({"path": global_path, "scope": "global"})

    # Org (pushed by environment)
    for org_path in _org_rules_paths(filename):
        results.append({"path": org_path, "scope": "org"})

    return results


def find_rules() -> list[dict]:
    """Find all rules files across all languages.

    Returns list of {"language": str, "path": str, "scope": str}.
    """
    results = []
    for lang, (filename, _) in _LANGUAGE_RULES.items():
        for entry in _rules_file_paths(lang):
            results.append({"language": lang, "filename": filename, **entry})
    return results


def _run_rules_file(path: str, widget_path: str, language: str, scope: str = "global") -> dict:
    """Execute a rules file and return {"blocks": [...], "warnings": [...]}.

    On error (bad exit code, invalid JSON, timeout), returns a clear
    error message explaining what went wrong and how to fix it.
    """
    info = _LANGUAGE_RULES.get(language)
    if not info:
        return {"blocks": [f"No runner configured for language '{language}'"], "warnings": []}

    _, runner = info
    cmd = runner + [path, os.path.abspath(widget_path)]
    label = f"{scope} rules ({os.path.basename(path)})"

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, cwd=widget_path,
        )
    except FileNotFoundError:
        return {
            "blocks": [f"{label}: runner not found ({runner[0]}). Is {language} installed?"],
            "warnings": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "blocks": [f"{label}: timed out after 30s"],
            "warnings": [],
        }

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        return {
            "blocks": [
                f"{label}: script error (exit {result.returncode}):\n"
                f"  {detail[:500]}\n\n"
                f"  Your rules file must exit 0 and print valid JSON.\n"
                f"  File: {path}"
            ],
            "warnings": [],
        }

    stdout = result.stdout.strip()
    if not stdout:
        return {"blocks": [], "warnings": []}

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        return {
            "blocks": [
                f"{label}: invalid JSON output: {e}\n\n"
                f"  Your rules file must print a JSON object like:\n"
                f'  {{"blocks": [...], "warnings": [...]}}\n\n'
                f"  Got: {stdout[:200]}\n"
                f"  File: {path}"
            ],
            "warnings": [],
        }

    blocks = data.get("blocks", [])
    warnings = data.get("warnings", [])

    if not isinstance(blocks, list) or not isinstance(warnings, list):
        return {
            "blocks": [
                f"{label}: 'blocks' and 'warnings' must be JSON arrays.\n"
                f"  File: {path}"
            ],
            "warnings": [],
        }

    # Tag messages with scope for traceability
    tag = f"[{scope}]"
    blocks = [f"{tag} {msg}" for msg in blocks]
    warnings = [f"{tag} {msg}" for msg in warnings]

    return {"blocks": blocks, "warnings": warnings}


def run_all_rules(widget_path: str, language: str) -> dict:
    """Find and run all rules files for the language. Returns merged results."""
    files = _rules_file_paths(language)
    if not files:
        return {"blocks": [], "warnings": [], "rules_run": 0}

    all_blocks = []
    all_warnings = []

    for entry in files:
        log.debug("Running %s rules: %s", entry["scope"], entry["path"])
        result = _run_rules_file(entry["path"], widget_path, language, entry["scope"])
        all_blocks.extend(result["blocks"])
        all_warnings.extend(result["warnings"])

    return {
        "blocks": all_blocks,
        "warnings": all_warnings,
        "rules_run": len(files),
    }


# ---------------------------------------------------------------------------
# Templates - used by `cartograph rules init`
# ---------------------------------------------------------------------------

_TEMPLATE_PYTHON = """\
\"\"\"
Custom validation rules for Python widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship (banned patterns, etc).
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".
              Use for things that are usually wrong but sometimes intentional.

Empty arrays (or no output at all) means all checks passed.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      widget.json       metadata (id, name, domain, version, dependencies)
      src/              source code - this is what gets imported
      tests/            test files
      examples/         example usage files

You can read any of these with normal file operations. For example:

    - os.walk(os.path.join(widget_path, "src"))     scan source files
    - os.walk(os.path.join(widget_path, "tests"))   scan test files
    - json.load(open(os.path.join(widget_path, "widget.json")))
                                                     read widget metadata

This is just a Python script. Use any stdlib module you want - ast for
parsing, re for patterns, pathlib if you prefer it. No special APIs needed.

RUNNING EXTRA TESTS
-------------------
Cartograph runs your widget's tests with its own flags (80% coverage, etc).
If you want more from your test engine (extra pytest flags, markers, plugins),
call it again from here via subprocess. Tests will run twice - once
Cartograph's way (the quality guarantee), once yours (your preferences).

    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--timeout=120", "-x"],
        capture_output=True, text=True, cwd=widget_path,
    )
    if result.returncode != 0:
        blocks.append(f"Custom test run failed:\\n{result.stdout[-500:]}")
\"\"\"
import json
import os
import sys


def validate(widget_path):
    blocks = []
    warnings = []

    src_dir = os.path.join(widget_path, "src")
    test_dir = os.path.join(widget_path, "tests")
    examples_dir = os.path.join(widget_path, "examples")

    # --- Your checks go here ---
    #
    # The pattern is simple: check a condition, append a message.
    #
    #   if something_is_wrong:
    #       blocks.append("what's wrong and where")    # hard fail
    #
    #   if something_is_suspicious:
    #       warnings.append("what looks off")          # soft, overridable
    #
    # Examples (uncomment to use):

    # Block: ban specific imports in source code
    # BANNED = {"pickle", "subprocess", "eval"}
    # for root, _, files in os.walk(src_dir):
    #     for fname in files:
    #         if not fname.endswith(".py"): continue
    #         content = open(os.path.join(root, fname)).read()
    #         for banned in BANNED:
    #             if f"import {banned}" in content:
    #                 blocks.append(f"{fname} uses banned import: {banned}")

    # Warning: source files over 200 lines
    # for root, _, files in os.walk(src_dir):
    #     for fname in files:
    #         if not fname.endswith(".py"): continue
    #         count = sum(1 for _ in open(os.path.join(root, fname)))
    #         if count > 200:
    #             warnings.append(f"{fname} is {count} lines (max 200)")

    # Warning: fewer than 2 test files
    # tests = [f for f in os.listdir(test_dir) if f.startswith("test_")]
    # if len(tests) < 2:
    #     warnings.append(f"Only {len(tests)} test file(s) - consider more coverage")

    return {"blocks": blocks, "warnings": warnings}


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1])))
"""

_TEMPLATE_JAVASCRIPT = """\
/**
 * Custom validation rules for JavaScript/TypeScript widgets.
 *
 * HOW THIS WORKS
 * --------------
 * This file runs automatically during `cartograph validate` (and therefore
 * `cartograph checkin`). Cartograph calls it with the widget directory as
 * a command-line argument. Your job is to inspect the widget and report problems.
 *
 * Print a JSON object to stdout with two keys:
 *
 *     {"blocks": [...], "warnings": [...]}
 *
 *   blocks    - hard failures. Checkin is rejected, no override possible.
 *   warnings  - soft issues. User can override with --override-warnings.
 *
 * Empty arrays (or no output at all) means all checks passed.
 *
 * WHAT YOU HAVE ACCESS TO
 * -----------------------
 * The widgetPath argument points to a standard widget directory:
 *
 *     widgetPath/
 *       widget.json       metadata (id, name, domain, version, dependencies)
 *       src/              source code
 *       tests/            test files
 *       examples/         example usage files
 *
 * Use normal fs operations to read any of these. This is just a Node script -
 * use any built-in module you want (fs, path, etc). No special APIs needed.
 *
 * RUNNING EXTRA TESTS
 * -------------------
 * Cartograph runs your widget's tests with its own flags. If you want more
 * from your test runner (extra jest flags, coverage config, etc), call it
 * again from here via child_process. Tests will run twice - once Cartograph's
 * way (the quality guarantee), once yours (your preferences).
 *
 *     const { execSync } = require("child_process");
 *     try {
 *         execSync("npx jest --coverage --coverageThreshold='{}'", { cwd: widgetPath });
 *     } catch (e) {
 *         blocks.push("Custom test run failed: " + e.stderr);
 *     }
 */
const fs = require("fs");
const path = require("path");

function validate(widgetPath) {
    const blocks = [];
    const warnings = [];

    const srcDir = path.join(widgetPath, "src");
    const testDir = path.join(widgetPath, "tests");
    const examplesDir = path.join(widgetPath, "examples");

    // --- Your checks go here ---
    //
    // The pattern is simple: check a condition, push a message.
    //
    //   if (somethingIsWrong) {
    //       blocks.push("what's wrong and where");    // hard fail
    //   }
    //   if (somethingIsSuspicious) {
    //       warnings.push("what looks off");          // soft, overridable
    //   }
    //
    // Examples (uncomment to use):

    // Block: ban specific patterns in source code
    // const BANNED = ["eval(", "document.write("];
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".js") && !fname.endsWith(".ts")) continue;
    //         const content = fs.readFileSync(path.join(srcDir, fname), "utf8");
    //         for (const banned of BANNED) {
    //             if (content.includes(banned)) {
    //                 blocks.push(`${fname} uses banned pattern: ${banned}`);
    //             }
    //         }
    //     }
    // }

    // Warning: source files over 200 lines
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".js") && !fname.endsWith(".ts")) continue;
    //         const lines = fs.readFileSync(path.join(srcDir, fname), "utf8").split("\\n").length;
    //         if (lines > 200) {
    //             warnings.push(`${fname} is ${lines} lines (max 200)`);
    //         }
    //     }
    // }

    // Warning: fewer than 2 test files
    // if (fs.existsSync(testDir)) {
    //     const tests = fs.readdirSync(testDir).filter(f => f.startsWith("test"));
    //     if (tests.length < 2) {
    //         warnings.push(`Only ${tests.length} test file(s) - consider more coverage`);
    //     }
    // }

    return { blocks, warnings };
}

console.log(JSON.stringify(validate(process.argv[2])));
"""

_TEMPLATE_NIM = """\
## Custom validation rules for Nim widgets.
##
## HOW THIS WORKS
## --------------
## This file runs automatically during `cartograph validate` (and therefore
## `cartograph checkin`). Cartograph calls it with the widget directory as
## the first argument. Your job is to inspect the widget and report problems.
##
## Print a JSON object to stdout with two keys:
##
##     {"blocks": [...], "warnings": [...]}
##
##   blocks    - hard failures. Checkin is rejected, no override possible.
##   warnings  - soft issues. User can override with --override-warnings.
##
## Empty arrays (or no output at all) means all checks passed.
##
## WHAT YOU HAVE ACCESS TO
## -----------------------
## The widgetPath argument points to a standard widget directory:
##
##     widgetPath/
##       widget.json       metadata (id, name, domain, version, dependencies)
##       src/              source code
##       tests/            test files
##       examples/         example usage files
##
## Use standard library modules (os, json, strutils) to inspect files.
## This is just a Nim script - no special APIs needed.
##
## RUNNING EXTRA TESTS
## -------------------
## Cartograph runs your widget's tests via nimble test. If you want to use
## testament (--megatest, custom patterns, etc), call it from here via
## execCmdEx. Tests will run twice - once Cartograph's way (the quality
## guarantee), once yours (your preferences).
##
##   let res = execCmdEx("testament --megatest pattern tests/")
##   if res.exitCode != 0:
##     blocks.add(%*("Custom test run failed: " & res.output))

import std/[json, os, strutils]

proc validate(widgetPath: string): JsonNode =
  var blocks = newJArray()
  var warnings = newJArray()

  let srcDir = widgetPath / "src"
  let testDir = widgetPath / "tests"
  let examplesDir = widgetPath / "examples"

  # --- Your checks go here ---
  #
  # The pattern is simple: check a condition, add a message.
  #
  #   if somethingIsWrong:
  #     blocks.add(%*"what's wrong and where")     # hard fail
  #
  #   if somethingIsSuspicious:
  #     warnings.add(%*"what looks off")           # soft, overridable
  #
  # Examples (uncomment to use):

  # Block: ban specific imports in source code
  # if dirExists(srcDir):
  #   for fpath in walkDirRec(srcDir):
  #     if not fpath.endsWith(".nim"): continue
  #     let content = readFile(fpath)
  #     for banned in ["os.execShellCmd", "system("]:
  #       if banned in content:
  #         blocks.add(%*(extractFilename(fpath) & " uses banned: " & banned))

  # Warning: source files over 200 lines
  # if dirExists(srcDir):
  #   for fpath in walkDirRec(srcDir):
  #     if not fpath.endsWith(".nim"): continue
  #     let count = readFile(fpath).splitLines().len
  #     if count > 200:
  #       warnings.add(%*(extractFilename(fpath) & " is " & $count & " lines (max 200)"))

  # Warning: fewer than 2 test files
  # if dirExists(testDir):
  #   var testCount = 0
  #   for fpath in walkDir(testDir):
  #     if fpath.path.endsWith(".nim"): inc testCount
  #   if testCount < 2:
  #     warnings.add(%*("Only " & $testCount & " test file(s) - consider more coverage"))

  result = %*{"blocks": blocks, "warnings": warnings}

echo $validate(paramStr(1))
"""

_TEMPLATE_PHP = """\
<?php
/**
 * Custom validation rules for PHP widgets.
 *
 * HOW THIS WORKS
 * --------------
 * This file runs automatically during `cartograph validate` (and therefore
 * `cartograph checkin`). Cartograph calls it with the widget directory as
 * the first argument. Your job is to inspect the widget and report problems.
 *
 * Print a JSON object to stdout with two keys:
 *
 *     {"blocks": [...], "warnings": [...]}
 *
 *   blocks    - hard failures. Checkin is rejected, no override possible.
 *               Use for things that must never ship (banned patterns, etc).
 *   warnings  - soft issues. Checkin pauses, but the user can override with
 *               --override-warnings --override-reason "why it's ok".
 *               Use for things that are usually wrong but sometimes intentional.
 *
 * Empty arrays (or no output at all) means all checks passed.
 *
 * WHAT YOU HAVE ACCESS TO
 * -----------------------
 * The $widgetPath argument points to a standard widget directory:
 *
 *     widgetPath/
 *       widget.json       metadata (id, name, domain, version, dependencies)
 *       src/              source code
 *       tests/            test files
 *       examples/         example usage files
 *
 * Use standard PHP functions (file_get_contents, glob, json_decode) to
 * inspect files. This is just a PHP script - no special APIs needed.
 *
 * RUNNING EXTRA TESTS
 * -------------------
 * Cartograph runs your widget's tests via vendor/bin/phpunit. If you want
 * to run additional PHPUnit suites or enforce custom test patterns, call
 * phpunit from here:
 *
 *   $res = shell_exec("php vendor/bin/phpunit --testsuite custom 2>&1");
 *   if ($res === null || str_contains($res, 'FAILURES')) {
 *       $blocks[] = "Custom test suite failed";
 *   }
 */

$widgetPath = $argv[1] ?? '.';
$blocks = [];
$warnings = [];

$srcDir      = $widgetPath . '/src';
$testDir     = $widgetPath . '/tests';
$examplesDir = $widgetPath . '/examples';

// --- Your checks go here ---
//
// The pattern is simple: check a condition, add a message.
//
//   if (somethingIsWrong) {
//       $blocks[] = "what's wrong and where";     // hard fail
//   }
//
//   if (somethingIsSuspicious) {
//       $warnings[] = "what looks off";           // soft, overridable
//   }
//
// Examples (uncomment to use):

// Block: ban specific function calls in source code
// $phpFiles = glob($srcDir . '/*.php') ?: [];
// foreach ($phpFiles as $file) {
//     $content = file_get_contents($file);
//     foreach (['exec(', 'shell_exec(', 'system('] as $banned) {
//         if (str_contains($content, $banned)) {
//             $blocks[] = basename($file) . " uses banned function: $banned";
//         }
//     }
// }

// Warning: source files over 200 lines
// $phpFiles = glob($srcDir . '/*.php') ?: [];
// foreach ($phpFiles as $file) {
//     $count = count(file($file));
//     if ($count > 200) {
//         $warnings[] = basename($file) . " is $count lines (max 200)";
//     }
// }

// Warning: fewer than 2 test files
// $testFiles = glob($testDir . '/*Test.php') ?: [];
// if (count($testFiles) < 2) {
//     $warnings[] = "Only " . count($testFiles) . " test file(s) - consider more coverage";
// }

echo json_encode(['blocks' => $blocks, 'warnings' => $warnings]);
"""

_TEMPLATE_TYPESCRIPT = """\
/**
 * Custom validation rules for TypeScript widgets.
 *
 * HOW THIS WORKS
 * --------------
 * This file runs automatically during `cartograph validate` (and therefore
 * `cartograph checkin`). Cartograph calls it with the widget directory as
 * a command-line argument. Your job is to inspect the widget and report problems.
 *
 * Print a JSON object to stdout with two keys:
 *
 *     {"blocks": [...], "warnings": [...]}
 *
 *   blocks    - hard failures. Checkin is rejected, no override possible.
 *   warnings  - soft issues. User can override with --override-warnings.
 *
 * Empty arrays (or no output at all) means all checks passed.
 *
 * TYPESCRIPT-SPECIFIC CHECKS TO CONSIDER
 * ---------------------------------------
 * TypeScript has its own quality concerns beyond JavaScript:
 *
 *   - `any` type usage: weakens type safety, consider banning or warning
 *   - Missing return types on exported functions: harder to use as a library
 *   - `@ts-ignore` / `@ts-expect-error`: may hide real type errors
 *   - `as <Type>` casts: can silently break if types change
 *
 * WHAT YOU HAVE ACCESS TO
 * -----------------------
 * The widgetPath argument points to a standard widget directory:
 *
 *     widgetPath/
 *       widget.json       metadata (id, name, domain, version, dependencies)
 *       src/              source code
 *       tests/            test files
 *       examples/         example usage files
 *
 * Use normal fs operations to read any of these. No special APIs needed.
 */
const fs = require("fs");
const path = require("path");

function validate(widgetPath) {
    const blocks = [];
    const warnings = [];

    const srcDir = path.join(widgetPath, "src");

    // --- Your checks go here ---
    //
    // Examples (uncomment to use):

    // Warning: `any` type usage in source files
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".ts")) continue;
    //         const content = fs.readFileSync(path.join(srcDir, fname), "utf8");
    //         const matches = (content.match(/: any\\b/g) || []).length;
    //         if (matches > 0) {
    //             warnings.push(fname + " uses `any` type " + matches + " time(s) - prefer explicit types");
    //         }
    //     }
    // }

    // Warning: `@ts-ignore` suppression
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".ts")) continue;
    //         const content = fs.readFileSync(path.join(srcDir, fname), "utf8");
    //         if (content.includes("@ts-ignore") || content.includes("@ts-expect-error")) {
    //             warnings.push(`${fname} suppresses TypeScript errors - review before shipping`);
    //         }
    //     }
    // }

    return { blocks, warnings };
}

console.log(JSON.stringify(validate(process.argv[2])));
"""

_TEMPLATE_ANGULAR = """\
/**
 * Custom validation rules for Angular widgets.
 *
 * HOW THIS WORKS
 * --------------
 * This file runs automatically during `cartograph validate` (and therefore
 * `cartograph checkin`). Cartograph calls it with the widget directory as
 * a command-line argument. Your job is to inspect the widget and report problems.
 *
 * Print a JSON object to stdout with two keys:
 *
 *     {"blocks": [...], "warnings": [...]}
 *
 *   blocks    - hard failures. Checkin is rejected, no override possible.
 *   warnings  - soft issues. User can override with --override-warnings.
 *
 * Empty arrays (or no output at all) means all checks passed.
 *
 * ANGULAR-SPECIFIC CHECKS TO CONSIDER
 * ------------------------------------
 * Angular has naming and structural conventions worth enforcing:
 *
 *   - Component class names should end with `Component`
 *   - Service class names should end with `Service`
 *   - `console.log` left in production components
 *   - Direct DOM manipulation (document.querySelector) bypasses Angular's
 *     change detection - prefer ElementRef or Renderer2
 *
 * WHAT YOU HAVE ACCESS TO
 * -----------------------
 * The widgetPath argument points to a standard widget directory:
 *
 *     widgetPath/
 *       widget.json       metadata (id, name, domain, version, dependencies)
 *       src/              source code (Angular component files)
 *       tests/            test files
 *       examples/         example usage files
 *
 * Use normal fs operations to read any of these. No special APIs needed.
 */
const fs = require("fs");
const path = require("path");

function validate(widgetPath) {
    const blocks = [];
    const warnings = [];

    const srcDir = path.join(widgetPath, "src");

    // --- Your checks go here ---
    //
    // Examples (uncomment to use):

    // Warning: direct DOM manipulation (bypasses Angular change detection)
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".ts")) continue;
    //         const content = fs.readFileSync(path.join(srcDir, fname), "utf8");
    //         if (content.includes("document.querySelector") || content.includes("document.getElementById")) {
    //             warnings.push(`${fname} uses direct DOM manipulation - prefer ElementRef/Renderer2`);
    //         }
    //     }
    // }

    // Warning: console.log in component source
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".ts")) continue;
    //         const content = fs.readFileSync(path.join(srcDir, fname), "utf8");
    //         if (content.includes("console.log(")) {
    //             warnings.push(`${fname} contains console.log - remove before shipping`);
    //         }
    //     }
    // }

    return { blocks, warnings };
}

console.log(JSON.stringify(validate(process.argv[2])));
"""

_TEMPLATE_OPENSCAD = """\
\"\"\"
Custom validation rules for OpenSCAD widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship (banned patterns, etc).
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".
              Use for things that are usually wrong but sometimes intentional.

Empty arrays (or no output at all) means all checks passed.

OPENSCAD-SPECIFIC CHECKS TO CONSIDER
--------------------------------------
OpenSCAD parametric models have their own quality concerns:

  - Magic numbers: hardcoded dimensions like `cube([25.4, 12.7, 6.35])` with
    no named variable make models hard to customize. Prefer `cube([width, height, depth])`.
  - Missing parameter validation: `assert(width > 0, "width must be positive")` prevents
    confusing geometry errors downstream.
  - echo() left in production: useful for debug, noisy in production widgets.
  - Very large $fn values: `$fn=360` makes renders unbearably slow; `$fn=100` is usually fine.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      widget.json       metadata (id, name, domain, version, dependencies)
      src/              .scad source files
      tests/            test files
      examples/         example_usage.scad

You can read any of these with normal file operations.
This is a Python script - use any stdlib module you want.
\"\"\"
import json
import os
import re
import sys


def validate(widget_path):
    blocks = []
    warnings = []

    src_dir = os.path.join(widget_path, "src")

    # --- Your checks go here ---
    #
    # Examples (uncomment to use):

    # Warning: echo() calls in source (debug output)
    # if os.path.isdir(src_dir):
    #     for fname in os.listdir(src_dir):
    #         if not fname.endswith(".scad"): continue
    #         content = open(os.path.join(src_dir, fname)).read()
    #         if re.search(r'\\becho\\s*\\(', content):
    #             warnings.append(f"{fname} contains echo() calls - remove before shipping")

    # Warning: very high $fn value (slow renders)
    # if os.path.isdir(src_dir):
    #     for fname in os.listdir(src_dir):
    #         if not fname.endswith(".scad"): continue
    #         content = open(os.path.join(src_dir, fname)).read()
    #         for m in re.finditer(r'\\$fn\\s*=\\s*(\\d+)', content):
    #             if int(m.group(1)) > 200:
    #                 warnings.append(f"{fname} sets $fn={m.group(1)} - values above 200 make renders very slow")

    return {"blocks": blocks, "warnings": warnings}


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1])))
"""

_TEMPLATE_SYSTEMVERILOG = """\
\"\"\"
Custom validation rules for SystemVerilog widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship (banned patterns, etc).
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".
              Use for things that are usually wrong but sometimes intentional.

Empty arrays (or no output at all) means all checks passed.

SYSTEMVERILOG-SPECIFIC CHECKS TO CONSIDER
------------------------------------------
RTL design has its own quality concerns:

  - Undriven outputs: a module output with no assignment silently drives X.
  - Blocking assignments (=) in always_ff blocks: should use non-blocking (<=).
  - Magic numbers in port widths: `input [7:0]` everywhere without a parameter
    makes designs hard to generalize.
  - `initial` blocks in synthesizable code: fine for simulation, may not synthesize.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      widget.json       metadata (id, name, domain, version, dependencies)
      src/              .sv source files
      tests/            testbench files
      examples/         example usage files

You can read any of these with normal file operations.
This is a Python script - use any stdlib module you want.
\"\"\"
import json
import os
import re
import sys


def validate(widget_path):
    blocks = []
    warnings = []

    src_dir = os.path.join(widget_path, "src")

    # --- Your checks go here ---
    #
    # Examples (uncomment to use):

    # Warning: blocking assignments in always_ff (should be non-blocking)
    # if os.path.isdir(src_dir):
    #     for fname in os.listdir(src_dir):
    #         if not fname.endswith(".sv"): continue
    #         content = open(os.path.join(src_dir, fname)).read()
    #         in_always_ff = False
    #         for line in content.splitlines():
    #             if "always_ff" in line:
    #                 in_always_ff = True
    #             if in_always_ff and re.search(r'\\w+\\s*=[^=<>!]', line):
    #                 warnings.append(f"{fname} may use blocking assignment in always_ff - use <= instead")
    #                 break

    # Warning: `initial` block in src/ (usually simulation-only)
    # if os.path.isdir(src_dir):
    #     for fname in os.listdir(src_dir):
    #         if not fname.endswith(".sv"): continue
    #         content = open(os.path.join(src_dir, fname)).read()
    #         if re.search(r'\\binitial\\b', content):
    #             warnings.append(f"{fname} contains `initial` block - verify it's synthesizable")

    return {"blocks": blocks, "warnings": warnings}


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1])))
"""

_TEMPLATE_CSS = """\
/**
 * Custom validation rules for CSS widgets.
 *
 * HOW THIS WORKS
 * --------------
 * This file runs automatically during `cartograph validate` (and therefore
 * `cartograph checkin`). Cartograph calls it with the widget directory as
 * a command-line argument. Your job is to inspect the widget and report problems.
 *
 * Print a JSON object to stdout with two keys:
 *
 *     {"blocks": [...], "warnings": [...]}
 *
 *   blocks    - hard failures. Checkin is rejected, no override possible.
 *   warnings  - soft issues. User can override with --override-warnings.
 *
 * Empty arrays (or no output at all) means all checks passed.
 *
 * CSS-SPECIFIC CHECKS TO CONSIDER
 * --------------------------------
 * CSS widgets are often shared design system components. Common team conventions:
 *
 *   - Hardcoded colors (#fff, rgb(255,255,255)) instead of CSS variables (--color-white)
 *   - Hardcoded px font sizes instead of rem (breaks user font scaling)
 *   - `!important` usage (overrides cascade, makes theming harder)
 *   - Magic z-index values (100, 9999) instead of a defined z-index scale
 *   - Hardcoded breakpoint px values instead of a shared breakpoint variable
 *
 * These are warnings by default - upgrade to blocks if your team wants to enforce them.
 *
 * WHAT YOU HAVE ACCESS TO
 * -----------------------
 * The widgetPath argument points to a standard widget directory:
 *
 *     widgetPath/
 *       widget.json       metadata (id, name, domain, version, dependencies)
 *       src/              .css source files
 *       tests/            test files
 *       examples/         example usage files
 *
 * Use normal fs operations to read any of these. No special APIs needed.
 */
const fs = require("fs");
const path = require("path");

function validate(widgetPath) {
    const blocks = [];
    const warnings = [];

    const srcDir = path.join(widgetPath, "src");

    // --- Your checks go here ---
    //
    // Examples (uncomment to use):

    // Warning: hardcoded colors instead of CSS variables
    // const HEX_COLOR = /#[0-9a-fA-F]{3,8}\\b/g;
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".css")) continue;
    //         const content = fs.readFileSync(path.join(srcDir, fname), "utf8");
    //         const matches = (content.match(HEX_COLOR) || []).length;
    //         if (matches > 0) {
    //             warnings.push(`${fname} has ${matches} hardcoded color(s) - prefer CSS custom properties (--color-name)`);
    //         }
    //     }
    // }

    // Warning: !important usage
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".css")) continue;
    //         const content = fs.readFileSync(path.join(srcDir, fname), "utf8");
    //         const count = (content.match(/!important/g) || []).length;
    //         if (count > 0) {
    //             warnings.push(`${fname} uses !important ${count} time(s) - may cause cascade conflicts`);
    //         }
    //     }
    // }

    // Warning: px font sizes (prefer rem for user font scaling)
    // if (fs.existsSync(srcDir)) {
    //     for (const fname of fs.readdirSync(srcDir)) {
    //         if (!fname.endsWith(".css")) continue;
    //         const content = fs.readFileSync(path.join(srcDir, fname), "utf8");
    //         if (/font-size:\\s*\\d+px/.test(content)) {
    //             warnings.push(`${fname} uses px font-size - consider rem for better accessibility`);
    //         }
    //     }
    // }

    return { blocks, warnings };
}

console.log(JSON.stringify(validate(process.argv[2])));
"""

_TEMPLATE_TERRAFORM = """\
\"\"\"
Custom validation rules for Terraform widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

TERRAFORM-SPECIFIC CHECKS TO CONSIDER
-------------------------------------
Terraform widgets often need stricter conventions than the engine's defaults:

  - Required tags: enforce a tagging schema (cost_center, owner, environment)
    on every taggable resource so cost allocation works downstream.
  - Naming convention: enforce a prefix or pattern on resource names so
    consumers can spot module-managed resources at a glance.
  - Forbidden providers: block certain providers (e.g. external data sources
    that hit shell scripts) for security policy.
  - Required outputs: ensure modules expose specific outputs (e.g. a `tags`
    output, a `name` output) so they compose with downstream modules.
  - Resource address conventions: enforce that resources within a module
    use a consistent address pattern.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      widget.json       metadata (id, name, domain, version, dependencies)
      src/              .tf source files (the module)
      tests/            root configurations that call the module
      examples/         example_usage.tf

You can read any of these with normal file operations.
This is a Python script - use any stdlib module you want.
\"\"\"
import json
import os
import re
import sys


def validate(widget_path):
    blocks = []
    warnings = []

    src_dir = os.path.join(widget_path, "src")

    # --- Your checks go here ---
    #
    # Examples (uncomment to use):

    # Warning: every resource block should have a tags argument
    # if os.path.isdir(src_dir):
    #     for fname in os.listdir(src_dir):
    #         if not fname.endswith(".tf"): continue
    #         content = open(os.path.join(src_dir, fname)).read()
    #         for m in re.finditer(r'resource\\s+"([^"]+)"\\s+"([^"]+)"\\s*\\{', content):
    #             # Crude check: is "tags" mentioned anywhere in the file?
    #             if "tags" not in content:
    #                 warnings.append(f"{fname}: resource {m.group(1)}.{m.group(2)} may be missing tags")

    # Block: forbid `external` provider in src/ (shell-out attack surface)
    # if os.path.isdir(src_dir):
    #     for fname in os.listdir(src_dir):
    #         if not fname.endswith(".tf"): continue
    #         content = open(os.path.join(src_dir, fname)).read()
    #         if re.search(r'data\\s+"external"\\s', content):
    #             blocks.append(f"{fname}: `data \\"external\\"` blocks shell scripts - forbidden by policy")

    return {"blocks": blocks, "warnings": warnings}


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1])))
"""

_TEMPLATE_GO = """\
\"\"\"
Custom validation rules for Go widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

GO-SPECIFIC CHECKS TO CONSIDER
------------------------------
Go widgets often need stricter conventions than the engine's defaults:

  - Error handling: forbid `_ = err` / blank-identifier error discards in
    src/ so failures always propagate.
  - Context discipline: require ctx context.Context as the first parameter
    on exported functions that do I/O.
  - Forbidden imports: block reflect, unsafe, or cgo for teams that want
    plain, portable Go.
  - Naming: enforce a package-name convention or an exported-identifier
    prefix so module-managed APIs are recognizable.
  - Goroutine hygiene: flag `go func()` in src/ without a wait/cancel
    mechanism in scope.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      go.mod
      src/        the library package
      tests/      black-box tests (*_test.go)
      examples/   example_usage.go
      widget.json

EXAMPLE
-------
\"\"\"
import json
import os
import re
import sys


def main() -> None:
    widget_path = sys.argv[1] if len(sys.argv) > 1 else "."
    blocks: list[str] = []
    warnings: list[str] = []

    src_dir = os.path.join(widget_path, "src")
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".go"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, widget_path)
            with open(fpath, encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    # [TODO] Replace with your team's checks. Example:
                    # discarded errors hide failures from consumers.
                    if re.search(r"^\\s*_\\s*=\\s*err\\b", line):
                        warnings.append(
                            f"{rel}:{lineno}: discarded error (`_ = err`) - "
                            f"propagate or handle it"
                        )

    print(json.dumps({"blocks": blocks, "warnings": warnings}))


if __name__ == "__main__":
    main()
"""

_TEMPLATE_RUST = """\
\"\"\"
Custom validation rules for Rust widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

RUST-SPECIFIC CHECKS TO CONSIDER
--------------------------------
The engine already blocks console output, process exit, and unsafe in src/,
and enforces rustfmt + 80% coverage. Teams often want more on top:

  - Error handling: forbid `.unwrap()` / `.expect(` in src/ so library code
    returns Result instead of panicking in the caller's process.
  - Forbidden crates: block a dependency your team bans (e.g. openssl-sys in
    favour of rustls) by inspecting Cargo.toml.
  - Clippy floor: shell out to `cargo clippy -- -D warnings` and surface any
    lint as a block.
  - Public API docs: require a `///` doc and at least one `# Examples` block
    on every exported item.
  - No blanket `#[allow(...)]`: flag crate-level allows that silence lints.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      Cargo.toml
      src/        the library crate (lib.rs)
      tests/      integration tests (test_*.rs)
      examples/   example_usage.rs
      widget.json

This is a Python script - use any stdlib module you want.
EXAMPLE
-------
\"\"\"
import json
import os
import re
import sys


def main() -> None:
    widget_path = sys.argv[1] if len(sys.argv) > 1 else "."
    blocks: list[str] = []
    warnings: list[str] = []

    src_dir = os.path.join(widget_path, "src")
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".rs"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, widget_path)
            with open(fpath, encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    # [TODO] Replace with your team's checks. Example:
                    # .unwrap() panics in the consumer's process.
                    if re.search(r"\\.unwrap\\(\\)", line):
                        warnings.append(
                            f"{rel}:{lineno}: .unwrap() in src/ - return a "
                            f"Result instead of panicking in the caller"
                        )

    print(json.dumps({"blocks": blocks, "warnings": warnings}))


if __name__ == "__main__":
    main()
"""

_TEMPLATE_GDSCRIPT = """\
\"\"\"
Custom validation rules for GDScript (Godot 4) widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

GDSCRIPT-SPECIFIC CHECKS TO CONSIDER
------------------------------------
The engine already blocks Godot 3 syntax, console output, absolute node
paths, and OS.delay_* in src/, and warns on untyped vars. Teams often want
more on top - and because there is no built-in coverage tool, a coverage
floor is exactly the kind of opinion that belongs HERE rather than in the
engine:

  - Coverage: instrument your tests (or count asserted public methods) and
    block below a threshold your studio agrees on.
  - Required @export docs: every @export field should have a ## doc comment
    so the inspector tooltip is useful.
  - Signal discipline: require signals to be declared (`signal x`) and named
    in past tense (health_changed, not change_health).
  - No print_rich/push_warning spam in src/.
  - Class naming: enforce a PascalCase class_name on every src script.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      project.godot
      src/        the reusable script(s)
      tests/      test_*.gd (headless, assert + ASSERT_PASS)
      examples/   example_usage.gd
      widget.json

This is a Python script - use any stdlib module you want.
EXAMPLE
-------
\"\"\"
import json
import os
import re
import sys


def main() -> None:
    widget_path = sys.argv[1] if len(sys.argv) > 1 else "."
    blocks: list[str] = []
    warnings: list[str] = []

    src_dir = os.path.join(widget_path, "src")
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".gd"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, widget_path)
            with open(fpath, encoding="utf-8", errors="replace") as f:
                text = f.read()
            # [TODO] Replace with your team's checks. Example: every src
            # script should declare a class_name.
            if not re.search(r"^\\s*class_name\\s+\\w+", text, re.MULTILINE):
                warnings.append(
                    f"{rel}: no class_name - declare one so consumers can "
                    f"reference the script by type"
                )

    print(json.dumps({"blocks": blocks, "warnings": warnings}))


if __name__ == "__main__":
    main()
"""

_TEMPLATE_SPICE = """\
\"\"\"
Custom validation rules for SPICE widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

SPICE-SPECIFIC CHECKS TO CONSIDER
---------------------------------
SPICE circuit blocks have their own quality concerns beyond the built-in
validation (which already enforces .subckt-only src/, a measured + asserted
testbench, and convergence):

  - Tolerance bands too loose: an assertion that passes for any plausible
    value isn't really testing the circuit. Tight the band where the math
    allows.
  - Missing operating-point sanity: a filter block might also want a .op
    check that bias nodes sit where intended.
  - Undocumented ports/params: every .subckt port and param should have a
    comment explaining its role and unit.
  - Temperature/corner coverage: a block claimed to work across temperature
    should sweep .temp in the testbench.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      widget.json       metadata (id, name, domain, version, dependencies)
      src/              .cir netlist (.subckt definitions)
      tests/            test_*.cir testbenches
      examples/         example_usage.cir

You can read any of these with normal file operations.
This is a Python script - use any stdlib module you want.
\"\"\"
import json
import os
import re
import sys


def validate(widget_path):
    blocks = []
    warnings = []

    src_dir = os.path.join(widget_path, "src")

    # --- Your checks go here ---
    #
    # Examples (uncomment to use):

    # Warning: a .subckt port or param with no explaining comment
    # if os.path.isdir(src_dir):
    #     for fname in os.listdir(src_dir):
    #         if not fname.endswith(".cir"): continue
    #         content = open(os.path.join(src_dir, fname)).read()
    #         if re.search(r'(?mi)^\\s*\\.subckt\\b', content) and ";" not in content:
    #             warnings.append(f"{fname}: no inline comments - document ports and params")

    return {"blocks": blocks, "warnings": warnings}


if __name__ == "__main__":
    print(json.dumps(validate(sys.argv[1])))
"""

_TEMPLATE_JAVA = """\
\"\"\"
Custom validation rules for Java widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

JAVA-SPECIFIC CHECKS TO CONSIDER
--------------------------------
The engine already blocks console printing, System.exit, and Thread.sleep
in src/, and enforces 80% JaCoCo line coverage. Teams often want more:

  - Checked-exception policy: forbid `throws Exception` on public methods
    (declare the specific exception types).
  - Forbidden deps: block a dependency your team bans by inspecting
    cartograph-deps.gradle or widget.json.
  - Null-safety: require @Nullable/@NonNull annotations on the public API.
  - Formatting floor: shell out to google-java-format --dry-run and block
    on any diff.
  - Reflection ban: flag Class.forName/setAccessible in src/.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      settings.gradle
      build.gradle             owned by the widget (plugins included)
      cartograph-deps.gradle   generated from widget.json deps
      src/        library sources
      tests/      JUnit 5 tests (*Test.java)
      examples/   ExampleUsage.java
      widget.json

This is a Python script - use any stdlib module you want.
EXAMPLE
-------
\"\"\"
import json
import os
import re
import sys


def main() -> None:
    widget_path = sys.argv[1] if len(sys.argv) > 1 else "."
    blocks: list[str] = []
    warnings: list[str] = []

    src_dir = os.path.join(widget_path, "src")
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".java"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, widget_path)
            with open(fpath, encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    # [TODO] Replace with your team's checks. Example:
                    # a blanket `throws Exception` hides the real contract.
                    if re.search(r"throws\\s+Exception\\b", line):
                        warnings.append(
                            f"{rel}:{lineno}: `throws Exception` on the "
                            f"public API - declare specific exception types"
                        )

    print(json.dumps({"blocks": blocks, "warnings": warnings}))


if __name__ == "__main__":
    main()
"""

_TEMPLATE_LEAN = """\
\"\"\"
Custom validation rules for Lean 4 widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

LEAN-SPECIFIC CHECKS TO CONSIDER
--------------------------------
The engine already hard-blocks sorry/admit, custom axioms in src/, console
output, and IO.sleep, and warns on native_decide/unsafe/partial. Teams
often want more on top:

  - Theorem density: require at least one theorem per public def (the
    "definition + theorem" house style, enforced).
  - Naming: theorems named after the property (encode_decode), defs in
    lowerCamelCase, no thm1/lemma2/aux3.
  - Doc comments: every public def and theorem gets a /-- ... -/ doc.
  - Proof hygiene: ban `native_decide` outright (the engine only warns),
    or cap tactic-block length to keep proofs reviewable.
  - Namespace discipline: everything under a namespace matching the
    widget slug.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      lakefile.toml
      src/        definitions + theorems
      tests/      test_*.lean (main + ASSERT_PASS, proofs elaborate)
      examples/   example_usage.lean
      widget.json

This is a Python script - use any stdlib module you want.
EXAMPLE
-------
\"\"\"
import json
import os
import re
import sys


def main() -> None:
    widget_path = sys.argv[1] if len(sys.argv) > 1 else "."
    blocks: list[str] = []
    warnings: list[str] = []

    src_dir = os.path.join(widget_path, "src")
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".lean"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, widget_path)
            with open(fpath, encoding="utf-8", errors="replace") as f:
                text = f.read()
            # [TODO] Replace with your team's checks. Example: a src file
            # should state at least one theorem about its definitions.
            if not re.search(r"^\\s*(theorem|example)\\b", text, re.MULTILINE):
                warnings.append(
                    f"{rel}: no theorem - state at least one machine-checked "
                    f"property about the public API"
                )

    print(json.dumps({"blocks": blocks, "warnings": warnings}))


if __name__ == "__main__":
    main()
"""

_TEMPLATE_CSHARP = """\
\"\"\"
Custom validation rules for C# widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

C#-SPECIFIC CHECKS TO CONSIDER
------------------------------
The engine already blocks Console printing, Environment.Exit/FailFast,
and Thread.Sleep in src/, and enforces 80% coverlet line coverage. Teams
often want more:

  - Async policy: forbid `.Result`/`.Wait()` on Tasks in src/ (deadlock
    bait) - require await.
  - Forbidden deps: block a dependency your team bans by inspecting
    cartograph-deps.props or widget.json.
  - Nullability floor: require `<Nullable>enable</Nullable>` to stay in
    the src csproj.
  - Formatting floor: shell out to `dotnet format --verify-no-changes`
    and block on any diff.
  - Reflection ban: flag Activator.CreateInstance/Type.GetType in src/.

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      cartograph-deps.props    generated from widget.json deps
      src/        class library (the widget owns its csproj)
      tests/      xUnit tests (*Tests.cs)
      examples/   ExampleUsage.cs
      widget.json

This is a Python script - use any stdlib module you want.
EXAMPLE
-------
\"\"\"
import json
import os
import re
import sys


def main() -> None:
    widget_path = sys.argv[1] if len(sys.argv) > 1 else "."
    blocks: list[str] = []
    warnings: list[str] = []

    src_dir = os.path.join(widget_path, "src")
    for root, _dirs, files in os.walk(src_dir):
        for fname in files:
            if not fname.endswith(".cs"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, widget_path)
            with open(fpath, encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    # [TODO] Replace with your team's checks. Example:
                    # blocking on a Task hides deadlocks until production.
                    if re.search(r"\\.(?:Result|Wait\\(\\))", line):
                        warnings.append(
                            f"{rel}:{lineno}: blocking Task access - "
                            f"prefer await on the public API"
                        )

    print(json.dumps({"blocks": blocks, "warnings": warnings}))


if __name__ == "__main__":
    main()
"""

_TEMPLATE_FLUTTER = """\
\"\"\"
Custom validation rules for Flutter widgets.

HOW THIS WORKS
--------------
This file runs automatically during `cartograph validate` (and therefore
`cartograph checkin`). Cartograph calls it with the widget directory as
the first argument. Your job is to inspect the widget and report problems.

Print a JSON object to stdout with two keys:

    {"blocks": [...], "warnings": [...]}

  blocks    - hard failures. Checkin is rejected, no override possible.
              Use for things that must never ship.
  warnings  - soft issues. Checkin pauses, but the user can override with
              --override-warnings --override-reason "why it's ok".

Empty arrays (or no output at all) means all checks passed.

FLUTTER-SPECIFIC CHECKS TO CONSIDER
-----------------------------------
The engine already blocks print/debugPrint, exit(), and sleep() in src/,
runs flutter analyze, and enforces 80% lcov line coverage. Teams often
want more:

  - Const discipline: flag non-const constructors invoked with all-const
    arguments in src/ (rebuild churn).
  - Forbidden deps: block a pub package your team bans by inspecting
    widget.json dependencies.
  - Material ban: require widgets to import package:flutter/widgets.dart
    rather than material.dart, so consumers choose their design system.
  - setState policy: flag setState in widgets above a size threshold -
    require a change notifier or controller instead.
  - Asset policy: block Image.network in src/ (URLs belong to callers).

WHAT YOU HAVE ACCESS TO
-----------------------
The widget_path argument points to a standard widget directory:

    widget_path/
      pubspec.yaml     the widget owns this (marker blocks are generated)
      src/             library sources (lib/ is a generated copy - ignore)
      tests/           flutter_test tests (*_test.dart)
      examples/        example_usage.dart (flutter_test harness)
      widget.json

This is a Python script - use any stdlib module you want.
EXAMPLE
-------
\"\"\"
import json
import os
import re
import sys


def main() -> None:
    widget_path = sys.argv[1] if len(sys.argv) > 1 else "."
    blocks: list[str] = []
    warnings: list[str] = []

    # Example: block Image.network in src/ - asset URLs belong to callers.
    src = os.path.join(widget_path, "src")
    for root, _dirs, files in os.walk(src):
        for fname in files:
            if not fname.endswith(".dart"):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    if re.search(r"Image\\.network\\s*\\(", line):
                        blocks.append(
                            f"{os.path.relpath(fpath, widget_path)}:{line_no}: "
                            "Image.network in src/ - accept an ImageProvider "
                            "parameter instead")

    print(json.dumps({"blocks": blocks, "warnings": warnings}))


if __name__ == "__main__":
    main()
"""

_TEMPLATES = {
    "python":        _TEMPLATE_PYTHON,
    "javascript":    _TEMPLATE_JAVASCRIPT,
    "typescript":    _TEMPLATE_TYPESCRIPT,
    "angular":       _TEMPLATE_ANGULAR,
    "nim":           _TEMPLATE_NIM,
    "php":           _TEMPLATE_PHP,
    "openscad":      _TEMPLATE_OPENSCAD,
    "systemverilog": _TEMPLATE_SYSTEMVERILOG,
    "css":           _TEMPLATE_CSS,
    "terraform":     _TEMPLATE_TERRAFORM,
    "go":            _TEMPLATE_GO,
    "spice":         _TEMPLATE_SPICE,
    "rust":          _TEMPLATE_RUST,
    "gdscript":      _TEMPLATE_GDSCRIPT,
    "java":          _TEMPLATE_JAVA,
    "lean":          _TEMPLATE_LEAN,
    "csharp":        _TEMPLATE_CSHARP,
    "flutter":       _TEMPLATE_FLUTTER,
}


def get_template(language: str) -> str | None:
    """Return a rules file template for the language, or None if unsupported."""
    return _TEMPLATES.get(language)


def get_rules_filename(language: str) -> str | None:
    """Return the rules filename for this language."""
    info = _LANGUAGE_RULES.get(language)
    return info[0] if info else None
