import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.block_walker import (
    find_close, extract_blocks, split_at_depth, strip_comments,
    Block, CommentSyntax, C_STYLE, HDL_STYLE, PYTHON_STYLE, SQL_STYLE,
)


# ---------------------------------------------------------------------------
# find_close
# ---------------------------------------------------------------------------

class TestFindClose:
    def test_simple_parens(self):
        text = "(a, b, c)"
        # start=1 is just after the opening (
        pos = find_close(text, 1, "(", ")")
        assert pos == 9  # just after the closing )
        assert text[1:pos-1] == "a, b, c"

    def test_nested_parens(self):
        text = "(a, (b, c), d)"
        pos = find_close(text, 1, "(", ")")
        assert pos == 14
        assert text[1:pos-1] == "a, (b, c), d"

    def test_deeply_nested(self):
        text = "(a, (b, (c, d)), e)"
        pos = find_close(text, 1, "(", ")")
        assert pos == 19

    def test_braces(self):
        text = "{ if (x) { y; } }"
        pos = find_close(text, 1, "{", "}")
        assert pos == 17

    def test_keyword_begin_end(self):
        text = "begin if (x) begin y; end end"
        pos = find_close(text, 6, "begin", "end")
        assert pos == len(text)  # one past final "end"

    def test_nested_keyword(self):
        text = "begin outer begin inner end still_outer end"
        pos = find_close(text, 6, "begin", "end")
        assert pos == len(text)

    def test_skips_string(self):
        text = '(a, ")", b)'
        pos = find_close(text, 1, "(", ")")
        assert pos == 11  # the ) inside the string is ignored

    def test_skips_line_comment(self):
        text = "(a, // )\nb)"
        pos = find_close(text, 1, "(", ")")
        assert pos == 11

    def test_skips_block_comment(self):
        text = "(a, /* ) */ b)"
        pos = find_close(text, 1, "(", ")")
        assert pos == 14

    def test_not_found(self):
        text = "(a, b"
        pos = find_close(text, 1, "(", ")")
        assert pos == -1

    def test_empty_block(self):
        text = "()"
        pos = find_close(text, 1, "(", ")")
        assert pos == 2

    def test_keyword_not_partial_match(self):
        # "endmodule" should not match "end"
        text = "begin endmodule; end"
        pos = find_close(text, 6, "begin", "end")
        assert pos == 20


# ---------------------------------------------------------------------------
# extract_blocks
# ---------------------------------------------------------------------------

class TestExtractBlocks:
    def test_c_function(self):
        text = "void foo() { int x = 1; }"
        blocks = extract_blocks(text, r'\bfoo\(\)', "{", "}")
        assert len(blocks) == 1
        assert blocks[0].name == "foo()"
        assert "int x = 1;" in blocks[0].body

    def test_always_ff_begin_end(self):
        text = """always_ff @(posedge clk) begin
    q <= d;
end"""
        blocks = extract_blocks(text, r'\balways_ff\b', "begin", "end",
                                syntax=HDL_STYLE)
        assert len(blocks) == 1
        assert "q <= d;" in blocks[0].body
        assert blocks[0].line == 1

    def test_multiple_blocks(self):
        text = """always_ff @(posedge clk) begin
    a <= b;
end

always_comb begin
    c = d;
end"""
        ff_blocks = extract_blocks(text, r'\balways_ff\b', "begin", "end")
        comb_blocks = extract_blocks(text, r'\balways_comb\b', "begin", "end")
        assert len(ff_blocks) == 1
        assert len(comb_blocks) == 1
        assert "a <= b;" in ff_blocks[0].body
        assert "c = d;" in comb_blocks[0].body

    def test_nested_begin_end(self):
        text = """always_ff @(posedge clk) begin
    if (rst) begin
        q <= 0;
    end else begin
        q <= d;
    end
end"""
        blocks = extract_blocks(text, r'\balways_ff\b', "begin", "end")
        assert len(blocks) == 1
        assert "if (rst)" in blocks[0].body
        assert "q <= d;" in blocks[0].body

    def test_line_number(self):
        text = "\n\n\nalways_ff @(posedge clk) begin\n    q <= d;\nend"
        blocks = extract_blocks(text, r'\balways_ff\b', "begin", "end")
        assert blocks[0].line == 4

    def test_openscad_module_params(self):
        text = """module gear(
    teeth = 20,
    pitch = max(1, 2),
    bore = 5
) {"""
        # entry regex stops before ( so extract_blocks finds the ( itself
        blocks = extract_blocks(text, r'\bmodule\s+\w+', "(", ")")
        assert len(blocks) == 1
        assert "max(1, 2)" in blocks[0].body

    def test_no_match(self):
        text = "always_comb begin x = 1; end"
        blocks = extract_blocks(text, r'\balways_ff\b', "begin", "end")
        assert len(blocks) == 0

    def test_skips_comment_in_block(self):
        text = """always_ff @(posedge clk) begin
    // this is a comment with end in it
    q <= d;
end"""
        blocks = extract_blocks(text, r'\balways_ff\b', "begin", "end")
        assert len(blocks) == 1
        assert "q <= d;" in blocks[0].body


# ---------------------------------------------------------------------------
# split_at_depth
# ---------------------------------------------------------------------------

class TestSplitAtDepth:
    def test_simple(self):
        assert split_at_depth("a, b, c") == ["a", "b", "c"]

    def test_nested_parens(self):
        result = split_at_depth("a, (b, c), d")
        assert result == ["a", "(b, c)", "d"]

    def test_nested_brackets(self):
        result = split_at_depth("pos=[0,0,0], size=[10,10,10]")
        assert result == ["pos=[0,0,0]", "size=[10,10,10]"]

    def test_function_call_default(self):
        result = split_at_depth("x = max(1, 5), y = 10")
        assert result == ["x = max(1, 5)", "y = 10"]

    def test_empty(self):
        assert split_at_depth("") == []

    def test_single_item(self):
        assert split_at_depth("x = 5") == ["x = 5"]

    def test_skips_string_comma(self):
        result = split_at_depth('a, "b,c", d')
        assert result == ["a", '"b,c"', "d"]

    def test_skips_comment_delimiter(self):
        # Comment with a comma inside doesn't cause a split
        result = split_at_depth("a, /* , */ b")
        assert result == ["a", "/* , */ b"]

    def test_mixed_brackets(self):
        result = split_at_depth("f(a, [b, c]), {d, e}")
        assert result == ["f(a, [b, c])", "{d, e}"]

    def test_custom_delimiter(self):
        result = split_at_depth("a; b; c", delimiter=";")
        assert result == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# strip_comments
# ---------------------------------------------------------------------------

class TestStripComments:
    def test_line_comment(self):
        text = "code // comment\nmore code"
        result = strip_comments(text)
        assert result == "code           \nmore code"

    def test_block_comment(self):
        text = "code /* block */ more"
        result = strip_comments(text)
        assert "code" in result
        assert "more" in result
        assert "block" not in result

    def test_preserves_line_count(self):
        text = "line1\n/* multi\nline\ncomment */\nline5"
        result = strip_comments(text)
        assert result.count("\n") == text.count("\n")

    def test_preserves_strings(self):
        text = 'code "// not a comment" more'
        result = strip_comments(text)
        assert '"// not a comment"' in result

    def test_python_style(self):
        text = 'code # comment\nmore'
        result = strip_comments(text, syntax=PYTHON_STYLE)
        assert "code" in result
        assert "comment" not in result

    def test_nested_block_comment(self):
        text = "a /* b /* c */ d */ e"
        result = strip_comments(text)
        # Standard C-style: first */ ends the comment
        assert "e" in result

    def test_empty_input(self):
        assert strip_comments("") == ""

    def test_comment_at_end(self):
        text = "code // trailing"
        result = strip_comments(text)
        assert result.startswith("code ")
        assert "trailing" not in result

    def test_escape_in_string(self):
        text = r'code "escaped \" quote" more'
        result = strip_comments(text)
        assert "escaped" in result
        assert "more" in result


# ---------------------------------------------------------------------------
# CommentSyntax presets
# ---------------------------------------------------------------------------

class TestCommentSyntax:
    def test_hdl_style(self):
        text = "module foo; // comment\n/* block */\nendmodule"
        result = strip_comments(text, syntax=HDL_STYLE)
        assert "module foo;" in result
        assert "comment" not in result
        assert "block" not in result
        assert "endmodule" in result

    def test_sql_style(self):
        text = "SELECT * -- comment\nFROM t /* block */ WHERE 1"
        result = strip_comments(text, syntax=SQL_STYLE)
        assert "SELECT *" in result
        assert "comment" not in result
        assert "WHERE 1" in result

    def test_python_triple_quote(self):
        # Triple-quoted strings are treated as block comments in PYTHON_STYLE
        # and get stripped - this is correct for code analysis
        text = '"""docstring"""\ncode # comment'
        result = strip_comments(text, syntax=PYTHON_STYLE)
        assert "docstring" not in result
        assert "comment" not in result
        assert "code" in result
