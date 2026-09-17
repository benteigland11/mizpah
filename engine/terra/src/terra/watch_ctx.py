"""Watch window context — probe owns the window; substrate injects deadline.

See docs/watch-duration.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def build_watch_ctx(meta: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Inject watch window semantics into ctx.

    - duration_s == 0 → snapshot (single shot)
    - duration_s  > 0 → window; probe must poll until deadline
    Substrate never re-invokes run() in a loop.
    """
    if meta.get("kind") != "watch":
        return {}
    try:
        duration = float(
            meta.get("duration_s") if meta.get("duration_s") is not None else 0
        )
    except (TypeError, ValueError):
        duration = 0.0
    if duration < 0:
        duration = 0.0

    out: dict[str, Any] = {
        "duration_s": duration,
        "watch_mode": "snapshot" if duration <= 0 else "window",
    }
    if dry_run or duration <= 0:
        return out

    deadline = datetime.now(timezone.utc) + timedelta(seconds=duration)
    out["deadline"] = (
        deadline.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    out["deadline_unix"] = deadline.timestamp()
    return out


def effective_run_timeout(meta: dict[str, Any], timeout_s: float) -> float:
    """Ensure process timeout allows a full watch window + slack."""
    base = float(timeout_s)
    if meta.get("kind") != "watch":
        return base
    try:
        duration = float(meta.get("duration_s") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        return max(base, duration + 5.0)
    return base
