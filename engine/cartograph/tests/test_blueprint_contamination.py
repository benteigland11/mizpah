"""Kind-aware contamination scanner: blueprint sealed-API-surface rules."""

import json
import os
import textwrap

import pytest

from cartograph.contamination import scan_contamination


def _bp(tmp_path, deps=None, files=None):
    """Create a blueprint dir with given deps and files dict {relpath: content}."""
    deps = deps or [{"id": "backend-rate-limit-python", "version": "1.0.0"}]
    files = files or {}
    bp = tmp_path / "bp"
    bp.mkdir()
    (bp / "src").mkdir()
    (bp / "tests").mkdir()
    (bp / "examples").mkdir()
    manifest = {
        "id": "bp-auth-flow-python",
        "name": "auth-flow",
        "language": "python",
        "version": "0.1.0",
        "description": "test",
        "tags": ["a"],
        "dependencies": deps,
        "domains": [],
    }
    (bp / "blueprint.json").write_text(json.dumps(manifest))
    for relpath, content in files.items():
        full = bp / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(textwrap.dedent(content))
    return bp


def test_blueprint_clean_passes(tmp_path):
    bp = _bp(tmp_path, files={
        "src/auth_flow.py": "def hello():\n    return 'ok'\n",
        "tests/test_auth_flow.py": "from src.auth_flow import hello\n\ndef test_x():\n    assert hello() == 'ok'\n",
        "examples/example_auth_flow.py": "from src.auth_flow import hello\nprint(hello())\n",
    })
    res = scan_contamination(str(bp))
    assert res["blocks"] == [], res


def test_blueprint_src_can_import_declared_dep(tmp_path):
    bp = _bp(
        tmp_path,
        deps=[{"id": "backend-rate-limit-python", "version": "1.0.0"}],
        files={
            "src/auth_flow.py": (
                "from cg.backend_rate_limit_python.src.rate_limit import limit\n"
                "def hello():\n    return limit('x')\n"
            ),
            "tests/test_x.py": "def test_x():\n    pass\n",
            "examples/example_x.py": "print('hi')\n",
        },
    )
    res = scan_contamination(str(bp))
    assert res["blocks"] == [], res


def test_blueprint_src_blocks_undeclared_dep_import(tmp_path):
    bp = _bp(
        tmp_path,
        deps=[{"id": "backend-rate-limit-python", "version": "1.0.0"}],
        files={
            "src/auth_flow.py": (
                "from cg.security_audit_log_python.src.audit import log\n"
            ),
            "tests/test_x.py": "def test_x():\n    pass\n",
            "examples/example_x.py": "print('hi')\n",
        },
    )
    res = scan_contamination(str(bp))
    assert any("security_audit_log_python" in b for b in res["blocks"])
    assert any("not a declared dependency" in b for b in res["blocks"])


def test_blueprint_src_blocks_bare_import_cg(tmp_path):
    bp = _bp(tmp_path, files={
        "src/auth_flow.py": "import cg\n",
        "tests/test_x.py": "def test_x():\n    pass\n",
        "examples/example_x.py": "print('hi')\n",
    })
    res = scan_contamination(str(bp))
    assert any("Bare `import cg`" in b for b in res["blocks"])


def test_blueprint_tests_cannot_import_cg(tmp_path):
    bp = _bp(
        tmp_path,
        deps=[{"id": "backend-rate-limit-python", "version": "1.0.0"}],
        files={
            "src/auth_flow.py": "def hello():\n    return 'ok'\n",
            "tests/test_x.py": (
                "from cg.backend_rate_limit_python.src.rate_limit import limit\n"
                "def test_x():\n    pass\n"
            ),
            "examples/example_x.py": "print('hi')\n",
        },
    )
    res = scan_contamination(str(bp))
    blocks = " ".join(res["blocks"])
    assert "tests/" in blocks
    assert "sealed API surface" in blocks


def test_blueprint_examples_cannot_import_cg(tmp_path):
    bp = _bp(
        tmp_path,
        deps=[{"id": "backend-rate-limit-python", "version": "1.0.0"}],
        files={
            "src/auth_flow.py": "def hello():\n    return 'ok'\n",
            "tests/test_x.py": "def test_x():\n    pass\n",
            "examples/example_x.py": (
                "from cg.backend_rate_limit_python.src.rate_limit import limit\n"
                "print(limit('x'))\n"
            ),
        },
    )
    res = scan_contamination(str(bp))
    blocks = " ".join(res["blocks"])
    assert "examples/" in blocks
    assert "sealed API surface" in blocks


def test_blueprint_comments_are_ignored(tmp_path):
    bp = _bp(tmp_path, files={
        "src/auth_flow.py": (
            "# from cg.bad_python.src import x  # this is a comment\n"
            "def hello():\n    return 'ok'\n"
        ),
        "tests/test_x.py": (
            "# from cg.something import x\n"
            "def test_x():\n    pass\n"
        ),
        "examples/example_x.py": "print('hi')\n",
    })
    res = scan_contamination(str(bp))
    assert res["blocks"] == [], res


def test_blueprint_absolute_path_blocked(tmp_path):
    bp = _bp(tmp_path, files={
        "src/auth_flow.py": "PATH = '/home/alice/secrets/key'\n",
        "tests/test_x.py": "def test_x():\n    pass\n",
        "examples/example_x.py": "print('hi')\n",
    })
    res = scan_contamination(str(bp))
    assert any("Absolute path" in b for b in res["blocks"])


def test_blueprint_credential_blocked(tmp_path):
    bp = _bp(tmp_path, files={
        "src/auth_flow.py": 'api_key = "sk-abcd1234efgh"\n',
        "tests/test_x.py": "def test_x():\n    pass\n",
        "examples/example_x.py": "print('hi')\n",
    })
    res = scan_contamination(str(bp))
    assert any("credential" in b.lower() for b in res["blocks"])


def test_blueprint_multiline_import_first_line_caught(tmp_path):
    """Parenthesized imports — first line carries the `from cg.X` token, regex catches it."""
    bp = _bp(
        tmp_path,
        deps=[{"id": "backend-rate-limit-python", "version": "1.0.0"}],
        files={
            "src/auth_flow.py": (
                "from cg.security_audit_log_python.src.audit import (\n"
                "    log,\n"
                "    flush,\n"
                ")\n"
            ),
            "tests/test_x.py": "def test_x():\n    pass\n",
            "examples/example_x.py": "print('hi')\n",
        },
    )
    res = scan_contamination(str(bp))
    assert any("security_audit_log_python" in b for b in res["blocks"])


def test_blueprint_indented_import_inside_function_caught(tmp_path):
    """Lazy / scoped imports must be caught even though they're indented."""
    bp = _bp(
        tmp_path,
        deps=[{"id": "backend-rate-limit-python", "version": "1.0.0"}],
        files={
            "src/auth_flow.py": (
                "def lazy():\n"
                "    from cg.security_audit_log_python.src.audit import log\n"
                "    return log\n"
            ),
            "tests/test_x.py": "def test_x():\n    pass\n",
            "examples/example_x.py": "print('hi')\n",
        },
    )
    res = scan_contamination(str(bp))
    assert any("security_audit_log_python" in b for b in res["blocks"])


def test_blueprint_string_with_cg_token_not_flagged(tmp_path):
    """A string that mentions cg.foo is not an import — must not block."""
    bp = _bp(
        tmp_path,
        files={
            "src/auth_flow.py": (
                'DOC = "see cg.backend_rate_limit_python for the rate limiter"\n'
                "def hello():\n    return DOC\n"
            ),
            "tests/test_x.py": "def test_x():\n    pass\n",
            "examples/example_x.py": "print('hi')\n",
        },
    )
    res = scan_contamination(str(bp))
    assert res["blocks"] == [], res


def test_blueprint_many_deps_all_allowed(tmp_path):
    """Sanity-check with a realistic dep count."""
    deps = [
        {"id": f"backend-dep{i}-python", "version": "1.0.0"}
        for i in range(8)
    ]
    src = "\n".join(
        f"from cg.backend_dep{i}_python.src.x import y" for i in range(8)
    ) + "\ndef hello():\n    return 'ok'\n"
    bp = _bp(tmp_path, deps=deps, files={
        "src/auth_flow.py": src,
        "tests/test_x.py": "def test_x():\n    pass\n",
        "examples/example_x.py": "print('hi')\n",
    })
    res = scan_contamination(str(bp))
    assert res["blocks"] == [], res


def test_blueprint_cg_namespace_lookalike_not_flagged(tmp_path):
    """`cgi` and `cgroup` are stdlib/system names; they must not trigger cg-import rules."""
    bp = _bp(tmp_path, files={
        "src/auth_flow.py": (
            "import cgi\n"
            "from cgroup import x\n"  # not real, but tests the prefix boundary
            "def hello():\n    return 'ok'\n"
        ),
        "tests/test_x.py": "def test_x():\n    pass\n",
        "examples/example_x.py": "print('hi')\n",
    })
    res = scan_contamination(str(bp))
    # The cg-import rules must not match — `cgi`/`cgroup` start with `cg`
    # but are not `cg` itself. Accept either no blocks, or only generic
    # ones unrelated to cg-imports.
    cg_blocks = [b for b in res["blocks"] if "cg." in b or "import cg" in b]
    assert cg_blocks == [], cg_blocks


def test_blueprint_dep_dir_normalization_underscore_id(tmp_path):
    """Dep id 'backend-foo-python' must map to dir 'backend_foo_python' (Python rule)."""
    bp = _bp(
        tmp_path,
        deps=[{"id": "backend-foo-python", "version": "1.0.0"}],
        files={
            "src/auth_flow.py": (
                "from cg.backend_foo_python.src.foo import bar\n"
                "def hello():\n    return bar()\n"
            ),
            "tests/test_x.py": "def test_x():\n    pass\n",
            "examples/example_x.py": "print('hi')\n",
        },
    )
    res = scan_contamination(str(bp))
    assert res["blocks"] == [], res


def test_blueprint_no_src_files_does_not_crash(tmp_path):
    """Empty src/ shouldn't crash the scanner — schema validation handles emptiness."""
    bp = _bp(tmp_path, files={
        "tests/test_x.py": "def test_x():\n    pass\n",
        "examples/example_x.py": "print('hi')\n",
    })
    res = scan_contamination(str(bp))
    # No cg-import problems; structure errors are validator's job.
    assert "blocks" in res and "warnings" in res


def test_widget_contamination_path_unchanged(tmp_path):
    """Widget-mode dispatch still works — kind is detected from manifest filename."""
    w = tmp_path / "w"
    w.mkdir()
    (w / "src").mkdir()
    (w / "tests").mkdir()
    manifest = {
        "meta": {"id": "backend-foo-python", "name": "Foo", "domain": "backend",
                 "version": "1.0.0", "tags": ["a"]},
        "tech_stack": {"language": "python", "dependencies": []},
        "description": "x",
    }
    (w / "widget.json").write_text(json.dumps(manifest))
    (w / "src" / "foo.py").write_text("def hello():\n    return 'ok'\n")
    (w / "tests" / "test_foo.py").write_text("def test_x():\n    pass\n")
    res = scan_contamination(str(w))
    # We don't assert the exact widget-engine output; just that dispatch
    # didn't error and returned a {blocks, warnings} dict.
    assert "blocks" in res and "warnings" in res
