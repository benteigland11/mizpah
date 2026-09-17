"""
Tests for library loading.
"""


def test_widgets_loaded(carto):
    """Fixture library should load all three widgets."""
    assert len(carto.widgets) >= 3


def test_widget_fields_present(carto):
    """Every widget should have the required fields."""
    required = {"id", "name", "version", "type", "path", "tags", "domain", "description"}
    for widget in carto.widgets:
        missing = required - widget.keys()
        assert not missing, f"Widget {widget.get('id')} missing fields: {missing}"


def test_widget_ids(widget_ids):
    """Specific fixture widgets should be present."""
    assert "http-client" in widget_ids
    assert "json-parser" in widget_ids
    assert "auth-middleware" in widget_ids


def test_language_normalized(carto):
    """Language field should be a non-empty string for widgets."""
    for w in carto.widgets:
        if w["type"] == "widget":
            assert isinstance(w["language"], str)
            assert w["language"] != ""


def test_test_count(carto):
    """Widgets with a tests/ dir should have test_count > 0."""
    http = next(w for w in carto.widgets if w["id"] == "http-client")
    assert http["test_count"] > 0
