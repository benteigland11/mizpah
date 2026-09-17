"""Depth-counting text walker for structured block boundary detection.

Three core operations:
  find_close()      - from an open marker, find its matching close
  extract_blocks()  - find all blocks matching a regex entry point
  split_at_depth()  - split text by delimiter respecting nesting
  strip_comments()  - remove comments preserving line structure

All functions accept configurable comment and string syntax so they work
across languages (C-style, Python, SQL, HDL keywords, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Block:
    """A matched block with its boundaries and metadata."""
    name: str           # matched entry text (e.g. "always_ff", "module foo")
    line: int           # 1-based line number of the entry point
    body_start: int     # character offset where the block body starts (after open marker)
    body_end: int       # character offset where the block body ends (before close marker)
    body: str           # the text between open and close markers


@dataclass(frozen=True)
class CommentSyntax:
    """Defines how to skip comments and strings during walking.

    line_comments:  list of line comment starters, e.g. ["//", "#"]
    block_comments: list of (open, close) pairs, e.g. [("/*", "*/")]
    string_delims:  list of string delimiter characters, e.g. ['"', "'"]
    escape_char:    escape character inside strings, e.g. "\\\\"
    """
    line_comments: Sequence[str] = ("//",)
    block_comments: Sequence[tuple[str, str]] = (("/*", "*/"),)
    string_delims: Sequence[str] = ('"',)
    escape_char: str = "\\"


# Pre-built syntax configs for common languages
C_STYLE = CommentSyntax()
PYTHON_STYLE = CommentSyntax(
    line_comments=("#",),
    block_comments=(('"""', '"""'), ("'''", "'''")),
    string_delims=('"', "'"),
)
SQL_STYLE = CommentSyntax(
    line_comments=("--",),
    block_comments=(("/*", "*/"),),
    string_delims=("'",),
)
HDL_STYLE = CommentSyntax(
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_delims=('"',),
)


def _skip_noise(content: str, pos: int, syntax: CommentSyntax) -> int:
    """Advance past a string literal or comment starting at pos.

    Returns the new position after the skipped region, or pos if
    the current position is not the start of a string or comment.
    """
    # Check line comments
    for lc in syntax.line_comments:
        if content[pos:pos+len(lc)] == lc:
            end = content.find("\n", pos)
            return end + 1 if end != -1 else len(content)

    # Check block comments
    for bo, bc in syntax.block_comments:
        if content[pos:pos+len(bo)] == bo:
            end = content.find(bc, pos + len(bo))
            return end + len(bc) if end != -1 else len(content)

    # Check string literals
    for delim in syntax.string_delims:
        if content[pos:pos+len(delim)] == delim:
            i = pos + len(delim)
            while i < len(content):
                if syntax.escape_char and content[i:i+len(syntax.escape_char)] == syntax.escape_char:
                    i += len(syntax.escape_char) + 1
                    continue
                if content[i:i+len(delim)] == delim:
                    return i + len(delim)
                i += 1
            return len(content)

    return pos


def find_close(
    content: str,
    start: int,
    open_marker: str = "(",
    close_marker: str = ")",
    syntax: CommentSyntax = C_STYLE,
) -> int:
    """Find the matching close marker starting from just after an open marker.

    Args:
        content:      full text to search
        start:        position immediately after the opening marker
        open_marker:  the opening delimiter (single char or keyword like "begin")
        close_marker: the closing delimiter (single char or keyword like "end")
        syntax:       comment/string syntax to skip

    Returns:
        Position immediately after the matching close marker, or -1 if not found.
        The body text is content[start:return_value - len(close_marker)].
    """
    depth = 1
    is_keyword = len(open_marker) > 1 or open_marker.isalpha()
    i = start

    while i < len(content) and depth > 0:
        # Try to skip comments/strings
        skipped = _skip_noise(content, i, syntax)
        if skipped != i:
            i = skipped
            continue

        if is_keyword:
            # Keyword markers: match whole word
            if re.match(r'\b' + re.escape(open_marker) + r'\b', content[i:]):
                depth += 1
                i += len(open_marker)
            elif re.match(r'\b' + re.escape(close_marker) + r'\b', content[i:]):
                depth -= 1
                if depth == 0:
                    return i + len(close_marker)
                i += len(close_marker)
            else:
                i += 1
        else:
            # Single-character markers
            if content[i] == open_marker:
                depth += 1
                i += 1
            elif content[i] == close_marker:
                depth -= 1
                if depth == 0:
                    return i + 1
                i += 1
            else:
                i += 1

    return -1


def extract_blocks(
    content: str,
    entry_re: str,
    open_marker: str = "{",
    close_marker: str = "}",
    syntax: CommentSyntax = C_STYLE,
) -> list[Block]:
    """Find all blocks in content that match the entry regex.

    For each match of entry_re, scans forward for the first open_marker,
    then depth-counts to find the matching close_marker.

    Args:
        content:      full text to search
        entry_re:     regex pattern for block entry points (e.g. r'\\\\balways_ff\\\\b')
        open_marker:  opening delimiter
        close_marker: closing delimiter
        syntax:       comment/string syntax to skip

    Returns:
        List of Block objects with boundaries and body text.
    """
    results = []
    pattern = re.compile(entry_re)

    for m in pattern.finditer(content):
        # Find the first open_marker after the entry match
        search_from = m.end()
        is_keyword = len(open_marker) > 1 or open_marker.isalpha()

        if is_keyword:
            open_match = re.search(r'\b' + re.escape(open_marker) + r'\b',
                                   content[search_from:])
        else:
            open_match = re.search(re.escape(open_marker), content[search_from:])

        if not open_match:
            continue

        body_start = search_from + open_match.end()
        line_no = content[:m.start()].count("\n") + 1

        close_pos = find_close(content, body_start, open_marker, close_marker, syntax)
        if close_pos == -1:
            continue

        body_end = close_pos - len(close_marker)
        results.append(Block(
            name=m.group().strip(),
            line=line_no,
            body_start=body_start,
            body_end=body_end,
            body=content[body_start:body_end],
        ))

    return results


def split_at_depth(
    content: str,
    delimiter: str = ",",
    openers: str = "([{",
    closers: str = ")]}",
    syntax: CommentSyntax = C_STYLE,
) -> list[str]:
    """Split text by delimiter, respecting nested brackets and skipping comments/strings.

    Args:
        content:   text to split
        delimiter: character to split on (only at depth 0)
        openers:   characters that increase depth
        closers:   characters that decrease depth
        syntax:    comment/string syntax to skip

    Returns:
        List of stripped segments. Empty segments are excluded.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0

    while i < len(content):
        # Skip comments/strings
        skipped = _skip_noise(content, i, syntax)
        if skipped != i:
            current.append(content[i:skipped])
            i = skipped
            continue

        ch = content[i]
        if ch in openers:
            depth += 1
            current.append(ch)
        elif ch in closers:
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == delimiter and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1

    if current:
        parts.append("".join(current).strip())

    return [p for p in parts if p]


def strip_comments(
    content: str,
    syntax: CommentSyntax = C_STYLE,
) -> str:
    """Remove comments from text, preserving line structure for line counting.

    Comments are replaced with spaces; newlines within block comments are
    preserved so line numbers remain accurate.

    Args:
        content: text to strip
        syntax:  comment/string syntax configuration

    Returns:
        Text with comments replaced by whitespace.
    """
    result: list[str] = []
    i = 0

    while i < len(content):
        # Check line comments
        matched_lc = False
        for lc in syntax.line_comments:
            if content[i:i+len(lc)] == lc:
                end = content.find("\n", i)
                if end == -1:
                    result.append(" " * (len(content) - i))
                    i = len(content)
                else:
                    result.append(" " * (end - i))
                    i = end
                matched_lc = True
                break
        if matched_lc:
            continue

        # Check block comments
        matched_bc = False
        for bo, bc in syntax.block_comments:
            if content[i:i+len(bo)] == bo:
                end = content.find(bc, i + len(bo))
                if end == -1:
                    end = len(content) - len(bc)
                block = content[i:end+len(bc)]
                result.append("".join("\n" if c == "\n" else " " for c in block))
                i = end + len(bc)
                matched_bc = True
                break
        if matched_bc:
            continue

        # Check string literals - keep as-is
        matched_str = False
        for delim in syntax.string_delims:
            if content[i:i+len(delim)] == delim:
                result.append(delim)
                i += len(delim)
                while i < len(content):
                    if syntax.escape_char and content[i:i+len(syntax.escape_char)] == syntax.escape_char:
                        result.append(content[i:i+len(syntax.escape_char)+1])
                        i += len(syntax.escape_char) + 1
                        continue
                    if content[i:i+len(delim)] == delim:
                        result.append(delim)
                        i += len(delim)
                        break
                    result.append(content[i])
                    i += 1
                matched_str = True
                break
        if matched_str:
            continue

        result.append(content[i])
        i += 1

    return "".join(result)
