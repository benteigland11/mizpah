"""Best-effort CLI update recommendations.

The check is intentionally advisory: it never upgrades code on the user's
machine, never writes to stdout, and never blocks command execution.
"""

import json
import os
import re
import sys
import time
import urllib.request
from importlib.metadata import version as package_version

_PACKAGE_NAME = "cartograph-cli"
_PYPI_URL = f"https://pypi.org/pypi/{_PACKAGE_NAME}/json"
_CACHE_SECONDS = 24 * 60 * 60
_REQUEST_TIMEOUT = 1.5


def _cache_path() -> str:
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), "update_check.json")


def _current_version() -> str:
    try:
        return package_version(_PACKAGE_NAME)
    except Exception:
        from . import __version__
        return __version__


def _version_key(value: str) -> tuple:
    """Return a sortable key for normal release versions.

    This is deliberately small and dependency-free. It handles the public
    release shape this project uses, including a leading "v" and suffixes
    like "1.2.3rc1" by treating prereleases as lower than the final release.
    """
    text = str(value).strip().lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+){0,2})(.*)$", text)
    if not match:
        return (0, 0, 0, -1)
    nums = [int(part) for part in match.group(1).split(".")]
    nums = (nums + [0, 0, 0])[:3]
    suffix = match.group(2).strip()
    stability = 0 if suffix in ("", ".post0") else -1
    return (*nums, stability)


def _is_newer(latest: str, current: str) -> bool:
    return _version_key(latest) > _version_key(current)


def _read_cache(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        if now - float(data.get("checked_at", 0)) < _CACHE_SECONDS:
            return data
    except Exception:
        pass
    return {}


def _write_cache(data: dict) -> None:
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _fetch_latest_version() -> str | None:
    req = urllib.request.Request(
        _PYPI_URL,
        headers={"Accept": "application/json", "User-Agent": f"cartograph/{_current_version()}"},
    )
    from .net_tls import registry_ssl_context
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT, context=registry_ssl_context()) as resp:
        data = json.loads(resp.read())
    latest = data.get("info", {}).get("version")
    return latest if isinstance(latest, str) and latest else None


def _latest_version(now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    cached = _read_cache(now=now)
    latest = cached.get("latest_version")
    if isinstance(latest, str) and latest:
        return latest
    if cached:
        return None

    payload = {"checked_at": now}
    try:
        latest = _fetch_latest_version()
        if latest:
            payload["latest_version"] = latest
            return latest
    except Exception as exc:
        payload["error"] = str(exc)
        return None
    finally:
        try:
            _write_cache(payload)
        except Exception:
            pass


def maybe_recommend_update(argv: list[str] | None = None) -> None:
    """Print an update recommendation to stderr when a newer CLI exists."""
    argv = list(sys.argv[1:] if argv is None else argv)

    if os.environ.get("CARTOGRAPH_NO_UPDATE_CHECK"):
        return
    if not sys.stderr.isatty() and not os.environ.get("CARTOGRAPH_FORCE_UPDATE_CHECK"):
        return
    if argv and argv[0] in {"config", "doctor", "login", "logout", "setup", "whoami"}:
        return
    if any(arg in ("-h", "--help", "-v", "--version") for arg in argv):
        return

    try:
        from .config import load_config
        if not load_config().get("library", {}).get("auto_update", True):
            return

        current = _current_version()
        latest = _latest_version()
        if latest and _is_newer(latest, current):
            print(
                f"Cartograph {latest} is available; you are running {current}. "
                "Update with: python -m pip install --upgrade cartograph-cli "
                "(disable with: cartograph config auto-update false)",
                file=sys.stderr,
            )
    except Exception:
        return
