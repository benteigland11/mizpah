"""JavaScript / TypeScript language engine - uses npm + vitest.

The engine handles JS/JSX/TS/TSX syntax; framework choices (React, Preact,
Solid, etc.) are the widget's business and must be declared in its own
package.json devDependencies plus widget.json tech_stack.dependencies.

Source scanning is handled by scanners/js_scanner.js (native JS) for
string/comment/template-literal-aware detection.
"""

import glob as _glob
import json
import os

from .base import LanguageEngine, _dep_bare_name, log


def _shared_npm_cache() -> str:
    """Persistent npm cache shared across validate runs.

    Lives under cartograph's data dir so HOME stays untouched (sandbox-safe)
    and the cache survives between runs. First install populates it, every
    subsequent install reads from it.
    """
    from ..engine import _user_data_dir
    path = os.path.join(_user_data_dir(), "npm-cache")
    os.makedirs(path, exist_ok=True)
    return path

# -- Scaffold templates --------------------------------------------------------

# Plain JS (backend, data, infra, universal, etc.)
_JS_PLAIN_SRC = '''\
/**
 * {name}
 */
export function {module}(value) {{
  return value
}}
'''

_JS_PLAIN_TEST = '''\
import {{ {module} }} from '../src/{module}.js'

test('placeholder', () => {{
  // TODO: replace with real tests
  expect({module}('hello')).toBe('hello')
}})
'''

_JS_PLAIN_EXAMPLE = '''\
/**
 * Example usage of {name}.
 *
 * This file must run and exit cleanly with no user input, no network calls,
 * and no external services or API keys. Use fake/hardcoded data to demonstrate the API.
 * The widget's own declared dependencies are fine - the validator installs them first.
 */
import {{ {module} }} from '../src/{module}.js'

// [TODO] Replace with a realistic call using fake data
const result = {module}('hello')
console.log(`Result: ${{result}}`)
'''



_TS_SRC = '''\
/**
 * {name}
 */
export function {module}(value: string): string {{
  return value
}}
'''

_TS_TEST = '''\
import {{ {module} }} from '../src/{module}'

test('placeholder', () => {{
  // TODO: replace with real tests
  expect({module}('hello')).toBe('hello')
}})
'''

_TS_EXAMPLE = '''\
/**
 * Example usage of {name}.
 *
 * This file must run and exit cleanly with no user input, no network calls,
 * and no external services or API keys. Use fake/hardcoded data to demonstrate the API.
 * The widget's own declared dependencies are fine - the validator installs them first.
 */
import {{ {module} }} from '../src/{module}'

// [TODO] Replace with a realistic call using fake data
const result = {module}('hello')
console.log(`Result: ${{result}}`)
'''

_COVERAGE_THRESHOLD = 80


_VITEST_FALLBACK = """\
export default {
  test: {
    include: ['tests/test_*.*'],
    globals: true,
    coverage: {
      include: ['src/**'],
    },
  }
}
"""

_VITEST_FRONTEND = """\
export default {
  test: {
    include: ['tests/test_*.*'],
    environment: 'happy-dom',
    globals: true,
    coverage: {
      include: ['src/**'],
    },
  }
}
"""


class JavaScriptEngine(LanguageEngine):
    name = "javascript"
    aliases = ["js"]
    validation_version = 2
    file_ext = "js"
    toolchain = {
        "node": "Install Node.js 18+ - nodejs.org",
        "npx": "Reinstall Node.js - npx ships with it",
    }
    scanner_runner = ["node"]
    scanner_messages = {
        "console_log": "console.log() found in src/ - remove debug output before checkin:",
        "process_exit": "process.exit() found in src/ - widgets must not exit the process:",
        "eval": "eval() found in src/ - dynamic code execution is a security risk:",
    }
    import_pattern = r"from\s+['\"]\.\.?/src/"
    manifest_patterns = ["package.json"]
    env_dir = "node_modules"

    def lock_patterns(self, path: str) -> list:
        pats = []
        for f in ("package-lock.json", "package.json"):
            fp = os.path.join(path, f)
            if os.path.exists(fp):
                pats.append(fp)
        return pats

    def verify_installed(self, path: str, dependencies: list) -> bool:
        from ..dep_cache import parse_requirement, satisfies
        nm = os.path.join(path, "node_modules")
        for dep in dependencies:
            name, spec = parse_requirement(dep)
            if not name:
                continue
            # Scoped names (@scope/pkg) map to nested dirs on disk.
            pj = os.path.join(nm, *name.split("/"), "package.json")
            if not os.path.isfile(pj):
                return False
            try:
                with open(pj, encoding="utf-8") as f:
                    ver = json.load(f).get("version")
            except (OSError, ValueError):
                return False
            if not ver or satisfies(ver, spec) is False:
                return False
        return True

    def runtime_version(self) -> str | None:
        try:
            res = self._run(["node", "--version"], cwd=".", timeout=10)
            if res.returncode == 0:
                ver = res.stdout.strip().lstrip("v")
                return f"node {ver}"
        except Exception:
            pass
        return None

    def check_optional(self) -> list[tuple[str, bool, str]]:
        checks = []
        try:
            # Through self._run so paths.npx (if configured) is honored.
            # --no-install: without it, npx DOWNLOADS playwright from the
            # registry just to answer --version when it isn't installed -
            # doctor hung for 30s+ on Windows and polluted the npm cache.
            # A status probe must never mutate the machine.
            r = self._run(["npx", "--no-install", "playwright", "--version"],
                          cwd=os.getcwd(), timeout=15)
            installed = r.returncode == 0
        except Exception:
            installed = False
        if installed:
            checks.append(("playwright", True, "browser widget validation available"))
        else:
            checks.append(("playwright", False,
                           "not installed - browser widgets (Canvas/WebGL) can't be validated"))
        return checks

    def scaffold(self, target_dir, module_name, display_name, **kwargs):
        domain = kwargs.get("domain", "")
        is_frontend = domain == "frontend"

        dev_deps = {
            "vitest": "^1.0.0",
            "@vitest/coverage-v8": "^1.0.0",
        }
        if is_frontend:
            dev_deps["happy-dom"] = "^15.0.0"

        pkg = {
            "name": os.path.basename(target_dir),
            "version": "1.0.0",
            "type": "module",
            "dependencies": {},
            "devDependencies": dev_deps,
        }
        with open(os.path.join(target_dir, "package.json"), "w", encoding="utf-8") as f:
            json.dump(pkg, f, indent=2)

        with open(os.path.join(target_dir, "src", f"{module_name}.js"), "w", encoding="utf-8") as f:
            f.write(_JS_PLAIN_SRC.format(name=display_name, module=module_name))
        with open(os.path.join(target_dir, "tests", f"test_{module_name}.js"), "w", encoding="utf-8") as f:
            f.write(_JS_PLAIN_TEST.format(module=module_name))
        with open(os.path.join(target_dir, "examples", "example_usage.js"), "w", encoding="utf-8") as f:
            f.write(_JS_PLAIN_EXAMPLE.format(name=display_name, module=module_name))

        # Always ship a vitest config so the widget runs standalone under
        # `vitest run` with no Cartograph injection. Frontend gets happy-dom.
        config = _VITEST_FRONTEND if is_frontend else _VITEST_FALLBACK
        with open(os.path.join(target_dir, "vitest.config.js"), "w", encoding="utf-8") as f:
            f.write(config)

    # ------------------------------------------------------------------ helpers

    # ------------------------------------------------------------------ validation

    scanner_warning_messages = {
        "abs_path": "Absolute paths found in src/ - widgets must be portable:",
        "credential": "Possible credentials found in src/ - remove before checkin:",
        "hardcoded_url": "Hardcoded URLs found in src/ - consider making these configurable:",
        "hardcoded_ip": "Hardcoded IPs found in src/ - consider making these configurable:",
        "hardcoded_value": "Hardcoded values found in src/ - consider making these configurable:",
        "env_var": "Environment variable access found in src/ - verify it's not project-specific:",
        "unlisted_import": "Unlisted imports - add to widget.json dependencies or remove:",
        "sleep": "Sleep/blocking calls found - widgets must not block the caller:",
        "risky_import": "Node.js I/O or network imports found in src/ - ensure no hardcoded paths, URLs, or commands:",
    }

    def validate_widget(self, path: str, dependencies: list) -> dict:
        """Scan src/ for contamination using native JS scanner."""
        errors = []

        # Native scanner - handles strings, comments, template literals
        src_dir = os.path.join(path, "src")
        src_files = []
        if os.path.isdir(src_dir):
            for ext in ("*.js", "*.jsx", "*.ts", "*.tsx"):
                src_files.extend(_glob.glob(os.path.join(src_dir, "**", ext), recursive=True))

        scanner = os.path.join(os.path.dirname(__file__), "scanners", "js_scanner.js")
        scan_errors, _, _ = self._run_native_scanner(
            scanner_path=scanner,
            runner=self.scanner_runner,
            src_files=src_files,
            cwd=path,
            finding_messages=self.scanner_messages,
        )
        errors.extend(scan_errors)

        errors.extend(self._check_dep_pinning(dependencies))

        if errors:
            return self._fail("\n".join(errors))
        return self._ok()

    def _collect_source_files(self, path: str) -> tuple[list[str], list[str], list[str]]:
        """JS/TS uses multiple extensions."""
        src_files, test_files, example_files = [], [], []
        for ext in ("*.js", "*.jsx", "*.ts", "*.tsx"):
            src_files.extend(_glob.glob(os.path.join(path, "src", "**", ext), recursive=True))
            test_files.extend(_glob.glob(os.path.join(path, "tests", "**", ext), recursive=True))
            example_files.extend(_glob.glob(os.path.join(path, "examples", "**", ext), recursive=True))
        return src_files, test_files, example_files

    def scan_contamination(self, path: str, widget: dict) -> dict:
        """JS contamination: native scanner on src + test + example files."""
        src_files, test_files, example_files = self._collect_source_files(path)
        all_files = src_files + test_files + example_files
        scanner = os.path.join(os.path.dirname(__file__), "scanners", "js_scanner.js")
        scan_errors, scan_warnings, scan_blocks = self._run_native_scanner(
            scanner_path=scanner,
            runner=self.scanner_runner,
            src_files=all_files,
            cwd=path,
            finding_messages=self.scanner_messages,
        )

        return {"blocks": scan_blocks + scan_errors, "warnings": scan_warnings}

    # ------------------------------------------------------------------ interface

    def find_test_files(self, path: str) -> list[str]:
        files = []
        for ext in ("js", "jsx", "ts", "tsx"):
            files.extend(_glob.glob(os.path.join(path, "tests", "**", f"test_*.{ext}"), recursive=True))
        return files

    def example_filename(self, path: str = "") -> str:
        """Find the widget's example file.

        Widgets pick the extension themselves (`.js`, `.jsx`, `.ts`, `.tsx`).
        Scaffold default is returned when no example exists yet.
        """
        if path:
            for ext in ("jsx", "tsx", "ts", "js"):
                candidate = os.path.join(path, "examples", f"example_usage.{ext}")
                if os.path.exists(candidate):
                    return f"example_usage.{ext}"
        return "example_usage.js"

    # ------------------------------------------------------------------ install

    def install_deps(self, path: str, dependencies: list) -> None:
        # Reuse node_modules when deps + lockfiles are unchanged, skipping the
        # full npm ci (which otherwise wipes and rebuilds the tree).
        if self._deps_cached(path, dependencies):
            log.debug("Reusing cached node_modules at %s", path)
            return

        log.debug("Installing npm packages...")

        package_json_path = os.path.join(path, "package.json")

        if not os.path.exists(package_json_path):
            pkg = {
                "name": os.path.basename(path),
                "version": "1.0.0",
                "type": "module",
                "dependencies": {},
                "devDependencies": {"vitest": "^1.0.0", "@vitest/coverage-v8": "^1.0.0"},
            }
            for dep in dependencies:
                bare = _dep_bare_name(dep)
                ver_part = dep[len(bare):].strip() or "*"
                if bare:
                    pkg["dependencies"][bare] = ver_part
            with open(package_json_path, "w", encoding="utf-8") as f:
                json.dump(pkg, f, indent=2)
        else:
            with open(package_json_path, encoding="utf-8") as f:
                pkg = json.load(f)
            dev = pkg.setdefault("devDependencies", {})
            changed = False
            if "vitest" not in dev and "vitest" not in pkg.get("dependencies", {}):
                dev["vitest"] = "^1.0.0"
                changed = True
            if changed:
                with open(package_json_path, "w", encoding="utf-8") as f:
                    json.dump(pkg, f, indent=2)

        # Shared npm cache under cartograph's data dir. Stays warm across
        # runs (so cold installs only happen once), and never touches HOME
        # so sandboxed envs (Codex, restricted devcontainers) keep working.
        npm_cache = _shared_npm_cache()

        # Lockfile-first: `npm ci` reads package-lock.json and installs the
        # exact pinned tree (fast, deterministic, offline once cached).
        # Falls back to `npm install` to bootstrap the lockfile on first run
        # or when package.json drifted past the lockfile.
        lockfile = os.path.join(path, "package-lock.json")
        use_ci = os.path.exists(lockfile)

        def _do(cmd: list[str], timeout: int):
            return self._run(cmd, cwd=path, timeout=timeout)

        if use_ci:
            res = _do(["npm", "ci", "--cache", npm_cache], 300)
            if res.returncode != 0:
                # Lockfile probably out of sync with package.json. Regenerate.
                log.debug("npm ci failed, regenerating lockfile via npm install")
                use_ci = False
        if not use_ci:
            res = _do(["npm", "install", "--cache", npm_cache], 300)

        if res.returncode != 0:
            output = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(
                "Failed to install npm dependencies."
                + (f"\n{output[:2000]}" if output else "")
            )

        self._record_dep_cache(path, dependencies)

    # ------------------------------------------------------------------ tests

    @staticmethod
    def _has_vitest_config(path: str) -> bool:
        """Check if the widget has its own vitest config."""
        for name in ("vitest.config.js", "vitest.config.ts", "vitest.config.mjs",
                      "vitest.config.mts"):
            if os.path.exists(os.path.join(path, name)):
                return True
        return False

    def run_tests(self, path: str) -> dict:
        # Widgets must ship their own vitest.config.* so they run identically
        # under `vitest run` outside Cartograph. No silent injection.
        if not self._has_vitest_config(path):
            return self._fail(
                "Missing vitest.config.js (or .ts/.mjs/.mts). Widgets must ship "
                "their own vitest config so tests run identically outside "
                "Cartograph. See scaffold default for a minimal example."
            )

        t = _COVERAGE_THRESHOLD
        cmd = [
            "npx", "vitest", "run", "--coverage",
            # Explicit file extensions: a bare `src/**` glob has been observed
            # matching nothing on Windows under v8 coverage path normalization.
            "--coverage.include=src/**/*.{js,mjs,cjs,jsx,ts,mts,cts,tsx}",
            f"--coverage.thresholds.statements={t}",
            f"--coverage.thresholds.branches={t}",
            f"--coverage.thresholds.functions={t}",
            f"--coverage.thresholds.lines={t}",
        ]
        res = self._run(cmd, cwd=path, timeout=300)
        if res.returncode != 0:
            combined = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            return self._fail(combined or "vitest failed with no output")
        return self._ok()

    # ------------------------------------------------------------------ example

    @staticmethod
    def _runner_for(example_file: str) -> list:
        """`node` for plain .js; `npx tsx` for anything that needs JSX/TS."""
        ext = os.path.splitext(example_file)[1].lower()
        if ext in (".jsx", ".ts", ".tsx"):
            return ["npx", "tsx"]
        return ["node"]

    def run_example(self, path: str) -> dict:
        example_file = self.example_filename(path)
        example_path = os.path.join(path, "examples", example_file)
        runner = self._runner_for(example_file)
        res = self._run(runner + [example_path], cwd=path, timeout=30)
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    # ------------------------------------------------------------------ cleanup

    def cleanup(self, path: str) -> None:
        self._cleanup_artifact_dirs(path)


class TypeScriptEngine(JavaScriptEngine):
    name = "typescript"
    aliases = ["ts"]
    file_ext = "ts"

    def scaffold(self, target_dir, module_name, display_name, **kwargs):
        # Let JS scaffold handle package.json and vitest config (domain branching for happy-dom)
        super().scaffold(target_dir, module_name, display_name, **kwargs)
        # Overwrite JS source files with TypeScript equivalents
        os.remove(os.path.join(target_dir, "src", f"{module_name}.js"))
        os.remove(os.path.join(target_dir, "tests", f"test_{module_name}.js"))
        os.remove(os.path.join(target_dir, "examples", "example_usage.js"))
        with open(os.path.join(target_dir, "src", f"{module_name}.ts"), "w", encoding="utf-8") as f:
            f.write(_TS_SRC.format(name=display_name, module=module_name))
        with open(os.path.join(target_dir, "tests", f"test_{module_name}.ts"), "w", encoding="utf-8") as f:
            f.write(_TS_TEST.format(module=module_name))
        with open(os.path.join(target_dir, "examples", "example_usage.ts"), "w", encoding="utf-8") as f:
            f.write(_TS_EXAMPLE.format(name=display_name, module=module_name))

    def example_filename(self, path: str = "") -> str:
        """Find the widget's example file. TS default is `.ts`."""
        if path:
            for ext in ("tsx", "jsx", "ts", "js"):
                candidate = os.path.join(path, "examples", f"example_usage.{ext}")
                if os.path.exists(candidate):
                    return f"example_usage.{ext}"
        return "example_usage.ts"
