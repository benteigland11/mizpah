"""Rust language engine - cargo test with cargo-llvm-cov coverage.

The core toolchain is the single `cargo` binary (build, test, run, dependency
management). Line coverage is the one thing cargo doesn't ship: it's provided
by cargo-llvm-cov, which is cross-platform (Linux/macOS/Windows) and drives
the LLVM source-based coverage that rustc already emits - so it stays true to
"only check in things we can fully validate" on every platform, unlike
tarpaulin (effectively Linux-only).

Source scanning is handled by scanners/rust_scanner.rs (native Rust, std-only,
comment- and string-aware lexing) for detection that regex can't do reliably
across Rust's // line comments, /* */ nested block comments, and raw strings
(r"...", r#"..."#). Custom validation adds a `cargo build` and a `cargo fmt
--check` formatting floor on top of the base scanner pipeline.

Layout
------
    widget_root/
      Cargo.toml      [package] name; [dependencies] floors
      src/lib.rs      the widget library crate
      tests/          integration tests (test_*.rs), each its own crate that
                      imports the widget by its crate name
      examples/       example_usage.rs - run with `cargo run --example`

Integration tests import the widget through its crate name, exactly as a
consumer would, so the same `use` line works for anyone reading the tests as
documentation.

Coverage is enforced at 80% over src/ only (tests/ and examples/ are excluded
from the coverage denominator). Widgets are libraries: no
println!/eprintln!/print!/process::exit/panic-at-init in src/.
"""

import glob as _glob
import json as _json
import os
import tempfile

from .base import LanguageEngine, _dep_bare_name, log

_COVERAGE_THRESHOLD = 80

# Cargo edition for scaffolded crates. 2021 is the stable, widely-packaged
# edition; stay there rather than chase 2024 so distro toolchains validate.
_RUST_EDITION = "2021"

_CARGO_TOML = """\
[package]
name = "{name}"
version = "0.1.0"
edition = "{edition}"

[dependencies]
"""

_RUST_SRC = """\
//! {name}

/// Returns the input value unchanged.
///
/// [TODO] Replace with the widget's real API. Public items are the widget's
/// surface - document each one with a `///` doc comment.
pub fn process(value: &str) -> String {{
    value.to_string()
}}
"""

_RUST_TEST = """\
// Integration tests for {name}. Imports the widget by its crate name,
// exactly as a consumer would.
use {crate_name}::process;

#[test]
fn test_process() {{
    // [TODO] Replace with real tests. Coverage of src/ must reach {threshold}%.
    assert_eq!(process("hello"), "hello");
}}
"""

_RUST_EXAMPLE = """\
// Example usage of {name}.
//
// This file must run and exit cleanly with no user input, no network calls,
// and no external services. Use fake/hardcoded data to demonstrate the API.
use {crate_name}::process;

fn main() {{
    // [TODO] Replace with a realistic call using fake data
    println!("{{}}", process("hello"));
}}
"""


def _rust_crate_name(module_name: str) -> str:
    """Cargo normalizes hyphens to underscores for the importable crate name;
    package names allow hyphens but `use` statements never do."""
    return module_name.replace("-", "_")


def _insert_under_section(text: str, header: str, line: str) -> str:
    """Insert `line` immediately after the `[section]` header in a TOML-ish
    manifest, creating the section at the end if it is absent. Stdlib-only
    (no toml dep) - the manifest is a flat Cargo.toml the scaffold controls."""
    lines = text.splitlines(keepends=True)
    for i, raw in enumerate(lines):
        if raw.strip() == header:
            lines.insert(i + 1, line)
            return "".join(lines)
    tail = "" if text.endswith("\n") or not text else "\n"
    return text + tail + f"\n{header}\n{line}"


class RustEngine(LanguageEngine):
    name = "rust"
    validation_version = 1
    file_ext = "rs"
    aliases = ["rs"]
    toolchain = {
        "cargo": "Install Rust - rustup.rs (ships cargo + rustc)",
        "cargo-llvm-cov": "Install coverage - cargo install cargo-llvm-cov "
                          "(needs the llvm-tools-preview component: "
                          "rustup component add llvm-tools-preview)",
    }
    supported = True
    # cargo/rustc are real .exe binaries on PATH - no shell needed, and running
    # through cmd.exe would reparse the coverage regex's `|` as a pipe.
    windows_shell = False

    # Like Go, `rustc scanner.rs <files...>` would treat the target files as
    # nothing useful; the scanner is compiled once to a cached binary
    # (_scanner_runner) and that binary is the runner. Stays truthy so generic
    # "does this engine have a native scanner" checks behave.
    scanner_runner = ["rustc"]
    scanner_messages = {
        "print": "println!/print!/eprintln!/eprint! found in src/ - widgets "
                 "are libraries, remove console output:",
        "exit": "process::exit/std::process::abort found in src/ - widgets "
                "must not exit the caller's process:",
        "panic_toplevel": "panic!/unwrap/expect at module init found in src/ "
                          "- failing init takes down every consumer:",
        "unsafe_block": "unsafe block found in src/ - widgets must be safe by "
                        "default; justify and isolate any unsafe, or remove:",
    }
    scanner_warning_messages = {
        "credential": "Possible credentials found - remove before checkin:",
        "hardcoded_url": "Hardcoded URLs found - consider making these "
                         "configurable:",
        "hardcoded_ip": "Hardcoded IPs found - consider making these "
                        "configurable:",
        "abs_path": "Absolute paths found - widgets must be portable:",
        "env_var": "Environment variable access found in src/ - verify it's "
                   "not project-specific:",
        "sleep": "thread::sleep found in src/ - widgets must not block the "
                 "caller:",
        "todo_macro": "todo!/unimplemented! found in src/ - finish the "
                      "implementation before checkin:",
        "missing_doc": "Public item without a doc comment - the public surface "
                       "is the widget's API, document it with ///:",
        "hardcoded_value": "Hardcoded numeric tunables found in src/ - prefer "
                           "parameters or named consts a consumer can set:",
        "unlisted_import": "Unlisted external crate imports - add to widget.json "
                           "dependencies or remove:",
    }
    import_pattern = r"^\s*(use|extern\s+crate)\s"
    manifest_patterns = ["Cargo.toml", "Cargo.lock"]

    # ---- toolchain ---------------------------------------------------------

    def runtime_version(self):
        try:
            res = self._run(["cargo", "--version"], cwd=".", timeout=10)
            if res.returncode == 0:
                # "cargo 1.83.0 (5ffbef321 2024-10-29)"
                parts = res.stdout.strip().split()
                if len(parts) >= 2:
                    return f"cargo {parts[1]}"
        except Exception:
            pass
        return None

    def check_optional(self):
        """Surface cargo-llvm-cov status in doctor - it's the one piece that
        isn't bundled with cargo, and validation hard-fails without it."""
        import shutil
        present = shutil.which("cargo-llvm-cov") is not None
        return [(
            "cargo-llvm-cov",
            present,
            "coverage tool - cargo install cargo-llvm-cov "
            "(+ rustup component add llvm-tools-preview)",
        )]

    # ---- scaffold ----------------------------------------------------------

    def scaffold(self, target_dir, module_name, display_name, **_):
        crate = _rust_crate_name(module_name)

        def _w(path, content):
            # Rust source is canonically LF; rustfmt treats CRLF as a diff, so
            # always write LF regardless of platform - otherwise the scaffold's
            # own files would fail the fmt gate on Windows text-mode writes.
            with open(path, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)

        _w(os.path.join(target_dir, "Cargo.toml"),
           _CARGO_TOML.format(name=module_name, edition=_RUST_EDITION))
        for d in ("src", "tests", "examples"):
            os.makedirs(os.path.join(target_dir, d), exist_ok=True)
        _w(os.path.join(target_dir, "src", "lib.rs"),
           _RUST_SRC.format(name=display_name))
        _w(os.path.join(target_dir, "tests", f"test_{crate}.rs"),
           _RUST_TEST.format(crate_name=crate, name=display_name,
                             threshold=_COVERAGE_THRESHOLD))
        _w(os.path.join(target_dir, "examples", "example_usage.rs"),
           _RUST_EXAMPLE.format(crate_name=crate, name=display_name))

    def find_test_files(self, path):
        return _glob.glob(os.path.join(path, "tests", "**", "*.rs"),
                          recursive=True)

    def example_filename(self, path=""):
        return "example_usage.rs"

    def required_files(self, path):
        if os.path.isfile(os.path.join(path, "Cargo.toml")):
            return []
        return [("Cargo.toml",
                 "A Cargo.toml file is required at the widget root - run "
                 "'cartograph create' to scaffold it")]

    # ---- validation (build + fmt on top of base scanner pipeline) ----------

    def validate_widget(self, path, dependencies):
        errors = []

        src_files = _glob.glob(os.path.join(path, "src", "**", "*.rs"),
                               recursive=True)
        if not src_files:
            errors.append("src/ contains no .rs files - add at least one "
                          "source file")

        if src_files:
            # `cargo build` parses, type-checks, and borrow-checks the lib.
            # Limit to --lib so we don't compile the examples main into a
            # stray binary; the example is exercised by run_example.
            try:
                res = self._run(["cargo", "build", "--lib", "--quiet"],
                                cwd=path, timeout=300, env=self._cargo_env())
                if res.returncode != 0:
                    output = (res.stderr or res.stdout or "").strip()
                    # validate_widget runs BEFORE install_deps in the validator
                    # pipeline, so a declared-but-not-yet-added crate is expected
                    # here (cargo emits E0432/E0433 "unresolved import/module or
                    # unlinked crate"). Defer to run_tests, which builds again
                    # after install_deps. Mirrors Go's missing-module skip.
                    deferred = bool(dependencies) and (
                        "unresolved module or unlinked crate" in output
                        or "unresolved import" in output)
                    if not deferred:
                        errors.append(f"cargo build failed:\n{output[:3000]}")
            except FileNotFoundError:
                errors.append("cargo not found - install the Rust toolchain "
                              "(rustup.rs).")

        # rustfmt: non-negotiable formatting floor (modern-standards), the
        # ecosystem's canonical format. `cargo fmt --check` exits non-zero when
        # any file would change. rustfmt ships with rustup's default profile
        # but some distro packages split it out, so a missing subcommand is a
        # toolchain error (install rustfmt), not a formatting failure.
        try:
            res = self._run(["cargo", "fmt", "--check"], cwd=path, timeout=60,
                            env=self._cargo_env())
            if res.returncode != 0:
                combined = (res.stderr or "") + (res.stdout or "")
                if "no such command" in combined or "not installed" in combined:
                    errors.append(
                        "rustfmt is not installed - it provides the `cargo fmt` "
                        "formatting floor. Install it with `rustup component "
                        "add rustfmt` (or your distro's rustfmt package).")
                else:
                    errors.append(
                        "Unformatted Rust source - run `cargo fmt` (rustfmt is "
                        "the ecosystem's canonical format):\n"
                        + (res.stdout or res.stderr or "").strip()[:1500])
        except FileNotFoundError:
            pass  # cargo absence already reported by the build step

        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "rust_scanner.rs")
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

    def scan_contamination(self, path, widget):
        all_files = []
        for sub in ("src", "tests", "examples"):
            all_files.extend(_glob.glob(
                os.path.join(path, sub, "**", "*.rs"), recursive=True))
        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "rust_scanner.rs")
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

    def install_deps(self, path, dependencies):
        """Add each widget.json dependency to Cargo.toml with its floor.

        Dependency format: "<crate>>=<version>", e.g. "serde>=1.0.0". Cargo's
        resolver picks the newest compatible version at or above the floor;
        Cargo.lock (written on build) pins the exact resolution. The crate
        cache is content-addressed under CARGO_HOME, so no per-validation
        isolation is needed.
        """
        if not dependencies:
            return
        env = self._cargo_env()
        for dep in dependencies:
            bare = _dep_bare_name(dep)
            if not bare:
                continue
            import re
            m = re.search(r">=\s*([\w.+-]+)", str(dep))
            spec = f"{bare}@>={m.group(1).lstrip('v')}" if m else bare
            res = self._run(["cargo", "add", spec], cwd=path, timeout=180,
                            env=env)
            if res.returncode != 0:
                output = (res.stderr or res.stdout or "").strip()
                raise RuntimeError(
                    f"Failed to add Rust dependency '{bare}'."
                    + (f"\n{output[:2000]}" if output else ""))

    # ---- tests + coverage --------------------------------------------------

    def run_tests(self, path):
        if not self.find_test_files(path):
            return self._fail("No test files found in tests/")

        # cargo-llvm-cov runs the tests AND reports line coverage in one pass.
        # --summary-only --json gives a machine-readable total; the
        # ignore-filename-regex keeps the denominator to src/ (tests/ and
        # examples/ code must not pad the coverage number).
        #
        ignore = self._coverage_ignore_regex(path)
        try:
            res = self._run(
                ["cargo", "llvm-cov", "--summary-only", "--json",
                 "--ignore-filename-regex", ignore],
                cwd=path, timeout=600, env=self._cargo_env(),
            )
        except FileNotFoundError:
            return self._fail("cargo not found - install the Rust toolchain "
                              "(rustup.rs).")
        if res.returncode != 0:
            output = (res.stderr or res.stdout or "").strip()
            if "no such subcommand" in output or "llvm-cov" in output and \
                    "not found" in output:
                return self._fail(
                    "cargo-llvm-cov is not installed - run "
                    "`cargo install cargo-llvm-cov` and "
                    "`rustup component add llvm-tools-preview`.")
            return self._fail(output[:3000] or "cargo test failed")

        pct = self._parse_coverage(res.stdout)
        if pct is None:
            return self._fail("Could not determine coverage from "
                              "cargo-llvm-cov output:\n"
                              + (res.stdout or "")[:1000])
        if pct < _COVERAGE_THRESHOLD:
            return self._fail(
                f"Coverage {pct:.1f}% is below the required "
                f"{_COVERAGE_THRESHOLD}% - add tests for the uncovered lines "
                f"(cargo llvm-cov --summary-only shows per-file gaps)")
        return self._ok()

    @staticmethod
    def _coverage_ignore_regex(path):
        """The --ignore-filename-regex for cargo-llvm-cov.

        Always excludes tests/ and examples/ from the coverage denominator.
        Only excludes cg/ for a BLUEPRINT sandbox, where composed dependency
        widgets live under cg/ and must not count toward the blueprint's own
        coverage. A normal widget is itself installed at cg/<id>/src/lib.rs, so
        excluding cg/ there would match the widget's OWN source via its install
        path and report a false 0%. A blueprint is detected by its manifest,
        not its path.
        """
        if os.path.isfile(os.path.join(path, "blueprint.json")):
            return r"(tests|examples|cg)[/\\]"
        return r"(tests|examples)[/\\]"

    @staticmethod
    def _parse_coverage(stdout):
        """Pull the total line-coverage percent from cargo-llvm-cov --json.

        The JSON is the llvm-cov export schema:
          {"data": [{"totals": {"lines": {"percent": 87.5, ...}}}]}
        """
        text = (stdout or "").strip()
        if not text:
            return None
        # The JSON object is the last line; earlier lines may be cargo noise.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            try:
                return float(data["data"][0]["totals"]["lines"]["percent"])
            except (KeyError, IndexError, TypeError, ValueError):
                return None
        return None

    # ---- example -----------------------------------------------------------

    def run_example(self, path):
        if not os.path.isfile(os.path.join(path, "examples",
                                           self.example_filename())):
            return self._fail("examples/example_usage.rs not found")
        try:
            res = self._run(
                ["cargo", "run", "--quiet", "--example", "example_usage"],
                cwd=path, timeout=300, env=self._cargo_env())
        except FileNotFoundError:
            return self._fail("cargo not found - install the Rust toolchain "
                              "(rustup.rs).")
        if res.returncode != 0:
            return self._fail((res.stderr or res.stdout or "").strip())
        return self._ok()

    def run_blueprint_example(self, sandbox, example_file):
        """Run a blueprint example. The blueprint's Cargo.toml declares path
        dependencies on each composed widget under cg/<dep-id>/ (Cargo path
        deps are declarative, so no go.work-style synthesis is needed - the
        sandbox layout already matches the declared paths). The base default
        invokes python on the file, which is wrong for a compiled language."""
        stem = os.path.splitext(os.path.basename(example_file))[0]
        try:
            res = self._run(["cargo", "run", "--quiet", "--example", stem],
                            cwd=sandbox, timeout=300, env=self._cargo_env())
        except FileNotFoundError:
            return {"passed": False,
                    "error": "cargo not found - install the Rust toolchain "
                             "(rustup.rs)."}
        if res.returncode != 0:
            return {"passed": False,
                    "error": (res.stderr or res.stdout or "").strip()}
        return {"passed": True}

    # ---- blueprint dep wiring ----------------------------------------------

    def wire_blueprint_dep(self, blueprint_dir, dep_id, dep_dir):
        """Add a Cargo path dependency on the composed widget so the blueprint
        compiles against it. Points at `cg/<dep_id>/`, the layout the validator
        sandbox populates. Idempotent - re-adding an existing dep is a no-op."""
        manifest = os.path.join(blueprint_dir, "Cargo.toml")
        if not os.path.isfile(manifest):
            return
        rel = f"cg/{dep_id}"
        with open(manifest, encoding="utf-8") as f:
            text = f.read()
        if f'path = "{rel}"' in text:
            return
        crate = self._cargo_package_name(dep_dir) or _rust_crate_name(dep_id)
        line = f'{crate} = {{ path = "{rel}" }}\n'
        text = _insert_under_section(text, "[dependencies]", line)
        with open(manifest, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def unwire_blueprint_dep(self, blueprint_dir, dep_id):
        """Drop the Cargo path dependency on the composed widget."""
        manifest = os.path.join(blueprint_dir, "Cargo.toml")
        if not os.path.isfile(manifest):
            return
        rel = f'path = "cg/{dep_id}"'
        with open(manifest, encoding="utf-8") as f:
            lines = f.readlines()
        kept = [ln for ln in lines if rel not in ln]
        if len(kept) != len(lines):
            with open(manifest, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(kept)

    @staticmethod
    def _cargo_package_name(widget_dir):
        """Read the [package] name from a widget's Cargo.toml, or None."""
        manifest = os.path.join(widget_dir, "Cargo.toml")
        if not os.path.isfile(manifest):
            return None
        import re
        section = None
        with open(manifest, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line
                    continue
                if section == "[package]":
                    m = re.match(r'name\s*=\s*"([^"]+)"', line)
                    if m:
                        return m.group(1)
        return None

    # ---- cleanup -----------------------------------------------------------

    def cleanup(self, path):
        # cargo drops a target/ tree that can be large; remove it after
        # validation like the other compiled engines clean their artifacts.
        self._cleanup_artifact_dirs(path)
        target = os.path.join(path, "target")
        if os.path.isdir(target):
            import shutil
            shutil.rmtree(target, ignore_errors=True)

    # ---- private -----------------------------------------------------------

    def _scanner_runner(self):
        """Compile rust_scanner.rs to a cached binary and return it as the
        runner. Rebuilds when the scanner source is newer than the binary."""
        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "rust_scanner.rs")
        cache_dir = os.path.join(tempfile.gettempdir(),
                                 "cartograph-rust-scanner")
        os.makedirs(cache_dir, exist_ok=True)
        binary = os.path.join(
            cache_dir,
            "rust_scanner.exe" if os.name == "nt" else "rust_scanner")
        try:
            stale = (not os.path.isfile(binary)
                     or os.path.getmtime(binary) < os.path.getmtime(scanner))
        except OSError:
            stale = True
        if stale:
            res = self._run(
                ["rustc", "-O", "--edition", _RUST_EDITION, scanner,
                 "-o", binary],
                cwd=os.path.dirname(scanner), timeout=180,
                env=self._cargo_env())
            if res.returncode != 0:
                raise RuntimeError(
                    "Failed to compile rust scanner:\n"
                    + (res.stderr or res.stdout or "")[:2000])
        return [binary]

    def _cargo_env(self):
        """Cargo env for subprocesses.

        Do NOT override CARGO_HOME: cargo resolves third-party subcommands
        (`cargo llvm-cov`, `cargo fmt`) from $CARGO_HOME/bin, so redirecting it
        would hide an installed cargo-llvm-cov. Instead make sure the default
        cargo bin dir is on PATH, so the coverage subcommand resolves even when
        the caller's shell PATH doesn't include ~/.cargo/bin (as in a bare
        non-login process). Build artifacts stay under the widget's target/
        dir, which cleanup() removes."""
        env = os.environ.copy()
        cargo_home = env.get("CARGO_HOME") or os.path.join(
            os.path.expanduser("~"), ".cargo")
        cargo_bin = os.path.join(cargo_home, "bin")
        if os.path.isdir(cargo_bin):
            sep = os.pathsep
            path = env.get("PATH", "")
            if cargo_bin not in path.split(sep):
                env["PATH"] = cargo_bin + sep + path
        return env
