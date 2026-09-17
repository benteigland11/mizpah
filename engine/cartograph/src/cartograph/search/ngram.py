"""
Character n-gram inverted index for fuzzy / typo-tolerant search.

Build pass produces posting lists keyed by gram plus a sidecar
`_field_sizes` map so the Jaccard denominator stays computable without
storing per-widget gram sets. Query work scales with the number of
widgets that share grams with the query, not the corpus size.

Field weights (higher = more important):
  id / name : 3.0
  tags      : 2.0
  description: 1.0
  source code: 0.3
"""

from __future__ import annotations

from collections import defaultdict


_FIELD_WEIGHTS = {
    "id":          3.0,
    "name":        3.0,
    "tags":        2.0,
    "description": 1.0,
    "code":        0.3,
}


def _ngrams(text: str, n: int) -> set[str]:
    """Return the set of character n-grams in text."""
    text = text.lower()
    if len(text) < n:
        return {text} if text else set()
    return {text[i: i + n] for i in range(len(text) - n + 1)}


def _field_ngrams(text: str, sizes=(2, 3)) -> set[str]:
    """Union of bigrams and trigrams for a piece of text."""
    grams: set[str] = set()
    for n in sizes:
        grams |= _ngrams(text, n)
    return grams


class NgramIndex:
    """Inverted n-gram index. O(query_grams * avg_posting_len) per query."""

    def __init__(self):
        self._widgets: list[dict] = []
        # gram -> list of (widget_idx, field)
        self._postings: dict[str, list[tuple[int, str]]] = {}
        # (widget_idx, field) -> |field_grams|, denominator for Jaccard
        self._field_sizes: dict[tuple[int, str], int] = {}

    def build(self, widgets: list[dict]) -> None:
        self._widgets = widgets
        postings: dict[str, list[tuple[int, str]]] = defaultdict(list)
        sizes: dict[tuple[int, str], int] = {}

        for widget_idx, w in enumerate(widgets):
            tags_text = " ".join(w.get("tags", []))
            field_texts = {
                "id":          w.get("id", ""),
                "name":        w.get("name", ""),
                "tags":        tags_text,
                "description": w.get("description", ""),
                "code":        w.get("_code_tokens", ""),
            }
            for field, text in field_texts.items():
                grams = _field_ngrams(text)
                if not grams:
                    continue
                sizes[(widget_idx, field)] = len(grams)
                for g in grams:
                    postings[g].append((widget_idx, field))

        self._postings = dict(postings)
        self._field_sizes = sizes

    def score(self, query: str) -> list[float]:
        """Return a weighted n-gram score for every widget."""
        n = len(self._widgets)
        if n == 0:
            return []

        query_grams = _field_ngrams(query)
        if not query_grams:
            return [0.0] * n

        intersections: dict[tuple[int, str], int] = defaultdict(int)
        for g in query_grams:
            for posting in self._postings.get(g, ()):
                intersections[posting] += 1

        if not intersections:
            return [0.0] * n

        q_size = len(query_grams)
        scores = [0.0] * n
        for (widget_idx, field), inter in intersections.items():
            f_size = self._field_sizes[(widget_idx, field)]
            union = q_size + f_size - inter
            if union <= 0:
                continue
            jaccard = inter / union
            scores[widget_idx] += _FIELD_WEIGHTS[field] * jaccard

        return scores
