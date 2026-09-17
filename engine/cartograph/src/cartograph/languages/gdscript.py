"""GDScript (Godot 4) language engine.

Validates reusable Godot 4 game-logic scripts headlessly - no editor, no
rendering. The toolchain is the single `godot` binary (the standard build
supports `--headless`), which keeps this engine zero-extra-dependency.

There is no line-coverage tool for GDScript, so - like OpenSCAD / SystemVerilog
/ SPICE - this engine enforces no coverage floor. The floor is instead that the
test *asserts behavior*: a test script runs headless, prints ASSERT_PASS for
each verified property, and exits non-zero on failure. A team that wants a
coverage bar can add it as a custom rule; it is not part of the validated
floor.

Layout
------
    widget_root/
      project.godot        minimal Godot project (so res:// resolves)
      src/<name>.gd        the reusable script (class_name <Name>)
      tests/test_<name>.gd extends SceneTree, loads src via res://, asserts,
                           prints ASSERT_PASS / ASSERT_FAIL, quit(code)
      examples/example_usage.gd  extends SceneTree, demos the API, quit(0)

The headline validation is Godot-4-only syntax: deprecated Godot 3 patterns
(@-less annotations, yield, 3-arg connect, Pool*Array, renamed classes,
.instance(), ...) are hard-blocked by the native scanner, because LLMs
constantly emit Godot 3 code into Godot 4 projects.
"""

import glob as _glob
import json as _json
import os
import tempfile

from .base import LanguageEngine

# Headers for native-scanner finding kinds. block kinds first, then warnings.
_BLOCK_HEADERS = {
    "godot3_syntax": "Deprecated Godot 3 syntax in src/ - this engine is "
                     "Godot 4 only; update each pattern:",
    "print": "print*/print_debug found in src/ - widgets are libraries, "
             "remove console output:",
    "abs_node_path": "Absolute node paths found in src/ - widgets must not "
                     "assume the consumer's scene tree:",
    "sleep": "OS.delay_* found in src/ - widgets must not block the caller:",
    "abs_path": "Absolute paths found - widgets must be portable:",
    "credential": "Possible credentials found - remove before checkin:",
    "hardcoded_ip": "Hardcoded IPs found - make these configurable:",
}
_WARN_HEADERS = {
    "untyped_var": "Untyped variables in src/ - GDScript widgets should be "
                   "statically typed (var x: T = ... or var x := ...):",
    "hardcoded_value": "Hardcoded numeric tunables in src/ - prefer @export "
                       "or named const a consumer can set:",
    "env_var": "OS.get_environment in src/ - verify it's not "
               "project-specific:",
    "hardcoded_url": "Hardcoded URLs found - consider making these "
                     "configurable:",
    "todo": "TODO/FIXME markers found - resolve before checkin:",
    # abs_path/ip/credential reuse the block header text when they land in
    # tests/examples as warnings (see _group).
    "abs_path": "Absolute paths found - widgets must be portable:",
    "credential": "Possible credentials found - remove before checkin:",
    "hardcoded_ip": "Hardcoded IPs found - make these configurable:",
}

_PROJECT_GODOT = """\
; Engine configuration for the {name} widget. Minimal by design - the widget
; is portable logic, not a game; this only exists so res:// paths resolve.
config_version=5

[application]

config/name="{slug}"
config/features=PackedStringArray("4.5")
"""

_GD_SRC = """\
class_name {klass}
## {name}
##
## [TODO] Replace with the widget's real API. Static typing is the house style:
## annotate every parameter, return, and member.
extends RefCounted


## Returns the input value unchanged.
static func process(value: String) -> String:
	return value
"""

_GD_TEST = """\
# Headless test for {name}. Loads the widget via res:// exactly as a consumer
# would, asserts behavior with fake data, and exits non-zero on failure.
extends SceneTree


func _init() -> void:
	# IMPORTANT: SceneTree.quit() does NOT return from _init() - it only
	# requests quit at end of frame, so execution keeps running. Always put
	# `return` right after quit(), or the next quit() will override your code.
	var widget = load("res://src/{slug}.gd")
	# [TODO] Replace with real assertions. Print ASSERT_PASS per verified
	# property; the validator requires at least one and fails on ASSERT_FAIL.
	if widget.process("hello") != "hello":
		print("ASSERT_FAIL process")
		quit(1)
		return
	print("ASSERT_PASS process")
	quit(0)
"""

_GD_EXAMPLE = """\
# Example usage of {name}. Must run headless and exit cleanly with fake data -
# no real input, no rendering, no external scenes.
extends SceneTree


func _init() -> void:
	var widget = load("res://src/{slug}.gd")
	# [TODO] Replace with a realistic call using fake data
	print(widget.process("hello"))
	# quit() does not return; keep it last (or `return` after it).
	quit(0)
"""


def _gd_class_name(module_name: str) -> str:
    """PascalCase class_name from a widget slug (inventory -> Inventory,
    a-star-grid -> AStarGrid)."""
    parts = module_name.replace("-", "_").split("_")
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


class GDScriptEngine(LanguageEngine):
    name = "gdscript"
    validation_version = 1
    file_ext = "gd"
    aliases = ["gd", "godot"]
    toolchain = {"godot": "Install Godot 4 - godotengine.org (the standard "
                          "binary supports --headless; the engine never opens "
                          "the editor)"}
    supported = True

    manifest_patterns = ["project.godot"]

    # ---- toolchain ---------------------------------------------------------

    def runtime_version(self):
        try:
            res = self._run(["godot", "--headless", "--version"], cwd=".",
                            timeout=30, env=self._godot_env())
            if res.returncode == 0:
                # "4.5.stable.official.876b29033"
                line = (res.stdout or res.stderr or "").strip().splitlines()
                if line:
                    return f"godot {line[-1].strip()}"
        except Exception:
            pass
        return None

    # ---- scaffold ----------------------------------------------------------

    def scaffold(self, target_dir, module_name, display_name, **_):
        slug = module_name.replace("-", "_")
        klass = _gd_class_name(module_name)

        def _w(path, content):
            # GDScript is tab-indented and canonically LF; write LF so the
            # scaffold is stable on Windows text-mode writes.
            with open(path, "w", newline="\n", encoding="utf-8") as f:
                f.write(content)

        _w(os.path.join(target_dir, "project.godot"),
           _PROJECT_GODOT.format(name=display_name, slug=slug))
        for d in ("src", "tests", "examples"):
            os.makedirs(os.path.join(target_dir, d), exist_ok=True)
        _w(os.path.join(target_dir, "src", f"{slug}.gd"),
           _GD_SRC.format(klass=klass, name=display_name))
        _w(os.path.join(target_dir, "tests", f"test_{slug}.gd"),
           _GD_TEST.format(slug=slug, name=display_name))
        _w(os.path.join(target_dir, "examples", "example_usage.gd"),
           _GD_EXAMPLE.format(slug=slug, name=display_name))

    def find_test_files(self, path):
        return _glob.glob(os.path.join(path, "tests", "**", "*.gd"),
                          recursive=True)

    def example_filename(self, path=""):
        return "example_usage.gd"

    def required_files(self, path):
        if os.path.isfile(os.path.join(path, "project.godot")):
            return []
        return [("project.godot",
                 "A project.godot file is required at the widget root - run "
                 "'cartograph create' to scaffold it")]

    # ---- validation --------------------------------------------------------

    def validate_widget(self, path, dependencies):
        errors = []

        src_files = _glob.glob(os.path.join(path, "src", "**", "*.gd"),
                               recursive=True)
        if not src_files:
            errors.append("src/ contains no .gd files - add at least one "
                          "source file")

        # Parse-check every script with --check-only (parses, does NOT run).
        all_gd = []
        for sub in ("src", "tests", "examples"):
            all_gd.extend(_glob.glob(os.path.join(path, sub, "**", "*.gd"),
                                     recursive=True))
        for gd in all_gd:
            try:
                res = self._run(
                    ["godot", "--headless", "--path", path,
                     "--check-only", "--script", gd],
                    cwd=path, timeout=120, env=self._godot_env())
            except FileNotFoundError:
                errors.append("godot not found - install Godot 4 "
                              "(godotengine.org).")
                break
            if res.returncode != 0:
                out = (res.stderr or res.stdout or "").strip()
                rel = os.path.relpath(gd, path)
                errors.append(f"GDScript parse error in {rel}:\n{out[:1500]}")

        # Native scanner blocks (Godot 3 syntax, print, abs node paths, ...).
        blocks, _warnings = self._scan(path, src_files)
        errors.extend(blocks)

        errors.extend(self._check_dep_pinning(dependencies))

        if errors:
            return self._fail("\n".join(errors))
        return self._ok()

    def scan_contamination(self, path, widget):
        all_files = []
        for sub in ("src", "tests", "examples"):
            all_files.extend(_glob.glob(
                os.path.join(path, sub, "**", "*.gd"), recursive=True))
        blocks, warnings = self._scan(path, all_files)
        return {"blocks": blocks, "warnings": warnings}

    # ---- dependencies ------------------------------------------------------

    def install_deps(self, path, dependencies):
        # GDScript widgets are pure engine builtins + their own scripts - no
        # package manager, nothing to fetch. Declared deps (if any) are
        # validated for pinning in validate_widget.
        return

    # ---- tests + example ---------------------------------------------------

    def run_tests(self, path):
        tests = self.find_test_files(path)
        if not tests:
            return self._fail("No test files found in tests/")
        for tf in sorted(tests):
            rel = os.path.relpath(tf, path)
            try:
                res = self._run(
                    ["godot", "--headless", "--path", path, "--script", tf],
                    cwd=path, timeout=180, env=self._godot_env())
            except FileNotFoundError:
                return self._fail("godot not found - install Godot 4 "
                                  "(godotengine.org).")
            out = (res.stdout or "") + (res.stderr or "")
            # Godot can exit 0 even when logic is wrong, so require an explicit
            # assertion contract: at least one ASSERT_PASS, no ASSERT_FAIL, and
            # a clean exit. Mirrors SPICE's parse-the-output discipline.
            if res.returncode != 0:
                return self._fail(f"{rel} exited {res.returncode}:\n"
                                  f"{out.strip()[:2000]}")
            if "ASSERT_FAIL" in out:
                fails = [ln for ln in out.splitlines() if "ASSERT_FAIL" in ln]
                return self._fail(f"{rel} reported failures:\n"
                                  + "\n".join(fails)[:2000])
            if "ASSERT_PASS" not in out:
                return self._fail(
                    f"{rel} produced no ASSERT_PASS - a test must assert at "
                    f"least one property (print(\"ASSERT_PASS ...\")) so a "
                    f"clean exit actually means the behavior was checked.")
        return self._ok()

    def run_example(self, path):
        ex = os.path.join(path, "examples", self.example_filename())
        if not os.path.isfile(ex):
            return self._fail("examples/example_usage.gd not found")
        try:
            res = self._run(
                ["godot", "--headless", "--path", path, "--script", ex],
                cwd=path, timeout=180, env=self._godot_env())
        except FileNotFoundError:
            return self._fail("godot not found - install Godot 4 "
                              "(godotengine.org).")
        if res.returncode != 0:
            return self._fail((res.stderr or res.stdout or "").strip()[:2000])
        return self._ok()

    def run_blueprint_example(self, sandbox, example_file):
        """Run a blueprint example headless from the sandbox root. The
        blueprint's scripts preload composed widgets via
        `res://cg/<dep-id>/src/...` (the dep widgets are copied under cg/ in the
        sandbox, and --path makes res:// the sandbox root). The base default
        invokes python on the file, which is wrong for GDScript."""
        ex = os.path.join(sandbox, "examples", example_file)
        if not os.path.isfile(ex):
            ex = os.path.join(sandbox, "examples", self.example_filename())
        try:
            res = self._run(
                ["godot", "--headless", "--path", sandbox, "--script", ex],
                cwd=sandbox, timeout=180, env=self._godot_env())
        except FileNotFoundError:
            return {"passed": False,
                    "error": "godot not found - install Godot 4 "
                             "(godotengine.org)."}
        if res.returncode != 0:
            return {"passed": False,
                    "error": (res.stderr or res.stdout or "").strip()[:2000]}
        return {"passed": True}

    # ---- private -----------------------------------------------------------

    def _scan(self, path, files):
        """Run the native GDScript scanner and return (blocks, warnings) as
        grouped, human-readable strings. Runs godot directly (not via the base
        helper) because the scanner needs --path to boot and `--` so the target
        files arrive on OS.get_cmdline_user_args()."""
        if not files:
            return [], []
        scanner = os.path.join(os.path.dirname(__file__), "scanners",
                               "gdscript_scanner.gd")
        if not os.path.isfile(scanner):
            return [], []
        cmd = (["godot", "--headless", "--path", path, "--script", scanner,
                "--"] + files)
        try:
            res = self._run(cmd, cwd=path, timeout=180, env=self._godot_env())
        except FileNotFoundError:
            return ["godot not found - install Godot 4 (godotengine.org)."], []
        findings = self._parse_scanner_json(res.stdout)
        return self._group(findings, path)

    @staticmethod
    def _parse_scanner_json(stdout):
        text = (stdout or "").strip()
        if not text:
            return []
        # The JSON array is the last non-empty line; godot prints engine banner
        # lines before it.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("["):
                try:
                    return _json.loads(line)
                except _json.JSONDecodeError:
                    return []
        return []

    @staticmethod
    def _group(findings, cwd):
        block_grouped = {}
        warn_grouped = {}
        for f in findings:
            kind = f.get("kind", "unknown")
            severity = f.get("severity", "warning")
            rel = os.path.relpath(f.get("file", ""), cwd)
            loc = f"  {rel}:{f.get('line', 0)}: {f.get('detail', '')}"
            if severity == "block":
                block_grouped.setdefault(kind, []).append(loc)
            else:
                warn_grouped.setdefault(kind, []).append(loc)
        blocks = []
        for kind, locs in block_grouped.items():
            header = _BLOCK_HEADERS.get(kind, f"{kind} found in src/:")
            blocks.append(header + "\n" + "\n".join(locs))
        warnings = []
        for kind, locs in warn_grouped.items():
            header = _WARN_HEADERS.get(kind, f"{kind} found:")
            warnings.append(header + "\n" + "\n".join(locs))
        return blocks, warnings

    def _godot_env(self):
        """Godot writes its config/cache under the platform data dir; point it
        at a writable temp dir so validation works in sandboxes where $HOME is
        read-only."""
        env = os.environ.copy()
        if not env.get("XDG_DATA_HOME"):
            data = os.path.join(tempfile.gettempdir(), "cartograph-godot-data")
            os.makedirs(data, exist_ok=True)
            env["XDG_DATA_HOME"] = data
        return env
