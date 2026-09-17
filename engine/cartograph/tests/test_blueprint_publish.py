"""Tests for blueprint publish flow.

The preflight gate is the headline behavior here: a blueprint publish
must short-circuit with a clean error when the target registry advertises
`allow_blueprints: false` (the public Cartograph registry's policy).
"""

import json
import os

import pytest

from cartograph import blueprint_publish
from cartograph.engine import Cartograph


def _write_blueprint(project_dir, bp_id="bp-auth-flow-python",
                     version="0.1.0", deps=None):
    deps = deps or []
    py_dir = bp_id.replace("-", "_")
    bp = os.path.join(project_dir, "cg", py_dir)
    os.makedirs(os.path.join(bp, "src"))
    os.makedirs(os.path.join(bp, "tests"))
    os.makedirs(os.path.join(bp, "examples"))
    manifest = {
        "id": bp_id,
        "name": "auth-flow",
        "language": "python",
        "version": version,
        "description": "fake bp",
        "tags": ["a", "b", "c"],
        "dependencies": deps,
        "domains": [],
    }
    with open(os.path.join(bp, "blueprint.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    open(os.path.join(bp, "src", "__init__.py"), "w").close()
    with open(os.path.join(bp, "src", "mod.py"), "w") as f:
        f.write("def go():\n    return 'ok'\n")
    with open(os.path.join(bp, "tests", "test_mod.py"), "w") as f:
        f.write("from src.mod import go\n\ndef test_go():\n    assert go() == 'ok'\n")
    with open(os.path.join(bp, "examples", "example_mod.py"), "w") as f:
        f.write("from src.mod import go\nprint(go())\n")
    return bp


@pytest.fixture
def carto(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    return Cartograph(library_path=str(lib))


# --- Preflight gate -----------------------------------------------------


def test_preflight_blocks_publish_when_registry_disallows(monkeypatch, carto, tmp_path):
    """allow_blueprints=False short-circuits before pack/upload."""
    monkeypatch.setattr(
        "cartograph.cloud.registry_info",
        lambda registry_url=None: {"allow_blueprints": False, "validates": False},
    )
    upload_called = []
    monkeypatch.setattr(
        "cartograph.cloud.publish_blueprint",
        lambda *a, **kw: upload_called.append(True) or {"status": "success"},
    )

    bp = _write_blueprint(str(tmp_path))
    res = blueprint_publish.publish_blueprint(carto, bp, dry_run=False)

    assert res["status"] == "error"
    assert "does not accept blueprints" in res["message"]
    assert "personal-template" in res["message"]
    assert not upload_called, "upload must not run when preflight rejects"


def test_preflight_allows_when_registry_opts_in(monkeypatch, carto, tmp_path):
    """allow_blueprints=True passes the gate (org/private registry case)."""
    monkeypatch.setattr(
        "cartograph.cloud.registry_info",
        lambda registry_url=None: {"allow_blueprints": True, "validates": False},
    )
    monkeypatch.setattr(
        "cartograph.blueprint_publish._fetch_cloud_version",
        lambda *a, **kw: None,  # pretend never published
    )
    sentinel = {"status": "success", "version": "0.1.0",
                "namespaced_id": "@me/bp-auth-flow-python"}
    monkeypatch.setattr(
        "cartograph.cloud.publish_blueprint",
        lambda *a, **kw: sentinel,
    )

    bp = _write_blueprint(str(tmp_path))
    res = blueprint_publish.publish_blueprint(carto, bp, dry_run=False)

    assert res["status"] == "success"
    assert res["upload"] == sentinel


def test_preflight_skipped_on_dry_run(monkeypatch, carto, tmp_path):
    """Dry runs work offline — registry info is never consulted."""
    info_calls = []

    def boom(registry_url=None):
        info_calls.append(True)
        return {"allow_blueprints": False}

    monkeypatch.setattr("cartograph.cloud.registry_info", boom)
    monkeypatch.setattr(
        "cartograph.blueprint_publish._fetch_cloud_version",
        lambda *a, **kw: None,
    )

    bp = _write_blueprint(str(tmp_path))
    res = blueprint_publish.publish_blueprint(carto, bp, dry_run=True)

    assert res["status"] == "success"
    assert res["dry_run"] is True
    assert info_calls == [], "dry_run must not hit the network"


def test_preflight_allows_when_field_missing(monkeypatch, carto, tmp_path):
    """Older registries without `allow_blueprints` are assumed permissive."""
    monkeypatch.setattr(
        "cartograph.cloud.registry_info",
        lambda registry_url=None: {"validates": False},  # no allow_blueprints key
    )
    monkeypatch.setattr(
        "cartograph.blueprint_publish._fetch_cloud_version",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cartograph.cloud.publish_blueprint",
        lambda *a, **kw: {"status": "success"},
    )

    bp = _write_blueprint(str(tmp_path))
    res = blueprint_publish.publish_blueprint(carto, bp, dry_run=False)

    assert res["status"] == "success"


def test_preflight_continues_when_registry_unreachable(monkeypatch, carto, tmp_path):
    """Network errors don't get swallowed by the preflight - they fall through
    so the eventual upload surfaces the real failure."""
    monkeypatch.setattr(
        "cartograph.cloud.registry_info",
        lambda registry_url=None: {"error": "Network unreachable"},
    )
    monkeypatch.setattr(
        "cartograph.blueprint_publish._fetch_cloud_version",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "cartograph.cloud.publish_blueprint",
        lambda *a, **kw: {"error": "Connection refused"},
    )

    bp = _write_blueprint(str(tmp_path))
    res = blueprint_publish.publish_blueprint(carto, bp, dry_run=False)

    assert res["status"] == "error"
    assert "Connection refused" in res["message"]
