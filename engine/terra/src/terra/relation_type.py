"""Relation type — F(x) measured as a curve, stored as a known.

A relation sample is an (x, y) pair; probes emit them as ordinary measures
with an ``x`` field:

  {"quantity": "cl", "x": 4.0, "value": 0.62}

One sweep run emits many points. Stats aggregate per **x-station** (exact x
match — sweeps should share a grid): each station carries its own n/mean/std
so scatter at a station is visible.

Ladder unit is **sweeps** (runs contributing points), not raw points: one
dense sweep is still a single observation of the curve.
  low: >=1 point   med: >=3 sweeps and >=3 stations
  high: >=5 sweeps, >=3 stations, tight stations, and corroboration
  (>=2 methods agreeing at shared stations)

Evaluation: linear interpolation between station means; reads outside the
measured x_range fail loudly (no extrapolation).
"""

from __future__ import annotations

from typing import Any


def extract_relation_pairs_from_run_meta(
    run_meta: dict[str, Any],
    *,
    quantity: str | None = None,
) -> list[tuple[float, float]]:
    """(x, y) pairs from measures rows carrying an ``x`` field."""
    pairs: list[tuple[float, float]] = []
    raw = run_meta.get("measures")
    if not isinstance(raw, list):
        return pairs
    for item in raw:
        if not isinstance(item, dict):
            continue
        q = item.get("quantity")
        if quantity is not None and q is not None and q != quantity:
            continue
        x = item.get("x")
        v = item.get("value")
        if (
            isinstance(x, (int, float))
            and not isinstance(x, bool)
            and isinstance(v, (int, float))
            and not isinstance(v, bool)
        ):
            pairs.append((float(x), float(v)))
    return pairs


def compute_relation_stats(
    pairs: list[tuple[float, float]],
    *,
    sweeps: int = 0,
) -> dict[str, Any]:
    """Per-station aggregation. ``n`` = sweeps (ladder unit), not points."""
    import statistics

    by_x: dict[float, list[float]] = {}
    for x, y in pairs:
        by_x.setdefault(float(x), []).append(float(y))
    stations = []
    for x in sorted(by_x):
        ys = by_x[x]
        stations.append(
            {
                "x": x,
                "n": len(ys),
                "mean": float(statistics.fmean(ys)),
                "std": float(statistics.stdev(ys)) if len(ys) >= 2 else None,
            }
        )
    return {
        "kind": "relation",
        "n": int(sweeps),
        "points": len(pairs),
        "stations": stations,
        "station_count": len(stations),
        "x_range": [stations[0]["x"], stations[-1]["x"]] if stations else None,
    }


def empty_relation_stats() -> dict[str, Any]:
    return compute_relation_stats([], sweeps=0)


def stations_tight(stats: dict[str, Any]) -> bool:
    """Every station with repetition has std/|mean| <= 0.5 (or std 0 at mean 0)."""
    for st in stats.get("stations") or []:
        std = st.get("std")
        mean = st.get("mean")
        if std is None or mean is None:
            continue
        if mean == 0:
            if std != 0:
                return False
        elif abs(std / mean) > 0.5:
            return False
    return True


def derive_confidence_relation(stats: dict[str, Any]) -> str:
    from .corroboration import corroboration_gate_high, methods_disagree

    if methods_disagree(stats):
        return "low"
    points = int(stats.get("points") or 0)
    sweeps = int(stats.get("n") or 0)
    station_count = int(stats.get("station_count") or 0)
    if points < 1:
        return "low"
    if (
        sweeps >= 5
        and station_count >= 3
        and stations_tight(stats)
        and corroboration_gate_high(stats)[0]
    ):
        return "high"
    if sweeps >= 3 and station_count >= 3:
        return "med"
    return "low"


def evaluate_relation(stats: dict[str, Any], x: float) -> float:
    """F(x): station mean at x, else linear interp; loud outside x_range."""
    stations = [
        s
        for s in stats.get("stations") or []
        if s.get("mean") is not None
    ]
    if not stations:
        raise ValueError("relation has no measured stations")
    x = float(x)
    xs = [s["x"] for s in stations]
    lo, hi = min(xs), max(xs)
    if x < lo or x > hi:
        raise ValueError(
            f"x={x} is outside the measured x_range [{lo}, {hi}] — no "
            f"extrapolation; run a sweep that covers it"
        )
    for s in stations:
        if s["x"] == x:
            return float(s["mean"])
    below = max((s for s in stations if s["x"] < x), key=lambda s: s["x"])
    above = min((s for s in stations if s["x"] > x), key=lambda s: s["x"])
    frac = (x - below["x"]) / (above["x"] - below["x"])
    return float(below["mean"] + frac * (above["mean"] - below["mean"]))


def relation_corroboration(
    by_probe: dict[str, dict[str, Any]],
    *,
    tolerance: Any = None,
) -> dict[str, Any]:
    """Methods agree iff every SHARED station is within tolerance.

    Needs >=2 shared stations to judge; otherwise agree=None (different
    grids are not evidence either way — use a shared sweep grid).
    """
    from .corroboration import parse_tolerance

    groups = {
        pid: g
        for pid, g in (by_probe or {}).items()
        if (g.get("points") or 0) > 0
    }
    methods = len(groups)
    out: dict[str, Any] = {
        "methods": methods,
        "tolerance": tolerance,
        "agree": None,
    }
    if methods < 2:
        return out

    station_means: dict[str, dict[float, float]] = {
        pid: {
            s["x"]: s["mean"]
            for s in g.get("stations") or []
            if s.get("mean") is not None
        }
        for pid, g in groups.items()
    }
    shared = set.intersection(*(set(m) for m in station_means.values()))
    out["shared_stations"] = len(shared)
    if len(shared) < 2:
        return out

    worst_spread = 0.0
    worst_rel: float | None = 0.0
    for x in shared:
        means = [station_means[pid][x] for pid in station_means]
        spread = max(means) - min(means)
        center = sum(abs(m) for m in means) / len(means)
        if spread > worst_spread:
            worst_spread = spread
        rel = (spread / center) if center else None
        if rel is None:
            worst_rel = None
        elif worst_rel is not None and rel > worst_rel:
            worst_rel = rel
    out["spread"] = worst_spread
    out["spread_rel"] = worst_rel

    tol = parse_tolerance(tolerance)
    if tol is None:
        return out
    kind, limit = tol
    if kind == "rel":
        out["agree"] = worst_rel is not None and worst_rel <= limit
    else:
        out["agree"] = worst_spread <= limit
    return out
