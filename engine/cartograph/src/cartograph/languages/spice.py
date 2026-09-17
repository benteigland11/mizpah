"""SPICE language engine - parametric analog/mixed-signal circuit widgets.

A SPICE widget is a reusable circuit block: src/ holds a parameterized
``.subckt`` (an RC filter, an amplifier stage, a voltage reference), and the
testbench drives it with a stimulus, runs an analysis, and asserts measured
behavior (cutoff frequency, gain, settling time) within a tolerance band.

Validation is artifact-style, exactly like OpenSCAD's render: there is no line
coverage for a netlist, so "passing" means the simulation reaches convergence
AND every claimed quantity is measured and asserted. The coverage-floor
substitute is structural - a testbench with no ``.meas`` is untested and is
rejected.

Toolchain: a single ``ngspice`` binary in batch mode (``ngspice -b``), which
keeps this engine zero-dependency like the rest of Cartograph.

Layout
------
    widget_root/
      src/        the reusable block - one or more .subckt definitions,
                  no analysis cards, no .control blocks
      tests/      test_<module>.cir - includes src/, instantiates the block,
                  applies a stimulus, runs an analysis, and asserts with
                  `meas` + an if/echo ASSERT_PASS/ASSERT_FAIL sentinel
      examples/   example_usage.cir - a runnable demonstration netlist

The exit-code gotcha
--------------------
ngspice exits 0 even when a `.meas` cannot find its target and even on some
analysis errors. The engine therefore never trusts the return code - it
classifies stdout/stderr for error markers (Error:/aborted/fatal/failed!) and
for the testbench's own ASSERT_PASS / ASSERT_FAIL sentinels.
"""

import glob as _glob
import logging
import os
import re
import shutil
import tempfile

from .base import LanguageEngine

log = logging.getLogger("cartograph")


# --------------------------------------------------------------------------
# Binary resolution
# --------------------------------------------------------------------------

def _resolve_ngspice_binary() -> str | None:
    """Find the ngspice executable: config override, then PATH."""
    try:
        from ..config import get_path_override
        override = get_path_override("ngspice")
    except Exception:
        override = None
    if override and os.path.isfile(override):
        return override
    for name in ("ngspice", "ngspice.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


# --------------------------------------------------------------------------
# Output classification - the exit code is unreliable, so parse the text
# --------------------------------------------------------------------------

# Hard failure markers. ngspice prints these to stdout/stderr regardless of
# its (always-0) batch exit status. "failed!" is a `.meas` that could not
# locate its target; the rest are analysis/convergence failures.
_ERROR_MARKERS = re.compile(
    r"(?:^|\W)(?:Error\b|fatal\b|aborted\b|failed!|"
    r"could not be simulated|incomplete or empty netlist)",
    re.IGNORECASE | re.MULTILINE,
)

# A testbench signals each assertion with one of these sentinels via if/echo.
_ASSERT_PASS = re.compile(r"\bASSERT_PASS\b")
_ASSERT_FAIL = re.compile(r"\bASSERT_FAIL\b")

# Static checks on testbench source: it must measure something and assert it.
_MEAS_RE = re.compile(r"(?mi)^\s*\.?meas(?:ure)?\b")
_ASSERT_SENTINEL_RE = re.compile(r"ASSERT_(?:PASS|FAIL)")

# src/ must be a pure block: no analysis cards, no interactive control blocks.
_ANALYSIS_CARD_RE = re.compile(
    r"(?mi)^\s*\.(ac|dc|tran|op|noise|disto|pz|sens|tf|four|fft)\b"
)
_CONTROL_RE = re.compile(r"(?mi)^\s*\.control\b")
_SUBCKT_RE = re.compile(r"(?mi)^\s*\.subckt\b")

# Include/library cards (portability checks).
_INCLUDE_RE = re.compile(
    r"(?mi)^\s*\.(?:include|inc|lib)\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))"
)

_CREDENTIAL_RE = re.compile(
    r"(?:api_key|password|secret|token)\s*=\s*\S{6,}", re.IGNORECASE
)
_URL_RE = re.compile(
    r"https?://(?!localhost|127\.0\.0\.1|example\.com|[\w.-]+\.test\b)[^\s\"'<>]+",
    re.IGNORECASE,
)

# Two-terminal passives/sources whose value field should be a {param} in src/.
# Line shape: <RefDes> <node+> <node-> <value> ...  e.g.  R1 in out 1k
_PASSIVE_DEVICE_RE = re.compile(
    r"(?mi)^\s*([RLCVI])\w*\s+\S+\s+\S+\s+(\S+)"
)


def _strip_comment(line: str) -> str:
    """SPICE comments: a full-line '*' card, or an inline ';'. Return code only."""
    s = line.lstrip()
    if s.startswith("*"):
        return ""
    return line.split(";", 1)[0]


def _is_parameterized(value: str) -> bool:
    """A device value is portable if it's a {expr} brace expression or a
    symbolic name (param reference), not a bare numeric literal like 1k/4.7u."""
    value = value.strip()
    if value.startswith("{") or value.startswith("'"):
        return True
    # Bare number with optional SPICE engineering suffix (k, u, n, p, meg...).
    if re.fullmatch(r"[-+]?\d*\.?\d+(?:e[-+]?\d+)?[a-zA-Z]*", value):
        return False
    return True  # symbolic / anything else - assume a parameter reference


# --------------------------------------------------------------------------
# Scaffold templates - a working parametric RC low-pass filter
# --------------------------------------------------------------------------

_SPICE_SRC = """\
* {name}
* Parametric RC low-pass filter block.
*
* A reusable .subckt - no analysis cards, no .control blocks live here.
* Every component value is a parameter so a consumer can retune the cutoff
* without editing source:  fc = 1 / (2*pi*R*C).
*
* Ports:  in  - signal input
*         out - filtered output
* Params: r (ohms)   - series resistance, default 1k
*         c (farads) - shunt capacitance, default 159.155n  (fc = 1 kHz)

.subckt {module} in out params: r=1k c=159.155n
R1 in out {{r}}
C1 out 0 {{c}}
.ends {module}
"""

_SPICE_TEST = """\
* Testbench for {name}
* Cartograph validates by simulating this file with `ngspice -b` and checking
* that it converges, measures the claimed quantities, and asserts them within
* tolerance. At least one `meas` and one ASSERT_PASS/ASSERT_FAIL sentinel are
* required - a testbench that measures nothing is untested.

.include ../src/{module}.cir

* Stimulus: 1 V AC source into the block under test.
Vin in 0 AC 1
Xdut in out {module} r=1k c=159.155n

.control
ac dec 100 10 100k

* Measure the -3 dB cutoff frequency. Theory: fc = 1/(2*pi*1k*159.155n) = 1 kHz.
meas ac fc when vdb(out)=-3

* Assert the cutoff lands within +/-5% of 1 kHz. The if/echo sentinel is the
* pass/fail contract - the engine fails the test on any ASSERT_FAIL and
* requires at least one ASSERT_PASS.
if (fc < 950) | (fc > 1050)
  echo "ASSERT_FAIL cutoff $&fc Hz outside [950, 1050]"
else
  echo "ASSERT_PASS cutoff $&fc Hz within +/-5% of 1 kHz"
end

* Assert the passband (DC) gain is ~0 dB.
meas ac gain_lf find vdb(out) at=10
if (gain_lf < -0.5)
  echo "ASSERT_FAIL passband gain $&gain_lf dB too low"
else
  echo "ASSERT_PASS passband gain near 0 dB"
end
.endc
.end
"""

_SPICE_EXAMPLE = """\
* Example usage of {name}
* A runnable demonstration: instantiate the block, sweep it, and report the
* response. Must simulate to convergence with `ngspice -b` and exit cleanly -
* no external model files, no machine-specific paths.

.include ../src/{module}.cir

Vin in 0 AC 1
Xfilter in out {module} r=2k c=79.577n   ; retuned for fc = 1 kHz a different way

.control
ac dec 50 10 100k
meas ac fc when vdb(out)=-3
echo "Example {module}: -3 dB cutoff = $&fc Hz"
.endc
.end
"""


class SpiceEngine(LanguageEngine):
    name = "spice"
    validation_version = 1
    file_ext = "cir"
    aliases = ["ngspice"]
    toolchain = {
        "ngspice": "Install ngspice 40+ - ngspice.sourceforge.io "
                   "(dnf install ngspice / brew install ngspice / "
                   "choco install ngspice)"
    }
    supported = True

    # Contamination is netlist-line oriented - Python handles it; no native
    # scanner. (SPICE has no general-purpose toolchain to host one anyway.)
    scanner_runner = None

    # ---- toolchain ---------------------------------------------------------

    def _binary(self) -> str | None:
        if not hasattr(self, "_ngspice_bin_cache"):
            self._ngspice_bin_cache = _resolve_ngspice_binary()
        return self._ngspice_bin_cache

    def check_available(self) -> tuple[bool, str]:
        if not self._binary():
            return False, (
                "ngspice not found - install ngspice 40+ "
                "(ngspice.sourceforge.io) or set paths.ngspice in "
                "`cartograph config`"
            )
        return True, ""

    def runtime_version(self) -> str | None:
        binary = self._binary()
        if not binary:
            return None
        try:
            res = self._run([binary, "--version"], cwd=tempfile.gettempdir(),
                            timeout=10)
            if res and res.returncode == 0:
                out = (res.stdout or res.stderr or "").strip()
                # "ngspice compiled from ngspice revision 46" / "** ngspice-46"
                m = re.search(r"ngspice[- ](?:revision\s+)?(\d+)", out,
                              re.IGNORECASE)
                if m:
                    return f"ngspice {m.group(1)}"
                return out.splitlines()[0] if out else None
        except FileNotFoundError:
            pass
        return None

    # ---- scaffold ----------------------------------------------------------

    def scaffold(self, target_dir, module_name, display_name, **kwargs):
        for d in ("src", "tests", "examples"):
            os.makedirs(os.path.join(target_dir, d), exist_ok=True)
        with open(os.path.join(target_dir, "src", f"{module_name}.cir"),
                  "w", encoding="utf-8") as f:
            f.write(_SPICE_SRC.format(module=module_name, name=display_name))
        with open(os.path.join(target_dir, "tests",
                               f"test_{module_name}.cir"), "w", encoding="utf-8") as f:
            f.write(_SPICE_TEST.format(module=module_name, name=display_name))
        with open(os.path.join(target_dir, "examples", "example_usage.cir"),
                  "w", encoding="utf-8") as f:
            f.write(_SPICE_EXAMPLE.format(module=module_name,
                                          name=display_name))

    def find_test_files(self, path: str) -> list[str]:
        return _glob.glob(os.path.join(path, "tests", "test_*.cir"))

    def example_filename(self, path: str = "") -> str:
        return "example_usage.cir"

    def src_import_pattern(self) -> str | None:
        # Example must .include the src/ block.
        return r"\.include\s+\S*src"

    def install_deps(self, path: str, dependencies: list) -> None:
        """SPICE has no package manager. Model libraries (.lib/.include) are
        declared in widget.json and must be supplied by the consumer, same
        policy as OpenSCAD's BOSL2."""
        if dependencies:
            log.warning(
                "SPICE widget declares dependencies %s - device-model "
                "libraries must be installed manually and referenced by a "
                "declared .lib/.include. Cartograph cannot fetch them.",
                dependencies,
            )

    # ---- simulation runner -------------------------------------------------

    def _simulate(self, netlist: str, cwd: str, timeout: int = 60):
        """Run `ngspice -b -o <log> <netlist>` and return (ok, combined_output).

        ok is False when the output carries any hard-error marker. The exit
        code is deliberately ignored - ngspice returns 0 even on failure.

        The `-o <log>` flag mirrors ngspice's batch output to a file. On
        Windows the `.control` block's `echo` lines (our ASSERT_PASS/FAIL
        sentinels) do not reliably reach the captured stdout/stderr pipes,
        but they always land in the `-o` log. We union the log with the
        captured streams so assertion detection is platform-independent.
        """
        binary = self._binary()
        if not binary:
            return False, "ngspice not found - install ngspice 40+"
        fd, logpath = tempfile.mkstemp(prefix="cg_ngspice_", suffix=".log")
        os.close(fd)
        try:
            res = self._run([binary, "-b", "-o", logpath, netlist],
                            cwd=cwd, timeout=timeout)
        except FileNotFoundError:
            os.unlink(logpath)
            return False, "ngspice not found - install ngspice 40+"
        log_text = ""
        try:
            with open(logpath, encoding="utf-8", errors="replace") as f:
                log_text = f.read()
        except OSError:
            pass
        finally:
            try:
                os.unlink(logpath)
            except OSError:
                pass
        combined = "\n".join(
            part for part in (res.stdout, res.stderr, log_text) if part
        )
        if _ERROR_MARKERS.search(combined):
            return False, combined.strip()
        return True, combined.strip()

    def run_tests(self, path: str) -> dict:
        test_files = self.find_test_files(path)
        if not test_files:
            return self._fail(
                "No test files found in tests/ - add at least one "
                "test_*.cir testbench")
        if not self._binary():
            return self._fail(
                "ngspice not found - install ngspice 40+ "
                "(ngspice.sourceforge.io) or set paths.ngspice")

        errors = []
        for test_file in sorted(test_files):
            name = os.path.basename(test_file)
            try:
                source = open(test_file, encoding="utf-8",
                              errors="replace").read()
            except Exception as e:
                errors.append(f"{name}: could not read - {e}")
                continue

            # Coverage substitute: a testbench that measures nothing or
            # asserts nothing is untested.
            if not _MEAS_RE.search(source):
                errors.append(
                    f"{name}: no `meas` statement - the testbench must "
                    f"measure at least one quantity (cutoff, gain, settling "
                    f"time) so behavior is verified, not just convergence")
                continue
            if not _ASSERT_SENTINEL_RE.search(source):
                errors.append(
                    f"{name}: no ASSERT_PASS/ASSERT_FAIL sentinel - assert "
                    f"each measured quantity with an `if (...) echo "
                    f"\"ASSERT_FAIL ...\" else echo \"ASSERT_PASS ...\" end` "
                    f"block so the measurement has a pass/fail contract")
                continue

            ok, output = self._simulate(test_file, cwd=path)
            if not ok:
                errors.append(f"{name}: simulation error:\n{output[:1500]}")
                continue
            if _ASSERT_FAIL.search(output):
                fails = [ln.strip() for ln in output.splitlines()
                         if _ASSERT_FAIL.search(ln)]
                errors.append(f"{name}: assertion(s) failed:\n  "
                              + "\n  ".join(fails))
                continue
            if not _ASSERT_PASS.search(output):
                errors.append(
                    f"{name}: ran without error but produced no ASSERT_PASS "
                    f"- the assertion block never executed (check the "
                    f"analysis actually ran and the meas succeeded)")

        if errors:
            return self._fail("\n".join(errors))
        return self._ok()

    def run_example(self, path: str) -> dict:
        ep = os.path.join(path, "examples", self.example_filename())
        if not os.path.exists(ep):
            return self._fail("examples/example_usage.cir not found")
        if not self._binary():
            return self._fail(
                "ngspice not found - install ngspice 40+ or set paths.ngspice")
        ok, output = self._simulate(ep, cwd=path)
        if not ok:
            return self._fail(output[:1500])
        return self._ok()

    # ---- contamination + structure ----------------------------------------

    def scan_contamination(self, path: str, tech_stack: dict) -> dict:
        """
        SPICE contamination + structural checks.

        blocks (src/): missing .subckt, analysis cards or .control in src/,
            absolute/local-path includes, credentials.
        blocks (all): credentials, absolute-path includes.
        warnings: hardcoded component values in src/ (.subckt should be
            parameterized), undeclared external model libraries, hardcoded URLs.
        """
        blocks, warnings = [], []
        declared = {d.split(">=")[0].split("==")[0].strip().lower()
                    for d in tech_stack.get("dependencies", [])}

        src_files = _glob.glob(os.path.join(path, "src", "*.cir"))
        test_files = _glob.glob(os.path.join(path, "tests", "*.cir"))
        example_files = _glob.glob(os.path.join(path, "examples", "*.cir"))
        all_files = (
            [(f, True) for f in src_files]
            + [(f, False) for f in test_files]
            + [(f, False) for f in example_files]
        )

        for filepath, is_src in all_files:
            rel = os.path.relpath(filepath, path)
            try:
                content = open(filepath, encoding="utf-8",
                               errors="replace").read()
            except Exception:
                continue

            if is_src:
                if not _SUBCKT_RE.search(content):
                    blocks.append(
                        f"{rel}: no .subckt definition - a SPICE widget's "
                        f"src/ must define a reusable block, not a flat "
                        f"netlist")
                for m in _ANALYSIS_CARD_RE.finditer(content):
                    line_no = content[:m.start()].count("\n") + 1
                    blocks.append(
                        f"{rel}:{line_no}: analysis card '.{m.group(1)}' in "
                        f"src/ - analyses belong in the testbench, not the "
                        f"reusable block")
                for m in _CONTROL_RE.finditer(content):
                    line_no = content[:m.start()].count("\n") + 1
                    blocks.append(
                        f"{rel}:{line_no}: .control block in src/ - "
                        f"interactive control belongs in the testbench")
                warnings.extend(self._check_parameterized(content, rel))

            # Include/library portability (all files).
            for m in _INCLUDE_RE.finditer(content):
                inc = (m.group(1) or m.group(2) or m.group(3) or "").strip()
                line_no = content[:m.start()].count("\n") + 1
                is_abs = inc.startswith("/") or (len(inc) > 1
                                                 and inc[1] == ":")
                if is_abs:
                    blocks.append(
                        f"{rel}:{line_no}: absolute path in include/lib: "
                        f"{inc} - widgets must be portable")
                    continue
                # A relative include into the widget's own src/ is fine; an
                # external library must be a declared dependency.
                points_into_widget = (
                    inc.startswith(".") or inc.startswith("src")
                    or "/" not in inc and inc.endswith(".cir")
                )
                lib_name = os.path.basename(inc).split(".")[0].lower()
                if not points_into_widget and lib_name not in declared:
                    msg = (f"{rel}:{line_no}: undeclared model library "
                           f"'{inc}' - add it to widget.json dependencies")
                    (blocks if is_src else warnings).append(msg)

            # Credentials + URLs.
            for line_no, line in enumerate(content.splitlines(), 1):
                code = _strip_comment(line)
                if not code.strip():
                    continue
                if _CREDENTIAL_RE.search(code):
                    blocks.append(
                        f"Possible credential in {rel}:{line_no}: "
                        f"{line.strip()}")
                if is_src and _URL_RE.search(code):
                    warnings.append(
                        f"Hardcoded URL in {rel}:{line_no}: {line.strip()}")

        return {"blocks": blocks, "warnings": warnings}

    def _check_parameterized(self, content: str, rel: str) -> list[str]:
        """Warn when a passive/source inside src/ uses a hardcoded literal
        value instead of a {param} - the analog domain rule is that every
        component value is a tunable parameter."""
        warnings = []
        in_subckt = False
        for line_no, line in enumerate(content.splitlines(), 1):
            code = _strip_comment(line)
            stripped = code.strip()
            if not stripped:
                continue
            low = stripped.lower()
            if low.startswith(".subckt"):
                in_subckt = True
                continue
            if low.startswith(".ends"):
                in_subckt = False
                continue
            if not in_subckt:
                continue
            m = _PASSIVE_DEVICE_RE.match(code)
            if m and not _is_parameterized(m.group(2)):
                warnings.append(
                    f"{rel}:{line_no}: device '{m.group(0).split()[0]}' has a "
                    f"hardcoded value '{m.group(2)}' - expose it as a "
                    f"{{param}} so consumers can retune the block")
        return warnings

    def cleanup(self, path: str) -> None:
        # ngspice -b leaves no artifacts when no rawfile is requested.
        pass
