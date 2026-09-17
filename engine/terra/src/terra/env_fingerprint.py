"""Environment fingerprint for data provenance."""

from __future__ import annotations

import os
import platform
import socket
from pathlib import Path
from typing import Any


def collect_fingerprint(
    *,
    cwd: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort local env snapshot. Must be non-empty after collect."""
    cwd = (cwd or Path.cwd()).resolve()
    fp: dict[str, Any] = {
        "cwd": str(cwd),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pid": os.getpid(),
    }
    # Optional enrichments — skip if missing/empty
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user:
        fp["user"] = user
    if extra:
        fp["extra"] = dict(extra)
    return fp
