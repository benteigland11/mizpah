"""Corroboration — the second axis of evidence.

Repetition (n-ladder) proves *precision*: the same instrument, run again,
scatters less. It cannot catch systematic error. Corroboration proves
*truth-shaped agreement*: independent methods (distinct probes) arriving at
the same answer within a declared tolerance.

Ladder consequences (hard opinions):
  - ``high`` requires >=2 methods agreeing — repetition alone caps at ``med``
  - methods in *disagreement* are louder than absence: derived confidence
    collapses to ``low``, reads refuse, the gate fails

Computed from per-probe sample groups at recompute time; never stored as
independent truth.
"""

from __future__ import annotations

from typing import Any

TOLERANCE_KEY = "tolerance"


def parse_tolerance(spec: Any) -> tuple[str, float] | None:
    """``"5%"`` → ("rel", 0.05); ``"0.5"``/``0.5`` → ("abs", 0.5); None → None."""
    if spec is None or (isinstance(spec, str) and not spec.strip()):
        return None
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        value = float(spec)
        kind = "abs"
    else:
        s = str(spec).strip()
        if s.endswith("%"):
            kind = "rel"
            body = s[:-1].strip()
        else:
            kind = "abs"
            body = s
        try:
            value = float(body)
        except ValueError:
            raise ValueError(
                f"tolerance must be a number or 'N%', got {spec!r}"
            ) from None
        if kind == "rel":
            value = value / 100.0
    if value < 0:
        raise ValueError(f"tolerance must be >= 0, got {spec!r}")
    return kind, value


def compute_corroboration(
    by_probe: dict[str, dict[str, Any]],
    *,
    map_type: str,
    tolerance: Any = None,
) -> dict[str, Any]:
    """→ {methods, tolerance, spread, spread_rel, agree, verdicts?}.

    agree: True/False when judgeable; None when <2 methods or (numbers)
    no tolerance declared.
    """
    groups = {
        pid: g for pid, g in (by_probe or {}).items() if (g.get("n") or 0) > 0
    }
    methods = len(groups)
    out: dict[str, Any] = {
        "methods": methods,
        "tolerance": tolerance,
        "agree": None,
    }
    if methods < 2:
        return out

    if map_type == "boolean":
        verdicts = {
            pid: bool((g.get("rate") or 0.0) >= 0.5) for pid, g in groups.items()
        }
        out["verdicts"] = verdicts
        out["agree"] = len(set(verdicts.values())) == 1
        return out

    means = {pid: g.get("mean") for pid, g in groups.items()}
    vals = [m for m in means.values() if m is not None]
    if len(vals) < 2:
        return out
    spread = max(vals) - min(vals)
    center = sum(abs(v) for v in vals) / len(vals)
    out["spread"] = spread
    out["spread_rel"] = (spread / center) if center else None

    tol = parse_tolerance(tolerance)
    if tol is None:
        # >=2 methods but nothing declared — surface the spread, don't judge
        return out
    kind, limit = tol
    if kind == "rel":
        rel = out["spread_rel"]
        out["agree"] = bool(rel is not None and rel <= limit) if center else (
            spread <= 0
        )
    else:
        out["agree"] = spread <= limit
    return out


def corroboration_gate_high(stats: dict[str, Any]) -> tuple[bool, str]:
    """Can these stats support ``high``? (statistical bar checked elsewhere)"""
    corr = stats.get("corroboration") or {}
    methods = int(corr.get("methods") or 0)
    agree = corr.get("agree")
    if agree is False:
        if corr.get("accepted") is True:
            return (
                False,
                "spread accepted as uncertainty — high needs actual "
                "agreement within tolerance, not an accepted band",
            )
        return False, "methods disagree — resolve before any promotion"
    if methods < 2:
        return (
            False,
            "high needs a second independent probe (repetition proves "
            "precision, not truth) — link runs from another method",
        )
    if agree is None:
        return (
            False,
            "2+ methods but agreement not judgeable — declare "
            "`terra known tolerance <id> --within 5%`",
        )
    return True, ""


def methods_disagree(stats: dict[str, Any]) -> bool:
    """Unaccepted disagreement — the alarm state that blocks everything."""
    corr = stats.get("corroboration") or {}
    return corr.get("agree") is False and corr.get("accepted") is not True


# Above this relative spread, two "methods" reporting the same quantity are
# very unlikely to be measuring the same proposition.
UNJUDGED_SPREAD_REL = 0.10


def methods_unjudged(stats: dict[str, Any]) -> bool:
    """>=2 methods, NO tolerance declared, and they are far apart.

    Terra matches evidence on the quantity NAME alone, which bites in both
    directions. A prefix made a genuine second method invisible (methods stuck
    at 1); and the inverse is worse — two probes emitting `n_stale` over
    DIFFERENT denominators produced methods=2 and a reported value of 3.5, the
    mean of 7 and 0. A number describing nothing, and completely silent,
    because with no tolerance `agree` is None and nothing objects.

    Absent a tolerance we must not CLAIM disagreement (that is what
    methods_disagree is for), but staying quiet lets a meaningless average
    ride. Say that agreement is unjudgeable and that the mean is averaging
    across it.
    """
    corr = stats.get("corroboration") or {}
    if (corr.get("methods") or 0) < 2:
        return False
    if corr.get("agree") is not None or corr.get("tolerance") is not None:
        return False
    rel = corr.get("spread_rel")
    return isinstance(rel, (int, float)) and rel > UNJUDGED_SPREAD_REL


def spread_accepted(stats: dict[str, Any]) -> bool:
    corr = stats.get("corroboration") or {}
    return corr.get("agree") is False and corr.get("accepted") is True


def reconcile_accepted_spread(
    record: dict[str, Any], corr: dict[str, Any]
) -> dict[str, Any]:
    """Apply a recorded accept-spread decision to a fresh corroboration verdict.

    Mutates ``corr`` (accepted / accepted_reason) and returns the record —
    with ``accepted_spread`` dropped when agreement was actually reached
    (acceptance is obsolete once methods agree).
    """
    acc = record.get("accepted_spread")
    if not acc:
        return record
    agree = corr.get("agree")
    if agree is True:
        # methods now agree within tolerance — acceptance no longer needed
        record = dict(record)
        record.pop("accepted_spread", None)
        return record
    if agree is False:
        spread = corr.get("spread")
        accepted_at_spread = acc.get("spread")
        if (
            spread is not None
            and accepted_at_spread is not None
            and float(spread) > float(accepted_at_spread) + 1e-12
        ):
            corr["accepted"] = False
            corr["accepted_reason"] = (
                f"spread grew to {spread} beyond accepted "
                f"{accepted_at_spread} — re-review "
                f"(terra known accept-spread … --reason)"
            )
        else:
            corr["accepted"] = True
            corr["accepted_reason"] = acc.get("reason")
            corr["accepted_at"] = acc.get("at")
    return record


def method_band(by_probe: dict[str, dict[str, Any]]) -> list[float] | None:
    """[min, max] of per-method means (numbers only; None if <2 methods)."""
    means = [
        g.get("mean")
        for g in (by_probe or {}).values()
        if (g.get("n") or 0) > 0 and g.get("mean") is not None
    ]
    if len(means) < 2:
        return None
    return [float(min(means)), float(max(means))]
