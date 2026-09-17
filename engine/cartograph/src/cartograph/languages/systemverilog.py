"""SystemVerilog language engine — RTL hardware design widgets.

Toolchain: Icarus Verilog (iverilog + vvp)
  Install: sudo apt/dnf install iverilog  OR  brew install icarus-verilog

Validation pipeline:
  1. Lint: iverilog -g2012 -Wall -tnull src/*.sv  (syntax + semantic check)
  2. Tests: for each tests/test_*.sv:
       iverilog -g2012 -o /tmp/sim tests/test_X.sv src/*.sv && vvp /tmp/sim
       Testbenches use $fatal() to signal failures — non-zero exit = test failed.
  3. Example: compile examples/example_usage.sv + src/*.sv, run with vvp.

Contamination rules specific to RTL:
  Blocks (hard failures — not synthesizable or vendor-locked):
    - Vendor primitives (LUT6, BUFG, RAMB36, ALTPLL, etc.) unless declared in dependencies
    - `initial` blocks in src/ (simulation-only; belongs in testbench)
    - `#delay` in src/ (simulation timing; belongs in testbench)
    - `timescale` directive in src/ (belongs in testbench)
    - `always @*` / `always @(posedge ...)` in src/ — use always_comb/always_ff
    - $display/$monitor/$write in src/ (console pollution — belongs in testbench)
    - Hardcoded file paths in $readmemh/$readmemb (parameterize the path)
    - Blocking `=` in always_ff / non-blocking `<=` in always_comb

  Warnings:
    - Hardcoded numeric constants (nudge to parameterize)
    - Hardcoded URLs, absolute paths, credentials (from base class)
"""

import glob as _glob
import logging
import os
import re
import tempfile

from .base import LanguageEngine

# Import block_walker widget - bundled at cg/universal_block_walker_python/.
# Package-qualified import (same pattern as cli.py's agent_cli import): a bare
# `from src...` after a sys.path insert collides with any other top-level
# `src` namespace package (sloppy third-party wheels, src-layout cwds) and
# silently knocks this engine out of the registry.
from cg.universal_block_walker_python.src.block_walker import (  # noqa: E402
    extract_blocks as _extract_blocks,
    strip_comments as _strip_comments_raw,
    HDL_STYLE as _HDL_STYLE,
)

log = logging.getLogger("cartograph")


def _strip_comments(content: str) -> str:
    """Strip // and /* */ comments from HDL source, preserving line structure."""
    return _strip_comments_raw(content, syntax=_HDL_STYLE)


# ---------------------------------------------------------------------------
# Contamination regexes
# ---------------------------------------------------------------------------

# Vendor-specific primitives — not synthesizable in generic RTL
_VENDOR_PRIMITIVES = re.compile(
    r'\b(LUT[1-6]|LUT[1-6]_L|BUFG|BUFGCE|BUFGMUX|IBUF|OBUF|IOBUF|'
    r'RAMB36|RAMB18|RAMB36E[12]|RAMB18E[12]|DSP48E[12]|'
    r'ALTPLL|ALTDDIO|ALTLVDS|ALTSYNCRAM|altera_pll|'
    r'sky130_fd_sc_hd__[a-z_]+|GTECH_[A-Z]+)\s*(?:#\s*\(|\s+\w)',
    re.IGNORECASE,
)

# `initial` blocks in src/ — simulation only, not synthesizable
_INITIAL_RE = re.compile(r'^\s*initial\b', re.MULTILINE)

# `#delay` — simulation timing, not synthesizable
_DELAY_RE = re.compile(r'^\s*#\s*\d', re.MULTILINE)

# `timescale` directive
_TIMESCALE_RE = re.compile(r'^\s*`timescale\b', re.MULTILINE)

# Hardcoded file paths in $readmemh/$readmemb — parameterize the path
_HARDCODED_READMEM_RE = re.compile(
    r'\$(readmemh|readmemb)\s*\(\s*"[^"]+"\s*,',
    re.IGNORECASE,
)

# Debug/display system tasks — block in src/ (console pollution)
_DISPLAY_RE = re.compile(
    r'\$(display|monitor|strobe|write|info|warning|error)\s*\(',
    re.IGNORECASE,
)

# Verilog-2001 always blocks — use always_comb/always_ff in SystemVerilog
_ALWAYS_LEGACY_RE = re.compile(
    r'^\s*always\s+@\s*\(',
    re.MULTILINE,
)

# Assignment type checker — blocking vs non-blocking in wrong context
_BLOCKING_ASSIGN_RE = re.compile(
    r'(?<![<>=!])=(?!=)',  # = not preceded by < > = ! and not followed by =
)
_NONBLOCKING_ASSIGN_RE = re.compile(r'<=')


def _find_always_blocks(content: str) -> list:
    """Find always_ff and always_comb blocks using block_walker widget."""
    ff = _extract_blocks(content, r'\balways_ff\b', "begin", "end", syntax=_HDL_STYLE)
    comb = _extract_blocks(content, r'\balways_comb\b', "begin", "end", syntax=_HDL_STYLE)
    return ff + comb


def _check_assignments(content: str, blocks: list, rel: str) -> list[str]:
    """Check for blocking assignments in always_ff and non-blocking in always_comb.

    Block bodies are already comment-stripped at the file level, so this scans
    line-by-line within each block without re-stripping. Strings in HDL source
    rarely contain `=` and the file-level strip preserves them, but the
    comparison-operator guard below filters the common cases.
    """
    errors = []
    for block in blocks:
        kind = block.name  # "always_ff" or "always_comb"
        for line_offset, line in enumerate(block.body.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            abs_line = block.line + line_offset
            if "always_ff" in kind:
                # Skip for-loop headers — loop variable = is not a signal assignment
                if re.match(r'\bfor\s*\(', stripped):
                    continue
                if _BLOCKING_ASSIGN_RE.search(stripped) and '<=' not in stripped:
                    if '==' not in stripped and '!=' not in stripped and '>=' not in stripped:
                        errors.append(
                            f"Blocking assignment `=` in always_ff at {rel}:{abs_line} — "
                            f"use `<=` (non-blocking) for sequential logic"
                        )
            elif "always_comb" in kind:
                if _NONBLOCKING_ASSIGN_RE.search(stripped):
                    if ';' in stripped:
                        errors.append(
                            f"Non-blocking assignment `<=` in always_comb at {rel}:{abs_line} — "
                            f"use `=` (blocking) for combinational logic"
                        )
    return errors


def _enum_body_ranges(content: str) -> list[tuple[int, int]]:
    """Return (start, end) byte ranges of typedef enum bodies via block_walker.

    Replaces the prior regex-blanking approach (`typedef enum [^{]*{[^}]*}`)
    which broke on nested braces. block_walker handles depth correctly.
    """
    enums = _extract_blocks(
        content, r'\btypedef\s+enum\b', "{", "}", syntax=_HDL_STYLE,
    )
    return [(e.body_start, e.body_end) for e in enums]


def _blank_ranges(content: str, ranges: list[tuple[int, int]]) -> str:
    """Replace byte ranges with spaces, preserving newlines so line numbers align."""
    if not ranges:
        return content
    out = list(content)
    for start, end in ranges:
        for i in range(start, min(end, len(out))):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


# Hardcoded numeric constants — values that should likely be parameters
# Matches: bare decimal/hex literals > 1 byte assigned to a wire/reg/logic,
# but NOT inside parameter/localparam declarations or comments.
_HARDCODED_NUMERIC_RE = re.compile(
    r"^\s*(?!.*(?:parameter|localparam|//|/\*))"   # not a param decl or comment
    r".*(?<!['\w])\d+\s*'[dhbo]\w{3,}",            # e.g. 32'd115200, 8'hFF
    re.MULTILINE | re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Scaffold templates
# ---------------------------------------------------------------------------

_SV_SRC = '''\
// {module_name}.sv — {display_name}
//
// Every synthesizable parameter must have a default and a unit comment.
// No `initial` blocks, `#delays`, `timescale`, or $display in src/.
// Use always_comb / always_ff only — `always @(...)` is blocked.
// Vendor primitives allowed only if declared in dependencies.

module {module_name} #(
    // -- Parameters (variation points) ----------------------------------------
    parameter int DATA_WIDTH = 8   // bits — width of the data bus
)(
    input  logic                  clk,    // system clock
    input  logic                  rst_n,  // active-low synchronous reset
    input  logic [DATA_WIDTH-1:0] d_in,
    output logic [DATA_WIDTH-1:0] d_out
);

    // TODO: implement module logic here
    always_ff @(posedge clk) begin
        if (!rst_n)
            d_out <= '0;
        else
            d_out <= d_in;
    end

endmodule
'''

_SV_TEST = '''\
// test_{module_name}.sv — testbench for {display_name}
//
// Use `$fatal(1, "message")` to signal test failures.
// Use `$finish` at the end so vvp exits cleanly.
// `timescale` belongs here, not in src/.
`timescale 1ns/1ps

module test_{module_name};

    // -- DUT instantiation ----------------------------------------------------
    localparam int DATA_WIDTH = 8;

    logic                  clk   = 0;
    logic                  rst_n = 0;
    logic [DATA_WIDTH-1:0] d_in  = 0;
    logic [DATA_WIDTH-1:0] d_out;

    {module_name} #(
        .DATA_WIDTH(DATA_WIDTH)
    ) dut (
        .clk   (clk),
        .rst_n (rst_n),
        .d_in  (d_in),
        .d_out (d_out)
    );

    // -- Clock generation -----------------------------------------------------
    always #5 clk = ~clk;   // 100 MHz

    // -- Stimulus and assertions ----------------------------------------------
    initial begin
        // Hold reset
        @(posedge clk); #1;
        rst_n = 0;
        @(posedge clk); #1;

        // Release reset, drive input
        rst_n = 1;
        d_in  = 8\'hAB;
        @(posedge clk); #1;

        // Check output registered the input
        if (d_out !== 8\'hAB)
            $fatal(1, "FAIL: expected d_out=0xAB, got 0x%02X", d_out);

        $display("PASS: {display_name} basic register test");
        $finish;
    end

endmodule
'''

_SV_EXAMPLE = '''\
// example_usage.sv — {display_name}
//
// Demonstrates instantiation of {module_name} with non-default parameters.
// Must compile cleanly and exit 0 via $finish.
`timescale 1ns/1ps

module example_usage;

    localparam int DATA_WIDTH = 16;   // 16-bit variant

    logic                   clk   = 0;
    logic                   rst_n = 0;
    logic [DATA_WIDTH-1:0]  d_in  = 0;
    logic [DATA_WIDTH-1:0]  d_out;

    {module_name} #(
        .DATA_WIDTH(DATA_WIDTH)
    ) dut (
        .clk   (clk),
        .rst_n (rst_n),
        .d_in  (d_in),
        .d_out (d_out)
    );

    always #5 clk = ~clk;

    initial begin
        @(posedge clk); #1; rst_n = 0;
        @(posedge clk); #1; rst_n = 1;
        d_in = 16\'hBEEF;
        @(posedge clk); #1;
        $display("d_out = 0x%04X (expected 0xBEEF)", d_out);
        $finish;
    end

endmodule
'''


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SystemVerilogEngine(LanguageEngine):
    name = "systemverilog"
    aliases = ["sv", "verilog"]
    file_ext = "sv"
    validation_version = 1

    toolchain = {
        "iverilog": "Install Icarus Verilog — sudo dnf/apt install iverilog or brew install icarus-verilog",
        "vvp":      "Install Icarus Verilog (vvp is included) — sudo dnf/apt install iverilog",
    }

    def runtime_version(self) -> str | None:
        if not self._binary_available("iverilog"):
            return None
        try:
            # Through self._run so paths.iverilog (if configured) wins.
            res = self._run(["iverilog", "-V"], cwd=os.getcwd(), timeout=10)
            output = (res.stderr or res.stdout or "").strip()
            for line in output.splitlines():
                if "Icarus Verilog" in line or "iverilog" in line.lower():
                    return line.strip()
            return output.splitlines()[0] if output else None
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def validate_widget(self, path: str, dependencies: list) -> dict:
        """Lint src/*.sv for syntax errors, then run dep pinning check."""
        errors = []
        warnings = []

        src_files = _glob.glob(os.path.join(path, "src", "*.sv"))
        if not src_files:
            return self._fail("No .sv files found in src/")

        # Lint: compile src only to null target — catches syntax + semantic errors
        # without needing a top-level testbench instantiation
        cmd = ["iverilog", "-g2012", "-Wall", "-tnull"] + src_files
        try:
            res = self._run(cmd, cwd=path, timeout=30)
            output = (res.stderr or res.stdout or "").strip()
            if res.returncode != 0:
                errors.append(f"Lint failed:\n{output}")
            elif output:
                # iverilog writes warnings to stderr even on success
                for line in output.splitlines():
                    if "warning:" in line.lower():
                        warnings.append(f"Lint warning: {line.strip()}")
        except FileNotFoundError:
            return self._fail("iverilog not found — install Icarus Verilog")

        errors.extend(self._check_dep_pinning(dependencies))

        if errors:
            result = self._fail("\n".join(errors))
        else:
            result = self._ok()
        if warnings:
            result["warnings"] = warnings
        return result

    def _collect_src_files(self, path: str) -> list[str]:
        """Blueprint-aware src collection: include the widget/blueprint's own
        src/*.sv plus every dep widget's src/*.sv under cg/. Widgets have no
        cg/ dir so the second glob is a harmless no-op for them."""
        src_files = _glob.glob(os.path.join(path, "src", "*.sv"))
        src_files += _glob.glob(os.path.join(path, "cg", "*", "src", "*.sv"))
        return src_files

    def run_tests(self, path: str) -> dict:
        """Compile and simulate each tests/test_*.sv with all src/*.sv files."""
        src_files = self._collect_src_files(path)
        test_files = _glob.glob(os.path.join(path, "tests", "test_*.sv"))

        if not src_files:
            return self._fail("No .sv files found in src/")
        if not test_files:
            return self._fail("No test_*.sv files found in tests/")

        errors = []
        for test_file in sorted(test_files):
            tb_name = os.path.basename(test_file)
            error = self._compile_and_run(src_files, test_file, path)
            if error:
                errors.append(f"{tb_name}: {error}")

        if errors:
            return self._fail("\n".join(errors))
        return self._ok()

    def run_example(self, path: str) -> dict:
        """Compile and simulate examples/example_usage.sv with src/*.sv."""
        ep = os.path.join(path, "examples", "example_usage.sv")
        if not os.path.exists(ep):
            return self._fail("examples/example_usage.sv not found")

        src_files = self._collect_src_files(path)
        error = self._compile_and_run(src_files, ep, path)
        if error:
            return self._fail(error)
        return self._ok()

    def _compile_and_run(self, src_files: list, tb_file: str, cwd: str) -> str | None:
        """Compile tb_file + src_files with iverilog, run with vvp.
        Returns an error string on failure, None on success."""
        tmp_fd, tmp_out = tempfile.mkstemp(prefix="cartograph_sv_")
        os.close(tmp_fd)
        try:
            compile_cmd = ["iverilog", "-g2012", "-Wall", "-o", tmp_out, tb_file] + src_files
            try:
                res = self._run(compile_cmd, cwd=cwd, timeout=30)
            except FileNotFoundError:
                return "iverilog not found — install Icarus Verilog"
            if res.returncode != 0:
                return (res.stderr or res.stdout or "compile failed").strip()

            try:
                res = self._run(["vvp", tmp_out], cwd=cwd, timeout=30)
            except FileNotFoundError:
                return "vvp not found — install Icarus Verilog (vvp is included)"
            if res.returncode != 0:
                output = (res.stderr or res.stdout or "simulation failed").strip()
                return output
            return None
        finally:
            if os.path.exists(tmp_out):
                os.unlink(tmp_out)

    # -------------------------------------------------------------------------
    # Contamination
    # -------------------------------------------------------------------------

    def scan_contamination(self, path: str, tech_stack: dict) -> dict:
        """
        RTL contamination checks.

        Blocks (hard failures):
          - Vendor primitives (unless declared in dependencies)
          - `initial` blocks in src/ (simulation-only)
          - `#delay` in src/ (simulation timing)
          - `timescale` in src/ (belongs in testbench)
          - `always @*` / `always @(posedge ...)` — use always_comb/always_ff
          - $display/$monitor/$write in src/ (console pollution)
          - Hardcoded file paths in $readmemh/$readmemb
          - Absolute paths, credentials (base class rules)

        Warnings:
          - Hardcoded numeric constants (nudge to parameterize)
          - Hardcoded URLs (base class rule)
        """
        blocks = []
        warnings = []

        src_files = _glob.glob(os.path.join(path, "src", "*.sv"))

        # Declared dependencies — vendor primitives are allowed if declared
        declared_deps = {d.split(">=")[0].split("==")[0].strip().lower()
                         for d in tech_stack.get("dependencies", [])}

        # Run base class checks (absolute paths, credentials, URLs, IPs)
        base = super().scan_contamination(path, tech_stack)
        blocks.extend(base["blocks"])
        warnings.extend(base["warnings"])

        # RTL-specific checks on src/ only
        for filepath in src_files:
            rel = os.path.relpath(filepath, path)
            try:
                raw_content = open(filepath, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            # Strip comments for contamination scanning — keeps line numbers intact
            # by preserving newlines. Comments can contain example code that would
            # false-positive on vendor primitives, etc.
            content = _strip_comments(raw_content)

            # Vendor primitives — allowed if the vendor library is declared
            # Map primitive families to vendor keywords that would appear in deps
            for m in _VENDOR_PRIMITIVES.finditer(content):
                prim_name = m.group(1).lower()
                if prim_name.startswith(("lut", "buf", "ibuf", "obuf", "iobuf", "ramb", "dsp48")):
                    vendor_keys = ("xilinx", "unisim", "unimacro")
                elif prim_name.startswith(("alt", "altera")):
                    vendor_keys = ("altera", "intel",)
                elif prim_name.startswith("sky130"):
                    vendor_keys = ("sky130", "skywater",)
                elif prim_name.startswith("gtech"):
                    vendor_keys = ("gtech", "synopsys",)
                else:
                    vendor_keys = ()
                is_declared = any(
                    vk in dep for dep in declared_deps for vk in vendor_keys
                )
                if not is_declared:
                    line_no = content[: m.start()].count("\n") + 1
                    blocks.append(
                        f"Vendor primitive in {rel}:{line_no}: '{m.group().strip()}' — "
                        f"declare the vendor library in dependencies or use generic RTL"
                    )

            # initial blocks
            for m in _INITIAL_RE.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                blocks.append(
                    f"`initial` block in {rel}:{line_no} — not synthesizable; "
                    f"move to testbench"
                )

            # #delay
            for m in _DELAY_RE.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                blocks.append(
                    f"`#delay` in {rel}:{line_no} — simulation-only construct; "
                    f"move to testbench"
                )

            # timescale directive
            for m in _TIMESCALE_RE.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                blocks.append(
                    f"`timescale in {rel}:{line_no} — belongs in testbench, not src/"
                )

            # Legacy always blocks — must use always_comb/always_ff
            for m in _ALWAYS_LEGACY_RE.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                blocks.append(
                    f"Verilog-2001 `always @(...)` in {rel}:{line_no} — "
                    f"use `always_comb` or `always_ff` (SystemVerilog)"
                )

            # Display/debug tasks — block in src/ (console pollution)
            for m in _DISPLAY_RE.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                blocks.append(
                    f"${m.group(1)} in {rel}:{line_no} — "
                    f"debug output in src/ pollutes consumer console; "
                    f"move to testbench"
                )

            # Hardcoded file paths in $readmemh/$readmemb
            for m in _HARDCODED_READMEM_RE.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                blocks.append(
                    f"Hardcoded file path in ${m.group(1)} at {rel}:{line_no} — "
                    f"use a parameter for the file path"
                )

            # Hardcoded numeric constants — warning to nudge parameterization.
            # Blank out typedef enum bodies first via block_walker (state
            # encodings aren't parameterizable). Newlines preserved so line
            # numbers stay accurate against the original file.
            content_no_enums = _blank_ranges(content, _enum_body_ranges(content))
            for m in _HARDCODED_NUMERIC_RE.finditer(content_no_enums):
                line_no = content_no_enums[: m.start()].count("\n") + 1
                blocks_text = m.group().strip()
                warnings.append(
                    f"Hardcoded numeric constant in {rel}:{line_no}: {blocks_text} — "
                    f"could this be a parameter?"
                )

            # Blocking/non-blocking assignment check
            always_blocks = _find_always_blocks(content)
            blocks.extend(_check_assignments(content, always_blocks, rel))

        return {"blocks": blocks, "warnings": warnings}

    # -------------------------------------------------------------------------
    # Scaffold
    # -------------------------------------------------------------------------

    def scaffold(self, target_dir: str, module_name: str, display_name: str, **kwargs) -> None:
        """Write starter src/, tests/, and examples/ files."""
        src_dir = os.path.join(target_dir, "src")
        tests_dir = os.path.join(target_dir, "tests")
        examples_dir = os.path.join(target_dir, "examples")
        for d in (src_dir, tests_dir, examples_dir):
            os.makedirs(d, exist_ok=True)

        def _write(path, content):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

        _write(
            os.path.join(src_dir, f"{module_name}.sv"),
            _SV_SRC.format(module_name=module_name, display_name=display_name),
        )
        _write(
            os.path.join(tests_dir, f"test_{module_name}.sv"),
            _SV_TEST.format(module_name=module_name, display_name=display_name),
        )
        _write(
            os.path.join(examples_dir, "example_usage.sv"),
            _SV_EXAMPLE.format(module_name=module_name, display_name=display_name),
        )

    # -------------------------------------------------------------------------
    # Misc overrides
    # -------------------------------------------------------------------------

    def find_test_files(self, path: str) -> list[str]:
        return _glob.glob(os.path.join(path, "tests", "test_*.sv"))

    def example_filename(self, path: str = "") -> str:
        return "example_usage.sv"

    def src_import_pattern(self) -> str | None:
        # Examples instantiate src/ modules — check they reference the widget dir
        return None  # compile-time check is sufficient; no import-style pattern
