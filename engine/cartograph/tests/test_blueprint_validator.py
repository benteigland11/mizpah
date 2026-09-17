"""End-to-end tests for the blueprint validator pipeline.

These tests build real (tiny) Python widgets in a tmp library, then build
a blueprint that depends on them, and exercise validate_blueprint().
The Python engine creates a venv per validation, so these tests are slow
(~30s each) but they verify the actual sandbox behavior, not a mock.
"""

import json
import os
import textwrap

import pytest

from cartograph.engine import Cartograph


def _write_widget(project_dir, widget_id, version="1.0.0", src="def go():\n    return 'ok'\n",
                  test="from src.mod import go\n\ndef test_go():\n    assert go() == 'ok'\n",
                  example="from src.mod import go\nprint(go())\n",
                  pip_deps=None):
    """Write a minimal pythonic widget into project_dir/cg/<py_dir>/.

    Blueprint validation now reads deps strictly from the project's cg/,
    not the local library, so test fixtures install widgets the same way
    a real `cartograph install` would.
    """
    pip_deps = pip_deps or []
    domain = widget_id.split("-")[0]
    name = widget_id[len(domain) + 1 : -len("-python") - 1]
    py_dir = widget_id.replace("-", "_")
    wdir = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(wdir, "src"))
    os.makedirs(os.path.join(wdir, "tests"))
    os.makedirs(os.path.join(wdir, "examples"))
    manifest = {
        "meta": {
            "id": widget_id,
            "name": name,
            "version": version,
            "domain": domain,
            "tags": ["a", "b", "c"],
        },
        "tech_stack": {
            "language": "python",
            "dependencies": pip_deps,
        },
        "description": "fake widget for blueprint validator tests",
    }
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(wdir, "src", "__init__.py"), "w").close()
    with open(os.path.join(wdir, "src", "mod.py"), "w") as f:
        f.write(src)
    with open(os.path.join(wdir, "tests", "test_mod.py"), "w") as f:
        f.write(test)
    with open(os.path.join(wdir, "examples", "example_mod.py"), "w") as f:
        f.write(example)
    return wdir


def _write_blueprint(project_dir, name="auth-flow", deps=None,
                     description="A real blueprint",
                     tags=None, src=None, tests=None, examples=None):
    """Write a blueprint dir under project_dir/cg/bp_<name>_python/."""
    tags = tags or ["auth", "demo", "test"]
    deps = deps or []
    bp_id = f"bp-{name}-python"
    py_dir = bp_id.replace("-", "_")
    bp = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(bp, "src"))
    os.makedirs(os.path.join(bp, "tests"))
    os.makedirs(os.path.join(bp, "examples"))
    manifest = {
        "id": bp_id,
        "name": name,
        "language": "python",
        "version": "0.1.0",
        "description": description,
        "tags": tags,
        "dependencies": deps,
        "domains": [],
    }
    with open(os.path.join(bp, "blueprint.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(bp, "src", "__init__.py"), "w").close()
    src_default = "def hello():\n    return 'ok'\n"
    test_default = "from src.mod import hello\n\ndef test_hello():\n    assert hello() == 'ok'\n"
    example_default = "from src.mod import hello\nprint(hello())\n"
    with open(os.path.join(bp, "src", "mod.py"), "w") as f:
        f.write(src or src_default)
    with open(os.path.join(bp, "tests", "test_mod.py"), "w") as f:
        f.write(tests or test_default)
    with open(os.path.join(bp, "examples", "example_mod.py"), "w") as f:
        f.write(examples or example_default)
    return bp


@pytest.fixture
def carto(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    return Cartograph(library_path=str(lib))


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


# --- Cheap (no venv) failure paths --------------------------------------


def test_rejects_widget_dir(carto, project, tmp_path):
    """Pointing validate_blueprint at a widget should error clearly."""
    w = tmp_path / "wgt"
    w.mkdir()
    (w / "src").mkdir()
    (w / "widget.json").write_text(json.dumps({
        "meta": {"id": "backend-foo-python", "name": "foo",
                 "domain": "backend", "version": "1.0.0", "tags": ["a"]},
        "tech_stack": {"language": "python", "dependencies": []},
        "description": "x",
    }))
    res = carto.validate_blueprint(str(w))
    assert res["status"] == "error"
    assert "Not a blueprint" in res["message"]


def test_rejects_missing_path(carto, tmp_path):
    res = carto.validate_blueprint(str(tmp_path / "nope"))
    assert res["status"] == "error"
    assert "Path not found" in res["message"]


def test_rejects_dir_with_no_manifest(carto, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    res = carto.validate_blueprint(str(bare))
    assert res["status"] == "error"


def test_rejects_schema_failure_no_deps(carto, project):
    bp = _write_blueprint(str(project), deps=[])
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    assert "schema" in res["message"].lower()
    assert any("dependencies" in e.lower() for e in res["errors"])


def test_rejects_dep_not_in_project_cg(carto, project):
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-ghost-python", "version": "1.0.0"},
    ])
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    # Schema's project-cg check fires before any sandbox setup.
    assert "cg/" in res["message"] or "ghost" in res["message"]


def test_rejects_pinned_version_not_available(carto, project, tmp_path):
    """Dep is installed at 1.0.0 in project's cg/, but blueprint pins 2.0.0."""
    _write_widget(str(project), "backend-thing-python", version="1.0.0")
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-thing-python", "version": "2.0.0"},
    ])
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    # Pin-resolution fires after schema; message mentions both versions.
    assert "2.0.0" in res["message"] and "1.0.0" in res["message"]


def test_rejects_missing_src_dir(carto, project, tmp_path):
    _write_widget(str(project), "backend-thing-python", version="1.0.0")
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-thing-python", "version": "1.0.0"},
    ])
    # Wipe src/
    import shutil as _sh
    _sh.rmtree(os.path.join(bp, "src"))
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    assert "src" in res["message"]


def test_rejects_example_with_todo(carto, project, tmp_path):
    _write_widget(str(project), "backend-thing-python", version="1.0.0")
    bp = _write_blueprint(
        str(project),
        deps=[{"id": "backend-thing-python", "version": "1.0.0"}],
        examples="# [TODO] write example\nfrom src.mod import hello\n",
    )
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    assert "TODO" in res["message"]


def test_rejects_example_not_using_src(carto, project, tmp_path):
    _write_widget(str(project), "backend-thing-python", version="1.0.0")
    bp = _write_blueprint(
        str(project),
        deps=[{"id": "backend-thing-python", "version": "1.0.0"}],
        examples="print('hi')\n",
    )
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    assert "import" in res["message"].lower()


def test_rejects_no_test_files(carto, project, tmp_path):
    _write_widget(str(project), "backend-thing-python", version="1.0.0")
    bp = _write_blueprint(
        str(project),
        deps=[{"id": "backend-thing-python", "version": "1.0.0"}],
    )
    # Replace test file with non-test_*.py file.
    os.remove(os.path.join(bp, "tests", "test_mod.py"))
    with open(os.path.join(bp, "tests", "helper.py"), "w") as f:
        f.write("def x(): pass\n")
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    assert "test" in res["message"].lower()


def test_rejects_contamination(carto, project, tmp_path):
    """src/ imports an undeclared cg dep -> contamination block."""
    _write_widget(str(project), "backend-thing-python", version="1.0.0")
    _write_widget(str(project), "backend-other-python", version="1.0.0")
    bp = _write_blueprint(
        str(project),
        deps=[{"id": "backend-thing-python", "version": "1.0.0"}],
        src=(
            "from cg.backend_other_python.src.mod import go\n"
            "def hello():\n    return go()\n"
        ),
        tests="from src.mod import hello\n\ndef test_h():\n    assert hello() == 'ok'\n",
    )
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    assert "Contamination" in res["message"] or "contamination" in res["message"].lower()


# --- Slow (full venv) success path --------------------------------------


@pytest.mark.slow
def test_happy_path_blueprint_validates(carto, project, tmp_path):
    """End-to-end: blueprint composes a real widget, runs example + tests."""
    _write_widget(str(project), "backend-greet-python", version="1.0.0",
                  src="def greet(name):\n    return f'hello, {name}'\n",
                  test=("from src.mod import greet\n\n"
                        "def test_greet():\n    assert greet('x') == 'hello, x'\n"),
                  example="from src.mod import greet\nprint(greet('world'))\n")
    bp = _write_blueprint(
        str(project),
        deps=[{"id": "backend-greet-python", "version": "1.0.0"}],
        src=(
            "from cg.backend_greet_python.src.mod import greet\n"
            "def shout(name):\n    return greet(name).upper()\n"
        ),
        tests=(
            "from src.mod import shout\n\n"
            "def test_shout():\n    assert shout('x') == 'HELLO, X'\n"
        ),
        examples=(
            "from src.mod import shout\nprint(shout('world'))\n"
        ),
    )
    res = carto.validate_blueprint(bp)
    assert res["status"] == "success", res
    assert res["kind"] == "blueprint"
    assert res["id"] == "bp-auth-flow-python"
    assert res["derived_domains"] == ["backend"]


@pytest.mark.slow
def test_failing_test_in_blueprint_blocks(carto, project, tmp_path):
    """Blueprint test file fails -> validator surfaces that."""
    _write_widget(str(project), "backend-greet-python", version="1.0.0",
                  src="def greet(name):\n    return f'hello, {name}'\n",
                  test=("from src.mod import greet\n\n"
                        "def test_greet():\n    assert greet('x') == 'hello, x'\n"),
                  example="from src.mod import greet\nprint(greet('world'))\n")
    bp = _write_blueprint(
        str(project),
        deps=[{"id": "backend-greet-python", "version": "1.0.0"}],
        src=(
            "from cg.backend_greet_python.src.mod import greet\n"
            "def shout(name):\n    return greet(name).upper()\n"
        ),
        tests=(
            "from src.mod import shout\n\n"
            "def test_shout():\n    assert shout('x') == 'WRONG'\n"
        ),
        examples=(
            "from src.mod import shout\nprint(shout('world'))\n"
        ),
    )
    res = carto.validate_blueprint(bp)
    assert res["status"] == "error"
    assert "test" in res["message"].lower() or "coverage" in res["message"].lower()
