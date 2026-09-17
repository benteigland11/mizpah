"""
Tests for inspect and list_popular.
"""
import pytest


def test_inspect_known_widget(carto):
    result = carto.inspect("http-client")
    assert result["id"] == "http-client"
    assert result["name"] == "HTTP Client"


def test_inspect_unknown_widget(carto):
    result = carto.inspect("does-not-exist")
    assert "error" in result


def test_inspect_returns_metadata(carto):
    result = carto.inspect("http-client")
    assert "version" in result
    assert "description" in result
    assert "language" in result
    assert "domain" in result
    assert "dependencies" in result


def test_inspect_always_includes_examples(carto):
    result = carto.inspect("http-client")
    assert "examples" in result
    assert isinstance(result["examples"], dict)


def test_inspect_always_includes_rating(carto):
    result = carto.inspect("http-client")
    assert "rating" in result
    assert "review_count" in result
    assert isinstance(result["rating"], (int, float))
    assert isinstance(result["review_count"], int)


def test_inspect_always_includes_versions(carto):
    result = carto.inspect("http-client")
    assert "versions" in result
    assert isinstance(result["versions"], list)
    assert len(result["versions"]) <= 5


def test_inspect_source_off_by_default(carto):
    result = carto.inspect("http-client")
    assert "source" not in result


def test_inspect_source_when_requested(carto):
    result = carto.inspect("http-client", show_source=True)
    assert "source" in result
    assert isinstance(result["source"], dict)


def test_inspect_reviews_off_by_default(carto):
    result = carto.inspect("http-client")
    assert "reviews" not in result


def test_inspect_reviews_when_requested(carto):
    result = carto.inspect("http-client", show_reviews=True)
    assert "reviews" in result
    assert isinstance(result["reviews"], list)


def test_inspect_no_internal_fields(carto):
    result = carto.inspect("http-client")
    assert "path" not in result
    assert "stats" not in result
    assert "hint" not in result


def test_list_popular_returns_list(carto):
    result = carto.list_popular()
    assert isinstance(result, dict)
    assert "top_assets" in result
    assert isinstance(result["top_assets"], list)


def test_list_popular_limit(carto):
    result = carto.list_popular(limit=2)
    assert len(result["top_assets"]) <= 2


def test_list_popular_fields(carto):
    result = carto.list_popular()
    for item in result["top_assets"]:
        assert "id" in item
        assert "name" in item
        assert "version" in item
        assert "install_count" in item
