"""Cartograph - widget library manager for AI agents."""

from importlib.metadata import version as _pkg_version
from pathlib import Path

from .engine import Cartograph, LIBRARY_PATH


def _read_version() -> str:
    # Editable installs: prefer pyproject.toml when it sits next to the source
    # tree, so version bumps in the dev checkout are reflected immediately
    # without needing a reinstall to refresh dist-info metadata. Wheels
    # shipped to PyPI don't include pyproject.toml, so end users fall through
    # to importlib.metadata as normal.
    pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib
            with pyproject.open("rb") as f:
                return tomllib.load(f)["project"]["version"]
        except Exception:
            pass
    try:
        return _pkg_version("cartograph-cli")
    except Exception:
        return "0.0.0"


__version__ = _read_version()
__all__ = ["Cartograph", "LIBRARY_PATH", "__version__"]
