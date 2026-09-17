"""Flutter language engine - flutter test + flutter_test + lcov coverage.

Toolchain is the Flutter SDK (3.x), which bundles its own Dart SDK - the
`dart` binary is used to run the native contamination scanner. Widgets may
be Flutter UI widgets or pure Dart logic; both validate through
`flutter test`, which runs headless with no device or emulator.

Layout
------
    widget_root/
      pubspec.yaml             the widget OWNS this file (marker blocks
                               inside are generated - see below)
      analysis_options.yaml    excludes generated dirs from analysis
      src/                     library sources - the canonical Cartograph
                               source dir; import siblings by relative path
      tests/                   flutter_test tests, files end in _test.dart,
                               import the widget as package:<module>/...
      examples/                example_usage.dart - flutter_test harness
      lib/                     GENERATED copy of src/ - never edit/commit

Flutter's coverage collector only reports libraries under lib/, so the
engine materializes lib/ as an exact copy of src/ before running tests
and examples, and removes it in cleanup(). Tests and examples import the
widget by package name (`package:<module>/<module>.dart`), which resolves
to the lib/ copy; coverage is therefore measured over the src/ sources
via that copy. src/ stays the single source of truth.

Example validation deviates from "run and exit cleanly": Flutter UI code
cannot execute on the bare Dart VM, so examples are runnable demos inside
the flutter_test harness (`flutter test examples/example_usage.dart`).
The example must define at least one test()/testWidgets() block - a plain
main() exits with "no tests found".

Declared widget.json dependencies ("package>=version") are written into
the marked cartograph-deps block inside pubspec.yaml by install_deps().
Blueprint-composed widgets are wired as pub path dependencies into the
marked cartograph-composed block, pointing at cg/<dep_id>/ - the layout
the blueprint validator sandbox populates.

Coverage is enforced at 80% (lcov line counters). Widgets are libraries:
no print/debugPrint, no exit(), no sleep() in src/.
"""

import glob as _glob
import os
import re
import shutil as _shutil

from .base import LanguageEngine, _dep_bare_name

_COVERAGE_THRESHOLD = 80

_DEPS_START = "# --- cartograph-deps start (generated from widget.json - do not edit) ---"
_DEPS_END = "# --- cartograph-deps end ---"
_COMPOSED_START = "# --- cartograph-composed start (blueprint deps - do not edit) ---"
_COMPOSED_END = "# --- cartograph-composed end ---"

_PUBSPEC = """\
name: {module}
description: {name} - a Cartograph widget.
publish_to: none

environment:
  sdk: ">=3.0.0 <4.0.0"

dependencies:
  flutter:
    sdk: flutter
  {deps_start}
  {deps_end}
  {composed_start}
  {composed_end}

dev_dependencies:
  flutter_test:
    sdk: flutter
"""

_ANALYSIS_OPTIONS = """\
# lib/ is a generated copy of src/ (see the engine notes in pubspec.yaml)
# and cg/ holds composed widgets in blueprint sandboxes - analyze neither.
analyzer:
  exclude:
    - "lib/**"
    - "cg/**"
"""

_DART_SRC = """\
import 'package:flutter/widgets.dart';

/// {name}.
///
/// [TODO] Replace with the widget's real API. Everything in src/ is the
/// widget's public surface - document each exported member. Import
/// sibling src/ files by relative path; consumers import this widget as
/// package:{module}/{module}.dart.
class {cls} extends StatelessWidget {{
  /// The text to render.
  final String label;

  /// Creates the widget.
  const {cls}({{super.key, required this.label}});

  @override
  Widget build(BuildContext context) {{
    return Text(label, textDirection: TextDirection.ltr);
  }}
}}
"""

_DART_TEST = """\
import 'package:flutter_test/flutter_test.dart';

import 'package:{module}/{module}.dart';

void main() {{
  // [TODO] Replace with real tests. Line coverage of src/ must reach {threshold}%.
  testWidgets('renders the label', (tester) async {{
    await tester.pumpWidget(const {cls}(label: 'value'));
    expect(find.text('value'), findsOneWidget);
  }});
}}
"""

_DART_EXAMPLE = """\
import 'package:flutter_test/flutter_test.dart';

import 'package:{module}/{module}.dart';

/// Example usage of {name}.
///
/// Examples run headless through the flutter_test harness - demonstrate
/// the API inside test()/testWidgets() blocks using fake/hardcoded data.
/// No user input, no network calls, no external services.
void main() {{
  // [TODO] Replace with a realistic demonstration using fake data.
  testWidgets('example: render a label and read it back', (tester) async {{
    await tester.pumpWidget(const {cls}(label: 'example value'));
    expect(find.text('example value'), findsOneWidget);
  }});
}}
"""

# lcov records:  SF:<path>  ...  LF:<n>  LH:<n>  end_of_record
_LCOV_SF_RE = re.compile(r"^SF:(.+)$", re.MULTILINE)
_LCOV_LF_RE = re.compile(r"^LF:(\d+)$", re.MULTILINE)
_LCOV_LH_RE = re.compile(r"^LH:(\d+)$", re.MULTILINE)

_PUBSPEC_NAME_RE = re.compile(r"^name:\s*([A-Za-z0-9_]+)", re.MULTILINE)

# "http>=1.2.0" -> ("http", ">=1.2.0")
_DEP_CONSTRAINT_RE = re.compile(r"^([A-Za-z0-9_]+)\s*(.*)$")


def _dart_class_name(module_name: str) -> str:
    """PascalCase class name from a hyphen/underscore slug."""
    return "".join(p.capitalize() for p in re.split(r"[-_]+", module_name) if p)


class FlutterEngine(LanguageEngine):
    name = "flutter"
    validation_version = 1
    file_ext = "dart"
    toolchain = {
        "flutter": "Install the Flutter SDK 3.x - docs.flutter.dev/get-started/install",
        "dart": "Bundled with the Flutter SDK - ensure the SDK's bin/ dir is on PATH",
    }
    supported = True

    scanner_runner = ["dart"]
    scanner_messages = {
        "print": "print/debugPrint/stdout output found in src/ - widgets are libraries, remove console output:",
        "exit": "exit() found in src/ - widgets must not exit the caller's process:",
    }
    scanner_warning_messages = {
        "credential": "Possible credentials found - remove before checkin:",
        "hardcoded_url": "Hardcoded URLs found - consider making these configurable:",
        "hardcoded_ip": "Hardcoded IPs found - consider making these configurable:",
        "hardcoded_value": "Hardcoded numeric tunables found in src/ - prefer parameters:",
        "abs_path": "Absolute paths found - widgets must be portable:",
        "env_var": "Environment variable access found in src/ - verify it's not project-specific:",
        "sleep": "Blocking sleep() found - widgets must not block the caller:",
        "unlisted_import": "Unlisted package imports - add to widget.json dependencies or remove:",
    }
    import_pattern = r"^import\s+'package:"
    manifest_patterns = ["pubspec.yaml", "analysis_options.yaml"]

    # flutter/dart on Windows are .bat wrappers - need the shell (base
    # default windows_shell = True).

    # ---- toolchain ---------------------------------------------------------

    def runtime_version(self) -> str | None:
        try:
            res = self._run(["flutter", "--version"], cwd=".", timeout=60)
            # First line:  Flutter 3.47.1 • channel stable • ...
            m = re.search(r"Flutter\s+([\w.+-]+)", res.stdout or res.stderr or "")
            if m:
                return f"flutter {m.group(1)}"
        except Exception:
            pass
        return None

    # ---- scaffold ----------------------------------------------------------

    def scaffold(self, target_dir, module_name, display_name, **_):
        cls = _dart_class_name(module_name)

        def _w(path, content):
            with open(path, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)

        _w(os.path.join(target_dir, "pubspec.yaml"),
           _PUBSPEC.format(module=module_name, name=display_name,
                           deps_start=_DEPS_START, deps_end=_DEPS_END,
                           composed_start=_COMPOSED_START,
                           composed_end=_COMPOSED_END))
        _w(os.path.join(target_dir, "analysis_options.yaml"),
           _ANALYSIS_OPTIONS)
        for d in ("src", "tests", "examples"):
            os.makedirs(os.path.join(target_dir, d), exist_ok=True)
        _w(os.path.join(target_dir, "src", f"{module_name}.dart"),
           _DART_SRC.format(module=module_name, cls=cls, name=display_name))
        _w(os.path.join(target_dir, "tests", f"{module_name}_test.dart"),
           _DART_TEST.format(module=module_name, cls=cls,
                             threshold=_COVERAGE_THRESHOLD))
        _w(os.path.join(target_dir, "examples", "example_usage.dart"),
           _DART_EXAMPLE.format(module=module_name, cls=cls,
                                name=display_name))

    def find_test_files(self, path: str) -> list[str]:
        return _glob.glob(os.path.join(path, "tests", "**", "*_test.dart"),
                          recursive=True)

    def example_filename(self, path: str = "") -> str:
        return "example_usage.dart"

    def required_files(self, path: str) -> list[tuple[str, str]]:
        if not os.path.isfile(os.path.join(path, "pubspec.yaml")):
            return [("pubspec.yaml",
                     "A pubspec.yaml is required at the widget root - run "
                     "'cartograph create' to scaffold it")]
        return []

    # ---- validation (analyze on top of base scanner pipeline) --------------

    def validate_widget(self, path: str, dependencies: list) -> dict:
        errors = []

        src_files = _glob.glob(os.path.join(path, "src", "**", "*.dart"),
                               recursive=True)
        if not src_files:
            errors.append("src/ contains no .dart files - add at least one "
                          "source file")

        if src_files:
            # Static-analyze before tests: a type error should fail
            # validation with an analyzer message, not surface later as a
            # confusing test failure. validate runs before install_deps in
            # the pipeline, so write the declared deps into pubspec.yaml
            # first and let pub resolve them. Resolution failures (offline,
            # registry hiccup) skip the analyze step; run_tests catches
            # genuinely missing deps after install.
            try:
                self._write_deps_block(path, dependencies)
            except RuntimeError as e:
                errors.append(str(e))
            self._materialize_all_libs(path)
            pub = self._run(["flutter", "pub", "get"], cwd=path, timeout=300)
            if pub.returncode == 0:
                res = self._run(["flutter", "analyze", "--no-pub",
                                 "--no-fatal-infos", "--no-fatal-warnings"],
                                cwd=path, timeout=300)
                if res.returncode != 0:
                    output = (res.stdout or res.stderr or "").strip()
                    errors.append(f"flutter analyze failed:\n{output[:3000]}")

        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "flutter_scanner.dart")
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

    def scan_contamination(self, path: str, widget: dict) -> dict:
        all_files = []
        for sub in ("src", "tests", "examples"):
            all_files.extend(_glob.glob(
                os.path.join(path, sub, "**", "*.dart"), recursive=True))
        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "flutter_scanner.dart")
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

    def install_deps(self, path: str, dependencies: list) -> None:
        """Write declared deps into pubspec.yaml and resolve them.

        Dependency format: "package>=version", e.g. "collection>=1.18.0".
        The constraint goes into the marked cartograph-deps block verbatim
        (pub treats ">=x.y.z" as a floor). Pub's shared cache (~/.pub-cache)
        holds the artifacts, so no per-widget isolation is needed.
        """
        self._write_deps_block(path, dependencies)
        res = self._run(["flutter", "pub", "get"], cwd=path, timeout=300)
        if res.returncode != 0:
            output = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(
                f"Failed to resolve Flutter dependencies:\n{output[:2000]}")

    # ---- tests + coverage --------------------------------------------------

    def run_tests(self, path: str) -> dict:
        if not self.find_test_files(path):
            return self._fail("No *_test.dart files found in tests/")

        self._materialize_all_libs(path)
        # --no-dds: the Dart Development Service is debug/devtools plumbing
        # that headless validation never uses - and it can outlive the test
        # run as an orphaned process (caught on the macOS validation node).
        res = self._run(["flutter", "test", "--coverage", "--no-dds",
                         "tests"], cwd=path, timeout=600)
        if res.returncode != 0:
            return self._fail(res.stdout or res.stderr)

        lcov = os.path.join(path, "coverage", "lcov.info")
        if not os.path.isfile(lcov):
            return self._fail(
                "coverage/lcov.info not found after tests - tests must "
                "import the widget as package:<module>/... so coverage "
                "can attribute lines (see the scaffolded test)")
        with open(lcov, encoding="utf-8") as f:
            report = f.read()
        found = _LCOV_LF_RE.findall(report)
        hit = _LCOV_LH_RE.findall(report)
        total = sum(int(v) for v in found)
        covered = sum(int(v) for v in hit)
        if total == 0:
            return self._fail(
                "Coverage report is empty - tests must import the widget "
                "as package:<module>/... so coverage can attribute lines "
                "(see the scaffolded test)")
        pct = 100.0 * covered / total
        if pct < _COVERAGE_THRESHOLD:
            files = ", ".join(
                m.replace("\\", "/").replace("lib/", "src/", 1)
                for m in _LCOV_SF_RE.findall(report))
            return self._fail(
                f"Coverage {pct:.1f}% is below the required "
                f"{_COVERAGE_THRESHOLD}% - add tests for the uncovered "
                f"lines (measured over: {files})")
        return self._ok()

    # ---- example -----------------------------------------------------------

    def run_example(self, path: str) -> dict:
        ep = os.path.join("examples", self.example_filename())
        if not os.path.isfile(os.path.join(path, ep)):
            return self._fail("examples/example_usage.dart not found")
        self._materialize_all_libs(path)
        # --no-dds for the same reason as run_tests: no debug service, no
        # orphaned process after the harness exits.
        res = self._run(["flutter", "test", "--no-dds", ep], cwd=path,
                        timeout=300)
        if res.returncode != 0:
            output = (res.stdout or "") + (res.stderr or "")
            if "No tests were found" in output or res.returncode == 79:
                return self._fail(
                    "examples/example_usage.dart defines no tests - Flutter "
                    "examples run through the flutter_test harness, so wrap "
                    "the demonstration in at least one test()/testWidgets() "
                    "block (see the scaffolded example)")
            return self._fail(res.stdout or res.stderr)
        return self._ok()

    # ---- blueprints --------------------------------------------------------

    def wire_blueprint_dep(self, blueprint_dir: str, dep_id: str,
                           dep_dir: str) -> None:
        """Add a composed widget as a pub path dependency.

        Writes `<dep_module>: {path: cg/<dep_id>}` into the marked
        cartograph-composed block - the layout the blueprint validator
        sandbox populates. The dep's package name comes from its own
        pubspec.yaml. Idempotent.
        """
        dep_module = self._pubspec_name(dep_dir)
        if not dep_module:
            return
        entries = self._composed_entries(blueprint_dir)
        entries[dep_module] = f"cg/{os.path.basename(dep_dir.rstrip(os.sep))}"
        self._write_composed_block(blueprint_dir, entries)

    def unwire_blueprint_dep(self, blueprint_dir: str, dep_id: str) -> None:
        entries = self._composed_entries(blueprint_dir)
        target = f"cg/{dep_id}"
        entries = {name: p for name, p in entries.items() if p != target}
        self._write_composed_block(blueprint_dir, entries)

    # ---- cleanup -----------------------------------------------------------

    def cleanup(self, path: str) -> None:
        for d in ("lib", "coverage", "build", ".dart_tool"):
            full = os.path.join(path, d)
            if os.path.isdir(full):
                _shutil.rmtree(full, ignore_errors=True)
        lock = os.path.join(path, "pubspec.lock")
        if os.path.isfile(lock):
            try:
                os.remove(lock)
            except OSError:
                pass
        self._cleanup_artifact_dirs(path)

    # ---- private -----------------------------------------------------------

    def _materialize_lib(self, root: str) -> None:
        """Regenerate lib/ as an exact copy of src/.

        Flutter's coverage collector only reports libraries under lib/, and
        package:<module>/... imports resolve there - so the validated code
        is the src/ sources via this copy. Removed again by cleanup().
        """
        src = os.path.join(root, "src")
        lib = os.path.join(root, "lib")
        if not os.path.isdir(src):
            return
        _shutil.rmtree(lib, ignore_errors=True)
        _shutil.copytree(src, lib)

    def _materialize_all_libs(self, path: str) -> None:
        """Materialize lib/ for the widget and any sandboxed composed deps."""
        self._materialize_lib(path)
        for pubspec in _glob.glob(os.path.join(path, "cg", "*",
                                               "pubspec.yaml")):
            self._materialize_lib(os.path.dirname(pubspec))

    @staticmethod
    def _pubspec_name(root: str) -> str | None:
        pubspec = os.path.join(root, "pubspec.yaml")
        if not os.path.isfile(pubspec):
            return None
        with open(pubspec, encoding="utf-8") as f:
            m = _PUBSPEC_NAME_RE.search(f.read())
        return m.group(1) if m else None

    @staticmethod
    def _replace_block(path: str, start: str, end: str,
                       lines: list[str]) -> None:
        """Replace the pubspec lines between two marker comments."""
        pubspec = os.path.join(path, "pubspec.yaml")
        if not os.path.isfile(pubspec):
            raise RuntimeError(
                "pubspec.yaml not found at the widget root - run "
                "'cartograph create' to scaffold it")
        with open(pubspec, encoding="utf-8") as f:
            content = f.read()
        pattern = re.compile(
            r"([ \t]*)" + re.escape(start) + r".*?" + re.escape(end),
            re.DOTALL)
        if not pattern.search(content):
            raise RuntimeError(
                f"pubspec.yaml is missing the '{start.strip('# -')}' marker "
                "block - restore both marker comments inside dependencies: "
                "(see a freshly scaffolded pubspec.yaml)")

        def _sub(m):
            indent = m.group(1)
            body = "".join(f"{indent}{line}\n" for line in lines)
            return f"{indent}{start}\n{body}{indent}{end}"

        content = pattern.sub(_sub, content, count=1)
        with open(pubspec, "w", newline="\n", encoding="utf-8") as f:
            f.write(content)

    def _write_deps_block(self, path: str, dependencies: list) -> None:
        """Regenerate the cartograph-deps block from widget.json deps."""
        lines = []
        for dep in dependencies or []:
            bare = _dep_bare_name(dep)
            if not bare:
                continue
            constraint = str(dep)[len(bare):].strip() if isinstance(dep, str) else ""
            lines.append(f'{bare}: "{constraint}"' if constraint
                         else f"{bare}: any")
        self._replace_block(path, _DEPS_START, _DEPS_END, lines)

    def _composed_entries(self, blueprint_dir: str) -> dict:
        """Parse {package_name: path} pairs out of the composed block."""
        pubspec = os.path.join(blueprint_dir, "pubspec.yaml")
        if not os.path.isfile(pubspec):
            return {}
        with open(pubspec, encoding="utf-8") as f:
            content = f.read()
        m = re.search(re.escape(_COMPOSED_START) + r"(.*?)"
                      + re.escape(_COMPOSED_END), content, re.DOTALL)
        if not m:
            return {}
        entries = {}
        block = m.group(1)
        for name, dep_path in re.findall(
                r"^\s*([A-Za-z0-9_]+):\s*\{\s*path:\s*([^}\s]+)\s*\}",
                block, re.MULTILINE):
            entries[name] = dep_path
        return entries

    def _write_composed_block(self, blueprint_dir: str, entries: dict) -> None:
        lines = [f"{name}: {{ path: {dep_path} }}"
                 for name, dep_path in sorted(entries.items())]
        self._replace_block(blueprint_dir, _COMPOSED_START, _COMPOSED_END,
                            lines)
