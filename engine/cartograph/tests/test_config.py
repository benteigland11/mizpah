"""Tests for config module - settings, language scope, edge cases."""
import json
import os

import pytest


def test_config_defaults():
    """Default config should include all expected keys."""
    from cartograph.config import load_config
    cfg = load_config()
    assert "publish" in cfg
    assert "library" in cfg
    assert cfg["library"]["show_unavailable"] is True
    assert cfg["library"]["auto_update"] is True


def test_config_list_values_not_empty():
    """list_values should return at least one entry."""
    from cartograph.config import list_values
    items = list_values()
    assert len(items) > 0
    for item in items:
        assert "key" in item
        assert "description" in item


def test_config_get_set_roundtrip(tmp_path, monkeypatch):
    """Setting a value should be retrievable."""
    monkeypatch.setattr("cartograph.config._config_path",
                        lambda: str(tmp_path / "config.toml"))
    from cartograph.config import get_value, set_value
    err = set_value("show-unavailable", "false")
    assert err is None
    val, err = get_value("show-unavailable")
    assert err is None
    assert val is False


def test_config_auto_update_roundtrip(tmp_path, monkeypatch):
    """auto-update should be configurable like other boolean settings."""
    monkeypatch.setattr("cartograph.config._config_path",
                        lambda: str(tmp_path / "config.toml"))
    from cartograph.config import get_value, set_value
    assert set_value("auto-update", "false") is None
    val, err = get_value("auto-update")
    assert err is None
    assert val is False


def test_config_paths_override_roundtrip(tmp_path, monkeypatch):
    """paths.<binary> keys should round-trip through set/get/list."""
    monkeypatch.setattr("cartograph.config._config_path",
                        lambda: str(tmp_path / "config.toml"))
    from cartograph.config import (get_path_override, get_value,
                                    list_values, set_value)

    assert set_value("paths.nim", r"C:\Program Files\Nim\bin\nim.exe") is None
    val, err = get_value("paths.nim")
    assert err is None
    assert val == r"C:\Program Files\Nim\bin\nim.exe"
    assert get_path_override("nim") == r"C:\Program Files\Nim\bin\nim.exe"

    # list_values surfaces configured paths so `cartograph config` shows them
    items = list_values()
    keys = {i["key"]: i["value"] for i in items}
    assert "paths.nim" in keys
    assert keys["paths.nim"] == r"C:\Program Files\Nim\bin\nim.exe"


def test_force_utf8_io_noop_off_windows(monkeypatch):
    """On non-Windows, _force_utf8_io must not touch stdout/stderr."""
    import sys

    from cartograph.cli import _force_utf8_io
    monkeypatch.setattr(sys, "platform", "linux")
    called = []

    class FakeStream:
        def reconfigure(self, **kwargs):
            called.append(kwargs)

    monkeypatch.setattr(sys, "stdout", FakeStream())
    monkeypatch.setattr(sys, "stderr", FakeStream())
    _force_utf8_io()
    assert called == []


def test_force_utf8_io_reconfigures_on_windows(monkeypatch):
    """On Windows, reconfigure must be called with utf-8 + errors=replace."""
    import sys

    from cartograph.cli import _force_utf8_io
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []

    class FakeStream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(sys, "stdout", FakeStream())
    monkeypatch.setattr(sys, "stderr", FakeStream())
    _force_utf8_io()
    assert calls == [
        {"encoding": "utf-8", "errors": "replace"},
        {"encoding": "utf-8", "errors": "replace"},
    ]


def test_force_utf8_io_tolerates_wrapped_streams(monkeypatch):
    """Wrapped streams (pytest capsys, pipes) may lack reconfigure."""
    import sys

    from cartograph.cli import _force_utf8_io
    monkeypatch.setattr(sys, "platform", "win32")
    # objects with no reconfigure attribute - should not raise
    monkeypatch.setattr(sys, "stdout", object())
    monkeypatch.setattr(sys, "stderr", object())
    _force_utf8_io()  # must not raise


def test_config_get_unknown_key():
    """Getting an unknown key should return an error."""
    from cartograph.config import get_value
    val, err = get_value("nonexistent-key")
    assert val is None
    assert "Unknown setting" in err


def test_config_set_unknown_key():
    """Setting an unknown key should return an error."""
    from cartograph.config import set_value
    err = set_value("nonexistent-key", "whatever")
    assert "Unknown setting" in err


def test_config_set_invalid_bool(tmp_path, monkeypatch):
    """Setting a bool config with a non-bool string should error."""
    monkeypatch.setattr("cartograph.config._config_path",
                        lambda: str(tmp_path / "config.toml"))
    from cartograph.config import set_value
    err = set_value("auto-publish", "maybe")
    assert "Invalid boolean" in err


def test_config_set_invalid_choice(tmp_path, monkeypatch):
    """Setting a choice config with an invalid value should error."""
    monkeypatch.setattr("cartograph.config._config_path",
                        lambda: str(tmp_path / "config.toml"))
    from cartograph.config import set_value
    err = set_value("visibility", "secret")
    assert "Invalid value" in err


def test_show_unavailable_filters_widgets(tmp_path, monkeypatch):
    """With show_unavailable=false, widgets for missing engines are hidden."""
    from cartograph.config import set_value
    monkeypatch.setattr("cartograph.config._config_path",
                        lambda: str(tmp_path / "config.toml"))
    set_value("show-unavailable", "false")

    from cartograph.languages.registry import _ENGINES
    nim_engine = _ENGINES.get("nim")
    if not nim_engine:
        pytest.skip("Nim engine not registered")

    orig_toolchain = nim_engine.toolchain
    nim_engine.toolchain = {"nonexistent_binary_xyz": "install nim"}
    try:
        from cartograph.engine import Cartograph, LIBRARY_PATH
        c = Cartograph(LIBRARY_PATH)
        languages = {w["language"] for w in c.widgets}
        assert "nim" not in languages
    finally:
        nim_engine.toolchain = orig_toolchain


def test_empty_library(tmp_path):
    """Cartograph should handle an empty library without crashing."""
    lib_path = str(tmp_path / "empty_lib")
    os.makedirs(lib_path)
    from cartograph.engine import Cartograph
    c = Cartograph(lib_path)
    assert c.widgets == []
    results = c.search("anything")
    assert results.get("results", []) == []


def test_weighted_rating_bayesian(carto):
    """Weighted rating should exist on all widgets and regress toward global mean."""
    for w in carto.widgets:
        assert "weighted_rating" in w
        if w["review_count"] == 0:
            assert w["weighted_rating"] == 0
        else:
            # Weighted rating should be between 0 and 5
            assert 0 < w["weighted_rating"] <= 5.0


def test_weighted_rating_penalizes_low_count():
    """A widget with few reviews should have a lower weighted rating than raw."""
    from cartograph.engine import Cartograph
    C = Cartograph._RATING_CONFIDENCE_THRESHOLD
    # Simulate: 1 review at 5.0, global mean 3.5
    M = 3.5
    count, avg = 1, 5.0
    weighted = (count * avg + C * M) / (count + C)
    assert weighted < avg, "Low-count widget should regress toward mean"
    # With many reviews, weighted should approach raw
    count = 100
    weighted_high = (count * avg + C * M) / (count + C)
    assert weighted_high > 4.9, "High-count widget should converge to raw rating"
