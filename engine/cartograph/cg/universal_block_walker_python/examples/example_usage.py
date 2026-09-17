"""
Example: using block_walker to analyze SystemVerilog and C code.

Demonstrates all four core operations with realistic inputs.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.block_walker import (
    find_close, extract_blocks, split_at_depth, strip_comments,
    HDL_STYLE, C_STYLE,
)

# --- 1. find_close: find matching parenthesis in nested expression ---
expr = "module foo(a, max(1, 5), b)"
start = expr.index("(") + 1  # just after first (
end = find_close(expr, start, "(", ")")
print(f"1. Params: {expr[start:end-1]}")
# Output: a, max(1, 5), b

# --- 2. extract_blocks: find always_ff blocks in SystemVerilog ---
sv_code = """
always_ff @(posedge clk) begin
    if (!rst_n) begin
        q <= '0;
    end else begin
        q <= d_in;
    end
end

always_comb begin
    out = a & b;
end
"""

ff_blocks = extract_blocks(sv_code, r'\balways_ff\b', "begin", "end",
                           syntax=HDL_STYLE)
print(f"2. Found {len(ff_blocks)} always_ff block(s) at line {ff_blocks[0].line}")
print(f"   Body contains 'q <= d_in': {'q <= d_in' in ff_blocks[0].body}")

# --- 3. split_at_depth: split parameters respecting nested brackets ---
params = "pos=[0,0,0], size=max(10, 20), color=[1,0,0]"
parts = split_at_depth(params, delimiter=",")
print(f"3. Split params: {parts}")
# Output: ['pos=[0,0,0]', 'size=max(10, 20)', 'color=[1,0,0]']

# --- 4. strip_comments: clean code for analysis ---
c_code = """int x = 1; // inline comment
/* block
   comment */
char *s = "not // a comment";
"""
clean = strip_comments(c_code, syntax=C_STYLE)
print(f"4. Lines preserved: {clean.count(chr(10)) == c_code.count(chr(10))}")
print(f"   Comments gone: {'inline comment' not in clean}")
print(f"   Strings kept: {'not // a comment' in clean}")
