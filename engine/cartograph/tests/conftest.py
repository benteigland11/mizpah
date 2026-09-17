"""
Shared fixtures for Cartograph tests.

All test data is generated in a session-scoped temp directory - no on-disk
fixtures directory required.
"""
import json
import os
import sys

import pytest

# Ensure the repo root is on the path so 'cartograph' package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Auto-apply language markers to parametrized tests
# ---------------------------------------------------------------------------
# Maps language strings (as they appear in parametrize args) to pytest marks.
# This lets `pytest -m openscad` run just openscad tests without touching
# every @pytest.mark.parametrize call.

_LANG_MARKS = {
    "python": pytest.mark.python,
    "javascript": pytest.mark.javascript,
    "nim": pytest.mark.nim,
    "openscad": pytest.mark.openscad,
    "systemverilog": pytest.mark.systemverilog,
}


def pytest_collection_modifyitems(items):
    """Add language markers to parametrized tests based on their param values."""
    for item in items:
        # Check parametrize callspec for a 'lang' or 'language' param
        if hasattr(item, "callspec"):
            for param_name in ("lang", "language"):
                val = item.callspec.params.get(param_name)
                if val and val in _LANG_MARKS:
                    item.add_marker(_LANG_MARKS[val])


# ---------------------------------------------------------------------------
# Fixture widget content
# ---------------------------------------------------------------------------

_HTTP_CLIENT_SRC = """\
def get(url: str, timeout: int = 30) -> str:
    \"\"\"Send an HTTP GET request and return the response body.\"\"\"
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode()


def post(url: str, data: bytes = None, timeout: int = 30) -> str:
    \"\"\"Send an HTTP POST request and return the response body.\"\"\"
    import urllib.request
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()
"""

_HTTP_CLIENT_TEST = """\
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from unittest.mock import patch, MagicMock
from http_client import get, post


def _mock_response(body="OK"):
    resp = MagicMock()
    resp.read.return_value = body.encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@patch("urllib.request.urlopen")
def test_get_returns_body(mock_urlopen):
    mock_urlopen.return_value = _mock_response("hello")
    assert get("http://example.com") == "hello"
    mock_urlopen.assert_called_once()


@patch("urllib.request.urlopen")
def test_get_passes_timeout(mock_urlopen):
    mock_urlopen.return_value = _mock_response()
    get("http://example.com", timeout=5)
    _, kwargs = mock_urlopen.call_args
    assert kwargs.get("timeout") == 5


@patch("urllib.request.urlopen")
def test_post_returns_body(mock_urlopen):
    mock_urlopen.return_value = _mock_response("created")
    assert post("http://example.com", data=b"x") == "created"


@patch("urllib.request.urlopen")
def test_post_sends_data(mock_urlopen):
    mock_urlopen.return_value = _mock_response()
    post("http://example.com", data=b"payload")
    req = mock_urlopen.call_args[0][0]
    assert req.data == b"payload"
    assert req.method == "POST"
"""

_HTTP_CLIENT_EXAMPLE = """\
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.http_client import get

# Example: fetch a URL (just show it's importable, don't hit the network)
print(f"get function loaded: {get}")
print("HTTP Client example complete.")
"""

_JSON_PARSER_SRC = """\
import json


def parse(raw: str) -> dict:
    \"\"\"Parse a JSON string with schema validation support.\"\"\"
    return json.loads(raw)


def validate(data: dict, schema: dict) -> bool:
    \"\"\"Basic schema validation — checks required keys exist.\"\"\"
    required = schema.get("required", [])
    return all(k in data for k in required)
"""

_JSON_PARSER_TEST = """\
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from json_parser import parse, validate

def test_parse_valid():
    result = parse('{"key": "value"}')
    assert result == {"key": "value"}

def test_validate_pass():
    assert validate({"name": "test"}, {"required": ["name"]})

def test_validate_fail():
    assert not validate({}, {"required": ["name"]})
"""

_JSON_PARSER_EXAMPLE = """\
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.json_parser import parse, validate

data = parse('{"name": "widget", "version": "1.0.0"}')
print(f"Parsed: {data}")

schema = {"required": ["name", "version"]}
print(f"Valid: {validate(data, schema)}")
"""

_AUTH_MIDDLEWARE_SRC = """\
def verify_token(token: str, secret: str = "default") -> dict:
    \"\"\"Verify a JWT-like token (stub implementation).\"\"\"
    if not token or not isinstance(token, str):
        raise ValueError("Invalid token")
    return {"sub": "user", "verified": True}


def require_auth(handler):
    \"\"\"Decorator that enforces authentication on a handler.\"\"\"
    def wrapper(*args, **kwargs):
        token = kwargs.get("token")
        if not token:
            return {"error": "unauthorized", "status": 401}
        verify_token(token)
        return handler(*args, **kwargs)
    return wrapper
"""

_AUTH_MIDDLEWARE_TEST = """\
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from auth_middleware import verify_token, require_auth
import pytest

def test_verify_valid_token():
    result = verify_token("some.jwt.token")
    assert result["verified"] is True

def test_verify_empty_token():
    with pytest.raises(ValueError):
        verify_token("")

def test_require_auth_blocks():
    @require_auth
    def handler(): return {"ok": True}
    result = handler()
    assert result["status"] == 401
"""

_AUTH_MIDDLEWARE_EXAMPLE = """\
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.auth_middleware import verify_token

result = verify_token("example.jwt.token")
print(f"Token verified: {result}")
"""


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _stamp_widget(widget_dir, language):
    """Write a validation stamp for a fixture widget so the library-integrity
    gate in _load_library() admits it. Fixture widgets are constructed from
    known-good content but never run through `cartograph checkin`, so without
    this helper they would be filtered out as unstamped."""
    from cartograph.languages import get_engine
    from cartograph.validation_stamp import write_stamp
    engine = get_engine(language)
    if engine is None:
        return
    write_stamp(widget_dir, language, engine)


def _make_nim_widget(base, widget_id, name, version, domain, tags,
                     description, nimble_name, src_name, src_content,
                     test_content, example_content):
    """Scaffold a minimal Nim widget directory under *base*."""
    wdir = os.path.join(base, widget_id)
    os.makedirs(wdir, exist_ok=True)
    manifest = {
        "meta": {"id": widget_id, "name": name, "version": version,
                 "tags": tags, "domain": domain},
        "description": description,
        "tech_stack": {"language": "nim", "dependencies": []},
    }
    _write(os.path.join(wdir, "widget.json"), json.dumps(manifest, indent=2))
    _write(os.path.join(wdir, f"{nimble_name}.nimble"),
           f'version = "0.1.0"\nauthor = "Test"\ndescription = "{name}"\n'
           f'license = "MIT"\nsrcDir = "src"\nrequires "nim >= 2.0.0"\n')
    _write(os.path.join(wdir, "src", src_name), src_content)
    _write(os.path.join(wdir, "tests", f"test_{nimble_name}.nim"), test_content)
    _write(os.path.join(wdir, "examples", "example_usage.nim"), example_content)
    _stamp_widget(wdir, "nim")
    return wdir


def _make_js_widget(base, widget_id, name, version, domain, tags,
                    description, src_name, src_content,
                    test_content, example_content):
    """Scaffold a minimal JS widget directory under *base*."""
    wdir = os.path.join(base, widget_id)
    os.makedirs(wdir, exist_ok=True)
    manifest = {
        "meta": {"id": widget_id, "name": name, "version": version,
                 "tags": tags, "domain": domain},
        "description": description,
        "tech_stack": {"language": "javascript", "dependencies": []},
    }
    _write(os.path.join(wdir, "widget.json"), json.dumps(manifest, indent=2))
    pkg = {"name": widget_id, "version": "0.1.0", "type": "module",
           "scripts": {"test": "vitest run"}}
    _write(os.path.join(wdir, "package.json"), json.dumps(pkg, indent=2))
    _write(os.path.join(wdir, "src", src_name), src_content)
    _write(os.path.join(wdir, "tests", f"test_{src_name}"), test_content)
    _write(os.path.join(wdir, "examples", "example_usage.js"), example_content)
    _stamp_widget(wdir, "javascript")
    return wdir


def _make_widget(base, widget_id, name, version, domain, language, tags,
                 description, dependencies, src_name, src_content,
                 test_content, example_content):
    """Scaffold a complete widget directory under *base*."""
    wdir = os.path.join(base, widget_id)
    os.makedirs(wdir, exist_ok=True)

    manifest = {
        "meta": {
            "id": widget_id,
            "name": name,
            "version": version,
            "tags": tags,
            "domain": domain,
        },
        "description": description,
        "tech_stack": {
            "language": language,
            "dependencies": dependencies,
        },
    }
    _write(os.path.join(wdir, "widget.json"), json.dumps(manifest, indent=2))
    _write(os.path.join(wdir, "src", "__init__.py"), "")
    _write(os.path.join(wdir, "src", src_name), src_content)
    _write(os.path.join(wdir, "tests", f"test_{src_name}"), test_content)
    _write(os.path.join(wdir, "examples", "example_usage.py"), example_content)
    _stamp_widget(wdir, language)
    return wdir


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory):
    """Generate a complete test fixture tree and return its root path."""
    root = str(tmp_path_factory.mktemp("fixtures"))
    lib = os.path.join(root, "Widget_Library")

    # --- Widgets ---
    _make_widget(
        lib, "http-client", "HTTP Client", "1.2.0", "backend", "python",
        ["http", "client", "requests"],
        "A lightweight HTTP client for making GET and POST requests.",
        [],
        "http_client.py", _HTTP_CLIENT_SRC,
        _HTTP_CLIENT_TEST, _HTTP_CLIENT_EXAMPLE,
    )
    _make_widget(
        lib, "json-parser", "JSON Parser", "1.0.0", "data", "python",
        ["json", "parser", "schema", "validation"],
        "JSON parsing with schema validation support.",
        [],
        "json_parser.py", _JSON_PARSER_SRC,
        _JSON_PARSER_TEST, _JSON_PARSER_EXAMPLE,
    )
    _make_widget(
        lib, "auth-middleware", "Auth Middleware", "1.0.0", "backend", "python",
        ["auth", "jwt", "middleware"],
        "JWT authentication middleware for backend services.",
        [],
        "auth_middleware.py", _AUTH_MIDDLEWARE_SRC,
        _AUTH_MIDDLEWARE_TEST, _AUTH_MIDDLEWARE_EXAMPLE,
    )
    _make_nim_widget(
        lib, "universal-add-nim", "Nim Add", "1.0.0", "universal",
        ["math", "add"], "A simple add function.",
        "add", "add_lib.nim",
        'func add*(a, b: int): int = a + b\n',
        'import std/unittest\nimport add_lib\nsuite "add":\n  test "1+1": check add(1,1) == 2\n',
        'import add_lib\necho add(2, 3)\n',
    )
    _make_js_widget(
        lib, "data-sum-javascript", "JS Sum", "1.0.0", "data",
        ["math", "sum"], "A simple sum function.",
        "sum.js",
        'export function sum(a, b) { return a + b; }\n',
        'import { sum } from "../src/sum.js";\nif (sum(1,1) !== 2) throw new Error("fail");\n',
        'import { sum } from "../src/sum.js";\nconsole.log(sum(2, 3));\n',
    )

    return root


@pytest.fixture(scope="session")
def fixture_library(fixture_dir):
    return os.path.join(fixture_dir, "Widget_Library")


@pytest.fixture(scope="session")
def carto(fixture_library):
    """A Cartograph instance loaded against the generated test fixtures."""
    from cartograph import Cartograph
    return Cartograph(library_path=fixture_library)


@pytest.fixture(scope="session")
def widget_ids(carto):
    return {w["id"] for w in carto.widgets}
