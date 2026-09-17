"""In-place mutation of architect.py source.

Used by `cartograph architect link` to update a Component's `widgets`
list without forcing the user to hand-edit the file. Surgery is
targeted: only the matching Component(...) call is rewritten.
Everything else - imports, comments outside the Component block, the
Architecture(...) wrapping call, prose - is preserved byte-for-byte.

The replacement Component(...) call is emitted in a multi-line, pretty
form (one keyword per line, indented to match the original Component's
column) so the file stays readable after surgery. Lists in keywords
(domains, widgets) are also expanded one element per line when they
have more than one entry.

Trade-off: comments *inside* the matched Component(...) call are not
preserved. For a vibes-layer doc this is acceptable; if you have
load-bearing comments inside a Component block, edit by hand instead.
"""

import ast
from typing import List


class ArchitectMutationError(Exception):
    """Raised when link operations can't find or modify the target."""


def set_component_widgets(
    source: str,
    component_id: str,
    widgets: List[str],
) -> str:
    """Return source with Component(id=<component_id>).widgets = <widgets>.

    Pass an empty list to clear the field. The matching Component call
    is rewritten in place; the rest of the file is untouched.

    Raises ArchitectMutationError if the component can't be found or
    appears more than once at the AST level.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ArchitectMutationError(
            f"architect.py is not valid Python: {e}"
        ) from e

    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_component_call(node):
            continue
        cid = _keyword_str(node, "id")
        if cid == component_id:
            matches.append(node)

    if not matches:
        raise ArchitectMutationError(
            f"No Component with id={component_id!r} found in architect.py"
        )
    if len(matches) > 1:
        raise ArchitectMutationError(
            f"Found {len(matches)} Component blocks with id={component_id!r}; "
            f"refusing to guess which one to edit"
        )

    call = matches[0]
    _set_widgets_keyword(call, widgets)
    base_indent = " " * call.col_offset
    new_call_src = _format_component_call(call, base_indent)

    return _splice(source, call, new_call_src)


def _format_component_call(call: ast.Call, base_indent: str) -> str:
    """Emit a Component(...) call in multi-line pretty form.

    One keyword per line, indented one level past `base_indent`. Lists
    with more than one element get expanded one entry per line. Single-
    element lists and empty lists stay inline so trivial cases don't
    blow up vertically.
    """
    inner = base_indent + "    "
    if not call.keywords:
        return "Component()"

    lines = ["Component("]
    for kw in call.keywords:
        rendered = _format_value(kw.value, inner)
        lines.append(f"{inner}{kw.arg}={rendered},")
    lines.append(f"{base_indent})")
    return "\n".join(lines)


def _format_value(node: ast.AST, indent: str) -> str:
    """Render a value node. Lists with >1 element expand to multi-line."""
    if isinstance(node, ast.List):
        elements = node.elts
        if len(elements) <= 1:
            return ast.unparse(node)
        item_indent = indent + "    "
        rendered_items = [
            f"{item_indent}{ast.unparse(e)}," for e in elements
        ]
        return "[\n" + "\n".join(rendered_items) + f"\n{indent}]"
    return ast.unparse(node)


def _is_component_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "Component":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "Component":
        return True
    return False


def _keyword_str(call: ast.Call, name: str):
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _set_widgets_keyword(call: ast.Call, widgets: List[str]) -> None:
    new_keywords = [kw for kw in call.keywords if kw.arg != "widgets"]
    if widgets:
        list_node = ast.List(
            elts=[ast.Constant(value=w) for w in widgets],
            ctx=ast.Load(),
        )
        new_keywords.append(ast.keyword(arg="widgets", value=list_node))
    call.keywords = new_keywords


def _splice(source: str, call: ast.Call, replacement: str) -> str:
    if call.end_lineno is None or call.end_col_offset is None:
        raise ArchitectMutationError(
            "AST node has no end position; cannot splice"
        )
    start = _offset(source, call.lineno, call.col_offset)
    end = _offset(source, call.end_lineno, call.end_col_offset)
    return source[:start] + replacement + source[end:]


def _offset(source: str, lineno: int, col: int) -> int:
    line_starts = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            line_starts.append(i + 1)
    if lineno - 1 >= len(line_starts):
        raise ArchitectMutationError(
            f"AST line {lineno} is past end of source"
        )
    return line_starts[lineno - 1] + col
