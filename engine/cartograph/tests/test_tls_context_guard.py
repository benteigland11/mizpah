"""Guard against the issue #18 macOS regression.

On the python.org macOS build, OpenSSL's default cert store is empty, so any
`urllib.request.urlopen(...)` without an OS-trust-store SSL context fails with
CERTIFICATE_VERIFY_FAILED ("Public Registry Can't Be Reached"). Most network
calls route through the `infra-urllib-client-python` widget, which applies the
context for us; the few that stay on raw urllib must pass
`context=registry_ssl_context()` explicitly.

This test parses the CLI source and fails if any raw `urlopen` call omits a
`context=` keyword. A new bare urlopen is a red build, not a shipped Mac bug.
"""

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "cartograph"


def _is_urlopen(call: ast.Call) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr == "urlopen"
    if isinstance(f, ast.Name):
        return f.id == "urlopen"
    return False


def test_every_urlopen_passes_a_tls_context():
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_urlopen(node):
                if "context" not in {kw.arg for kw in node.keywords}:
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, (
        "Bare urlopen without context= reintroduces the macOS "
        "CERTIFICATE_VERIFY_FAILED bug (issue #18). Pass "
        "context=registry_ssl_context() or route the call through the "
        "infra-urllib-client-python widget. Offenders: " + ", ".join(offenders)
    )
