"""
Tests for search, filtering, and result formatting.
"""
import pytest


def test_search_returns_dict(carto):
    result = carto.search("http")
    assert isinstance(result, dict)
    assert "results" in result


def test_search_finds_widget_by_name(carto):
    result = carto.search("http client")
    ids = [w["id"] for w in result["results"]]
    assert "http-client" in ids


def test_search_finds_widget_by_tag(carto):
    result = carto.search("jwt")
    ids = [w["id"] for w in result["results"]]
    assert "auth-middleware" in ids


def test_search_finds_widget_by_description(carto):
    result = carto.search("schema validation")
    ids = [w["id"] for w in result["results"]]
    assert "json-parser" in ids


def test_search_no_results_for_garbage(carto):
    result = carto.search("xyzzy_qqqq_zzzzzzzzz_abc")
    assert result["results"] == []


def test_search_domain_filter(carto):
    result = carto.search("auth", domain_filter="frontend")
    # auth-middleware is backend, should not appear under frontend filter
    ids = [w["id"] for w in result["results"]]
    assert "auth-middleware" not in ids


def test_search_domain_filter_excludes_universal(carto):
    # universal widgets no longer pass every domain filter - a domain
    # search means that domain only.
    result = carto.search("math", domain_filter="data")
    ids = [w["id"] for w in result["results"]]
    assert "data-sum-javascript" in ids
    assert "universal-add-nim" not in ids


def test_search_domain_miss_nudges_to_universal(carto):
    # "add" matches universal-add-nim, but there's no backend match -
    # surface a hint to retry under universal rather than silently
    # folding universal in.
    result = carto.search("add", domain_filter="backend")
    assert result["results"] == []
    assert "universal" in result.get("message", "").lower()


def test_search_language_filter(carto):
    result = carto.search("parser", language_filter="python")
    ids = [w["id"] for w in result["results"]]
    assert "json-parser" in ids


def test_search_result_fields(carto):
    result = carto.search("http")
    for item in result["results"]:
        assert "id" in item
        assert "name" in item
        assert "description" in item
        assert "language" in item
        assert "domain" in item
        assert "rating" in item
        assert "install_count" in item
        assert "relevance_score" in item


def test_search_sorted_by_relevance(carto):
    result = carto.search("json")
    scores = [w["relevance_score"] for w in result["results"]]
    assert scores == sorted(scores, reverse=True)


def test_search_empty_library():
    """Search on a Cartograph with no widgets returns empty result."""
    from cartograph import Cartograph
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        lib = os.path.join(tmp, "Widget_Library")
        os.makedirs(lib)
        c = Cartograph(library_path=lib)
        result = c.search("anything")
    assert result == {"results": []}


def test_search_top_k_respected(carto):
    result = carto.search("backend", top_k=2)
    assert len(result["results"]) <= 2


# ---------------------------------------------------------------------------
# Search row contract (cli._search_row) — bounded rows for predictable payload
# ---------------------------------------------------------------------------

def test_search_row_drops_trend():
    from cartograph.cli import _search_row
    row = _search_row({"id": "x", "trend": "up", "rating": 4.5})
    assert "trend" not in row
    assert row["rating"] == 4.5


def test_search_row_truncates_long_description_at_word_boundary():
    from cartograph.cli import _search_row, _SEARCH_DESC_LIMIT
    desc = "word " * 100  # 500 chars
    row = _search_row({"id": "x", "description": desc})
    assert row["description"].endswith("...")
    assert len(row["description"]) <= _SEARCH_DESC_LIMIT + 3
    # No mid-word cut: everything before the ellipsis is whole words
    assert row["description"][:-3].split(" ")[-1] == "word"


def test_search_row_keeps_short_description_untouched():
    from cartograph.cli import _search_row
    row = _search_row({"id": "x", "description": "Short and sweet."})
    assert row["description"] == "Short and sweet."


def test_search_row_does_not_mutate_input():
    from cartograph.cli import _search_row
    original = {"id": "x", "trend": "up", "description": "d" * 999}
    _search_row(original)
    assert original["trend"] == "up"
    assert len(original["description"]) == 999


# ---------------------------------------------------------------------------
# show-unavailable: language-set scoping (cloud search parity with local).
#
# Local search filters unavailable languages out of the corpus at library-load
# time, so the index never contains them. Cloud rows bypass that path, so the
# CLI sends the client's available-language SET to the registry and the shared
# backend filters on it BEFORE ranking/truncation. These tests pin that the
# filter is pre-rank (fills top_k with installable widgets) and alias-tolerant.
# ---------------------------------------------------------------------------

def _retry_corpus(langs):
    """One identically-relevant 'retry' widget per language."""
    return [
        {"id": f"universal-retry-{l}", "name": "retry", "domain": "universal",
         "description": "retry backoff", "language": l, "weighted_rating": 0}
        for l in langs
    ]


def test_languages_filter_scopes_results():
    from cartograph.search import HybridBackend
    b = HybridBackend()
    b.build(_retry_corpus(["python", "go", "nim", "php", "typescript"]))
    res = b.query("retry", top_k=10, languages={"python", "go"})
    langs = {w["language"] for w in res["results"]}
    assert langs == {"python", "go"}


def test_languages_filter_is_pre_rank_fills_top_k():
    """The discriminator: with the cut smaller than the corpus and unavailable
    widgets interleaved through the ranking, a post-rank-after-truncation filter
    would under-fill. Pre-rank must return a full top_k of available widgets."""
    from cartograph.search import HybridBackend
    widgets = []
    for i in range(8):  # interleaved go, python, go, python, ...
        widgets.append({"id": f"universal-retry-go-{i}", "name": "retry",
                        "domain": "universal", "description": "retry",
                        "language": "go", "weighted_rating": 0})
        widgets.append({"id": f"universal-retry-py-{i}", "name": "retry",
                        "domain": "universal", "description": "retry",
                        "language": "python", "weighted_rating": 0})
    b = HybridBackend()
    b.build(widgets)
    res = b.query("retry", top_k=5, languages={"python"})
    assert [w["language"] for w in res["results"]] == ["python"] * 5


def test_languages_filter_normalizes_aliases():
    from cartograph.search import HybridBackend
    b = HybridBackend()
    b.build(_retry_corpus(["python", "typescript", "go"]))
    res = b.query("retry", top_k=10, languages={"py", "ts"})  # raw aliases
    assert {w["language"] for w in res["results"]} == {"python", "typescript"}


def test_languages_none_is_noop():
    from cartograph.search import HybridBackend
    corpus = _retry_corpus(["python", "go", "nim"])
    b = HybridBackend()
    b.build(corpus)
    assert len(b.query("retry", top_k=10)["results"]) == len(corpus)


def test_languages_filter_combines_with_single_language():
    from cartograph.search import HybridBackend
    b = HybridBackend()
    b.build(_retry_corpus(["python", "go"]))
    res = b.query("retry", top_k=10, language_filter="go", languages={"python", "go"})
    assert [w["language"] for w in res["results"]] == ["go"]


def test_apply_filters_languages_set():
    from cartograph.search.filters import apply_filters
    w = {"language": "python", "domain": "universal"}
    assert apply_filters(w, None, None, {"python", "go"}) is True
    assert apply_filters(w, None, None, {"go", "nim"}) is False
    # alias members are normalized
    assert apply_filters(w, None, None, {"py"}) is True
