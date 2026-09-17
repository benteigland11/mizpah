"""Resolve pinned dependency specs against a registry-lookup callable.

Given a list of pin specs (id + exact version) and a lookup callable
that maps an id to the version currently available in some registry,
classify each pin as one of:

  * ok               - found at the pinned version
  * version-mismatch - found, but at a different version
  * missing          - lookup returned None

Pure logic; no I/O, no registry-shape assumptions. Callers compose the
lookup themselves (single registry, prefix-routed multi-tier, cached
in-memory map - the resolver does not care). The resolver also makes
no semver assumptions: versions are compared as opaque strings, since
pinning policy is the consumer's concern.
"""

from dataclasses import dataclass
from typing import Callable, Iterable


class PinResolutionError(Exception):
    """Base error for pin resolution."""


class InvalidPinSpec(PinResolutionError):
    """Raised when a pin spec is missing required fields."""


@dataclass(frozen=True)
class ResolvedPin:
    """One pin's resolution result.

    `found` is the version returned by the lookup, or None when missing.
    `state` is one of: "ok", "version-mismatch", "missing".
    """

    id: str
    pinned: str
    found: str | None
    state: str


def resolve_pin(
    pin: dict,
    lookup: Callable[[str], str | None],
) -> ResolvedPin:
    """Resolve a single pin spec.

    `pin` must be a dict with string `id` and `version` fields. Any
    other keys are ignored, so callers can pass richer pin records
    without reshaping them first.
    """
    pin_id = pin.get("id")
    pinned = pin.get("version")
    if not isinstance(pin_id, str) or not pin_id:
        raise InvalidPinSpec(f"pin missing string 'id': {pin!r}")
    if not isinstance(pinned, str) or not pinned:
        raise InvalidPinSpec(f"pin {pin_id!r} missing string 'version'")

    found = lookup(pin_id)
    if found is None:
        state = "missing"
    elif found == pinned:
        state = "ok"
    else:
        state = "version-mismatch"
    return ResolvedPin(id=pin_id, pinned=pinned, found=found, state=state)


def resolve_pins(
    pins: Iterable[dict],
    lookup: Callable[[str], str | None],
) -> list[ResolvedPin]:
    """Resolve a list of pin specs in order. Order is preserved."""
    return [resolve_pin(p, lookup) for p in pins]


def partition(resolved: Iterable[ResolvedPin]) -> dict[str, list[ResolvedPin]]:
    """Group resolved pins by state for reporting/branching."""
    out: dict[str, list[ResolvedPin]] = {
        "ok": [], "version-mismatch": [], "missing": [],
    }
    for r in resolved:
        out.setdefault(r.state, []).append(r)
    return out


def all_ok(resolved: Iterable[ResolvedPin]) -> bool:
    """True iff every resolved pin is in the 'ok' state."""
    return all(r.state == "ok" for r in resolved)
