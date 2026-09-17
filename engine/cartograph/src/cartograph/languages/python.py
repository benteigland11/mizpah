"""Python language engine - uses pip + pytest + pytest-cov."""

import ast
import glob
import json
import os
import re
import subprocess
import sys
import time

from .base import LanguageEngine, _dep_bare_name, log
from ..engine import _is_os_metadata


def _py_files(path: str, subdir: str) -> list:
    """All .py files under a widget subdir, minus OS metadata.

    AppleDouble resource forks are named `._<original>.py`, so a bare
    `**/*.py` glob picks them up - they are binary garbage, not source.
    """
    found = glob.glob(os.path.join(path, subdir, "**", "*.py"), recursive=True)
    return [f for f in found if not _is_os_metadata(os.path.basename(f))]

# Starter file contents for scaffold
_SRC_INIT = "# Package marker - add explicit exports here once the public API is stable.\n"

_SRC_TEMPLATE = '''\
def {module}(value):
    """{name}: process a value."""
    return value
'''

_TEST_TEMPLATE = '''\
def test_placeholder():
    # TODO: replace with real tests
    pass
'''

_EXAMPLE_TEMPLATE = '''\
"""
Example usage of {name}.

This file must run and exit cleanly with no user input, no network calls,
and no external services or API keys. Use fake/hardcoded data to demonstrate the API.
The widget's own declared dependencies are fine - the validator installs them first.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.{module} import {module}

# [TODO] Replace with a realistic call using fake data
result = {module}("hello")
print(f"Result: {{result}}")
'''

_COVERAGE_THRESHOLD = 80

# Large ML frameworks that cannot be installed in a temp venv during validation.
# These are hardware-dependent or extremely large — users must pre-install them.
# If a widget lists one of these as a dependency, validation will use the
# caller's existing environment install (if present) or fail with a clear message.
_HEAVY_ML_DEPS = {
    "torch", "torchvision", "torchaudio", "torch-nightly",
    "tensorflow", "tensorflow-gpu", "tensorflow-cpu", "tf-nightly",
    "keras",
    "jax", "jaxlib",
    "mxnet", "mxnet-cu102", "mxnet-cu110",
    "paddle", "paddlepaddle", "paddlepaddle-gpu",
    "flax", "optax",
}


class PythonEngine(LanguageEngine):
    name = "python"
    validation_version = 1
    file_ext = "py"
    env_dir = ".venv"

    def lock_patterns(self, path: str) -> list:
        pats = []
        for f in ("requirements.txt", "requirements-dev.txt",
                  "pyproject.toml", "setup.cfg"):
            fp = os.path.join(path, f)
            if os.path.exists(fp):
                pats.append(fp)
        return pats

    def verify_installed(self, path: str, dependencies: list) -> bool:
        from ..dep_cache import parse_requirement, satisfies
        venv_dir = os.path.join(path, ".venv")
        py = (os.path.join(venv_dir, "Scripts", "python.exe") if os.name == "nt"
              else os.path.join(venv_dir, "bin", "python"))
        if not os.path.exists(py):
            return False
        res = self._run([py, "-m", "pip", "list", "--format=json"],
                        cwd=path, timeout=30)
        if res.returncode != 0:
            return False
        try:
            installed = {
                p["name"].lower().replace("_", "-"): p["version"]
                for p in json.loads(res.stdout)
            }
        except (ValueError, KeyError):
            return False
        for dep in dependencies:
            name, spec = parse_requirement(dep)
            if not name:
                continue
            base = name.split("[")[0].lower().replace("_", "-")
            if base in _HEAVY_ML_DEPS:
                continue  # inherited from global site-packages, not in the venv list
            ver = installed.get(base)
            if ver is None:
                return False
            if satisfies(ver, spec) is False:
                return False
        return True

    def runtime_version(self) -> str | None:
        v = sys.version_info
        return f"python {v.major}.{v.minor}.{v.micro}"

    def check_available(self) -> tuple[bool, str]:
        import subprocess
        missing = []
        for tool in ("pytest", "coverage"):
            r = subprocess.run(
                [sys.executable, "-m", tool, "--version"],
                capture_output=True,
            )
            if r.returncode != 0:
                missing.append(tool)
        if missing:
            return False, (
                f"Python engine requires {' and '.join(missing)} - "
                f"install with: pip install {' '.join(missing)}"
            )
        return True, ""

    def validate_widget(self, path: str, dependencies: list) -> dict:
        errors = []

        # 1. src/__init__.py must exist
        init = os.path.join(path, "src", "__init__.py")
        if not os.path.exists(init):
            errors.append("src/__init__.py is missing — add an empty one so the package is importable")

        # 2. No print() calls in src/ (AST-based: ignores docstrings and comments)
        src_files = _py_files(path, "src")
        for fpath in src_files:
            for lineno in self._find_print_calls(fpath):
                rel = os.path.relpath(fpath, path)
                errors.append(f"print() in {rel}:{lineno} — remove debug output from src/")

        # 3. Dependencies must have a version floor
        errors.extend(self._check_dep_pinning(dependencies))

        if errors:
            return self._fail("\n".join(errors))
        return self._ok()

    def _find_print_calls(self, fpath: str) -> list:
        """Return line numbers of print() calls in actual code, skipping docstrings."""
        try:
            with open(fpath, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
        except Exception:
            return []
        lines = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                lines.append(node.lineno)
        return lines

    _STDLIB = sys.stdlib_module_names
    _TEST_FRAMEWORKS = {"pytest", "hypothesis", "faker", "mock", "unittest"}
    _ENVVAR_RE = re.compile(r'os\.getenv\(|os\.environ')
    _SLEEP_MODULES = {"time", "asyncio"}
    _ABS_PATH_RE = re.compile(
        r'["\'](?:/home/|/Users/|/root/|[A-Za-z]:[/\\\\])[^"\']{3,}["\']'
    )
    _CREDENTIAL_RE = re.compile(
        r'(?:api_key|api_secret|secret_key|access_token|auth_token|password|passwd|credential)\s*=\s*["\'][^"\']{6,}["\']',
        re.IGNORECASE,
    )
    _URL_RE = re.compile(
        r'["\']https?://(?!(?:localhost|127\.0\.0\.1|(?:[\w-]+\.)*example\.com|[\w.-]+\.test(?:[/:"\'#?]|$)|schemas?\.))[^"\']{8,}["\']'
    )
    _IP_RE = re.compile(r'["\'](?:\d{1,3}\.){3}\d{1,3}(?::\d+)?["\']')

    def scan_contamination(self, path: str, widget: dict) -> dict:
        """Python contamination: AST-based checks for all contamination concerns."""
        blocks, warnings = [], []

        deps = widget.get("dependencies", [])
        dep_names = {_dep_bare_name(d).lower() for d in deps if isinstance(d, str)}

        # Map declared pip packages → top-level import names they provide.
        # Handles python-docx → docx, Pillow → PIL, beautifulsoup4 → bs4, etc.
        # Falls back to raw dep name when metadata isn't available (dep not installed).
        provided_modules = set(dep_names)
        try:
            from importlib.metadata import packages_distributions
            mod_to_pkgs = packages_distributions()
            for mod, pkgs in mod_to_pkgs.items():
                if any(p.lower() in dep_names for p in pkgs):
                    provided_modules.add(mod.lower())
        except Exception:
            pass

        own_modules = {"src"}
        src_dir = os.path.join(path, "src")
        if os.path.isdir(src_dir):
            for f in os.listdir(src_dir):
                if f.endswith(".py"):
                    own_modules.add(f[:-3])

        src_files = _py_files(path, "src")
        test_files = _py_files(path, "tests")
        example_files = _py_files(path, "examples")

        for fpath in src_files + test_files + example_files:
            rel = os.path.relpath(fpath, path)
            is_src = fpath in src_files
            is_example = fpath in example_files
            try:
                code = open(fpath, encoding="utf-8").read()
            except Exception as e:
                blocks.append(f"Could not read source file {rel}: {e}")
                continue

            # Line-level checks (abs paths, credentials, URLs, IPs)
            for line_no, line in enumerate(code.splitlines(), 1):
                loc = f"{rel}:{line_no}"
                stripped = line.strip()
                is_comment = stripped.startswith("#")
                # abs_path: block in both src and tests - test files should use
                # tmp_path fixtures, not /home/... or C:\... paths
                if not is_comment and self._ABS_PATH_RE.search(line):
                    blocks.append(f"Absolute path in {loc}: {stripped}")
                if is_src:
                    if self._CREDENTIAL_RE.search(line):
                        blocks.append(f"Possible credential in {loc}: {stripped}")
                else:
                    if self._CREDENTIAL_RE.search(line):
                        warnings.append(f"Possible credential in test {loc} - verify it's fake: {stripped}")

            # hardcoded_url: src only - tests legitimately use mock URLs as
            # fixtures (matches hardcoded_value precedent)
            if is_src:
                for m in self._URL_RE.finditer(code):
                    line_no = code[:m.start()].count("\n") + 1
                    warnings.append(f"Hardcoded URL in {rel}:{line_no}: {m.group()}")

            # hardcoded_ip: src only - tests legitimately use mock IPs as
            # fixture data (matches hardcoded_url/hardcoded_value precedent)
            if is_src:
                for m in self._IP_RE.finditer(code):
                    line_no = code[:m.start()].count("\n") + 1
                    # Only flag if at least one octet has 2+ digits - single-digit-only
                    # patterns like "1.2.3.4" are indistinguishable from version strings.
                    octets = m.group().strip("\"'").split(":")[0].split(".")
                    if any(len(o) >= 2 for o in octets):
                        blocks.append(f"Hardcoded IP in {rel}:{line_no}: {m.group()}")

            # AST-based checks. A src/ file that doesn't parse cannot have
            # been exercised by tests or scanned for contamination - block
            # instead of silently skipping it.
            try:
                tree = ast.parse(code)
            except SyntaxError as e:
                if is_src:
                    blocks.append(
                        f"src file {rel} is not valid Python "
                        f"(line {e.lineno}: {e.msg}) - fix or remove it"
                    )
                continue
            except Exception:
                continue

            # Sleep/blocking calls (all files)
            # Collect bare names imported from sleep modules:
            # "from time import sleep" -> sleep_names = {"sleep"}
            # "from time import sleep as s" -> sleep_names = {"s"}
            sleep_names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in self._SLEEP_MODULES:
                    for alias in node.names:
                        if alias.name == "sleep":
                            sleep_names.add(alias.asname or alias.name)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_sleep = False
                # time.sleep() or asyncio.sleep()
                if (isinstance(func, ast.Attribute) and func.attr == "sleep"
                        and isinstance(func.value, ast.Name)
                        and func.value.id in self._SLEEP_MODULES):
                    is_sleep = True
                # from time import sleep; sleep()
                elif (isinstance(func, ast.Name) and func.id in sleep_names):
                    is_sleep = True

                if is_sleep:
                    if is_src:
                        blocks.append(
                            f"sleep() call in {rel}:{node.lineno} - widgets must not block the caller"
                        )
                    else:
                        # In tests/examples: warn if duration > 1 second
                        if (node.args and isinstance(node.args[0], ast.Constant)
                                and isinstance(node.args[0].value, (int, float))
                                and node.args[0].value > 1):
                            warnings.append(
                                f"sleep({node.args[0].value}) in {rel}:{node.lineno} - consider reducing sleep duration"
                            )

            # Unlisted imports - block in src/, warn in tests/examples. An import
            # that isn't stdlib, a declared dep (resolved via importlib.metadata),
            # a local src module, or a test framework will fail to install for
            # users. Not overridable - fix is always trivial (add to deps or
            # remove).
            unlisted_sink = blocks if is_src else warnings
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0].lower()
                        if top and top not in self._STDLIB and top not in provided_modules and top not in own_modules and top not in self._TEST_FRAMEWORKS:
                            unlisted_sink.append(
                                f"Unlisted import '{top}' in {rel}:{node.lineno} - add to dependencies or remove"
                            )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0].lower()
                    if top and top not in self._STDLIB and top not in provided_modules and top not in own_modules and top not in self._TEST_FRAMEWORKS:
                        unlisted_sink.append(
                            f"Unlisted import '{top}' in {rel}:{node.lineno} - add to dependencies or remove"
                        )

            # Remaining AST checks are src/ only
            if not is_src:
                continue

            # Hardcoded values
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.Assign):
                    warnings.extend(self._check_assign_targets(node.targets, node.value, node.lineno, rel))
                elif isinstance(node, ast.ClassDef):
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, ast.Assign):
                            warnings.extend(self._check_assign_targets(child.targets, child.value, child.lineno, rel))

            # os.environ/getenv
            for m in self._ENVVAR_RE.finditer(code):
                line_no = code[:m.start()].count("\n") + 1
                warnings.append(f"os.environ/getenv call in {rel}:{line_no} - verify it's not project-specific")

        return {"blocks": blocks, "warnings": warnings}

    def _check_assign_targets(self, targets, value, lineno, rel="") -> list[str]:
        """Check if an assignment's value is a hardcoded constant."""
        results = []
        names = []
        for t in targets:
            if isinstance(t, ast.Name):
                names.append(t.id)
        if not names:
            return results

        name = names[0]

        if isinstance(value, ast.Constant):
            val = value.value
            if isinstance(val, (int, float)):
                results.append(f"Hardcoded value in {rel}:{lineno}: {name} = {val} - consider making this a parameter")
            elif isinstance(val, str) and len(val) > 0:
                results.append(f"Hardcoded value in {rel}:{lineno}: {name} = \"{val[:60]}\" - consider making this a parameter")
        elif isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
            if isinstance(value.operand, ast.Constant):
                val = -value.operand.value
                if isinstance(val, (int, float)):
                    results.append(f"Hardcoded value in {rel}:{lineno}: {name} = {val} - consider making this a parameter")

        return results

    def scaffold(self, target_dir, module_name, display_name, **_):
        with open(os.path.join(target_dir, "src", "__init__.py"), "w", encoding="utf-8") as f:
            f.write(_SRC_INIT)
        with open(os.path.join(target_dir, "src", f"{module_name}.py"), "w", encoding="utf-8") as f:
            f.write(_SRC_TEMPLATE.format(module=module_name, name=display_name))
        with open(os.path.join(target_dir, "tests", f"test_{module_name}.py"), "w", encoding="utf-8") as f:
            f.write(_TEST_TEMPLATE)
        with open(os.path.join(target_dir, "examples", "example_usage.py"), "w", encoding="utf-8") as f:
            f.write(_EXAMPLE_TEMPLATE.format(module=module_name, name=display_name))

    def src_import_pattern(self) -> str | None:
        return r'from src\.|import src\.'

    def run_blueprint_example(self, sandbox: str, example_file: str) -> dict:
        """Run an example from a blueprint validation sandbox using the
        venv python set up by install_deps. PYTHONPATH points at the
        sandbox root so `from src.X` and `from cg.X` resolve."""
        py = self._venv_python()
        ep = os.path.join(sandbox, "examples", example_file)
        env = {**os.environ, "PYTHONPATH": sandbox,
               "PYTHONDONTWRITEBYTECODE": "1"}
        res = subprocess.run(
            [py, ep],
            cwd=sandbox, capture_output=True, text=True, timeout=60, env=env,
        )
        if res.returncode != 0:
            return {"passed": False,
                    "error": (res.stderr or res.stdout or "").strip()}
        return {"passed": True}

    def _venv_python(self) -> str:
        """Return the venv python path if a venv was created, else sys.executable."""
        return getattr(self, "_venv_py", sys.executable)

    def install_deps(self, path: str, dependencies: list) -> None:
        import shutil
        import venv
        venv_dir = os.path.join(path, ".venv")

        # Reuse a cached venv when the declared deps and lockfiles are
        # unchanged. The venv inherits global site-packages, so a hit means
        # both the venv and the prior pip resolve can be skipped entirely.
        if self._deps_cached(path, dependencies):
            cached_py = (
                os.path.join(venv_dir, "Scripts", "python.exe")
                if os.name == "nt"
                else os.path.join(venv_dir, "bin", "python")
            )
            if os.path.exists(cached_py):
                self._venv_py = cached_py
                log.debug("Reusing cached venv at %s", venv_dir)
                return

        log.debug("Creating isolated venv at %s", venv_dir)

        # Retry venv creation to handle stale venvs whose python binary is still
        # held open by a lingering subprocess from a previous validation run.
        # Linux raises ETXTBSY (errno 26), Windows raises PermissionError (WinError 32).
        _MAX_RETRIES = 3
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                venv.create(venv_dir, with_pip=True, system_site_packages=True)
                break
            except (OSError, PermissionError) as exc:
                _errno = getattr(exc, "errno", None)
                _winerr = getattr(exc, "winerror", None)
                is_busy = _errno == 26 or _winerr == 32
                if is_busy and attempt < _MAX_RETRIES:
                    log.warning(
                        "Venv python binary is still held open by a previous process "
                        "(attempt %d/%d) - waiting 2s before retry...",
                        attempt, _MAX_RETRIES,
                    )
                    if os.path.isdir(venv_dir):
                        shutil.rmtree(venv_dir, ignore_errors=True)
                    time.sleep(2)
                else:
                    raise

        # Locate the venv python
        if os.name == "nt":
            self._venv_py = os.path.join(venv_dir, "Scripts", "python.exe")
        else:
            self._venv_py = os.path.join(venv_dir, "bin", "python")

        all_deps = list(dependencies) + ["pytest", "pytest-cov"]
        log.debug("Installing %d Python package(s) into venv...", len(all_deps))
        py = self._venv_python()
        for dep in all_deps:
            dep_name = dep
            if not dep_name:
                continue
            # Normalise: strip version specifiers to get the base package name
            base_name = dep_name.split("[")[0].split("==")[0].split(">=")[0].split("<=")[0].split("!=")[0].strip().lower()
            if base_name in _HEAVY_ML_DEPS:
                # Heavy ML frameworks (torch, tensorflow, jax, etc.) are not auto-installed
                # by Cartograph because they are large, hardware-dependent, and may require
                # specific CUDA versions. They must be pre-installed in the user's environment.
                # With system_site_packages=True, the venv inherits them automatically.
                import importlib.util
                import_name = base_name.replace("-", "_")
                if importlib.util.find_spec(import_name) is None:
                    raise RuntimeError(
                        f"'{dep_name}' is a heavy ML framework that must be pre-installed before "
                        f"validation. Install it in your environment first, then re-run validation.\n"
                        f"  pip install {dep_name}"
                    )
                log.debug("Heavy ML dep '%s' found in environment - skipping install.", dep_name)
                continue
            # --no-cache-dir keeps pip from writing to ~/.cache/pip, which
            # may not exist or be writable in sandboxed environments (Codex,
            # restricted devcontainers). The venv is throwaway so caching
            # provides no benefit here.
            res = self._run(
                [py, "-m", "pip", "install", "--no-cache-dir", "-q", dep_name],
                cwd=path,
                timeout=60,
            )
            if res.returncode != 0:
                output = (res.stderr or res.stdout or "").strip()
                raise RuntimeError(
                    f"Failed to install Python dependency '{dep_name}'."
                    + (f"\n{output[:2000]}" if output else "")
                )

        self._record_dep_cache(path, dependencies)

    def run_tests(self, path: str) -> dict:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        res = self._run(
            [
                self._venv_python(), "-m", "pytest", "tests/",
                "--cov=src",
                f"--cov-fail-under={_COVERAGE_THRESHOLD}",
                "--cov-report=term-missing",
                "--tb=short",
            ],
            cwd=path,
            timeout=60,
            env=env,
        )
        if res.returncode != 0:
            return self._fail(res.stdout + res.stderr)
        return self._ok()

    def run_example(self, path: str) -> dict:
        ep = os.path.join(path, "examples", self.example_filename(path))
        import subprocess
        res = subprocess.run(
            [self._venv_python(), ep],
            cwd=path, capture_output=True, text=True, timeout=60,
        )
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    def validate_subtree(self, widget_path: str, subdir: str,
                          coverage: int = 60) -> dict:
        """Validate a flat Python sidecar dir at <widget_path>/<subdir>/.

        Sidecars are stdlib-only calc helpers attached to non-Python widgets
        (e.g. ``python/`` inside an openscad widget). They have no
        ``__init__.py``, no examples, and no per-sidecar dependencies — every
        non-test file is a calc module, every ``test_*.py`` is a pytest test.

        Runs:
          1. print() AST check on calc files
          2. Contamination subset (abs paths, credentials, sleep, non-stdlib imports)
          3. pytest with coverage gated at ``coverage`` percent

        Empty / missing sidecar returns success silently — the dispatcher in
        validator.py decides whether to call this at all.
        """
        sub = os.path.join(widget_path, subdir)
        if not os.path.isdir(sub):
            return self._ok()
        py_files = sorted(
            f for f in glob.glob(os.path.join(sub, "*.py"))
            if not _is_os_metadata(os.path.basename(f))
        )
        if not py_files:
            return self._ok()

        src_files = [f for f in py_files if not os.path.basename(f).startswith("test_")]
        test_files = [f for f in py_files if os.path.basename(f).startswith("test_")]

        if not src_files:
            return self._fail(
                f"{subdir}/ contains only test files — add the calc module being tested"
            )
        if not test_files:
            return self._fail(
                f"{subdir}/ has calc modules but no test_*.py — sidecar requires tests"
            )

        errors = []
        for fpath in src_files:
            rel = os.path.relpath(fpath, widget_path)
            for lineno in self._find_print_calls(fpath):
                errors.append(f"print() in {rel}:{lineno} — remove debug output from {subdir}/")

        own_modules = {os.path.basename(f)[:-3] for f in py_files}
        contam = self._scan_subtree_contamination(
            widget_path, src_files, test_files, own_modules
        )
        errors.extend(contam["blocks"])
        if errors:
            return self._fail("\n".join(errors))

        import subprocess
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        res = subprocess.run(
            [
                sys.executable, "-m", "pytest", f"{subdir}/",
                f"--cov={subdir}",
                f"--cov-fail-under={coverage}",
                "--cov-report=term-missing",
                "--tb=short",
            ],
            cwd=widget_path,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        if res.returncode != 0:
            return self._fail(
                f"{subdir}/ sidecar failed:\n" + (res.stdout + res.stderr)[:2000]
            )
        return self._ok()

    def _scan_subtree_contamination(self, widget_path: str, src_files: list,
                                     test_files: list, own_modules: set) -> dict:
        """Contamination subset for a flat sidecar tree.

        Stricter than widget-level scan in one way: imports must be stdlib,
        test framework, or sidecar-local. No declared-deps escape hatch.
        """
        blocks = []
        for fpath in src_files + test_files:
            rel = os.path.relpath(fpath, widget_path)
            is_src = fpath in src_files
            try:
                code = open(fpath, encoding="utf-8").read()
            except Exception as e:
                blocks.append(f"Could not read {rel}: {e}")
                continue

            for line_no, line in enumerate(code.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                loc = f"{rel}:{line_no}"
                if self._ABS_PATH_RE.search(line):
                    blocks.append(f"Absolute path in {loc}: {stripped}")
                if is_src and self._CREDENTIAL_RE.search(line):
                    blocks.append(f"Possible credential in {loc}: {stripped}")

            try:
                tree = ast.parse(code)
            except Exception:
                continue

            if is_src:
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "sleep"
                            and isinstance(node.func.value, ast.Name)
                            and node.func.value.id in self._SLEEP_MODULES):
                        blocks.append(
                            f"sleep() in {rel}:{node.lineno} — sidecar must not block"
                        )

            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [(a.name.split(".")[0].lower(), node.lineno)
                              for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [(node.module.split(".")[0].lower(), node.lineno)]
                for top, lineno in names:
                    if not top:
                        continue
                    if (top in self._STDLIB or top in self._TEST_FRAMEWORKS
                            or top in own_modules):
                        continue
                    blocks.append(
                        f"Non-stdlib import '{top}' in {rel}:{lineno} — "
                        f"sidecars must use stdlib only"
                    )
        return {"blocks": blocks}

    def cleanup(self, path: str) -> None:
        import shutil
        try:
            from cg.universal_build_artifact_ignore_python.src.build_artifact_ignore import (
                excludes_for,
            )
            artifact_dirs = excludes_for(language="python")
        except ImportError:
            artifact_dirs = frozenset({"__pycache__", ".pytest_cache", ".venv"})
        self._venv_py = None
        # Preserve the cached venv so the next run can reuse it; still strip
        # __pycache__ / .pytest_cache. Pruning .venv from traversal also keeps
        # us from walking into site-packages to delete its __pycache__ dirs.
        preserve = {self.env_dir} if self.env_dir else set()
        for root, dirs, _files in os.walk(path):
            for d in list(dirs):
                if d in artifact_dirs and d not in preserve:
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            dirs[:] = [d for d in dirs if d not in artifact_dirs and d not in preserve]
