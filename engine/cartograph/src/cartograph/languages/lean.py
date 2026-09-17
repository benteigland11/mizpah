"""Lean 4 language engine.

Validates formally verified Lean 4 widgets - definitions with machine-checked
proofs about their behavior. The toolchain is elan (toolchain manager) + lake
(build) + lean, all from one installer. The build IS the primary validator:
if `lake build` succeeds with no `sorry`, every theorem in src/ is proven.

Two gaps the build does NOT catch, which this engine closes:
  - `sorry`/`admit` compile with only a *warning* - an unproven hole would
    silently pass. Blocked by the native scanner AND by parsing build output.
  - `axiom` declarations are accepted silently and can prove anything.
    Blocked by the native scanner in src/.

There is no line-coverage tool for Lean, so - like OpenSCAD / SystemVerilog /
SPICE / GDScript - this engine enforces no coverage floor. The floor is
stronger than coverage for proof code: the kernel checks every theorem on
every build. Runtime behavior is covered by the ASSERT_PASS test contract
(mirrors GDScript): each test file defines `main`, prints ASSERT_PASS per
verified property, and exits non-zero on failure. Theorems in test files are
kernel-checked during elaboration before main runs, so proof-style assertions
(`example : f x = y := rfl`) piggyback on the same run.

Dependencies: `mathlib` is the only supported external dependency, served
from one shared, version-pinned workspace under the platform data dir
(provisioned explicitly via `cartograph setup-mathlib` - never fetched
automatically, per the ML-frameworks policy). All other widgets build
against the Lean core library only. The workspace wiring (a path require +
lean-toolchain pin) is injected per build and stripped by cleanup, so
checked-in widgets stay machine-independent.

Layout
------
    widget_root/
      lakefile.toml            lake package manifest ([[lean_lib]] srcDir=src)
      src/<slug>.lean          definitions + theorems (module name = slug)
      tests/test_<slug>.lean   `main : IO UInt32`, ASSERT_PASS contract
      examples/example_usage.lean  `main : IO Unit`, imports the module

Widgets do NOT carry a lean-toolchain pin - the library validates against the
machine's default elan toolchain (like GDScript widgets don't pin a Godot
version), and the toolchain version is stamped into widget.json at checkin
via runtime_version(). Exception: mathlib widgets build under the engine's
pinned toolchain (written transiently, removed at cleanup) because .olean
compatibility requires the exact workspace toolchain.
"""

import glob as _glob
import json as _json
import os
import re as _re
import shutil as _shutil

from .base import LanguageEngine

_SORRY_WARNING = "declaration uses `sorry`"

# Build timeouts. Mathlib widgets elaborate against a multi-GB dependency;
# even with prebuilt .oleans, imports like Mathlib.Analysis take real time.
_BUILD_TIMEOUT = 300
_BUILD_TIMEOUT_MATHLIB = 1800
_RUN_TIMEOUT = 180
_RUN_TIMEOUT_MATHLIB = 600

_MATHLIB_REQUIRE_MARK = "# managed by cartograph - mathlib workspace require"

_LAKEFILE = """\
name = "{slug}"
defaultTargets = ["{slug}"]

[[lean_lib]]
name = "{slug}"
srcDir = "src"
"""

_LEAN_SRC = '''\
/-!
# {name}

[TODO] Replace with the widget's real API. The house style is
definition + theorem: every core function ships with at least one
machine-checked property about its behavior.
-/

/-- Returns the input value unchanged. -/
def process (value : String) : String :=
  value

/-- `process` never alters its input. -/
theorem process_id (value : String) : process value = value :=
  rfl
'''

_LEAN_TEST = '''\
import {slug}

/-!
Tests for {name}. Two layers, both required:
  - theorems/examples here are kernel-checked when this file elaborates
  - `main` exercises runtime behavior with fake data, printing ASSERT_PASS
    per verified property and exiting non-zero on failure
-/

-- [TODO] Replace with real proof-level assertions about the API.
example : process "hello" = "hello" := rfl

def main : IO UInt32 := do
  -- [TODO] Replace with real runtime assertions using fake data.
  if process "hello" != "hello" then
    IO.eprintln "ASSERT_FAIL process identity"
    return 1
  IO.println "ASSERT_PASS process identity"
  return 0
'''

_LEAN_EXAMPLE = '''\
import {slug}

/-- Example usage of {name}. Must run and exit cleanly with fake data. -/
def main : IO Unit := do
  -- [TODO] Replace with a realistic call using fake data.
  IO.println (process "hello")
'''


class LeanEngine(LanguageEngine):
    name = "lean"
    validation_version = 1
    file_ext = "lean"
    aliases = ["lean4"]
    toolchain = {
        "lean": "Install Lean 4 via elan - lean-lang.org/install "
                "(one installer provides elan, lean, and lake)",
        "lake": "Install Lean 4 via elan - lean-lang.org/install "
                "(lake ships with the toolchain)",
    }
    supported = True

    manifest_patterns = ["lakefile.toml"]
    # lean/lake are real executables (not .cmd shims) - keep args unparsed
    # by cmd.exe on Windows.
    windows_shell = False

    scanner_runner = ["lean", "--run"]
    scanner_messages = {
        "sorry": "Unproven `sorry`/`admit` found - every theorem must be "
                 "fully proven before checkin:",
        "axiom": "Custom axiom declarations found in src/ - axioms bypass "
                 "the kernel and can prove anything; derive it or drop it:",
        "print": "Console output found in src/ - widgets are libraries, "
                 "return values instead:",
        "sleep": "IO.sleep found in src/ - widgets must not block the "
                 "caller:",
        "abs_path": "Absolute paths found - widgets must be portable:",
        "credential": "Possible credentials found - remove before checkin:",
        "hardcoded_ip": "Hardcoded IPs found - make these configurable:",
        "scan_error": "Scanner could not read source files:",
    }
    scanner_warning_messages = {
        "native_decide": "native_decide found - it trusts the compiled "
                         "evaluator instead of the kernel; prefer decide "
                         "or an explicit proof:",
        "unsafe": "unsafe definitions in src/ - these escape the type "
                  "system; verify each is unavoidable:",
        "partial": "partial defs in src/ - the termination proof is "
                   "skipped; prefer structural/well-founded recursion or "
                   "a fuel parameter:",
        "env_var": "Environment variable access - verify it's not "
                   "project-specific:",
        "hardcoded_url": "Hardcoded URLs found - consider making these "
                         "configurable:",
        "hardcoded_value": "Hardcoded numeric constants - consider making "
                           "these parameters:",
        "unlisted_import": "Imports not declared in widget.json "
                           "dependencies:",
    }

    # ---- toolchain ---------------------------------------------------------

    def runtime_version(self):
        try:
            res = self._run(["lean", "--version"], cwd=".", timeout=30)
            if res.returncode == 0:
                # "Lean (version 4.32.2, x86_64-unknown-linux-gnu, ...)"
                m = _re.search(r"version\s+([\w.\-]+)", res.stdout or "")
                if m:
                    return f"lean {m.group(1)}"
        except Exception:
            pass
        return None

    # ---- scaffold ----------------------------------------------------------

    def scaffold(self, target_dir, module_name, display_name, **_):
        slug = module_name.replace("-", "_")

        def _w(path, content):
            with open(path, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)

        _w(os.path.join(target_dir, "lakefile.toml"),
           _LAKEFILE.format(slug=slug))
        for d in ("src", "tests", "examples"):
            os.makedirs(os.path.join(target_dir, d), exist_ok=True)
        _w(os.path.join(target_dir, "src", f"{slug}.lean"),
           _LEAN_SRC.format(name=display_name))
        _w(os.path.join(target_dir, "tests", f"test_{slug}.lean"),
           _LEAN_TEST.format(slug=slug, name=display_name))
        _w(os.path.join(target_dir, "examples", "example_usage.lean"),
           _LEAN_EXAMPLE.format(slug=slug, name=display_name))

    def find_test_files(self, path):
        return _glob.glob(os.path.join(path, "tests", "**", "test_*.lean"),
                          recursive=True)

    def example_filename(self, path=""):
        return "example_usage.lean"

    def src_import_pattern(self):
        # The example must import the widget's module by name.
        return r"^\s*import\s+\w"

    def required_files(self, path):
        if os.path.isfile(os.path.join(path, "lakefile.toml")):
            return []
        return [("lakefile.toml",
                 "A lakefile.toml is required at the widget root - run "
                 "'cartograph create' to scaffold it")]

    # ---- validation --------------------------------------------------------

    def validate_widget(self, path, dependencies):
        # Base runs the native scanner (sorry/axiom/... blocks) + dep pinning.
        result = super().validate_widget(path, dependencies)
        errors = []
        if not result.get("passed", False):
            errors.append(result.get("error", ""))

        # Compile check: lake build type-checks src/ and kernel-checks every
        # proof. A `sorry` only warns, so also parse the output for it - the
        # scanner catches the token, this catches anything it misses (e.g.
        # a tactic that elaborates to sorry).
        build = self._lake_build(path)
        if build is not None:
            errors.append(build)

        if errors:
            out = self._fail("\n".join(e for e in errors if e))
        else:
            out = self._ok()
        if result.get("warnings"):
            out["warnings"] = result["warnings"]
        return out

    def _sync_lakefile_globs(self, path):
        """Keep the lean_lib's module list in sync with src/**/*.lean.

        Lake builds only the declared module roots, so a second src module
        would silently not build (and its imports would fail) unless listed.
        There is no wildcard glob, so - like Java's validator-managed
        cartograph-deps.gradle - the validator owns this line: the `globs`
        entry after `srcDir = "src"` is regenerated on every build.
        Idempotent; leaves the rest of the lakefile untouched.
        """
        lakefile = os.path.join(path, "lakefile.toml")
        src_files = _glob.glob(os.path.join(path, "src", "**", "*.lean"),
                               recursive=True)
        if not os.path.isfile(lakefile) or not src_files:
            return
        modules = sorted(
            os.path.splitext(os.path.relpath(f, os.path.join(path, "src")))[0]
            .replace(os.sep, ".").replace("/", ".")
            for f in src_files)
        globs_line = ("globs = ["
                      + ", ".join(f'"{m}"' for m in modules)
                      + "]  # managed by cartograph - one entry per src module")
        with open(lakefile, encoding="utf-8") as f:
            lines = f.read().splitlines()
        out = []
        synced = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("globs =") and "managed by cartograph" in line:
                continue
            out.append(line)
            if not synced and stripped == 'srcDir = "src"':
                out.append(globs_line)
                synced = True
        if synced:
            with open(lakefile, "w", newline="\n", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")

    def _lake_build(self, path):
        """Run lake build; return an error string or None on success."""
        self._sync_lakefile_globs(path)
        mathlib_err = self._sync_mathlib_require(path)
        if mathlib_err is not None:
            return mathlib_err
        timeout = (_BUILD_TIMEOUT_MATHLIB if self._wants_mathlib(path)
                   else _BUILD_TIMEOUT)
        try:
            res = self._run(["lake", "build"], cwd=path, timeout=timeout)
        except FileNotFoundError:
            return ("lake not found - install Lean 4 via elan "
                    "(lean-lang.org/install).")
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0:
            return f"lake build failed:\n{out.strip()[:3000]}"
        if _SORRY_WARNING in out:
            lines = [ln for ln in out.splitlines() if _SORRY_WARNING in ln]
            return ("Unproven `sorry` found - the build only warns, but "
                    "Cartograph requires every proof completed:\n"
                    + "\n".join(lines)[:2000])
        return None

    def scan_contamination(self, path, widget):
        all_files = []
        for sub in ("src", "tests", "examples"):
            all_files.extend(_glob.glob(
                os.path.join(path, sub, "**", "*.lean"), recursive=True))
        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "lean_scanner.lean")
        scan_errors, scan_warnings, scan_blocks = self._run_native_scanner(
            scanner_path=scanner,
            runner=self.scanner_runner,
            src_files=all_files,
            cwd=path,
            finding_messages=self.scanner_messages,
        )
        return {"blocks": scan_blocks + scan_errors,
                "warnings": scan_warnings}

    # ---- dependencies ------------------------------------------------------

    def install_deps(self, path, dependencies):
        # The only supported external dependency is `mathlib`, served from
        # the shared pinned workspace (never fetched per-widget, never
        # auto-installed - ML-frameworks policy). Everything else builds
        # against the Lean core library only.
        names = [d if isinstance(d, str) else (d or {}).get("name")
                 for d in (dependencies or [])]
        others = [n for n in names if n != "mathlib"]
        if others:
            raise RuntimeError(
                "The Lean engine supports only `mathlib` as an external "
                f"dependency - remove {others} from widget.json. Widgets "
                "otherwise build against the Lean core library only."
            )
        if "mathlib" in names:
            from ..mathlib_setup import mathlib_status, \
                missing_workspace_error
            state = mathlib_status()
            if not state.ready:
                raise RuntimeError(missing_workspace_error(state.reason))

    @staticmethod
    def _check_dep_pinning(dependencies):
        # `mathlib` is engine-pinned (one workspace pin per Cartograph
        # release), so the generic version-floor rule does not apply to it -
        # a widget-level pin would be ignored and only mislead.
        from .base import LanguageEngine
        rest = [d for d in dependencies
                if (d if isinstance(d, str)
                    else (d or {}).get("name")) != "mathlib"]
        return LanguageEngine._check_dep_pinning(rest)

    @staticmethod
    def _wants_mathlib(path):
        """True when the widget declares a mathlib dependency."""
        try:
            with open(os.path.join(path, "widget.json"),
                      encoding="utf-8") as f:
                deps = _json.load(f).get("tech_stack", {}).get(
                    "dependencies", [])
        except (OSError, ValueError):
            return False
        return any((d if isinstance(d, str) else (d or {}).get("name"))
                   == "mathlib" for d in deps)

    def _sync_mathlib_require(self, path):
        """Point a mathlib widget at the shared workspace for this build.

        Injects a managed [[require]] path entry into lakefile.toml and a
        matching lean-toolchain file. Both carry machine-specific state
        (an absolute path; a pin the engine owns), so they are transient:
        regenerated before every build, stripped by cleanup() so neither
        is ever checked in. Returns an error string, or None.
        """
        lakefile = os.path.join(path, "lakefile.toml")
        wants = self._wants_mathlib(path)
        with open(lakefile, encoding="utf-8") as f:
            content = f.read()
        blocks = content.split("\n[[require]]")
        kept = [blocks[0]] + [b for b in blocks[1:]
                              if _MATHLIB_REQUIRE_MARK not in b]
        content = "\n[[require]]".join(kept)
        if not wants:
            with open(lakefile, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)
            return None
        from ..mathlib_setup import (MATHLIB_TOOLCHAIN, mathlib_package_dir,
                                     mathlib_status,
                                     missing_workspace_error)
        state = mathlib_status()
        if not state.ready:
            return missing_workspace_error(state.reason)
        pkg = mathlib_package_dir().replace("\\", "/")
        content += (f"\n[[require]]\n{_MATHLIB_REQUIRE_MARK}\n"
                    f'name = "mathlib"\npath = "{pkg}"\n')
        with open(lakefile, "w", newline="\n", encoding="utf-8") as f:
            f.write(content)
        with open(os.path.join(path, "lean-toolchain"), "w",
                  newline="\n", encoding="utf-8") as f:
            f.write(MATHLIB_TOOLCHAIN + "\n")
        self._seed_mathlib_packages(path)
        return None

    def _seed_mathlib_packages(self, path):
        """Pre-seed the widget's lockfile and packages from the workspace.

        Without this, the widget's first `lake build` git-clones Mathlib's
        transitive dependencies (batteries, aesop, proofwidgets, ...) even
        though identical pinned checkouts already sit in the shared
        workspace - a per-widget network fetch. Copying the workspace's
        resolved packages plus a rewritten lockfile (mathlib flipped to a
        path entry) lets Lake resolve everything from disk. Best-effort: on
        any failure the seed is skipped and Lake falls back to fetching.
        """
        from ..mathlib_setup import MATHLIB_PIN, mathlib_package_dir, \
            mathlib_root
        from cg.infra_mathlib_workspace_python.src.mathlib_workspace import (
            seed_manifest, workspace_path)
        widget_manifest = os.path.join(path, "lake-manifest.json")
        if os.path.isfile(widget_manifest):
            return
        ws = str(workspace_path(mathlib_root(), MATHLIB_PIN))
        try:
            with open(os.path.join(ws, "lake-manifest.json"),
                      encoding="utf-8") as f:
                plan = seed_manifest(f.read(), mathlib_package_dir())
        except (OSError, ValueError):
            return
        pkg_src = os.path.join(ws, ".lake", "packages")
        pkg_dst = os.path.join(path, ".lake", "packages")
        for name in plan.package_names:
            src = os.path.join(pkg_src, name)
            dst = os.path.join(pkg_dst, name)
            if os.path.isdir(src) and not os.path.isdir(dst):
                try:
                    _shutil.copytree(src, dst, symlinks=True)
                except OSError:
                    return
        with open(widget_manifest, "w", newline="\n",
                  encoding="utf-8") as f:
            f.write(plan.manifest_text)

    # ---- tests + example ---------------------------------------------------

    def run_tests(self, path):
        tests = self.find_test_files(path)
        if not tests:
            return self._fail("No test files found in tests/ "
                              "(expected tests/test_<name>.lean)")
        # Build src first so test imports resolve from .lake/build.
        build = self._lake_build(path)
        if build is not None:
            return self._fail(build)
        run_timeout = (_RUN_TIMEOUT_MATHLIB if self._wants_mathlib(path)
                       else _RUN_TIMEOUT)
        for tf in sorted(tests):
            rel = os.path.relpath(tf, path)
            try:
                res = self._run(["lake", "env", "lean", "--run", tf],
                                cwd=path, timeout=run_timeout)
            except FileNotFoundError:
                return self._fail("lake not found - install Lean 4 via elan "
                                  "(lean-lang.org/install).")
            out = (res.stdout or "") + (res.stderr or "")
            if res.returncode != 0:
                return self._fail(f"{rel} exited {res.returncode}:\n"
                                  f"{out.strip()[:2000]}")
            if _SORRY_WARNING in out:
                return self._fail(f"{rel} contains an unproven `sorry` - "
                                  f"every test-level proof must be complete.")
            if "ASSERT_FAIL" in out:
                fails = [ln for ln in out.splitlines() if "ASSERT_FAIL" in ln]
                return self._fail(f"{rel} reported failures:\n"
                                  + "\n".join(fails)[:2000])
            if "ASSERT_PASS" not in out:
                return self._fail(
                    f"{rel} produced no ASSERT_PASS - a test's `main` must "
                    f"assert at least one runtime property "
                    f"(IO.println \"ASSERT_PASS ...\") so a clean exit means "
                    f"behavior was actually checked. Proof-level assertions "
                    f"(example/theorem) are checked automatically on top.")
        return self._ok()

    def run_example(self, path):
        ex = os.path.join(path, "examples", self.example_filename())
        if not os.path.isfile(ex):
            return self._fail("examples/example_usage.lean not found")
        build = self._lake_build(path)
        if build is not None:
            return self._fail(build)
        run_timeout = (_RUN_TIMEOUT_MATHLIB if self._wants_mathlib(path)
                       else _RUN_TIMEOUT)
        try:
            res = self._run(["lake", "env", "lean", "--run", ex],
                            cwd=path, timeout=run_timeout)
        except FileNotFoundError:
            return self._fail("lake not found - install Lean 4 via elan "
                              "(lean-lang.org/install).")
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0:
            return self._fail(out.strip()[:2000])
        if _SORRY_WARNING in out:
            return self._fail("examples/example_usage.lean contains an "
                              "unproven `sorry`.")
        return self._ok()

    def wire_blueprint_dep(self, blueprint_dir, dep_id, dep_dir):
        """Add a lake path-require for a composed widget.

        Lake resolves `import <slug>` through [[require]] entries; a path
        require pointing at cg/<dep_id>/ (the layout the validator sandbox
        populates) makes the composed widget's lib build as part of the
        blueprint workspace - same shape as Rust's Cargo path deps.
        """
        lakefile = os.path.join(blueprint_dir, "lakefile.toml")
        if not os.path.isfile(lakefile):
            return
        dep_name = self._lakefile_name(dep_dir) or dep_id.replace("-", "_")
        rel_path = f"cg/{dep_id}"
        with open(lakefile, encoding="utf-8") as f:
            content = f.read()
        if f'path = "{rel_path}"' in content:
            return
        entry = (f'\n[[require]]\nname = "{dep_name}"\n'
                 f'path = "{rel_path}"\n')
        with open(lakefile, "a", newline="\n", encoding="utf-8") as f:
            f.write(entry)

    def unwire_blueprint_dep(self, blueprint_dir, dep_id):
        lakefile = os.path.join(blueprint_dir, "lakefile.toml")
        if not os.path.isfile(lakefile):
            return
        with open(lakefile, encoding="utf-8") as f:
            content = f.read()
        rel_path = f"cg/{dep_id}"
        blocks = content.split("\n[[require]]")
        kept = [blocks[0]] + [b for b in blocks[1:]
                              if f'path = "{rel_path}"' not in b]
        new = "\n[[require]]".join(kept)
        if new != content:
            with open(lakefile, "w", newline="\n", encoding="utf-8") as f:
                f.write(new)

    @staticmethod
    def _lakefile_name(widget_dir):
        """Package name from a widget's lakefile.toml, or None."""
        lakefile = os.path.join(widget_dir, "lakefile.toml")
        try:
            import tomllib
            with open(lakefile, "rb") as f:
                return tomllib.load(f).get("name")
        except Exception:
            return None

    def run_blueprint_example(self, sandbox, example_file):
        """Blueprint sandboxes are shaped like widget roots; delegate to the
        engine's own runner (base would invoke python on the file)."""
        result = self.run_example(sandbox)
        return {"passed": bool(result.get("passed", False)),
                "error": result.get("error", "") or ""}

    # ---- cleanup -----------------------------------------------------------

    def cleanup(self, path):
        # .lake holds the entire build output; widgets carry sources only.
        # lake-manifest.json is the dep lockfile - the mathlib require is
        # injected per-machine, so the lockfile is build noise, not source.
        _shutil.rmtree(os.path.join(path, ".lake"), ignore_errors=True)
        try:
            os.remove(os.path.join(path, "lake-manifest.json"))
        except OSError:
            pass
        # Strip the transient mathlib wiring: the managed require carries an
        # absolute machine path and lean-toolchain carries the engine-owned
        # pin - neither belongs in a checked-in widget.
        lakefile = os.path.join(path, "lakefile.toml")
        if os.path.isfile(lakefile):
            with open(lakefile, encoding="utf-8") as f:
                content = f.read()
            blocks = content.split("\n[[require]]")
            kept = [blocks[0]] + [b for b in blocks[1:]
                                  if _MATHLIB_REQUIRE_MARK not in b]
            new = "\n[[require]]".join(kept)
            if new != content:
                with open(lakefile, "w", newline="\n",
                          encoding="utf-8") as f:
                    f.write(new)
        if self._wants_mathlib(path):
            try:
                os.remove(os.path.join(path, "lean-toolchain"))
            except OSError:
                pass
        self._cleanup_artifact_dirs(path)
