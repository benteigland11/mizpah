"""Global case store: the platform data dir (XDG_DATA_HOME on Linux), one JSON file per case."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from casebook.app_paths import resolve_app_paths
from casebook.atomic_write.atomic_file_write import atomic_write_text

APP_NAME = "casebook"


def cases_dir() -> Path:
    return resolve_app_paths(APP_NAME).data_dir / "cases"


def case_path(case_id: str) -> Path:
    return cases_dir() / f"{case_id}.json"


def load_all() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    root = cases_dir()
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            case = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(case, dict):
            out[path.stem] = case
    return out


def load(case_id: str) -> dict[str, Any]:
    path = case_path(case_id)
    if not path.exists():
        raise KeyError(f"no case {case_id!r}")
    return json.loads(path.read_text())


def save(case: dict[str, Any]) -> Path:
    path = case_path(case["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(case, indent=2, ensure_ascii=False) + "\n")
    return path
