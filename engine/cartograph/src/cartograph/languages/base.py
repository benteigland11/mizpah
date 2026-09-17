"""
Base class for language engines.

To add a new language engine (simple case - no custom validation logic):

  1. Create languages/<lang>.py:

     class LuaEngine(LanguageEngine):
         name = "lua"
         file_ext = "lua"
         toolchain = {"lua": "Install Lua - lua.org",
                      "luarocks": "Install LuaRocks - luarocks.org"}
         test_command = ["lua", "tests/run_tests.lua"]
         example_command = ["lua"]
         dep_install_command = ["luarocks", "install"]
         scanner_runner = ["lua"]
         import_pattern = r'require.*src%.'

  2. Create a native scanner at languages/scanners/lua_scanner.lua
     that outputs a JSON array of {kind, file, line, detail} objects.
     Define scanner_messages on the class to map kind -> human-readable header.

  3. Add a scaffold() method on the engine class to write starter files
     for src/, tests/, and examples/. This keeps everything about the
     language in one place.

  That's it. The engine is auto-discovered - no registry edits needed.
  Doctor picks it up automatically via check_available().

  For custom behavior (compile checks, React detection, etc.), override
  any method. See nim.py and javascript.py for examples.

Return shape for run_tests / validate_widget / run_example:
  {"passed": True}
  {"passed": False, "error": "<human-readable explanation>"}
"""

import glob as _glob
import logging
import os
import re
import subprocess
import sys
import tempfile

from cg.infra_binary_resolver_python.src.binary_resolver import (
    ResolveError,
    ResolvedBinary,
    resolve as _resolve_binary,
)

log = logging.getLogger("cartograph")


def _override_for(binary: str) -> str | None:
    """Look up a user-configured override for a binary name.

    Kept as a module-level helper so base.py stays importable during
    pytest collection of widgets, where cartograph.config may load a
    config.toml outside the repo.
    """
    try:
        from ..config import get_path_override
        return get_path_override(binary)
    except Exception:
        return None


def _resolve_or_passthrough(binary: str) -> ResolvedBinary | None:
    """Resolve a binary via config override + PATH, or return None.

    None means "no override configured and not on PATH" - which is fine
    for call sites like _run(), where subprocess does its own PATH
    lookup and can surface a clearer native error. The caller only
    substitutes when a ResolvedBinary is returned.
    """
    override = _override_for(binary)
    try:
        return _resolve_binary(binary, override=override,
                               override_key=f"paths.{binary}")
    except ResolveError:
        # With an override set, ResolveError means the override is
        # broken (missing/non-executable). Let it surface later through
        # the normal code path; callers that need to fail loud can call
        # resolve() themselves.
        if override:
            raise
        return None


# Compiled once at module level — used by _dep_bare_name and _check_dep_pinning.
_DEP_SPLIT_RE = re.compile(r'[><=!~;\[]')
_PIN_RE = re.compile(r'[><=!~]|\d')


def _dep_bare_name(dep) -> str:
    """Return the bare package name from a dep string like 'fastapi>=0.128.0' or a dict."""
    if isinstance(dep, dict):
        return dep.get("name", "")
    return _DEP_SPLIT_RE.split(dep)[0].strip()


class LanguageEngine:
    name = "base"
    supported = True       # set False to hide a WIP engine from users
    aliases: list = []     # short names, e.g. ["js"] for javascript

    # -- Declarative attributes (set these instead of overriding methods) ------
    # If set, the base class provides default implementations for
    # check_available, validate_widget, install_deps, run_tests, run_example,
    # find_test_files, and example_filename automatically.

    file_ext: str = ""               # e.g. "lua", "nim", "js" - the source file extension
    toolchain: dict = {}             # {"binary": "install hint"} - checked by check_available()
    test_command: list = []          # e.g. ["lua", "tests/run_tests.lua"] - run from widget root
    example_command: list = []       # e.g. ["lua"] - example file path is appended
    dep_install_command: list = []   # e.g. ["luarocks", "install"] - dep name is appended
    scanner_runner: list = []        # e.g. ["lua"] - runs scanners/<name>_scanner.<ext>
    scanner_messages: dict = {}      # {finding_kind: "human-readable header"} - errors (block checkin)
    scanner_warning_messages: dict = {}  # {finding_kind: "human-readable header"} - warnings (don't block)
    import_pattern: str = ""         # regex for "does example import from src?"
    manifest_patterns: list = []     # e.g. ["*.rockspec"] - added to watched_patterns
    # Project-local dependency directory installed into the widget tree
    # (e.g. ".venv", "node_modules", "vendor"). When set, install_deps can
    # reuse it across runs via the dep cache, and cleanup preserves it.
    # Empty string means this engine has no cacheable per-project install.
    env_dir: str = ""

    # -- Methods (override for custom behavior) --------------------------------

    def runtime_version(self) -> str | None:
        """Return the runtime version string, or None if unavailable.
        Override per engine. Used to stamp widget.json at checkin time."""
        return None

    def check_available(self) -> tuple[bool, str]:
        """Check that all system dependencies for this engine are installed.

        Honors per-binary overrides from config (paths.<binary>) - if the
        user has pointed at an installed binary that isn't on PATH, the
        engine still counts as available.
        """
        if not self.toolchain:
            return True, ""
        missing = []
        bad_override = []
        for name in self.toolchain:
            try:
                _resolve_or_passthrough(name)
            except ResolveError as e:
                bad_override.append(str(e))
                continue
            if not self._binary_available(name):
                missing.append(name)
        if bad_override:
            return False, "; ".join(bad_override)
        if missing:
            hints = [self.toolchain[m] for m in missing]
            return False, (
                f"{self.name.capitalize()} engine requires {' and '.join(missing)} - "
                + "; ".join(hints)
                + f" (or set paths.{missing[0]} in `cartograph config`)"
            )
        return True, ""

    def _binary_available(self, name: str) -> bool:
        """True if `name` can be resolved via override or PATH."""
        resolved = _resolve_or_passthrough(name)
        return resolved is not None

    def resolved_binary(self, name: str) -> ResolvedBinary | None:
        """Public accessor for doctor and similar callers.

        Returns the ResolvedBinary when a binary is locatable (either via
        a config override or via PATH), or None when it isn't found at
        all. Raises ResolveError when an override is set but invalid.
        """
        return _resolve_or_passthrough(name)

    def check_optional(self) -> list[tuple[str, bool, str]]:
        """Return optional capability checks: [(label, installed, detail), ...].
        Override per engine. Doctor renders these as info items."""
        return []

    def validate_widget(self, path: str, dependencies: list) -> dict:
        """Source scanning + dep pinning check. Override to add custom steps."""
        errors = []
        warnings = []

        # Run native scanner if configured
        if self.file_ext and self.scanner_runner:
            src_dir = os.path.join(path, "src")
            src_files = _glob.glob(
                os.path.join(src_dir, "**", f"*.{self.file_ext}"), recursive=True
            )
            scanner = os.path.join(
                os.path.dirname(__file__), "scanners",
                f"{self.name}_scanner.{self.file_ext}"
            )
            scan_errors, scan_warnings, _ = self._run_native_scanner(
                scanner_path=scanner,
                runner=self.scanner_runner,
                src_files=src_files,
                cwd=path,
                finding_messages=self.scanner_messages,
            )
            errors.extend(scan_errors)
            warnings.extend(scan_warnings)

        errors.extend(self._check_dep_pinning(dependencies))

        if errors:
            result = self._fail("\n".join(errors))
        else:
            result = self._ok()
        if warnings:
            result["warnings"] = warnings
        return result

    def scan_contamination(self, path: str, widget: dict) -> dict:
        """Scan for contamination. Override to implement language-specific checks.

        Each language engine must implement detection for the following using
        native tooling (AST, native scanners) that understands the language's
        strings, comments, and code constructs.

        Required checks (blocks - hard failures, no override):
          1. Absolute paths    - /home/, /Users/, /root/, C:\\ in string literals
          2. Credentials       - api_key/secret/token/password assignments in src/
                                 (in tests/, emit as warning instead)
          3. Hardcoded IPs     - IP addresses in src/ string literals
                                 (in tests/, emit as warning instead)
          4. Sleep/blocking    - sleep calls in src/ (widgets must not block the caller)
                                 (in tests/examples, warn if duration > 1 second)

        Required checks (warnings - overridable with --override-warnings):
          5. Hardcoded URLs    - http(s):// URLs (except localhost, example.com, .test TLD)
                                 (warn in both src/ and tests/)
          6. Hardcoded values  - numeric and string constant assignments
          7. Env var access    - language-specific env var APIs
          8. Unlisted imports  - imports not in widget.json dependencies or stdlib

        Both src/ and tests/ files must be scanned. Credentials and IPs in tests/
        are warnings (verify they're fake), not blocks.

        Returns {"blocks": [...], "warnings": [...]}.
        See python.py, javascript.py, nim.py for reference implementations.

        The base class provides a regex fallback that covers checks 1-4.
        Check 5 (sleep/blocking) REQUIRES language-native tooling - sleep calls
        cannot be reliably detected with regex because language-specific call
        syntax must be understood (e.g. time.sleep vs a variable named sleep_timer).
        Each engine must implement sleep detection natively.
        Override with native tooling for accurate detection.
        """
        blocks, warnings = [], []
        src_files, test_files = self._collect_source_files(path)

        abs_path_re = re.compile(r'["\'](?:/home/|/Users/|/root/|[A-Za-z]:[/\\\\])[^"\']{3,}["\']')
        credential_re = re.compile(
            r'(?:api_key|api_secret|secret_key|access_token|auth_token|password|passwd|credential)\s*=\s*["\'][^"\']{6,}["\']',
            re.IGNORECASE,
        )
        url_re = re.compile(
            r'["\']https?://(?!(?:localhost|127\.0\.0\.1|(?:[\w-]+\.)*example\.com|[\w.-]+\.test(?:[/:"\'#?]|$)|schemas?\.))[^"\']{8,}["\']'
        )
        ip_re = re.compile(r'["\'](?:\d{1,3}\.){3}\d{1,3}(?::\d+)?["\']')
        number_re = re.compile(r'^\s*\w+\s*=\s*-?\d+\.?\d*(?:e[+-]?\d+)?\s*$', re.IGNORECASE)

        for fpath in src_files + test_files:
            rel = os.path.relpath(fpath, path)
            is_src = fpath in src_files
            try:
                code = open(fpath, encoding="utf-8").read()
            except Exception as e:
                blocks.append(f"Could not read source file {rel}: {e}")
                continue

            for line_no, line in enumerate(code.splitlines(), 1):
                loc = f"{rel}:{line_no}"
                stripped = line.strip()
                is_comment = stripped.startswith("//") or stripped.startswith("#")
                if is_src:
                    if not is_comment and abs_path_re.search(line):
                        blocks.append(f"Absolute path in {loc}: {stripped}")
                    if credential_re.search(line):
                        blocks.append(f"Possible credential in {loc}: {stripped}")
                    if url_re.search(line):
                        warnings.append(f"Hardcoded URL in {loc}: {stripped}")
                    m_ip = ip_re.search(line)
                    if m_ip:
                        octets = m_ip.group().strip("\"'").split(":")[0].split(".")
                        if any(len(o) >= 2 for o in octets):
                            blocks.append(f"Hardcoded IP in {loc}: {stripped}")
                    if number_re.match(line):
                        warnings.append(f"Hardcoded value in {loc}: {stripped} - consider making this a parameter")
                else:
                    if credential_re.search(line):
                        warnings.append(f"Possible credential in test {loc} - verify it's fake: {stripped}")
                    if url_re.search(line):
                        warnings.append(f"Hardcoded URL in test {loc} - verify it's not project-specific: {stripped}")
                    m_ip = ip_re.search(line)
                    if m_ip:
                        octets = m_ip.group().strip("\"'").split(":")[0].split(".")
                        if any(len(o) >= 2 for o in octets):
                            warnings.append(f"Hardcoded IP in test {loc} - verify it's not project-specific: {stripped}")

        return {"blocks": blocks, "warnings": warnings}

    def _collect_source_files(self, path: str) -> tuple[list[str], list[str]]:
        """Return (src_files, test_files) for this language. Override for multi-ext languages."""
        ext = self.file_ext or "*"
        src_files = _glob.glob(os.path.join(path, "src", "**", f"*.{ext}"), recursive=True)
        test_files = _glob.glob(os.path.join(path, "tests", "**", f"*.{ext}"), recursive=True)
        return src_files, test_files

    def lock_patterns(self, path: str) -> list:
        """Glob patterns for lockfiles whose change invalidates the dep cache.

        Override per engine. The declared dependency set is always part of
        the cache key; these patterns add file-content sensitivity (e.g.
        package-lock.json, composer.lock) so a lockfile edit forces a
        rebuild even when the declared deps are unchanged.
        """
        return []

    def _deps_cached(self, path: str, dependencies: list) -> bool:
        """True if a reusable project-local install already exists for these deps.

        Two gates: the fingerprint (declared deps + lockfiles unchanged) AND
        verify_installed (the env actually contains the declared packages at
        satisfying versions). The second gate makes the cache self-healing
        against a drifted, partial, or hand-edited env.
        """
        if not self.env_dir:
            return False
        from ..dep_cache import deps_satisfied
        if not deps_satisfied(
            path, self.name, dependencies, self.env_dir, self.lock_patterns(path)
        ):
            return False
        if not self.verify_installed(path, dependencies):
            log.debug("Dep cache fingerprint hit but env verification failed for %s "
                      "- forcing reinstall", path)
            return False
        return True

    def verify_installed(self, path: str, dependencies: list) -> bool:
        """Confirm the project-local env satisfies the declared dependencies.

        Override per engine to check installed versions against the declared
        specifiers. Default trusts the fingerprint (engines without a
        per-project env have nothing to verify).
        """
        return True

    def _record_dep_cache(self, path: str, dependencies: list) -> None:
        """Stamp the project-local install so the next run can reuse it."""
        if not self.env_dir:
            return
        from ..dep_cache import record_deps
        record_deps(path, self.name, dependencies, self.lock_patterns(path))

    def install_deps(self, path: str, dependencies: list) -> None:
        """Install dependencies required to run tests."""
        if not self.dep_install_command or not dependencies:
            return
        for dep in dependencies:
            bare = _dep_bare_name(dep)
            if not bare:
                continue
            log.debug("Installing %s package: %s", self.name, bare)
            res = self._run(self.dep_install_command + [bare], cwd=path, timeout=120)
            if res.returncode != 0:
                output = (res.stderr or res.stdout or "").strip()
                raise RuntimeError(
                    f"Failed to install {self.name} dependency '{bare}'."
                    + (f"\n{output[:2000]}" if output else "")
                )

    def run_tests(self, path: str) -> dict:
        """Execute the test suite. Override for custom test runners."""
        if not self.test_command:
            raise NotImplementedError(
                f"{self.name} engine must set test_command or override run_tests()"
            )
        res = self._run(self.test_command, cwd=path, timeout=120)
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    def find_test_files(self, path: str) -> list[str]:
        """Return list of test files in tests/."""
        ext = self.file_ext or "py"
        return _glob.glob(os.path.join(path, "tests", f"test_*.{ext}"))

    def example_filename(self, path: str = "") -> str:
        """Return expected example filename in examples/."""
        ext = self.file_ext or "py"
        return f"example_usage.{ext}"

    def sidecar(self) -> tuple[str, str, int] | None:
        """Optional companion validation tree. Override per engine.

        Returns ``(language, subdir, coverage_threshold)`` if this engine
        permits a non-primary-language helper tree alongside its own files
        (e.g. a stdlib-Python calc helper inside an OpenSCAD widget).

        Returning ``None`` (the default) means no sidecar — the validator
        skips the dispatch entirely. Returning a tuple does NOT require the
        sidecar dir to exist; the dispatcher is the one that checks for an
        empty/missing dir and silently skips.
        """
        return None

    def cleanup(self, path: str) -> None:
        """Remove any temp artifacts created during validation. Best-effort."""
        pass

    def _cleanup_artifact_dirs(self, path: str) -> None:
        """Remove top-level build artifacts for this engine's language.

        Sourced from the universal-build-artifact-ignore widget so adding a
        new ecosystem cache (e.g. .turbo, .nx) flows to every engine that
        adopts it. Handles both directories (caches like nimcache/htmldocs)
        and generated files (e.g. nimble.paths). Subclasses call this from
        their cleanup() override.
        """
        import shutil
        try:
            from cg.universal_build_artifact_ignore_python.src.build_artifact_ignore import (
                excludes_for,
            )
        except ImportError:
            return
        for entry in excludes_for(language=self.name):
            # Preserve the cached dependency dir so the next run can reuse it.
            if self.env_dir and entry == self.env_dir:
                continue
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            elif os.path.isfile(full):
                try:
                    os.remove(full)
                except OSError:
                    pass

    def run_example(self, path: str) -> dict:
        """Execute the example file. Called after install_deps."""
        ep = os.path.join(path, "examples", self.example_filename(path))
        if self.example_command:
            res = self._run(self.example_command + [ep], cwd=path, timeout=60)
        else:
            res = subprocess.run(
                [sys.executable, ep],
                cwd=path, capture_output=True, text=True, timeout=60,
            )
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    def scaffold(self, target_dir: str, module_name: str, display_name: str, **kwargs) -> None:
        """Write starter src/, tests/, and examples/ files for a new widget.
        Override this to co-locate the scaffold template with the engine.
        Return None if this engine has no scaffold (falls back to templates.py).
        """
        return None

    def blueprint_scaffold(self, target_dir: str, module_name: str,
                           display_name: str, **kwargs) -> None:
        """Write starter files for a new blueprint of this language.

        A blueprint is a widget that composes other widgets as deps -
        same on-disk shape, only the manifest differs. By default we
        reuse the engine's widget scaffold so blueprints inherit the
        same opinionated starter the language already produces for
        widgets. Engines without a widget scaffold fall back to minimal
        placeholders so the structural checks have something to find.
        """
        if self.__class__.scaffold is not LanguageEngine.scaffold:
            return self.scaffold(target_dir, module_name, display_name, **kwargs)

        if not self.file_ext:
            return None
        ext = self.file_ext
        src = os.path.join(target_dir, "src")
        tests = os.path.join(target_dir, "tests")
        examples = os.path.join(target_dir, "examples")
        for d in (src, tests, examples):
            os.makedirs(d, exist_ok=True)

        marker = (
            f"# [TODO] Blueprint: {display_name}\n"
            f"# Compose declared widget deps here. Tests and examples must\n"
            f"# consume only what src/ exposes - no direct cg/ imports past\n"
            f"# this sealed surface.\n"
        )
        with open(os.path.join(src, f"{module_name}.{ext}"), "w", encoding="utf-8") as f:
            f.write(marker)
        with open(os.path.join(tests, f"test_{module_name}.{ext}"), "w", encoding="utf-8") as f:
            f.write(f"# [TODO] Test the {display_name} blueprint surface.\n")
        with open(os.path.join(examples, f"example_{module_name}.{ext}"), "w", encoding="utf-8") as f:
            f.write(f"# [TODO] Exercise the {display_name} blueprint.\n")
        return None

    def example_extension(self) -> str:
        """File extension used to glob example files (without the dot)."""
        return self.file_ext or "py"

    def run_blueprint_example(self, sandbox: str, example_file: str) -> dict:
        """Run a blueprint example from the validator sandbox.

        If the engine has overridden run_example, delegate to it - the
        engine already knows how to invoke its language runtime, and a
        blueprint sandbox is shaped like a widget root. Engines that
        need blueprint-specific wiring (e.g. Python's PYTHONPATH so
        `from cg.X` resolves; Nim's --path: per dep dir) override.
        """
        if self.__class__.run_example is not LanguageEngine.run_example:
            result = self.run_example(sandbox)
            return {"passed": bool(result.get("passed", False)),
                    "error": result.get("error", "") or ""}

        ep = os.path.join(sandbox, "examples", example_file)
        if self.example_command:
            res = self._run(self.example_command + [ep], cwd=sandbox, timeout=60)
        else:
            res = subprocess.run(
                [sys.executable, ep],
                cwd=sandbox, capture_output=True, text=True, timeout=60,
            )
        if res.returncode != 0:
            return {"passed": False,
                    "error": (res.stderr or res.stdout or "").strip()}
        return {"passed": True}

    def wire_blueprint_dep(self, blueprint_dir: str, dep_id: str,
                           dep_dir: str) -> None:
        """Wire a composed widget into the blueprint's language manifest.

        Called by `blueprint add-dep` after the dep is pinned in
        blueprint.json. Compiled languages that resolve deps through a
        manifest (Rust's Cargo.toml, Go's go.mod) override this to add a
        path/replace entry pointing at `cg/<dep_id>/` - the layout the
        validator sandbox populates - so the blueprint compiles against
        the composed widget without the author hand-editing the manifest.

        Base is a no-op: interpreted languages resolve composed widgets via
        the sandbox's cg/ copy plus relative references in source (Python's
        PYTHONPATH, JS/PHP relative requires, Nim's --path:), so there is no
        separate manifest to touch. Must be idempotent.
        """
        return

    def unwire_blueprint_dep(self, blueprint_dir: str, dep_id: str) -> None:
        """Remove a composed widget's entry from the blueprint's language
        manifest. The inverse of wire_blueprint_dep; base is a no-op."""
        return

    def required_files(self, path: str) -> list[tuple[str, str]]:
        """Return [(relative_path, error_hint)] for files that must exist before tests run."""
        return []

    def src_import_pattern(self) -> str | None:
        """Regex pattern used to verify an example imports from src/."""
        return self.import_pattern or None

    def watched_patterns(self, path: str) -> list[str]:
        """Glob patterns for files that feed the validation stamp fingerprint."""
        patterns = [
            os.path.join(path, "src", "**", "*"),
            os.path.join(path, "tests", "**", "*"),
            os.path.join(path, "examples", "**", "*"),
            os.path.join(path, "widget.json"),
        ]
        sc = self.sidecar()
        if sc:
            _, sc_dir, _ = sc
            patterns.append(os.path.join(path, sc_dir, "**", "*"))
        for pat in self.manifest_patterns:
            patterns.extend(_glob.glob(os.path.join(path, pat)))
        return patterns

    # ------------------------------------------------------------------ helpers

    def _run_native_scanner(self, scanner_path: str, runner: list,
                            src_files: list, cwd: str,
                            finding_messages: dict = None,
                            warning_messages: dict = None) -> tuple[list[str], list[str], list[str]]:
        """
        Run a native language scanner and return (errors, warnings, blocks).

        scanner_path: absolute path to the scanner source file
        runner: command prefix to execute it, e.g. ["node"] or ["nim", "r", "--hints:off"]
        src_files: list of source files to scan
        finding_messages: dict mapping finding kind -> human-readable header (errors)
        warning_messages: dict mapping finding kind -> human-readable header (warnings)

        The scanner must output a JSON array of objects with keys:
          kind, file, line, detail, [severity]
        Severity values: "error" (default), "warning", "block".
        """
        if not src_files or not os.path.exists(scanner_path):
            return [], [], []

        cmd = list(runner) + [scanner_path] + src_files
        env = os.environ.copy()

        # Nim defaults to ~/.cache/nim for scanner builds, which may not be
        # writable in sandboxed environments. Point it at /tmp instead.
        if cmd and cmd[0] == "nim":
            cache_root = os.path.join(tempfile.gettempdir(), "cartograph-nim-cache")
            os.makedirs(cache_root, exist_ok=True)
            env.setdefault("XDG_CACHE_HOME", cache_root)

        # 120s headroom: cold node/nim startup on Windows runners can exceed
        # 60s under antivirus + temp-path overhead, surfacing as flaky CI.
        res = self._run(cmd, cwd=cwd, timeout=120, env=env)

        if res.returncode != 0:
            output = (res.stderr or res.stdout or "").strip()
            message = f"{self.name} scanner failed to run"
            if output:
                message += ":\n" + output[:3000]
            return [message], [], []

        findings = []
        if res.stdout.strip():
            import json
            try:
                # Scanner may emit other output before the JSON line
                findings = json.loads(res.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                output = res.stdout.strip()[:3000]
                return [f"{self.name} scanner produced invalid output:\n{output}"], [], []

        if not findings:
            return [], [], []

        finding_messages = finding_messages or {}
        warning_messages = warning_messages or self.scanner_warning_messages or {}
        warning_kinds = set(warning_messages.keys())

        error_grouped: dict[str, list[str]] = {}
        warn_grouped: dict[str, list[str]] = {}
        block_grouped: dict[str, list[str]] = {}
        for f in findings:
            kind = f.get("kind", "unknown")
            severity = f.get("severity", "error")
            rel = os.path.relpath(f.get("file", ""), cwd)
            loc = f"  {rel}:{f.get('line', 0)}: {f.get('detail', '')}"
            if severity == "block":
                block_grouped.setdefault(kind, []).append(loc)
            elif severity == "warning" or kind in warning_kinds:
                warn_grouped.setdefault(kind, []).append(loc)
            else:
                error_grouped.setdefault(kind, []).append(loc)

        all_messages = {**finding_messages, **warning_messages}

        errors = []
        for kind, violations in error_grouped.items():
            header = all_messages.get(kind, f"{kind} found in src/:")
            errors.append(header + "\n" + "\n".join(violations))

        warnings = []
        for kind, violations in warn_grouped.items():
            header = all_messages.get(kind, f"{kind} found in src/:")
            warnings.append(header + "\n" + "\n".join(violations))

        blocks = []
        for kind, violations in block_grouped.items():
            header = all_messages.get(kind, f"{kind} found in src/:")
            blocks.append(header + "\n" + "\n".join(violations))

        return errors, warnings, blocks

    # On Windows, shell scripts like npm/npx/ng are .cmd wrappers that
    # subprocess can't launch without a shell, so _run defaults to shell=True
    # there. But shell=True + a bare list means cmd.exe reparses every arg:
    # metacharacters like `|` (e.g. a coverage --ignore-filename-regex of
    # `(tests|examples|cg)/`) get treated as pipes. Engines whose toolchain is
    # real .exe binaries (cargo, rustc, go, ...) don't need the shell and must
    # opt out so their args pass through untouched.
    windows_shell = True

    def _run(self, cmd: list, cwd: str, timeout: int = 60,
             env: dict = None) -> subprocess.CompletedProcess:
        use_shell = os.name == "nt" and self.windows_shell
        if cmd:
            cmd = [self._maybe_override(cmd[0]), *cmd[1:]]
        return subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
            env=env or os.environ.copy(),
            shell=use_shell,
        )

    def _maybe_override(self, binary: str) -> str:
        """Swap a bare binary name for its configured override when set.

        Only reaches into config when there's a path separator-free name
        like 'nim' or 'nimble' - absolute paths and relative paths are
        already committed to a specific binary and pass through as-is.
        """
        if not binary or os.sep in binary or (os.altsep and os.altsep in binary):
            return binary
        override = _override_for(binary)
        if not override:
            return binary
        try:
            resolved = _resolve_binary(binary, override=override,
                                       override_key=f"paths.{binary}")
            return resolved.path
        except ResolveError:
            # Broken override - let subprocess fail on the bare name so
            # the error surfaces in context. check_available catches the
            # same situation earlier for users running `doctor`.
            return binary

    def _fail(self, error: str) -> dict:
        return {"passed": False, "error": str(error or "Unknown error")[:3000]}

    def _ok(self) -> dict:
        return {"passed": True}

    # ------------------------------------------------------------------ shared checks

    @staticmethod
    def _check_dep_pinning(dependencies: list) -> list[str]:
        """
        Return a list of error messages for dependencies that have no version floor.
        Dependencies must be strings like 'fastapi>=0.128.0', not dicts.
        A valid dep string contains >=, ==, ~=, <=, !=, or a bare version number.
        """
        errors = []
        for dep in dependencies:
            if not isinstance(dep, str):
                name = dep.get("name", str(dep)) if isinstance(dep, dict) else str(dep)
                errors.append(
                    f"Dependency '{name}' must be a string like '{name}>=<version>', "
                    f"not a dict — use plain strings in tech_stack.dependencies"
                )
                continue
            if dep and not _PIN_RE.search(_DEP_SPLIT_RE.split(dep)[0]):
                bare = _dep_bare_name(dep)
                if bare == dep.strip():
                    errors.append(
                        f"Dependency '{dep}' has no version pin — "
                        f"use '{dep}>=<version>' for reproducibility"
                    )
        return errors
