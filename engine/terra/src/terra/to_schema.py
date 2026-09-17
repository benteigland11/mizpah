"""Recommended `to` (target) schema — convention, not a domain funnel.

Probes may ignore extra keys. Live runs **warn** (never block) when the
recommended envelope is thin — especially missing `kind`.
"""

from __future__ import annotations

from typing import Any

# Open set of *recommended* kinds. Unknown kinds are fine (custom probes).
RECOMMENDED_TO_KINDS = frozenset(
    {
        "entity",
        "region",
        "path",
        "server",
        "literal",
        "default",
    }
)

RECOMMENDED_TO_EXAMPLE: dict[str, Any] = {
    "kind": "entity|region|path|server|literal|default",
    "id": "…",
    "at": "ISO-8601 optional wall intent",
    "window": {"day_phase": "night|day|any"},
    "limit": 50,
}


def warn_to_shape(
    to: Any,
    *,
    live: bool = True,
    which: str = "input",
) -> list[str]:
    """Return warn-only messages for a `to` value.

    Hard emptiness is enforced elsewhere (level-1). This only nudges
    multi-probe composition toward a shared envelope.
    """
    if not live:
        return []

    warnings: list[str] = []
    prefix = f"to/{which}"

    if not isinstance(to, dict):
        warnings.append(
            f"{prefix}: recommended shape is a JSON object with at least "
            f"'kind' (got {type(to).__name__}). See docs/to-schema.md"
        )
        return warnings

    kind = to.get("kind")
    if kind is None or (isinstance(kind, str) and not kind.strip()):
        warnings.append(
            f"{prefix}: missing recommended key 'kind' "
            f"(one of {sorted(RECOMMENDED_TO_KINDS)} or a project-specific kind). "
            "Shared kinds make multi-probe composition easier."
        )
    elif isinstance(kind, str) and kind not in RECOMMENDED_TO_KINDS:
        # Custom kinds are allowed — soft note only
        warnings.append(
            f"{prefix}: kind {kind!r} is project-specific "
            f"(recommended builtins: {sorted(RECOMMENDED_TO_KINDS)})"
        )

    # Soft tips when common companions are odd types (never required)
    if "limit" in to and to["limit"] is not None:
        if not isinstance(to["limit"], int) or isinstance(to["limit"], bool):
            warnings.append(f"{prefix}: 'limit' is usually a positive int")
        elif to["limit"] < 0:
            warnings.append(f"{prefix}: 'limit' is usually >= 0")

    if "window" in to and to["window"] is not None:
        if not isinstance(to["window"], dict):
            warnings.append(f"{prefix}: 'window' is usually an object, e.g. {{day_phase: …}}")
        else:
            phase = to["window"].get("day_phase")
            if phase is not None and phase not in ("night", "day", "any"):
                warnings.append(
                    f"{prefix}: window.day_phase usually night|day|any "
                    f"(got {phase!r})"
                )

    if "at" in to and to["at"] is not None and not isinstance(to["at"], str):
        warnings.append(f"{prefix}: 'at' is usually an ISO-8601 string")

    return warnings
