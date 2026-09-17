"""End-to-end tests for blueprint checkin into the local library."""

import json
import os

import pytest

from cartograph.engine import Cartograph


def _write_widget(project_dir, widget_id, version="1.0.0",
                  src="def go():\n    return 'ok'\n"):
    """Write a widget into project_dir/cg/<py_dir>/.

    Blueprint validation reads deps strictly from the project's cg/, so
    the test fixture mirrors `cartograph install`.
    """
    domain = widget_id.split("-")[0]
    name = widget_id[len(domain) + 1 : -len("-python") - 1]
    py_dir = widget_id.replace("-", "_")
    wdir = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(wdir, "src"))
    os.makedirs(os.path.join(wdir, "tests"))
    os.makedirs(os.path.join(wdir, "examples"))
    manifest = {
        "meta": {
            "id": widget_id, "name": name, "version": version,
            "domain": domain, "tags": ["a", "b", "c"],
        },
        "tech_stack": {"language": "python", "dependencies": []},
        "description": "fake widget",
    }
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(wdir, "src", "__init__.py"), "w").close()
    with open(os.path.join(wdir, "src", "mod.py"), "w") as f:
        f.write(src)
    with open(os.path.join(wdir, "tests", "test_mod.py"), "w") as f:
        f.write("from src.mod import go\n\ndef test_go():\n    assert go() == 'ok'\n")
    with open(os.path.join(wdir, "examples", "example_mod.py"), "w") as f:
        f.write("from src.mod import go\nprint(go())\n")
    return wdir


def _write_blueprint(project_dir, name="auth-flow", deps=None,
                     description="A real blueprint",
                     tags=None, version="0.1.0"):
    deps = deps or []
    tags = tags or ["auth", "demo", "test"]
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
        "version": version,
        "description": description,
        "tags": tags,
        "dependencies": deps,
        "domains": [],
    }
    with open(os.path.join(bp, "blueprint.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(bp, "src", "__init__.py"), "w").close()
    with open(os.path.join(bp, "src", "mod.py"), "w") as f:
        f.write("from cg.backend_greet_python.src.mod import greet\n"
                "def shout(name):\n    return greet(name).upper()\n")
    with open(os.path.join(bp, "tests", "test_mod.py"), "w") as f:
        f.write("from src.mod import shout\n\n"
                "def test_shout():\n    assert shout('x') == 'HELLO, X'\n")
    with open(os.path.join(bp, "examples", "example_mod.py"), "w") as f:
        f.write("from src.mod import shout\nprint(shout('world'))\n")
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


def test_rejects_path_not_found(carto, tmp_path):
    res = carto.checkin_blueprint(path=str(tmp_path / "nope"))
    assert res["status"] == "error"
    assert "not found" in res["message"].lower()


def test_rejects_missing_blueprint_json(carto, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    res = carto.checkin_blueprint(path=str(bare))
    assert res["status"] == "error"
    assert "blueprint.json" in res["message"]


@pytest.mark.slow
def test_first_time_registration(carto, project, tmp_path):
    """First checkin registers the blueprint, derives domains, writes a stamp."""
    lib = carto.library_path
    _write_widget(str(project), "backend-greet-python", version="1.0.0",
                  src="def greet(name):\n    return f'hello, {name}'\n")
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-greet-python", "version": "1.0.0"},
    ])
    res = carto.checkin_blueprint(path=bp, reason="initial")
    assert res["status"] == "success", res
    assert res["action"] == "registered"
    assert res["kind"] == "blueprint"
    assert res["id"] == "bp-auth-flow-python"
    assert res["version"] == "0.1.0"  # no bump on first registration
    assert res["domains"] == ["backend"]

    # Library now has the blueprint.
    lib_bp = os.path.join(lib, "bp-auth-flow-python")
    assert os.path.isdir(lib_bp)
    assert os.path.isfile(os.path.join(lib_bp, "blueprint.json"))
    # Stamp written.
    assert os.path.isfile(os.path.join(lib_bp, ".validation_stamp.json"))
    # Domains persisted.
    with open(os.path.join(lib_bp, "blueprint.json")) as f:
        stored = json.load(f)
    assert stored["domains"] == ["backend"]
    # Changelog written.
    cl = os.path.join(lib_bp, "changelog.json")
    assert os.path.isfile(cl)
    with open(cl) as f:
        entries = json.load(f)
    assert entries[0]["version"] == "0.1.0"
    assert entries[0]["reason"] == "initial"


@pytest.mark.slow
def test_update_bumps_and_archives(carto, project, tmp_path):
    """Second checkin bumps version and archives the previous version."""
    lib = carto.library_path
    _write_widget(str(project), "backend-greet-python", version="1.0.0",
                  src="def greet(name):\n    return f'hello, {name}'\n")
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-greet-python", "version": "1.0.0"},
    ])
    first = carto.checkin_blueprint(path=bp, reason="initial")
    assert first["status"] == "success", first

    # Edit the blueprint and bump.
    src_file = os.path.join(bp, "src", "mod.py")
    with open(src_file, "w") as f:
        f.write("from cg.backend_greet_python.src.mod import greet\n"
                "def shout(name):\n    return greet(name).upper() + '!'\n")
    test_file = os.path.join(bp, "tests", "test_mod.py")
    with open(test_file, "w") as f:
        f.write("from src.mod import shout\n\n"
                "def test_shout():\n    assert shout('x') == 'HELLO, X!'\n")

    second = carto.checkin_blueprint(path=bp, reason="add bang", version_bump="patch")
    assert second["status"] == "success", second
    assert second["action"] == "updated"
    assert second["version"] == "0.1.1"

    # 0.1.0 archived.
    history = os.path.join(lib, "bp-auth-flow-python", "history", "0.1.0")
    assert os.path.isdir(history), os.listdir(os.path.join(lib, "bp-auth-flow-python"))


@pytest.mark.slow
def test_no_op_blocked(carto, project, tmp_path):
    """Second checkin with identical content is rejected."""
    _write_widget(str(project), "backend-greet-python", version="1.0.0",
                  src="def greet(name):\n    return f'hello, {name}'\n")
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-greet-python", "version": "1.0.0"},
    ])
    first = carto.checkin_blueprint(path=bp, reason="initial")
    assert first["status"] == "success", first

    # Re-bump local manifest to match library so version-conflict doesn't fire first.
    bp_manifest = os.path.join(bp, "blueprint.json")
    with open(bp_manifest) as f:
        local = json.load(f)
    # Library is at 0.1.0; we wrote 0.1.0 here too. No edits → no-op guard fires.

    second = carto.checkin_blueprint(path=bp, reason="nothing changed")
    assert second["status"] == "error"
    assert "identical" in second["message"].lower()


def test_modified_dep_blocks_checkin(carto, project, monkeypatch):
    """If a dep widget has uncommitted modifications (status reports
    modified=True), blueprint checkin must refuse - we won't ship a
    blueprint pinned to a widget version that's drifted locally."""
    _write_widget(str(project), "backend-greet-python", version="1.0.0",
                  src="def greet(name):\n    return f'hello, {name}'\n")
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-greet-python", "version": "1.0.0"},
    ])

    def fake_status(self, widget_id, target_dir, check_cloud=True):
        return {"widget_id": widget_id, "modified": True, "outdated": False}

    monkeypatch.setattr(Cartograph, "widget_status", fake_status)

    res = carto.checkin_blueprint(path=bp, reason="x")
    assert res["status"] == "error"
    assert "modifications" in res["message"].lower()
    assert res["modified_deps"] == ["backend-greet-python"]


def test_outdated_dep_does_not_block_checkin(carto, project, monkeypatch):
    """`outdated` (newer library version available) is not local drift,
    so it must NOT block. The pin is still honest about what was tested."""
    _write_widget(str(project), "backend-greet-python", version="1.0.0",
                  src="def greet(name):\n    return f'hello, {name}'\n")
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-greet-python", "version": "1.0.0"},
    ])

    def fake_status(self, widget_id, target_dir, check_cloud=True):
        return {"widget_id": widget_id, "modified": False, "outdated": True}

    monkeypatch.setattr(Cartograph, "widget_status", fake_status)

    # outdated alone does not raise the dep-integrity gate. Whatever
    # validate/checkin returns next is fine - we only assert we got past
    # the gate (no `modified_deps` field on the response).
    res = carto.checkin_blueprint(path=bp, reason="x")
    assert "modified_deps" not in res


@pytest.mark.slow
def test_version_conflict(carto, project, tmp_path):
    """Local out-of-sync with library is rejected."""
    _write_widget(str(project), "backend-greet-python", version="1.0.0",
                  src="def greet(name):\n    return f'hello, {name}'\n")
    bp = _write_blueprint(str(project), deps=[
        {"id": "backend-greet-python", "version": "1.0.0"},
    ])
    first = carto.checkin_blueprint(path=bp, reason="initial")
    assert first["status"] == "success", first

    # Lie about the local version (someone hand-edited blueprint.json).
    bp_manifest = os.path.join(bp, "blueprint.json")
    with open(bp_manifest) as f:
        local = json.load(f)
    local["version"] = "0.0.5"
    with open(bp_manifest, "w") as f:
        json.dump(local, f)

    res = carto.checkin_blueprint(path=bp, reason="x")
    assert res["status"] == "error"
    assert "conflict" in res["message"].lower()
