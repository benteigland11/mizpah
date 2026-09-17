"""Nim language engine - uses nim + nimble test.

Source scanning is handled by scanners/nim_scanner.nim (native Nim) for
string/comment-aware detection. Custom validation adds nim check and
compile check steps on top of the base scanner pipeline.
"""

import glob as _glob
import os
import re
import shutil
import tempfile

from .base import LanguageEngine, _dep_bare_name, log

# Starter file contents for scaffold
_NIMBLE_TEMPLATE = '''\
# Package
version = "0.1.0"
author = "Widget Author"
description = "{name}"
license = "MIT"
srcDir = "src"

# Dependencies
requires "nim >= 2.0.0"

# Tasks
task test, "run tests":
  for f in listFiles("tests"):
    if f.endsWith(".nim"):
      exec "nimble c -r --path:src " & f
'''

_SRC_TEMPLATE = '''\
## {name}

func {module}*(value: string): string =
  ## Process a value.
  value
'''

_TEST_TEMPLATE = '''\
import std/unittest
import {src_module}

suite "{name}":
  test "placeholder":
    # TODO: replace with real tests
    check true
'''

_EXAMPLE_TEMPLATE = '''\
## Example usage of {name}.
##
## This file must compile and run cleanly with no user input,
## no network calls, and no external services. Use fake/hardcoded
## data to demonstrate the API.

import {src_module}

# [TODO] Replace with a realistic call using fake data
let result = {module}("hello")
discard result  # replace with meaningful output or assertions
'''


class NimEngine(LanguageEngine):
    name = "nim"
    validation_version = 3
    file_ext = "nim"
    toolchain = {
        "nim": "Install Nim 2.0+ - nim-lang.org",
        "nimble": "Reinstall Nim - nimble ships with it",
    }
    scanner_runner = ["nim", "r", "--hints:off", "--warnings:off"]
    scanner_messages = {
        "echo": "echo found in src/ - remove debug output before checkin:",
        "quit": "quit() found in src/ - widgets must not exit the process:",
        "ffi": "C FFI found - Cartograph widgets are pure Nim. For C bindings, publish a nimble package and depend on it via `dependencies`:",
        "global": "{.global.} found in src/ - widgets must not use global mutable state:",
        "main_module": "when isMainModule found in src/ - widgets are libraries, not executables:",
        "os_specific": "OS-specific when defined() found in src/ - widgets must validate on all platforms:",
        "cast_seq": "cast[seq[...]] found in src/ - GC-managed seq cast is a memory-safety hazard:",
    }
    scanner_warning_messages = {
        "risky_import": "Stdlib I/O or network imports found in src/ - ensure no hardcoded paths, URLs, or commands:",
        "abs_path": "Absolute paths found in src/ - widgets must be portable:",
        "credential": "Possible credentials found in src/ - remove before checkin:",
        "hardcoded_url": "Hardcoded URLs found in src/ - consider making these configurable:",
        "hardcoded_ip": "Hardcoded IPs found in src/ - consider making these configurable:",
        "hardcoded_value": "Hardcoded values found in src/ - consider making these configurable:",
        "env_var": "Environment variable access found in src/ - verify it's not project-specific:",
        "unlisted_import": "Unlisted imports - add to widget.json dependencies or remove:",
        "sleep": "Sleep/blocking calls found - widgets must not block the caller:",
        "std_import_style": "Old-style stdlib imports found - prefer std/... imports in modern Nim:",
        "top_level_var": "Top-level mutable state found - prefer local state or explicit parameters:",
        "cast_seq": "cast[seq[...]] in tests/examples - GC-managed seq cast is a memory-safety hazard:",
        "bare_except": "Bare `except:` clauses catch Defect/KeyboardInterrupt - use a typed except (e.g. except CatchableError):",
        "raw_memory": "Raw memory primitives found - widgets should prefer GC-managed types unless there is a documented reason:",
    }
    import_pattern = r'^import\s+\w+'
    manifest_patterns = ["*.nimble"]

    def runtime_version(self) -> str | None:
        try:
            res = self._run(["nim", "--version"], cwd=".", timeout=10)
            if res.returncode == 0:
                first_line = res.stdout.strip().splitlines()[0]
                # "Nim Compiler Version 2.0.4 [Linux: amd64]"
                parts = first_line.split()
                if len(parts) >= 4:
                    return f"nim {parts[3]}"
        except Exception:
            pass
        return None

    def scaffold(self, target_dir, module_name, display_name, **_):
        # Nim stdlib modules are resolved before --path:src, so suffix with _lib
        # to guarantee no collision with any stdlib module.
        src_module = f"{module_name}_lib"

        with open(os.path.join(target_dir, f"{module_name}.nimble"), "w", encoding="utf-8") as f:
            f.write(_NIMBLE_TEMPLATE.format(name=display_name))
        with open(os.path.join(target_dir, "src", f"{src_module}.nim"), "w", encoding="utf-8") as f:
            f.write(_SRC_TEMPLATE.format(name=display_name, module=module_name))
        with open(os.path.join(target_dir, "tests", f"test_{module_name}.nim"), "w", encoding="utf-8") as f:
            f.write(_TEST_TEMPLATE.format(name=display_name, src_module=src_module))
        with open(os.path.join(target_dir, "examples", "example_usage.nim"), "w", encoding="utf-8") as f:
            f.write(_EXAMPLE_TEMPLATE.format(name=display_name, module=module_name, src_module=src_module))

    # -- Custom validation (nim check + compile check on top of base scanner) --

    def _nim_check(self, src_files, path, src_dir, cmd_prefix, label, timeout=60):
        """Run a nim command on each src file, return errors (skipping missing-import failures)."""
        errors = []
        for fpath in src_files:
            rel = os.path.relpath(fpath, path)
            try:
                res = self._run(
                    cmd_prefix + [f"--path:{src_dir}", fpath],
                    cwd=path, timeout=timeout,
                )
                if res.returncode != 0:
                    output = (res.stderr or res.stdout).strip()
                    lines = output.splitlines()
                    if any("cannot find module" in l.lower() or "cannot open file" in l.lower()
                           for l in lines):
                        continue
                    real_errors = [l for l in lines if l.strip()]
                    if real_errors:
                        errors.append(f"{label} failed on {rel}:\n" + "\n".join(real_errors))
            except FileNotFoundError:
                pass
        return errors

    def validate_widget(self, path: str, dependencies: list) -> dict:
        errors = []

        # 1. src/ must contain at least one .nim file
        src_dir = os.path.join(path, "src")
        src_files = _glob.glob(os.path.join(src_dir, "**", "*.nim"), recursive=True)
        if not src_files:
            errors.append("src/ contains no .nim files - add at least one source file")

        # Allocate a per-validation nim cache dir so `nim check` and `nim c`
        # never write to ~/.cache/nim. This makes Cartograph robust against
        # sandboxed environments (Codex, restricted devcontainers) where $HOME
        # is read-only. Scoped strictly to this method - we clean it up in
        # the finally block below so it doesn't depend on engine.cleanup().
        nimcache = tempfile.mkdtemp(prefix="cartograph_nimcache_")
        self._nimcache_dir = nimcache
        try:
            # 2. Semantic check - nim check catches type errors, undefined symbols, bad syntax
            errors.extend(self._nim_check(src_files, path, src_dir,
                                          ["nim", "check", "--hints:off", "--warnings:off",
                                           f"--nimcache:{nimcache}"],
                                          "nim check", timeout=60))

            # 3. Compile check - catches linker/codegen issues nim check misses
            errors.extend(self._nim_check(src_files, path, src_dir,
                                          ["nim", "c", "--compileOnly", "--hints:off", "--warnings:off",
                                           f"--nimcache:{nimcache}"],
                                          "nim compile", timeout=120))

            # 4. Native source scanner (uses base class helper)
            scanner = os.path.join(os.path.dirname(__file__), "scanners", "nim_scanner.nim")
            scan_errors, _, _ = self._run_native_scanner(
                scanner_path=scanner,
                runner=self.scanner_runner,
                src_files=src_files,
                cwd=path,
                finding_messages=self.scanner_messages,
            )
            errors.extend(scan_errors)

            # 5. Dependencies must have a version floor
            errors.extend(self._check_dep_pinning(dependencies))

            if errors:
                return self._fail("\n".join(errors))
            return self._ok()
        finally:
            self._cleanup_nimcache_dir()

    def scan_contamination(self, path: str, widget: dict) -> dict:
        """Nim contamination: native scanner on src + test + example files."""
        src_files = _glob.glob(os.path.join(path, "src", "**", "*.nim"), recursive=True)
        test_files = _glob.glob(os.path.join(path, "tests", "**", "*.nim"), recursive=True)
        example_files = _glob.glob(os.path.join(path, "examples", "**", "*.nim"), recursive=True)
        all_files = src_files + test_files + example_files
        scanner = os.path.join(os.path.dirname(__file__), "scanners", "nim_scanner.nim")
        scan_errors, scan_warnings, scan_blocks = self._run_native_scanner(
            scanner_path=scanner,
            runner=self.scanner_runner,
            src_files=all_files,
            cwd=path,
            finding_messages=self.scanner_messages,
        )

        return {"blocks": scan_blocks + scan_errors, "warnings": scan_warnings}

    def required_files(self, path: str) -> list[tuple[str, str]]:
        if _glob.glob(os.path.join(path, "*.nimble")):
            return []
        return [("<widget>.nimble", "A .nimble file is required at the widget root - run 'cartograph create' to scaffold it")]

    # -- Custom install/test/example (nimble isolation) --

    def install_deps(self, path: str, dependencies: list) -> None:
        if not dependencies:
            self._nimble_dir = None
            return

        # Isolated install - each validation gets its own NIMBLE_DIR
        tmpdir = tempfile.mkdtemp(prefix="cartograph_nim_")
        self._nimble_dir = tmpdir
        env = {**os.environ, "NIMBLE_DIR": tmpdir}
        log.debug("Nim isolated env: NIMBLE_DIR=%s", tmpdir)

        for dep in dependencies:
            bare = _dep_bare_name(dep)
            if not bare:
                continue
            log.debug("Installing Nim package: %s", bare)
            res = self._run(["nimble", "install", "-y", bare], cwd=path, timeout=120, env=env)
            if res.returncode != 0:
                output = (res.stderr or res.stdout or "").strip()
                raise RuntimeError(
                    f"Failed to install Nim dependency '{bare}'."
                    + (f"\n{output[:2000]}" if output else "")
                )

        self._sync_nimble_requires(path, dependencies)

    def run_tests(self, path: str) -> dict:
        env = self._nimble_env()
        try:
            res = self._run(["nimble", "test", "-y"], cwd=path, timeout=120, env=env)
        except FileNotFoundError:
            return self._fail("Nim not found - install Nim toolchain.")
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    def run_example(self, path: str) -> dict:
        ep = os.path.join(path, "examples", self.example_filename())
        src_path = os.path.join(path, "src")
        env = self._nimble_env()
        cmd = ["nim", "r", f"--path:{src_path}"]
        nimble_dir = getattr(self, "_nimble_dir", None)
        if nimble_dir:
            pkgs2 = os.path.join(nimble_dir, "pkgs2")
            if os.path.isdir(pkgs2):
                for pkg in os.listdir(pkgs2):
                    pkg_path = os.path.join(pkgs2, pkg)
                    if os.path.isdir(pkg_path):
                        cmd.append(f"--path:{pkg_path}")
        cmd.append(ep)
        try:
            res = self._run(cmd, cwd=path, timeout=60, env=env)
        except FileNotFoundError:
            return self._fail("Nim not found - install Nim toolchain.")
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    def run_blueprint_example(self, sandbox: str, example_file: str) -> dict:
        """Compile and run a blueprint example with explicit search paths
        for the blueprint's own src/ and every dep widget's src/ under
        sandbox/cg/. The base default invokes python on the file, which
        is wrong for any compiled language."""
        ep = os.path.join(sandbox, "examples", example_file)
        env = self._nimble_env()
        cmd = ["nim", "r", "--hints:off", "--warnings:off"]
        cmd.append(f"--path:{os.path.join(sandbox, 'src')}")
        cg_root = os.path.join(sandbox, "cg")
        if os.path.isdir(cg_root):
            for entry in os.listdir(cg_root):
                dep_src = os.path.join(cg_root, entry, "src")
                if os.path.isdir(dep_src):
                    cmd.append(f"--path:{dep_src}")
        cmd.append(ep)
        try:
            res = self._run(cmd, cwd=sandbox, timeout=120, env=env)
        except FileNotFoundError:
            return {"passed": False, "error": "Nim not found - install Nim toolchain."}
        if res.returncode != 0:
            return {"passed": False,
                    "error": (res.stderr or res.stdout or "").strip()}
        return {"passed": True}

    def cleanup(self, path: str) -> None:
        self._cleanup_nimble_dir()
        self._cleanup_nimcache_dir()
        self._cleanup_compiled_binaries(path)

    # -- Private helpers --

    def _nimble_env(self) -> dict:
        nimble_dir = getattr(self, "_nimble_dir", None)
        if nimble_dir:
            return {**os.environ, "NIMBLE_DIR": nimble_dir}
        return os.environ.copy()

    def _cleanup_nimble_dir(self) -> None:
        nimble_dir = getattr(self, "_nimble_dir", None)
        if nimble_dir and os.path.exists(nimble_dir):
            shutil.rmtree(nimble_dir, ignore_errors=True)
            log.debug("Removed isolated Nim env: %s", nimble_dir)
        self._nimble_dir = None

    def _cleanup_nimcache_dir(self) -> None:
        nimcache_dir = getattr(self, "_nimcache_dir", None)
        if nimcache_dir and os.path.exists(nimcache_dir):
            shutil.rmtree(nimcache_dir, ignore_errors=True)
            log.debug("Removed isolated nim cache: %s", nimcache_dir)
        self._nimcache_dir = None

    def _cleanup_compiled_binaries(self, path: str) -> None:
        """Remove compiled test/example binaries left by nimble c -r."""
        for subdir in ("tests", "examples"):
            dirpath = os.path.join(path, subdir)
            if not os.path.isdir(dirpath):
                continue
            for fname in os.listdir(dirpath):
                fpath = os.path.join(dirpath, fname)
                if os.path.isfile(fpath) and not os.path.splitext(fname)[1]:
                    # No extension = likely compiled binary
                    try:
                        with open(fpath, "rb") as f:
                            header = f.read(4)
                        # ELF binary or Mach-O or PE
                        if header[:4] in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"MZ\x90\x00"):
                            os.remove(fpath)
                            log.debug("Removed compiled binary: %s", fpath)
                    except Exception:
                        pass

    def _sync_nimble_requires(self, path: str, dependencies: list) -> None:
        """Keep .nimble requires in sync with widget.json dependencies."""
        matches = _glob.glob(os.path.join(path, "*.nimble"))
        if not matches:
            return
        nimble_path = matches[0]
        if not os.path.exists(nimble_path):
            return
        try:
            with open(nimble_path, encoding="utf-8") as f:
                content = f.read()

            wanted: dict[str, str] = {}
            for dep in dependencies:
                bare = _dep_bare_name(dep).lower()
                if bare and bare != "nim":
                    wanted[bare] = dep

            declared: set[str] = set()
            lines = content.splitlines(keepends=True)
            new_lines = []
            for line in lines:
                m = re.match(r'(\s*requires\s+")([^"\s>=<!]+)([^"]*".*)', line)
                if m:
                    bare = m.group(2).lower()
                    declared.add(bare)
                    if bare in wanted:
                        new_lines.append(f'requires "{wanted[bare]}"\n')
                        log.debug("Updated requires for %s in %s", bare,
                                  os.path.basename(nimble_path))
                        continue
                new_lines.append(line)

            additions = [
                f'requires "{dep}"\n'
                for bare, dep in wanted.items()
                if bare not in declared
            ]
            new_lines.extend(additions)

            new_content = "".join(new_lines)
            if new_content != content:
                with open(nimble_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                log.debug("Synced %d dep(s) to %s", len(additions),
                          os.path.basename(nimble_path))
        except Exception as e:
            log.debug("Could not sync .nimble requires: %s", e)
