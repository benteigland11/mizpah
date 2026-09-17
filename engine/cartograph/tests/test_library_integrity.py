"""
Library integrity gate (Day 40): widgets without a valid .validation_stamp.json
must not appear in search/install, and tampered widgets must fall out
automatically when their fingerprint stops matching.
"""
import json
import os
import shutil

import pytest


def _drop_unstamped_widget(lib_dir, widget_id="backend-evil-python"):
    """Drop a widget into the library by hand, bypassing `cartograph checkin`.
    This is the exact attack the library-integrity gate is meant to block."""
    wdir = os.path.join(lib_dir, widget_id)
    os.makedirs(os.path.join(wdir, "src"))
    os.makedirs(os.path.join(wdir, "tests"))
    manifest = {
        "meta": {"id": widget_id, "name": "Evil", "version": "1.0.0",
                 "tags": ["evil"], "domain": "backend"},
        "description": "Widget dropped into library without running checkin.",
        "tech_stack": {"language": "python", "dependencies": []},
    }
    with open(os.path.join(wdir, "widget.json"), "w") as f:
        json.dump(manifest, f)
    with open(os.path.join(wdir, "src", "__init__.py"), "w") as f:
        f.write("")
    with open(os.path.join(wdir, "src", "evil.py"), "w") as f:
        f.write("def evil(): return 42\n")
    with open(os.path.join(wdir, "tests", "test_evil.py"), "w") as f:
        f.write("def test_evil(): assert True\n")
    return wdir


@pytest.fixture
def lib_with_unstamped(fixture_library, tmp_path):
    dst = tmp_path / "Widget_Library"
    shutil.copytree(fixture_library, dst)
    _drop_unstamped_widget(str(dst))
    return str(dst)


def test_unstamped_widget_excluded_from_index(lib_with_unstamped):
    """A widget dropped into the library without a stamp must not appear in
    carto.widgets at all — invisible to search, inspect, and install."""
    from cartograph import Cartograph
    c = Cartograph(library_path=lib_with_unstamped)
    ids = {w["id"] for w in c.widgets}
    assert "backend-evil-python" not in ids


def test_unstamped_widget_not_in_search(lib_with_unstamped):
    from cartograph import Cartograph
    c = Cartograph(library_path=lib_with_unstamped)
    result = c.search("evil")
    hits = {r["id"] for r in result["results"]}
    assert "backend-evil-python" not in hits


def test_unstamped_widget_tracked_separately(lib_with_unstamped):
    """Gate hits must be retrievable so `cartograph status` can surface them."""
    from cartograph import Cartograph
    c = Cartograph(library_path=lib_with_unstamped)
    unstamped_ids = {u["id"] for u in c.unstamped_widgets}
    assert "backend-evil-python" in unstamped_ids


def test_stamped_widget_still_visible(carto):
    """Regression guard: the gate must not hide legitimate fixtures."""
    ids = {w["id"] for w in carto.widgets}
    assert "http-client" in ids
    assert "json-parser" in ids


def test_tampered_widget_falls_out(fixture_library, tmp_path):
    """Stamped widget whose source was modified after stamping (fingerprint
    mismatch) is treated the same as unstamped — hidden from the index."""
    dst = tmp_path / "Widget_Library"
    shutil.copytree(fixture_library, dst)
    # Tamper with http-client's source without re-stamping
    target = os.path.join(str(dst), "http-client", "src", "http_client.py")
    with open(target, "a") as f:
        f.write("\n# injected\n")

    from cartograph import Cartograph
    c = Cartograph(library_path=str(dst))
    ids = {w["id"] for w in c.widgets}
    assert "http-client" not in ids
    unstamped_ids = {u["id"] for u in c.unstamped_widgets}
    assert "http-client" in unstamped_ids


def test_install_of_unstamped_widget_not_found(lib_with_unstamped, tmp_path):
    """Explicit install of an unstamped widget ID returns 'not found' since
    the gate keeps it out of the library index entirely."""
    from cartograph import Cartograph
    c = Cartograph(library_path=lib_with_unstamped)
    target = str(tmp_path / "myproject")
    result = c.install("backend-evil-python", target_dir=target)
    assert result.get("status") != "success"
    combined = f"{result.get('error', '')} {result.get('message', '')}".lower()
    assert "not found" in combined or "unknown" in combined
