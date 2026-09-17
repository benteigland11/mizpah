"""Recommended probe/run status vocabulary — convention, not a hard enum.

Level-1 only requires a non-empty string. Map tooling can filter better when
probes use these values (or map custom strings in docs).
"""

from __future__ import annotations

from typing import Any

# Recommended statuses for filterable map tooling
RECOMMENDED_STATUSES = frozenset(
    {
        "ok",  # survey succeeded with usable evidence
        "degraded",  # partial evidence; usable with care
        "unavailable",  # world / server / instrument down
        "empty",  # succeeded; nothing in scope
        "error",  # instrument failed
    }
)

STATUS_MEANINGS: dict[str, str] = {
    "ok": "survey succeeded",
    "degraded": "partial evidence",
    "unavailable": "world/server/instrument down",
    "empty": "succeeded, nothing in scope",
    "error": "instrument failed",
}


def warn_status_vocab(status: Any, *, live: bool = True) -> list[str]:
    """Warn-only: free strings stay legal; nudge toward shared vocabulary."""
    if not live:
        return []
    if not isinstance(status, str) or not status.strip():
        return []  # hard bar lives in level-1 / run validate
    s = status.strip()
    if s in RECOMMENDED_STATUSES:
        return []
    # Custom statuses are fine — soft note for tooling composition
    return [
        f"status {s!r} is freeform (recommended: "
        f"{sorted(RECOMMENDED_STATUSES)} — see docs/status-vocab.md). "
        "Map filters work best with the shared set."
    ]


def normalize_status_filter(raw: str) -> str:
    return raw.strip().lower()
