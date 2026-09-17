"""
Field-weighted inverted index with fuzzy token resolution.

Query pipeline:
  1. Tokenize + stem (lightweight suffix stripping)
  2. Expand synonyms (flat synonym list, like Elasticsearch synonym filters)
  3. Fuzzy-resolve against known vocabulary (trigram similarity)
  4. Score via inverted index (field_weight * idf * coverage)

Inverted index keeps queries O(matches) not O(corpus). Fuzzy resolution
uses a trigram reverse index over the vocabulary for fast correction.

Zero external dependencies.
"""

from __future__ import annotations

import json
import math
import os
import re

_SYNONYMS_PATH = os.path.join(os.path.dirname(__file__), "synonyms.json")

try:
    with open(_SYNONYMS_PATH, encoding="utf-8") as f:
        _SYNONYMS: dict[str, list[str]] = json.load(f)
except Exception:
    _SYNONYMS = {}

# Field importance weights
_FIELD_WEIGHTS = {
    "id":          4.0,
    "name":        4.0,
    "tags":        3.0,
    "description": 1.0,
}

_FIELDS = list(_FIELD_WEIGHTS.keys())
_FIELD_WEIGHT_LIST = [_FIELD_WEIGHTS[f] for f in _FIELDS]

# Fuzzy resolution thresholds
_FUZZY_MIN_SIMILARITY = 0.45
_FUZZY_PREFIX_WEIGHT = 0.85
_FUZZY_CORRECTION_WEIGHT = 0.7

# Stemming - common suffixes to strip (longest first)
_SUFFIXES = (
    "ation", "tion", "sion", "ment", "ness", "ible", "able",
    "ying", "ing", "ies", "ers", "ous", "ive", "ful",
    "ed", "ly", "er", "or", "es", "al",
)
_MIN_STEM_LENGTH = 5


def _stem(token: str) -> str:
    """Lightweight suffix stripping."""
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM_LENGTH:
            return token[:-len(suffix)]
    return token


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _expand_synonyms(tokens: list[str]) -> list[str]:
    """Flat synonym expansion - same approach as Elasticsearch synonym filters."""
    expanded: list[str] = []
    for t in tokens:
        expanded.append(t)
        expanded.extend(_SYNONYMS.get(t, []))
    return expanded


def _trigrams(token: str) -> set[str]:
    if len(token) < 3:
        return {token} if token else set()
    return {token[i:i + 3] for i in range(len(token) - 3 + 1)}


def _extract_field(widget: dict, field: str) -> str:
    if field == "id":
        return widget.get("id", "").replace("-", " ")
    if field == "name":
        return widget.get("name", "")
    if field == "tags":
        return " ".join(widget.get("tags", []))
    if field == "description":
        return widget.get("description", "")
    return ""


class TFIDFBackend:
    """Field-weighted inverted index with synonym expansion and fuzzy resolution."""

    def __init__(self):
        self._widgets: list[dict] = []
        self._n: int = 0
        self._postings: dict[str, list[tuple[int, int]]] = {}
        self._idf: dict[str, float] = {}
        self._vocab: set[str] = set()
        self._trigram_index: dict[str, set[str]] = {}

    def build(self, widgets: list[dict]) -> None:
        self._widgets = widgets
        self._n = len(widgets)
        self._postings = {}

        if not widgets:
            self._idf = {}
            self._vocab = set()
            self._trigram_index = {}
            return

        doc_freq: dict[str, int] = {}

        for widget_idx, w in enumerate(widgets):
            widget_tokens: set[str] = set()

            for field_idx, field in enumerate(_FIELDS):
                text = _extract_field(w, field)
                stemmed = [_stem(t) for t in _tokenize(text)]

                seen_in_field: set[str] = set()
                for token in stemmed:
                    if token not in seen_in_field:
                        seen_in_field.add(token)
                        postings = self._postings.get(token)
                        if postings is None:
                            postings = []
                            self._postings[token] = postings
                        postings.append((widget_idx, field_idx))

                widget_tokens.update(seen_in_field)

            for token in widget_tokens:
                doc_freq[token] = doc_freq.get(token, 0) + 1

        n = self._n
        self._idf = {
            token: math.log(1 + n / df)
            for token, df in doc_freq.items()
        }

        self._vocab = set(self._postings.keys())
        self._trigram_index = {}
        for token in self._vocab:
            for tri in _trigrams(token):
                bucket = self._trigram_index.get(tri)
                if bucket is None:
                    bucket = set()
                    self._trigram_index[tri] = bucket
                bucket.add(token)

    def _resolve_token(self, query_token: str) -> list[tuple[str, float]]:
        """Resolve a query token against the vocabulary.

        Returns (resolved_token, weight) pairs:
          - Exact match: [(token, 1.0)]
          - Prefix matches: [(vocab_token, 0.85), ...]
          - Fuzzy correction: [(best_match, 0.7)]
          - No match: []
        """
        stemmed = _stem(query_token)

        if stemmed in self._vocab:
            return [(stemmed, 1.0)]

        # Prefix matching
        results: list[tuple[str, float]] = []
        if len(stemmed) >= 2:
            for vocab_token in self._vocab:
                if vocab_token.startswith(stemmed) and vocab_token != stemmed:
                    results.append((vocab_token, _FUZZY_PREFIX_WEIGHT))
        if results:
            return results

        # Fuzzy correction via trigram similarity
        query_tris = _trigrams(stemmed)
        if not query_tris:
            return []

        candidates: dict[str, int] = {}
        for tri in query_tris:
            for vocab_token in self._trigram_index.get(tri, ()):
                candidates[vocab_token] = candidates.get(vocab_token, 0) + 1

        best_token = None
        best_sim = 0.0
        for vocab_token, shared in candidates.items():
            vocab_tris = _trigrams(vocab_token)
            sim = shared / len(query_tris | vocab_tris)
            if sim > best_sim:
                best_sim = sim
                best_token = vocab_token

        if best_token and best_sim >= _FUZZY_MIN_SIMILARITY:
            return [(best_token, _FUZZY_CORRECTION_WEIGHT)]

        return []

    def score(self, query: str) -> list[float]:
        """Score every widget against the query.

        Pipeline: tokenize -> synonym expand -> stem -> resolve -> score.
        """
        if not self._widgets:
            return []

        raw_tokens = _tokenize(query)
        if not raw_tokens:
            return [0.0] * self._n

        # Synonym expansion before stemming (synonyms are in natural form)
        expanded = _expand_synonyms(raw_tokens)

        # Resolve each token against vocabulary (stem + fuzzy)
        resolved: list[tuple[str, float]] = []
        for token in expanded:
            resolved.extend(self._resolve_token(token))

        if not resolved:
            return [0.0] * self._n

        # Score via inverted index
        scores: dict[int, float] = {}
        matched_terms: dict[int, int] = {}
        n_query_terms = len(raw_tokens)

        for token, weight in resolved:
            idf = self._idf.get(token, 0.0)
            if idf == 0.0:
                continue
            postings = self._postings.get(token)
            if postings is None:
                continue

            token_score = idf * weight
            for widget_idx, field_idx in postings:
                contribution = _FIELD_WEIGHT_LIST[field_idx] * token_score
                scores[widget_idx] = scores.get(widget_idx, 0.0) + contribution
                matched_terms[widget_idx] = matched_terms.get(widget_idx, 0) + 1

        # Coverage bonus for multi-term queries
        if n_query_terms > 1:
            for widget_idx in scores:
                n_matched = min(matched_terms.get(widget_idx, 0), n_query_terms)
                coverage = (n_matched / n_query_terms) ** 0.5
                scores[widget_idx] *= coverage

        result = [0.0] * self._n
        for idx, s in scores.items():
            result[idx] = s
        return result

    def query(self, query, domain_filter=None, language_filter=None, top_k=15):
        from .filters import apply_filters, format_results
        scores = self.score(query)
        pairs = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results = []
        for idx, s in pairs:
            if s <= 0:
                break
            w = self._widgets[idx]
            if not apply_filters(w, domain_filter, language_filter):
                continue
            results.append({**w, "relevance_score": round(s, 4)})
            if len(results) >= top_k:
                break
        return format_results(results, corpus_size=len(self._widgets))
