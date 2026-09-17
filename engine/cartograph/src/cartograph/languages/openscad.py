"""OpenSCAD language engine - parametric 3D modeling widgets."""

import glob as _glob
import logging
import os
import re
import shutil
import tempfile

from .base import LanguageEngine

# Import block_walker widget - bundled at cg/universal_block_walker_python/.
# Dogfooded for OpenSCAD's parameter splitting, comment stripping, and module
# block extraction so we don't maintain a parallel implementation here.
# Package-qualified import (same pattern as cli.py's agent_cli import): a bare
# `from src...` after a sys.path insert collides with any other top-level
# `src` namespace package (sloppy third-party wheels, src-layout cwds) and
# silently knocks this engine out of the registry.
from cg.universal_block_walker_python.src.block_walker import (  # noqa: E402
    split_at_depth as _split_at_depth,
    strip_comments as _strip_comments_raw,
    extract_blocks as _extract_blocks_raw,
    CommentSyntax as _CommentSyntax,
)

log = logging.getLogger("cartograph")

# OpenSCAD comment/string syntax for the block_walker widget
_SCAD_SYNTAX = _CommentSyntax(
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_delims=('"',),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STL_EMPTY_BYTES = 84  # 80-byte header + 4-byte triangle count = minimum binary STL


def _resolve_openscad_binary() -> str | None:
    """Find the openscad executable.

    Resolution order:
      1. `paths.openscad` from config.toml - the canonical user-facing
         override, shared with every other engine.
      2. OPENSCAD_BINARY env var - legacy, kept for CI/test setups.
      3. PATH lookup (covers every platform that has it wired up).
      4. Known Windows install locations - the OpenSCAD installer
         doesn't add itself to PATH by default.
    """
    # Config override (canonical). Only accept it if it points at a
    # real file - a broken value shouldn't hide a working PATH/fallback.
    try:
        from ..config import get_path_override
        override = get_path_override("openscad")
    except Exception:
        override = None
    if override and os.path.isfile(override):
        return override

    # PATH lookup (works on all platforms when openscad is on PATH)
    for name in ("openscad", "openscad.exe", "openscad.cmd"):
        found = shutil.which(name)
        if found:
            return found

    # Windows fallback: probe standard install directories
    if os.name == "nt":
        candidates = [
            r"C:\Program Files\OpenSCAD\openscad.exe",
            r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
        ]
        env_override = os.environ.get("OPENSCAD_BINARY")
        if env_override:
            candidates.insert(0, env_override)
        for path in candidates:
            if os.path.isfile(path):
                return path

    # Unix env override (mainly for nonstandard installs / CI)
    env_override = os.environ.get("OPENSCAD_BINARY")
    if env_override and os.path.isfile(env_override):
        return env_override

    return None


def _split_params(params_str: str) -> list[str]:
    """Split a module parameter list by commas, respecting nested brackets.
    e.g. 'pos=[0,0,0], size=[10,10,10]' -> ['pos=[0,0,0]', 'size=[10,10,10]']"""
    return _split_at_depth(params_str, delimiter=",", syntax=_SCAD_SYNTAX)

def _stl_has_geometry(path: str) -> bool:
    """Return True if the STL file contains at least one triangle."""
    try:
        return os.path.getsize(path) > _STL_EMPTY_BYTES
    except OSError:
        return False


# Top-level statements that aren't allowed in src/ files.
# Any line at depth 0 that begins with `<identifier>(` is a call or
# control-flow statement - both belong inside a module, not at file root.
# The match deliberately catches user-defined modules too (e.g. `stud(...)`),
# not just OpenSCAD builtins, so a published widget can't ship a "preview"
# instantiation that auto-renders on every consumer's import.
_TOP_LEVEL_STATEMENT_RE = re.compile(
    r'^[ \t]*([A-Za-z_]\w*)\s*\(',
    re.MULTILINE,
)
# Identifiers that legitimately start a top-level construct (declarations,
# imports). Everything else followed by `(` at depth 0 is an instantiation.
_TOP_LEVEL_DECLARATIONS = frozenset({
    "module", "function", "include", "use",
})

# Coordinate frame declaration. Every src/*.scad must declare at least one
# frame so consumers know how the part is oriented before they instantiate it.
# Three systems are supported and scaffolded; the author keeps whichever
# match the part's geometry (a gear may want cylindrical, a sphere spherical,
# a chassis cartesian - some parts benefit from declaring two).
_COORD_HEADER_RE = re.compile(
    r'//\s*COORDINATES\s*\(\s*(cartesian|cylindrical|spherical)\s*\)\s*:',
    re.IGNORECASE,
)
# Per-system required fields. A block passes only when every token below
# appears (case-insensitive substring) in its body and no [TODO] remains.
# Tokens match the scaffold labels exactly so authors who follow the
# template are guided into a complete frame.
_COORD_REQUIRED = {
    "cartesian": ("origin:", "+x:", "+y:", "+z:"),
    "cylindrical": ("axis:", "origin:", "theta"),
    "spherical": ("origin:", "theta", "phi"),
}


def _check_coordinate_frame(content: str, rel: str) -> list[str]:
    """Block src/*.scad files that lack a complete coordinate frame block.

    A block passes when: (a) it has a recognized system label, (b) its body
    has no [TODO] markers, AND (c) every required field for that system is
    present (cartesian: Origin/+X/+Y/+Z; cylindrical: Axis/Origin/theta;
    spherical: Origin/theta/phi). At least one such block must exist.
    Right-hand rule is assumed across all three systems.
    """
    lines = content.splitlines()
    headers = []
    for i, line in enumerate(lines):
        m = _COORD_HEADER_RE.search(line)
        if m:
            headers.append((i, m.group(1).lower()))

    if not headers:
        return [
            f"{rel}: missing coordinate frame - declare at least one of "
            f"`// COORDINATES (cartesian):`, `// COORDINATES (cylindrical):`, "
            f"or `// COORDINATES (spherical):` near the top of the file. "
            f"Right-hand rule assumed."
        ]

    incomplete_systems = []
    for start, system in headers:
        body = []
        for j in range(start + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped.startswith("//"):
                break
            if _COORD_HEADER_RE.search(stripped):
                break
            body.append(stripped)
        if any("[TODO" in line for line in body):
            incomplete_systems.append((system, "still contains [TODO]"))
            continue
        body_lower = "\n".join(body).lower()
        required = _COORD_REQUIRED[system]
        missing = [tok for tok in required if tok not in body_lower]
        if missing:
            incomplete_systems.append(
                (system, f"missing fields: {', '.join(missing)}")
            )
            continue
        return []  # complete, filled block found

    detail = "; ".join(f"{s} ({why})" for s, why in incomplete_systems)
    return [
        f"{rel}: no complete COORDINATES block - {detail}. "
        f"Cartesian requires Origin/+X/+Y/+Z; cylindrical requires "
        f"Axis/Origin/theta; spherical requires Origin/theta/phi. "
        f"Delete blocks you don't need; at least one must be fully filled in."
    ]

_ECHO_RE = re.compile(r'\becho\s*\(')
_RESOLUTION_RE = re.compile(r'(?:^|;)\s*\$f[nas]\s*=')
_INCLUDE_RE = re.compile(r'^\s*include\s*<', re.MULTILINE)


def _check_top_level_geometry(content: str, rel: str) -> list[str]:
    """Block any non-declaration statement at the top level of a src/ file.

    src/ widgets must define modules and constants only - calling a module
    (builtin or user-defined) at file root means it auto-renders on import.
    Catches both ``cube([...])`` (builtin) and ``stud(...)`` (user module).
    """
    blocks = []
    depth = 0  # brace depth - 0 means top level
    lines = content.splitlines()
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        depth_before = depth
        depth += stripped.count("{") - stripped.count("}")
        depth = max(depth, 0)
        if depth_before != 0:
            continue
        m = _TOP_LEVEL_STATEMENT_RE.match(line)
        if not m:
            continue
        ident = m.group(1)
        if ident in _TOP_LEVEL_DECLARATIONS:
            continue
        blocks.append(
            f"Top-level statement in src/ at line {line_no}: {stripped!r} - "
            f"src/ must only define modules and constants; "
            f"move calls/instantiations into a module body"
        )
    return blocks


def _strip_line_comments(content: str) -> str:
    """Remove comments from content, preserving line structure."""
    return _strip_comments_raw(content, syntax=_SCAD_SYNTAX)


def _extract_module_signatures(content: str) -> list[tuple[str, str, int, int, int]]:
    """Return all module signatures with bracket-aware param extraction.
    Handles function-call defaults like x = max(1, 5) correctly.
    Returns list of (module_name, params_str, line_no, params_start_pos, params_end_pos)."""
    clean = _strip_line_comments(content)
    blocks = _extract_blocks_raw(clean, r'\bmodule\s+\w+', "(", ")",
                                  syntax=_SCAD_SYNTAX)
    results = []
    for block in blocks:
        # Extract module name from the matched entry text
        name_match = re.search(r'\bmodule\s+(\w+)', block.name)
        if not name_match:
            continue
        module_name = name_match.group(1)
        # Line number from the original (unstripped) content
        line_no = block.line
        results.append((module_name, block.body.strip(), line_no,
                         block.body_start, block.body_end))
    return results


def _check_params_have_defaults(content: str, rel: str) -> list[str]:
    """Block public module parameters that have no default value.
    Private modules (name starting with _) are exempt - they are internal
    helpers always called with positional arguments."""
    blocks = []
    for module_name, params_str, line_no, _, _ in _extract_module_signatures(content):
        if module_name.startswith("_"):
            continue
        if not params_str:
            continue
        for param in _split_params(params_str):
            if "=" not in param:
                blocks.append(
                    f"{rel}:{line_no}: parameter '{param}' in module '{module_name}' "
                    f"has no default - all parameters must have defaults"
                )
    return blocks


def _check_param_unit_comments(content: str, rel: str) -> list[str]:
    """Warn when module parameters lack inline unit comments (// mm, // degrees, etc.)."""
    warnings = []
    lines = content.splitlines()
    for module_name, params_str, _, params_start, params_end in _extract_module_signatures(content):
        if module_name.startswith("_"):
            continue
        if not params_str:
            continue
        sig_start_line = content[:params_start].count("\n")
        sig_end_line = content[:params_end].count("\n")
        sig_lines = lines[sig_start_line : sig_end_line + 1]
        for param in _split_params(params_str):
            param_name = param.split("=")[0].strip()
            if not param_name:
                continue
            for i, sig_line in enumerate(sig_lines):
                if re.search(rf'\b{re.escape(param_name)}\s*[=,)]', sig_line):
                    if "//" not in sig_line:
                        line_no = sig_start_line + i + 1
                        warnings.append(
                            f"{rel}:{line_no}: parameter '{param_name}' in module '{module_name}' "
                            f"has no unit comment - add // mm, // degrees, etc."
                        )
                    break
    return warnings


def _parse_openscad_year(version_str: str) -> int | None:
    """Extract the release year from an openscad --version string.
    e.g. 'OpenSCAD version 2021.01.31' -> 2021. Returns None if unparseable."""
    m = re.search(r'\b(20\d{2})\b', version_str)
    return int(m.group(1)) if m else None


def _bosl2_installed() -> bool:
    """Check common OpenSCAD library paths for BOSL2, including OPENSCADPATH env var."""
    import platform
    home = os.path.expanduser("~")
    system = platform.system()
    if system == "Linux":
        candidates = [
            os.path.join(home, ".local", "share", "OpenSCAD", "libraries", "BOSL2"),
            "/usr/share/openscad/libraries/BOSL2",
        ]
    elif system == "Darwin":
        candidates = [
            os.path.join(home, "Documents", "OpenSCAD", "libraries", "BOSL2"),
            os.path.join(home, "Library", "Application Support", "OpenSCAD", "libraries", "BOSL2"),
        ]
    else:  # Windows
        candidates = [
            os.path.join(home, "Documents", "OpenSCAD", "libraries", "BOSL2"),
            os.path.join(os.environ.get("APPDATA", ""), "OpenSCAD", "libraries", "BOSL2"),
        ]
    # Also check OPENSCADPATH - users with non-standard installs set this
    for entry in os.environ.get("OPENSCADPATH", "").split(os.pathsep):
        if entry:
            candidates.append(os.path.join(entry, "BOSL2"))
    return any(os.path.isdir(p) for p in candidates)


def _bosl2_detail() -> str:
    import platform
    home = os.path.expanduser("~")
    system = platform.system()
    if system == "Linux":
        path = os.path.join(home, ".local", "share", "OpenSCAD", "libraries", "BOSL2")
    elif system == "Darwin":
        path = os.path.join(home, "Documents", "OpenSCAD", "libraries", "BOSL2")
    else:
        path = os.path.join(home, "Documents", "OpenSCAD", "libraries", "BOSL2")
    if _bosl2_installed():
        return "found"
    return f"not found - clone github.com/revarbat/BOSL2 into {path}"


# ---------------------------------------------------------------------------
# Scaffold templates
# ---------------------------------------------------------------------------

_SCAD_SRC = """\
// {name}
// Parametric module - all dimensions in millimeters.
//
// Parameters:
//   width  (mm) : overall width, default 20
//   height (mm) : overall height, default 10
//   depth  (mm) : overall depth, default 5

// COORDINATES (cartesian):
// Origin: [TODO: e.g. center of part]
// +X: [TODO: e.g. forward]
// +Y: [TODO: e.g. right]
// +Z: [TODO: e.g. up]
// Right-hand rule.

// COORDINATES (cylindrical):
// Axis: [TODO: e.g. +Z]
// Origin: [TODO: e.g. base of part on axis]
// theta=0: [TODO: e.g. +X direction]
// +theta: [TODO: e.g. counterclockwise looking down the axis]
// Right-hand rule.

// COORDINATES (spherical):
// Origin: [TODO: e.g. geometric center]
// theta (azimuth) zero: [TODO: e.g. +X]
// phi (polar) zero: [TODO: e.g. +Z]
// Right-hand rule.
//
// Delete the coordinate blocks above that don't apply. Keep at least one
// filled in. Multiple systems are allowed when the part is genuinely dual.

module {module}(
    width  = 20,   // mm - overall width
    height = 10,   // mm - overall height
    depth  = 5     // mm - overall depth
) {{
    // [TODO] Replace with your geometry
    cube([width, height, depth], center = true);
}}
"""

_SCAD_TEST = """\
// Tests for {module}
// Cartograph validates by rendering this file to a non-empty STL.
// Use assert() to enforce geometry contracts - a failing assert causes a
// non-zero exit and fails validation with a descriptive message.
//
// NOTE: if your src module uses include<> for a library (e.g. BOSL2), change
// the line below from use<> to include<> so the library constants propagate.
use <../src/{module}.scad>

// Test: default parameters render without error
{module}();

// Test: custom dimensions
{module}(width = 40, height = 20, depth = 10);

// Test: minimum viable dimensions
{module}(width = 1, height = 1, depth = 1);

// Test: assert geometry contracts (OpenSCAD 2021+)
// Use assert() to validate parameter relationships and computed values.
// Example: assert that width must be positive
module test_contracts() {{
    w = 20;
    h = 10;
    assert(w > 0, "width must be positive");
    assert(h > 0, "height must be positive");
    assert(w >= h, "width should be >= height for this shape");
    {module}(width = w, height = h);
}}
test_contracts();
"""

_PY_SIDECAR_SRC = '''\
"""Calc helpers for {name}.

Optional Python sidecar for parameter math (printability, gear ratios,
threading, etc.). Stdlib only - sidecars cannot declare their own dependencies.
Delete this directory if you do not need it. Validated at 60% coverage.
"""


def {module}_defaults():
    """Return suggested default parameters for {module}.

    Replace this with real calc logic - printability checks, ratio derivations,
    whatever the .scad source can't compute on its own.
    """
    return {{"width": 20.0, "height": 10.0, "depth": 5.0}}
'''

_PY_SIDECAR_TEST = '''\
from {module} import {module}_defaults


def test_defaults_have_positive_dimensions():
    params = {module}_defaults()
    for key in ("width", "height", "depth"):
        assert params[key] > 0, f"{{key}} must be positive"


def test_defaults_returns_dict():
    assert isinstance({module}_defaults(), dict)
'''

_SCAD_EXAMPLE = """\
// Example usage of {name}
// Open in OpenSCAD and enable View > Customizer to adjust parameters interactively.
//
// NOTE: if your src module uses include<> for a library (e.g. BOSL2), change
// the line below from use<> to include<> so the library constants propagate.
use <../src/{module}.scad>

/* [Parameters] */
// [TODO] Replace with your module's actual parameters and sensible ranges.
// Slider syntax: value; // [min:step:max]
// Dropdown syntax: "option"; // [option1, option2, option3]
width  = 20;  // [1:1:100]
height = 10;  // [1:1:100]
depth  = 5;   // [1:1:50]

/* [Hidden] */
$fn = 32;  // preview resolution - increase for final render

{module}(width = width, height = height, depth = depth);
"""


class OpenSCADEngine(LanguageEngine):
    name = "openscad"
    validation_version = 1
    file_ext = "scad"
    aliases = ["scad"]

    # No native scanner - contamination is simple enough for pure Python
    scanner_runner = None

    # OpenSCAD: no package manager. Dependencies declared in widget.json
    # are treated like heavy ML deps - must be pre-installed by the user.

    def _binary(self) -> str | None:
        """Cached resolver for the openscad executable path."""
        if not hasattr(self, "_openscad_bin_cache"):
            self._openscad_bin_cache = _resolve_openscad_binary()
        return self._openscad_bin_cache

    def check_available(self) -> tuple[bool, str]:
        """Require openscad binary AND version >= 2021 (needed for assert())."""
        if not self._binary():
            hint = "Install OpenSCAD 2021.01+ at openscad.org"
            if os.name == "nt":
                hint += (
                    " (Windows: the installer does not add openscad to PATH; either"
                    " add C:\\Program Files\\OpenSCAD to PATH or set OPENSCAD_BINARY)"
                )
            return False, f"openscad not found - {hint}"
        ver = self.runtime_version() or ""
        year = _parse_openscad_year(ver)
        if year is not None and year < 2021:
            return False, f"OpenSCAD 2021.01+ required for assert() - found {ver.strip()}"
        return True, ""

    def check_optional(self) -> list[tuple[str, bool, str]]:
        """Verify BOSL2 is installed AND that OpenSCAD can actually resolve it.
        Directory existence is necessary but not sufficient - the library path
        must be in OpenSCAD's search path for use<BOSL2/...> to resolve."""
        if not _bosl2_installed():
            return [("BOSL2", False, _bosl2_detail())]
        # Render a minimal file that imports BOSL2 to confirm it's truly usable
        binary = self._binary()
        if not binary:
            return [("BOSL2", False, "openscad not found - cannot verify BOSL2")]
        src = "use <BOSL2/std.scad>\nsphere(r=1, $fn=4);\n"
        with tempfile.NamedTemporaryFile(suffix=".scad", mode="w", delete=False) as f:
            f.write(src)
            scad_path = f.name
        tmp_stl = scad_path.replace(".scad", ".stl")
        try:
            res = self._run(
                [binary, "-o", tmp_stl, scad_path],
                cwd=tempfile.gettempdir(),
                timeout=15,
            )
            if res and res.returncode == 0 and _stl_has_geometry(tmp_stl):
                return [("BOSL2", True, "found and usable")]
            return [(
                "BOSL2", False,
                f"directory found but OpenSCAD cannot resolve BOSL2/std.scad - "
                f"check your library path. To install: {_bosl2_detail().replace('not found - ', '')}",
            )]
        except Exception:
            return [("BOSL2", False, "directory found but render test failed")]
        finally:
            for p in [scad_path, tmp_stl]:
                if os.path.exists(p):
                    os.unlink(p)

    def scaffold(self, target_dir, module_name, display_name, **kwargs):
        with open(os.path.join(target_dir, "src", f"{module_name}.scad"), "w", encoding="utf-8") as f:
            f.write(_SCAD_SRC.format(module=module_name, name=display_name))
        with open(os.path.join(target_dir, "tests", f"test_{module_name}.scad"), "w", encoding="utf-8") as f:
            f.write(_SCAD_TEST.format(module=module_name, name=display_name))
        with open(os.path.join(target_dir, "examples", "example_usage.scad"), "w", encoding="utf-8") as f:
            f.write(_SCAD_EXAMPLE.format(module=module_name, name=display_name))
        # Optional Python sidecar - calc helpers that mirror the .scad module.
        # Always scaffolded so users can opt in by editing; delete the dir to opt out.
        py_dir = os.path.join(target_dir, "python")
        os.makedirs(py_dir, exist_ok=True)
        with open(os.path.join(py_dir, f"{module_name}.py"), "w", encoding="utf-8") as f:
            f.write(_PY_SIDECAR_SRC.format(module=module_name, name=display_name))
        with open(os.path.join(py_dir, f"test_{module_name}.py"), "w", encoding="utf-8") as f:
            f.write(_PY_SIDECAR_TEST.format(module=module_name, name=display_name))

    def example_filename(self, path: str = "") -> str:
        return "example_usage.scad"

    def sidecar(self) -> tuple[str, str, int]:
        """OpenSCAD widgets may carry a stdlib-Python sidecar at python/.

        Useful for printability calcs, threading math, gear ratios - anything
        the OpenSCAD source can't compute on its own. Validated at a relaxed
        60% coverage; the goal is verified math, not exhaustive testing.
        """
        return ("python", "python", 60)

    def runtime_version(self) -> str | None:
        binary = self._binary()
        if not binary:
            return None
        try:
            res = self._run([binary, "--version"], cwd=tempfile.gettempdir(), timeout=10)
            if res and res.returncode == 0:
                out = (res.stdout or res.stderr or "").strip()
                return out or None
        except FileNotFoundError:
            pass
        return None

    def install_deps(self, path: str, dependencies: list) -> None:
        """OpenSCAD has no package manager. Warn if deps declared."""
        if dependencies:
            log.warning(
                "OpenSCAD widget declares dependencies %s - these must be manually "
                "installed into your OpenSCAD library path (openscad.org/libraries). "
                "Cartograph cannot install them automatically.",
                dependencies,
            )

    def run_tests(self, path: str) -> dict:
        """Render each test_*.scad to a temp STL. Passes if exit 0 and mesh is non-empty."""
        test_files = _glob.glob(os.path.join(path, "tests", "test_*.scad"))
        if not test_files:
            return self._fail("No test files found in tests/ - add at least one test_*.scad")

        binary = self._binary()
        if not binary:
            return self._fail("openscad not found - install OpenSCAD 2021.01+ or set OPENSCAD_BINARY")

        errors = []
        for test_file in sorted(test_files):
            tmp = tempfile.NamedTemporaryFile(suffix=".stl", delete=False)
            tmp.close()
            try:
                res = self._run(
                    [binary, "-o", tmp.name, test_file],
                    cwd=path,
                    timeout=60,
                )
                if res.returncode != 0:
                    errors.append(
                        f"{os.path.basename(test_file)}: {(res.stderr or res.stdout or '').strip()}"
                    )
                elif not _stl_has_geometry(tmp.name):
                    errors.append(
                        f"{os.path.basename(test_file)}: rendered successfully but produced an empty mesh"
                    )
            except FileNotFoundError:
                return self._fail("openscad not found - install OpenSCAD (openscad.org)")
            finally:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)

        if errors:
            return self._fail("\n".join(errors))
        return self._ok()

    def run_example(self, path: str) -> dict:
        """Render examples/example_usage.scad to a temp STL. Mesh must be non-empty."""
        ep = os.path.join(path, "examples", self.example_filename())
        if not os.path.exists(ep):
            return self._fail("examples/example_usage.scad not found")

        binary = self._binary()
        if not binary:
            return self._fail("openscad not found - install OpenSCAD 2021.01+ or set OPENSCAD_BINARY")

        tmp = tempfile.NamedTemporaryFile(suffix=".stl", delete=False)
        tmp.close()
        try:
            res = self._run([binary, "-o", tmp.name, ep], cwd=path, timeout=60)
            if res.returncode != 0:
                return self._fail((res.stderr or res.stdout or "").strip())
            if not _stl_has_geometry(tmp.name):
                return self._fail("example rendered successfully but produced an empty mesh")
            return self._ok()
        except FileNotFoundError:
            return self._fail("openscad not found - install OpenSCAD (openscad.org)")
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def scan_contamination(self, path: str, tech_stack: dict) -> dict:
        """
        OpenSCAD contamination checks:
          blocks : absolute paths, credentials, top-level geometry/control-flow in src/,
                   module parameters without defaults, include<> in src/, echo() in src/,
                   global resolution variables ($fn/$fa/$fs) in src/
          warnings: hardcoded URLs, unlisted external libraries, parameters without
                    unit comments
        """
        blocks = []
        warnings = []

        _ABS_INCLUDE_RE = re.compile(
            r'(?:include|use)\s*<([^>]*)>',
        )
        _CREDENTIAL_RE = re.compile(
            r'(?:api_key|password|secret|token)\s*=\s*"[^"]{6,}"',
            re.IGNORECASE,
        )
        _URL_RE = re.compile(
            r'https?://(?!localhost|127\.0\.0\.1|example\.com|.*\.test/)[^\s"\'<>]+',
            re.IGNORECASE,
        )

        declared_deps = {d.split(">=")[0].split("==")[0].strip().lower()
                         for d in tech_stack.get("dependencies", [])}

        src_files = _glob.glob(os.path.join(path, "src", "*.scad"))
        test_files = _glob.glob(os.path.join(path, "tests", "*.scad"))
        example_files = _glob.glob(os.path.join(path, "examples", "*.scad"))

        all_files = (
            [(f, True, False) for f in src_files]
            + [(f, False, False) for f in test_files]
            + [(f, False, True) for f in example_files]
        )

        for filepath, is_src, is_example in all_files:
            rel = os.path.relpath(filepath, path)
            try:
                content = open(filepath, encoding="utf-8", errors="replace").read()
            except Exception:
                continue

            if is_src:
                blocks.extend(_check_top_level_geometry(content, rel))
                blocks.extend(_check_params_have_defaults(content, rel))
                blocks.extend(_check_coordinate_frame(content, rel))
                warnings.extend(_check_param_unit_comments(content, rel))
                # include <> of LOCAL files executes the whole file - use <> only.
                # External library includes (e.g. BOSL2/std.scad) are allowed when
                # the library is declared in dependencies; their constants are
                # intentionally global and consumers of BOSL2 widgets expect them.
                for m in _INCLUDE_RE.finditer(content):
                    line_no = content[: m.start()].count("\n") + 1
                    # Extract the path inside the angle brackets
                    rest = content[m.end():]
                    close = rest.find(">")
                    inc_path = rest[:close].strip() if close != -1 else ""
                    is_local = inc_path.startswith(".") or inc_path.startswith("/") or (
                        len(inc_path) > 1 and inc_path[1] == ":"
                    )
                    lib_name = inc_path.split("/")[0].lower()
                    is_declared_dep = lib_name in declared_deps
                    if is_local or not is_declared_dep:
                        blocks.append(
                            f"{rel}:{line_no}: found include<{inc_path}> in src/ - "
                            + (
                                "use use<> instead (include executes the full file on import)"
                                if is_local or not inc_path
                                else f"declare {lib_name} in widget.json dependencies to use include<> with it"
                            )
                        )
                # $fn/$fa/$fs override the consumer's resolution settings globally
                for line_no, line in enumerate(content.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("//"):
                        continue
                    # Strip inline comment before pattern matching
                    code_part = line.split("//")[0]
                    if _RESOLUTION_RE.search(code_part):
                        blocks.append(
                            f"{rel}:{line_no}: global resolution variable ($fn/$fa/$fs) in src/ - "
                            f"expose as a module parameter instead"
                        )
                    if _ECHO_RE.search(code_part):
                        blocks.append(
                            f"{rel}:{line_no}: echo() in src/ - remove debug output before checkin"
                        )

            for m in _ABS_INCLUDE_RE.finditer(content):
                inc = m.group(1).strip()
                line_no = content[: m.start()].count("\n") + 1
                # Absolute Unix path or Windows drive letter
                if inc.startswith("/") or (len(inc) > 1 and inc[1] == ":"):
                    blocks.append(
                        f"Absolute path in include/use in {rel}:{line_no}: <{inc}>"
                    )
                # External library not declared in dependencies
                elif not inc.startswith(".") and not inc.startswith("../") and not inc.startswith("src/"):
                    lib_name = inc.split("/")[0].lower()
                    if lib_name not in declared_deps:
                        msg = f"Unlisted library in {rel}:{line_no}: <{inc}> - add to widget.json dependencies"
                        if is_src:
                            blocks.append(msg)
                        else:
                            warnings.append(msg)

            for line_no, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("//"):
                    continue
                if _CREDENTIAL_RE.search(line):
                    blocks.append(f"Possible credential in {rel}:{line_no}: {stripped}")
                if is_src and _URL_RE.search(line):
                    warnings.append(f"Hardcoded URL in {rel}:{line_no}: {stripped}")

            # Customizer annotations make modeling examples interactive in the OpenSCAD GUI
            if is_example and "/* [" not in content:
                warnings.append(
                    f"{rel}: no Customizer annotations found - add /* [Section] */ blocks "
                    f"with parameter ranges so users can tweak values in View > Customizer"
                )

        return {"blocks": blocks, "warnings": warnings}

    def cleanup(self, path: str) -> None:
        # OpenSCAD leaves no artifacts to clean up
        pass
