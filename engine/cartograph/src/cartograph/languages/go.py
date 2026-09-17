"""Go language engine - go test with built-in coverage.

The whole toolchain is the single `go` binary: tests, coverage, vet, build,
and module management all ship with it, which keeps this engine's toolchain
dict to one entry and matches Cartograph's zero-dependency philosophy.

Source scanning is handled by scanners/go_scanner.go (native Go, stdlib-only
go/ast parsing) for string/comment-aware detection. Custom validation adds
`go vet` and a build check on top of the base scanner pipeline.

Layout
------
    widget_root/
      go.mod        module <module_name>; declares dependency floors
      src/          the widget package (import path "<module_name>/src")
      tests/        black-box tests (package tests, *_test.go)
      examples/     example_usage.go - package main, run with `go run`

Tests import the widget through its module path ("<module>/src"), never by
relative path, so the same import line works for every consumer reading the
tests as documentation.

Coverage is enforced at 80% (go test -coverpkg gives it for free). Widgets
are libraries: no fmt.Print*/os.Exit/log.Fatal in src/.
"""

import glob as _glob
import os
import re
import tempfile

from .base import LanguageEngine, _dep_bare_name, log

_COVERAGE_THRESHOLD = 80

# Floor for the go.mod `go` directive. Two majors are officially supported;
# stay one behind latest so distro-packaged toolchains validate cleanly.
_GO_DIRECTIVE = "1.24"

_GO_MOD = """\
module {module}

go {go_directive}
"""

_GO_SRC = """\
// Package {pkg} - {name}.
package {pkg}

// Process returns the input value.
// [TODO] Replace with the widget's real API. Exported identifiers are the
// widget's public surface - document each one.
func Process(value string) string {{
	return value
}}
"""

_GO_TEST = """\
// Black-box tests for {name}. Imports the widget by module path, exactly
// as a consumer would.
package tests

import (
	"testing"

	{pkg} "{module}/src"
)

func TestProcess(t *testing.T) {{
	// [TODO] Replace with real tests. Coverage of src/ must reach {threshold}%.
	if got := {pkg}.Process("hello"); got != "hello" {{
		t.Fatalf("Process(%q) = %q, want %q", "hello", got, "hello")
	}}
}}
"""

_GO_EXAMPLE = """\
// Example usage of {name}.
//
// This file must run and exit cleanly with no user input, no network
// calls, and no external services. Use fake/hardcoded data to
// demonstrate the API.
package main

import (
	"fmt"

	{pkg} "{module}/src"
)

func main() {{
	// [TODO] Replace with a realistic call using fake data
	fmt.Println({pkg}.Process("hello"))
}}
"""

# Parses the "total:" line of `go tool cover -func` output:
#   total:        (statements)    87.5%
_COVER_TOTAL_RE = re.compile(r"^total:.*?(\d+(?:\.\d+)?)%\s*$", re.MULTILINE)


def _go_package_name(module_name: str) -> str:
    """Go identifiers can't contain hyphens; module paths can."""
    return module_name.replace("-", "_")


class GoEngine(LanguageEngine):
    name = "go"
    validation_version = 1
    file_ext = "go"
    aliases = ["golang"]
    toolchain = {"go": "Install Go 1.23+ - go.dev/dl"}
    supported = True

    # NOTE: `go run scanner.go <files...>` cannot work - go run consumes
    # every leading .go argument as a source file to compile, so the target
    # files would never reach the scanner as argv. The engine instead
    # compiles the scanner once to a cached binary (_scanner_runner) and
    # passes that as the runner. This attribute stays truthy so generic
    # "does this engine have a native scanner" checks behave.
    scanner_runner = ["go", "run"]
    scanner_messages = {
        "print": "fmt.Print*/print/println found in src/ - widgets are libraries, remove console output:",
        "exit": "os.Exit/log.Fatal* found in src/ - widgets must not exit the caller's process:",
        "panic_toplevel": "panic in package initialization found in src/ - failing init takes down every consumer:",
        "deprecated_import": "Deprecated stdlib import in src/ - io/ioutil has named replacements in io and os (deprecated since Go 1.16):",
    }
    scanner_warning_messages = {
        "credential": "Possible credentials found - remove before checkin:",
        "hardcoded_url": "Hardcoded URLs found - consider making these configurable:",
        "hardcoded_ip": "Hardcoded IPs found - consider making these configurable:",
        "hardcoded_value": "Hardcoded numeric tunables found in src/ - prefer parameters:",
        "abs_path": "Absolute paths found - widgets must be portable:",
        "env_var": "Environment variable access found in src/ - verify it's not project-specific:",
        "sleep": "time.Sleep found in src/ - widgets must not block the caller:",
        "unlisted_import": "Unlisted module imports - add to widget.json dependencies or remove:",
        "top_level_var": "Top-level mutable state found in src/ - prefer local state or explicit parameters:",
        # Modern-standards nudges (warnings, overridable at checkin)
        "legacy_rand": "math/rand found in src/ - prefer math/rand/v2 (Go 1.22+):",
        "empty_interface": "interface{} found in src/ - use the 'any' alias (Go 1.18+):",
        "missing_doc": "Exported identifier without a doc comment - the exported surface is the widget's API, document it:",
        "bare_goroutine": "Anonymous goroutine in src/ - confirm a WaitGroup or context governs its lifetime:",
        "error_equality": "Error compared with ==/!= in src/ - use errors.Is so wrapped errors match:",
    }
    import_pattern = r'^import\s|^\t"'
    manifest_patterns = ["go.mod", "go.sum"]

    # ---- toolchain ---------------------------------------------------------

    def runtime_version(self) -> str | None:
        try:
            res = self._run(["go", "version"], cwd=".", timeout=10)
            if res.returncode == 0:
                # "go version go1.25.11 linux/amd64"
                parts = res.stdout.strip().split()
                for p in parts:
                    if p.startswith("go1"):
                        return f"go {p[2:]}"
        except Exception:
            pass
        return None

    # ---- scaffold ----------------------------------------------------------

    def scaffold(self, target_dir, module_name, display_name, **_):
        pkg = _go_package_name(module_name)
        # Go source is canonically LF; gofmt treats CRLF as unformatted, so
        # always write LF (newline="\n") regardless of platform - otherwise
        # the scaffold's own files would fail the gofmt gate on Windows,
        # where text-mode writes translate \n to \r\n.
        def _w(path, content):
            with open(path, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)
        _w(os.path.join(target_dir, "go.mod"),
           _GO_MOD.format(module=module_name, go_directive=_GO_DIRECTIVE))
        for d in ("src", "tests", "examples"):
            os.makedirs(os.path.join(target_dir, d), exist_ok=True)
        _w(os.path.join(target_dir, "src", f"{pkg}.go"),
           _GO_SRC.format(pkg=pkg, name=display_name))
        _w(os.path.join(target_dir, "tests", f"{pkg}_test.go"),
           _GO_TEST.format(pkg=pkg, module=module_name, name=display_name,
                           threshold=_COVERAGE_THRESHOLD))
        _w(os.path.join(target_dir, "examples", "example_usage.go"),
           _GO_EXAMPLE.format(pkg=pkg, module=module_name, name=display_name))

    def find_test_files(self, path: str) -> list[str]:
        return _glob.glob(os.path.join(path, "tests", "**", "*_test.go"),
                          recursive=True)

    def example_filename(self, path: str = "") -> str:
        return "example_usage.go"

    def required_files(self, path: str) -> list[tuple[str, str]]:
        if os.path.isfile(os.path.join(path, "go.mod")):
            return []
        return [("go.mod",
                 "A go.mod file is required at the widget root - run "
                 "'cartograph create' to scaffold it")]

    # ---- validation (vet + build on top of base scanner pipeline) ----------

    def validate_widget(self, path: str, dependencies: list) -> dict:
        errors = []

        src_files = _glob.glob(os.path.join(path, "src", "**", "*.go"),
                               recursive=True)
        if not src_files:
            errors.append("src/ contains no .go files - add at least one "
                          "source file")

        if src_files:
            # go vet covers parse, type-check, and the standard analyzers in
            # one pass across all packages (tests/ and examples/ included, so
            # a broken example fails validation, not just checkin). The build
            # is src/ only: tests-only dirs don't build, and building the
            # examples main package would drop a binary in the widget root.
            for label, cmd in (("go vet", ["go", "vet", "./..."]),
                               ("go build", ["go", "build", "./src/..."])):
                try:
                    res = self._run(cmd, cwd=path, timeout=120,
                                    env=self._go_env())
                    if res.returncode != 0:
                        output = (res.stderr or res.stdout or "").strip()
                        # validate_widget runs before install_deps in the
                        # pipeline, so declared-but-not-yet-fetched modules
                        # are expected here (same policy as Nim's "cannot
                        # find module" skip). run_tests catches genuinely
                        # missing deps after install.
                        if "no required module provides package" in output \
                                or "missing go.sum entry" in output:
                            continue
                        errors.append(f"{label} failed:\n{output[:3000]}")
                except FileNotFoundError:
                    errors.append("Go not found - install the Go toolchain.")
                    break

        # gofmt: non-negotiable formatting floor (modern-standards). Runs
        # over every .go file, not just src/ - canonical formatting is the
        # single strongest convention signal in the Go ecosystem and ships
        # with the toolchain. `gofmt -l` lists files that differ; exit code
        # is 0 unless gofmt itself errors, so a non-empty stdout = block.
        all_go = []
        for sub in ("src", "tests", "examples"):
            all_go.extend(_glob.glob(
                os.path.join(path, sub, "**", "*.go"), recursive=True))
        if all_go:
            try:
                res = self._run(["gofmt", "-l"] + sorted(all_go), cwd=path,
                                timeout=60, env=self._go_env())
                unformatted = [ln.strip() for ln in
                               (res.stdout or "").splitlines() if ln.strip()]
                if unformatted:
                    rels = ", ".join(os.path.relpath(u, path)
                                     for u in unformatted)
                    errors.append(
                        "Unformatted Go source - run `gofmt -w .` (gofmt is "
                        f"the ecosystem's canonical format): {rels}")
            except FileNotFoundError:
                errors.append("Go not found - install the Go toolchain.")

        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "go_scanner.go")
        scan_errors, _, _ = self._run_native_scanner(
            scanner_path=scanner,
            runner=self._scanner_runner(),
            src_files=src_files,
            cwd=path,
            finding_messages=self.scanner_messages,
        )
        errors.extend(scan_errors)

        errors.extend(self._check_dep_pinning(dependencies))

        if errors:
            return self._fail("\n".join(errors))
        return self._ok()

    def scan_contamination(self, path: str, widget: dict) -> dict:
        all_files = []
        for sub in ("src", "tests", "examples"):
            all_files.extend(_glob.glob(
                os.path.join(path, sub, "**", "*.go"), recursive=True))
        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "go_scanner.go")
        scan_errors, scan_warnings, scan_blocks = self._run_native_scanner(
            scanner_path=scanner,
            runner=self._scanner_runner(),
            src_files=all_files,
            cwd=path,
            finding_messages=self.scanner_messages,
        )
        return {"blocks": scan_blocks + scan_errors,
                "warnings": scan_warnings}

    # ---- dependencies ------------------------------------------------------

    def install_deps(self, path: str, dependencies: list) -> None:
        """Pin each widget.json dependency in go.mod, then tidy.

        Dependency format: "<module-path>>=<version>", e.g.
        "github.com/google/uuid>=1.6.0". The floor becomes the go.mod
        requirement (Go's MVS resolves minimum versions, so the floor IS
        the resolved version unless a consumer raises it). The shared
        module cache (GOMODCACHE) is version-addressed and checksummed,
        so no per-validation isolation is needed - go.sum carries the
        integrity guarantee.
        """
        if not dependencies:
            return
        env = self._go_env()
        for dep in dependencies:
            bare = _dep_bare_name(dep)
            if not bare:
                continue
            m = re.search(r">=\s*([\w.+-]+)", str(dep))
            target = f"{bare}@v{m.group(1).lstrip('v')}" if m else bare
            res = self._run(["go", "get", target], cwd=path, timeout=180,
                            env=env)
            if res.returncode != 0:
                output = (res.stderr or res.stdout or "").strip()
                raise RuntimeError(
                    f"Failed to install Go dependency '{bare}'."
                    + (f"\n{output[:2000]}" if output else "")
                )
        res = self._run(["go", "mod", "tidy"], cwd=path, timeout=180, env=env)
        if res.returncode != 0:
            output = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(f"go mod tidy failed:\n{output[:2000]}")

    # ---- tests + coverage --------------------------------------------------

    def run_tests(self, path: str) -> dict:
        if not self.find_test_files(path):
            return self._fail("No *_test.go files found in tests/")

        profile = os.path.join(tempfile.mkdtemp(prefix="cartograph_go_"),
                               "cover.out")
        try:
            res = self._run(
                ["go", "test", "./tests/...", "-count=1",
                 "-coverpkg=./src/...", f"-coverprofile={profile}"],
                cwd=path, timeout=300, env=self._go_env(),
            )
        except FileNotFoundError:
            return self._fail("Go not found - install the Go toolchain.")
        if res.returncode != 0:
            return self._fail(res.stdout or res.stderr)

        cover = self._run(["go", "tool", "cover", f"-func={profile}"],
                          cwd=path, timeout=60, env=self._go_env())
        m = _COVER_TOTAL_RE.search(cover.stdout or "")
        if not m:
            return self._fail("Could not determine coverage from "
                              "go tool cover output:\n"
                              + (cover.stdout or cover.stderr or "")[:1000])
        pct = float(m.group(1))
        if pct < _COVERAGE_THRESHOLD:
            return self._fail(
                f"Coverage {pct:.1f}% is below the required "
                f"{_COVERAGE_THRESHOLD}% - add tests for the uncovered "
                f"statements (go tool cover -func shows per-function gaps)")
        return self._ok()

    # ---- example -----------------------------------------------------------

    def run_example(self, path: str) -> dict:
        if not os.path.isfile(os.path.join(path, "examples",
                                           self.example_filename())):
            return self._fail("examples/example_usage.go not found")
        try:
            res = self._run(["go", "run", "./examples"], cwd=path,
                            timeout=120, env=self._go_env())
        except FileNotFoundError:
            return self._fail("Go not found - install the Go toolchain.")
        if res.returncode != 0:
            return self._fail(res.stderr or res.stdout)
        return self._ok()

    def run_blueprint_example(self, sandbox: str, example_file: str) -> dict:
        """Run a blueprint example with a synthesized go.work so the
        blueprint's own module and every dep widget module under
        sandbox/cg/ resolve together. The base default invokes python on
        the file, which is wrong for any compiled language."""
        work_dirs = ["."]
        cg_root = os.path.join(sandbox, "cg")
        if os.path.isdir(cg_root):
            for entry in sorted(os.listdir(cg_root)):
                if os.path.isfile(os.path.join(cg_root, entry, "go.mod")):
                    work_dirs.append(os.path.join("cg", entry))
        work_path = os.path.join(sandbox, "go.work")
        had_work = os.path.isfile(work_path)
        if not had_work:
            with open(work_path, "w", encoding="utf-8") as f:
                f.write(f"go {_GO_DIRECTIVE}\n\nuse (\n")
                for d in work_dirs:
                    f.write(f"\t{d}\n")
                f.write(")\n")
        try:
            res = self._run(["go", "run", "./examples"], cwd=sandbox,
                            timeout=180, env=self._go_env())
        except FileNotFoundError:
            return {"passed": False,
                    "error": "Go not found - install the Go toolchain."}
        finally:
            if not had_work and os.path.isfile(work_path):
                try:
                    os.remove(work_path)
                except OSError:
                    pass
        if res.returncode != 0:
            return {"passed": False,
                    "error": (res.stderr or res.stdout or "").strip()}
        return {"passed": True}

    # ---- blueprint dep wiring ----------------------------------------------

    def wire_blueprint_dep(self, blueprint_dir, dep_id, dep_dir):
        """Wire a composed widget into go.mod via Go's local-module mechanism:
        a `require <module> v0.0.0` plus a `replace <module> => ./cg/<dep_id>`
        pointing at the layout the validator sandbox populates. Idempotent."""
        manifest = os.path.join(blueprint_dir, "go.mod")
        if not os.path.isfile(manifest):
            return
        rel = f"./cg/{dep_id}"
        with open(manifest, encoding="utf-8") as f:
            text = f.read()
        if f"=> {rel}" in text:
            return
        module = self._go_module_name(dep_dir)
        if not module:
            return
        block = f"\nrequire {module} v0.0.0\n\nreplace {module} => {rel}\n"
        text = text.rstrip("\n") + "\n" + block
        with open(manifest, "w", newline="\n", encoding="utf-8") as f:
            f.write(text)

    def unwire_blueprint_dep(self, blueprint_dir, dep_id):
        """Drop the require/replace pair that composes the given widget."""
        manifest = os.path.join(blueprint_dir, "go.mod")
        if not os.path.isfile(manifest):
            return
        rel = f"./cg/{dep_id}"
        with open(manifest, encoding="utf-8") as f:
            lines = f.readlines()
        module = None
        for ln in lines:
            parts = ln.split()
            if (len(parts) >= 4 and parts[0] == "replace"
                    and parts[2] == "=>" and parts[3] == rel):
                module = parts[1]
                break
        if module is None:
            return
        kept = []
        for ln in lines:
            parts = ln.split()
            if (len(parts) >= 4 and parts[0] == "replace"
                    and parts[2] == "=>" and parts[3] == rel):
                continue
            if (len(parts) >= 2 and parts[0] == "require"
                    and parts[1] == module):
                continue
            kept.append(ln)
        with open(manifest, "w", newline="\n", encoding="utf-8") as f:
            f.writelines(kept)

    @staticmethod
    def _go_module_name(widget_dir):
        """Read the module path from a widget's go.mod, or None."""
        manifest = os.path.join(widget_dir, "go.mod")
        if not os.path.isfile(manifest):
            return None
        with open(manifest, encoding="utf-8") as f:
            for raw in f:
                parts = raw.split()
                if len(parts) >= 2 and parts[0] == "module":
                    return parts[1]
        return None

    # ---- cleanup -----------------------------------------------------------

    def cleanup(self, path: str) -> None:
        self._cleanup_artifact_dirs(path)

    # ---- private -----------------------------------------------------------

    def _scanner_runner(self) -> list:
        """Compile go_scanner.go to a cached binary and return it as the
        runner. Rebuilds when the scanner source is newer than the binary."""
        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "go_scanner.go")
        cache_dir = os.path.join(tempfile.gettempdir(),
                                 "cartograph-go-scanner")
        os.makedirs(cache_dir, exist_ok=True)
        binary = os.path.join(
            cache_dir, "go_scanner.exe" if os.name == "nt" else "go_scanner")
        try:
            stale = (not os.path.isfile(binary)
                     or os.path.getmtime(binary) < os.path.getmtime(scanner))
        except OSError:
            stale = True
        if stale:
            res = self._run(["go", "build", "-o", binary, scanner],
                            cwd=os.path.dirname(scanner), timeout=120,
                            env=self._go_env())
            if res.returncode != 0:
                raise RuntimeError(
                    "Failed to compile go scanner:\n"
                    + (res.stderr or res.stdout or "")[:2000])
        return [binary]

    def _go_env(self) -> dict:
        """Keep Go's build cache writable in sandboxed environments where
        $HOME is read-only (same reasoning as Nim's nimcache redirect)."""
        env = os.environ.copy()
        if not env.get("GOCACHE"):
            cache_root = os.path.join(tempfile.gettempdir(),
                                      "cartograph-go-cache")
            os.makedirs(cache_root, exist_ok=True)
            env["GOCACHE"] = cache_root
        return env
