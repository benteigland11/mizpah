"""
Hybrid TF-IDF + n-gram search backend.

Combines two complementary signals:
  - TF-IDF  : field-weighted term matching, good for natural-language queries
  - N-gram  : character-level fuzzy matching, good for typos, partial IDs, prefix queries

Final score = alpha * norm(tfidf) + beta * norm(ngram)

Both score vectors are normalised to [0, 1] before combining so neither
dominates by accident of scale. We weight n-gram slightly higher because
Cartograph queries tend to be short and ID-like.

Additionally, exact substring matches in name/id get a hard boost so that
"auth" always surfaces "Auth Middleware" near the top regardless of corpus
statistics.
"""

from __future__ import annotations

from .filters import apply_filters, format_results
from .ngram import NgramIndex
from .tfidf import TFIDFBackend

# Mix weights - must sum to 1.0
_ALPHA = 0.40   # TF-IDF contribution
_BETA  = 0.60   # N-gram contribution

# Score boost for exact substring match in name or id (added after normalisation)
_EXACT_BOOST = 0.30

# Rating nudge - small enough to never override relevance, large enough to
# break ties between equally relevant widgets.  weighted_rating is 0-5,
# so dividing by 5 normalises to [0, 1] before applying the weight.
_RATING_WEIGHT = 0.05

# Minimum combined score to appear in results - filters out weak/tangential matches
_MIN_SCORE = 0.10


def _normalise(scores: list[float]) -> list[float]:
    """Min-max normalise to [0, 1].

    When all scores are equal (including the single-widget case), return
    1.0 for any non-zero score so that the signal isn't silently discarded.
    """
    lo, hi = min(scores), max(scores)
    if hi == lo:
        # Preserve the signal: non-zero scores -> 1.0, true zeros stay 0.0
        return [1.0 if s > 0 else 0.0 for s in scores]
    span = hi - lo
    return [(s - lo) / span for s in scores]


class HybridBackend:
    def __init__(self):
        self._tfidf = TFIDFBackend()
        self._ngram = NgramIndex()
        self._widgets: list[dict] = []

    def build(self, widgets: list[dict]) -> None:
        self._widgets = widgets
        self._tfidf.build(widgets)
        self._ngram.build(widgets)

    def query(self, query: str, domain_filter: str | None = None,
              language_filter: str | None = None, top_k: int = 10,
              languages: set[str] | None = None) -> dict:
        if not self._widgets:
            return {"results": []}

        tfidf_raw = self._tfidf.score(query)
        ngram_raw = self._ngram.score(query)

        tfidf_norm = _normalise(tfidf_raw)
        ngram_norm = _normalise(ngram_raw)

        query_lower = query.lower()
        # Score everything that clears the query + language bar, ignoring
        # domain for now - we apply domain separately so we can tell an
        # empty domain result apart from a weak query.
        scored_all = []
        for idx, widget in enumerate(self._widgets):
            combined = _ALPHA * tfidf_norm[idx] + _BETA * ngram_norm[idx]

            # Hard boost for exact substring in name or id (per query term)
            name_lower = widget.get("name", "").lower()
            id_lower = widget.get("id", "").lower()
            for term in query_lower.split():
                if term in name_lower or term in id_lower:
                    combined += _EXACT_BOOST
                    break

            # Rating nudge - tiebreaker for equally relevant widgets
            wr = widget.get("weighted_rating", 0)
            if wr > 0:
                combined += _RATING_WEIGHT * (wr / 5.0)

            if combined < _MIN_SCORE:
                continue

            if not apply_filters(widget, None, language_filter, languages):
                continue

            scored_all.append({**widget, "relevance_score": round(combined, 4)})

        if domain_filter and domain_filter != "all":
            scored = [w for w in scored_all
                      if apply_filters(w, domain_filter, None)]
        else:
            scored = scored_all

        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        corpus_size = len(self._widgets)
        result = format_results(scored[:top_k], corpus_size=corpus_size)

        # Domain-scoped miss: universal is no longer folded into every
        # domain filter, so point the agent at it explicitly - but only
        # when universal widgets actually match the query.
        if (domain_filter and domain_filter not in ("all", "universal")
                and not result.get("results")):
            universal = sorted(
                (w for w in scored_all
                 if w.get("domain") == "universal"
                 or "universal" in (w.get("domains") or [])),
                key=lambda x: x["relevance_score"], reverse=True,
            )
            if format_results(universal[:top_k], corpus_size=corpus_size).get("results"):
                result["message"] = (
                    f"No '{domain_filter}' widgets matched. Universal widgets "
                    f"match this query - retry with --domain universal."
                )

        return result
