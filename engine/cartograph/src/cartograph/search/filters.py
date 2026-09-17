"""
Shared filtering and result formatting used by all backends.
"""

from __future__ import annotations

from ..engine import normalize_language


def apply_filters(widget: dict, domain_filter: str | None,
                  language_filter: str | None,
                  languages: set[str] | None = None) -> bool:
    """Return True if the widget passes all active filters.

    `language_filter` is the single-language `--language` narrowing.
    `languages`, if given, is a set of canonical languages the widget's
    language must intersect (machine-availability scoping for the
    show-unavailable config). Both may be active at once; the widget must
    satisfy each.
    """
    if domain_filter and domain_filter != "all":
        # Blueprints carry a multi-valued `domains` list (union of dep
        # widget domains). Widgets carry single-valued `domain`. Either
        # form satisfies the filter as long as the requested domain is
        # listed. Universal is NOT folded in automatically - a domain
        # filter means that domain only. Callers that come up empty are
        # nudged toward `--domain universal` (see HybridBackend.query).
        domains = widget.get("domains") or []
        single = widget.get("domain", "")
        if domain_filter not in domains and single != domain_filter:
            return False

    if language_filter or languages:
        w_lang = widget.get("language", "")
        if isinstance(w_lang, list):
            w_langs = {normalize_language(l) for l in w_lang}
        else:
            w_langs = {normalize_language(l)
                       for l in str(w_lang).replace(",", " ").replace("/", " ").split()}
        if language_filter and normalize_language(language_filter) not in w_langs:
            return False
        if languages:
            # Normalize set members so callers can pass raw aliases (e.g. the
            # server receives "py"/"ts" straight off the query string).
            allowed = {normalize_language(l) for l in languages}
            if w_langs.isdisjoint(allowed):
                return False

    return True


# Results at or below this score had no query term in any widget name/id.
# It's the max possible combined score without the exact-match boost (see hybrid.py).
# Below it = normalized noise; above it = at least one term found in name or id.
_EXACT_BOOST_THRESHOLD = 1.0

# When no exact match exists, cap results to avoid token bloat on noise.
_LOW_CONFIDENCE_LIMIT = 3

# --- Small-corpus bypass ---
# With very small libraries (< 10 widgets), IDF-based scoring can behave
# oddly since most terms appear in a large fraction of docs. Below this
# size we skip the strict threshold and return anything that passed the
# backend's own _MIN_SCORE (0.10). Once the library grows past this point,
# the full threshold kicks in automatically.
_SMALL_CORPUS_THRESHOLD = 10


def format_results(scored_widgets: list[dict], corpus_size: int = 0) -> dict:
    """Format scored widgets into a flat results list.

    If the top score exceeds _EXACT_BOOST_THRESHOLD, at least one query term
    appeared in a widget name or id — return up to top_k results.
    For small corpora (< _SMALL_CORPUS_THRESHOLD), return whatever the
    backend matched — BM25 IDF is unreliable at that scale.
    """
    results = []
    for res in scored_widgets:
        entry = {
            "id": res.get("namespaced_id") or res["id"],
            "name": res["name"],
            "version": res.get("version", "0.0.0"),
            "description": res["description"],
            "language": res.get("language", "unknown"),
            "domain": res["domain"],
            "dependencies": res.get("dependencies", []),
            "rating": res.get("rating", 0),
            "trend": res.get("trend") or "insufficient data",
            "install_count": res.get("install_count", 0),
            "relevance_score": res["relevance_score"],
            "kind": res.get("type", "widget"),
        }
        # Blueprints expose their union-of-dep-domains so an agent can see
        # cross-domain coverage at a glance.
        if res.get("type") == "blueprint" and res.get("domains"):
            entry["domains"] = list(res["domains"])
        results.append(entry)

    if corpus_size > 0 and corpus_size < _SMALL_CORPUS_THRESHOLD:
        return {"results": results}

    top_score = scored_widgets[0]["relevance_score"] if scored_widgets else 0
    if top_score <= _EXACT_BOOST_THRESHOLD:
        return {"results": [], "message": "No results found. Try broader or simpler terms — single keywords, synonyms, or the core concept."}
    # High confidence: still apply a floor to drop noise below the exact match
    return {"results": [r for r in results if r["relevance_score"] >= _EXACT_BOOST_THRESHOLD * 0.3]}
