"""The user's layer over the committed configs: what this machine chose, kept outside the repository.

The engine config and the harness config it names are checked in; they hold policy and defaults, and paths as
placeholders. Everything a person picks on their own machine — a local server added as a provider, a provider
override, the model a seat runs on — is written to one file under the user's config directory instead:

    $MIZPAH_CONFIG_HOME/config.json    (else $XDG_CONFIG_HOME/mizpah/config.json, else ~/.config/mizpah/config.json)
    {"engine": {"providers": {...}, ...}, "harness": {"worker": {...}, "controller": {...}, "deputy": {...}}}

A key in a section replaces the committed one whole (a seat is chosen as a unit; merging it key by key would bring
back what the choice removed); `engine.providers` is the exception, merged provider by provider.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


def path() -> Path:
    """The user's config file (it may not exist yet)."""
    home = os.environ.get('MIZPAH_CONFIG_HOME')
    if home:
        return Path(home).expanduser()/'config.json'
    base = os.environ.get('XDG_CONFIG_HOME') or '~/.config'
    return Path(base).expanduser()/'mizpah'/'config.json'


def load() -> dict[str, Any]:
    """The user's layer; empty when there is none."""
    try:
        raw = json.loads(path().read_text())
    except FileNotFoundError:
        return {}
    return raw if isinstance(raw, dict) else {}


def save(layer: dict[str, Any]) -> Path:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(layer, indent=2)+'\n')
    temporary.replace(target)
    return target


def section(name: str) -> dict[str, Any]:
    return dict(load().get(name) or {})


def update_section(name: str, changes: dict[str, Any]) -> Path:
    """Replace keys of one section (a value of None removes the key)."""
    layer = load()
    part = dict(layer.get(name) or {})
    for key, value in changes.items():
        if value is None:
            part.pop(key, None)
        else:
            part[key] = value
    layer[name] = part
    return save(layer)


def layered(committed: dict[str, Any], name: str) -> dict[str, Any]:
    """The committed section with the user's over it."""
    user = section(name)
    out = dict(committed)
    for key, value in user.items():
        if key == 'providers' and isinstance(value, dict):
            out['providers'] = dict(out.get('providers') or {}, **value)
        else:
            out[key] = value
    return out


def engine(config_path: str | Path | None) -> dict[str, Any]:
    """The engine config (the `mizpah` section) as this machine sees it; the user's layer alone without a file."""
    raw: dict[str, Any] = {}
    if config_path is not None:
        raw = json.loads(Path(config_path).read_text())
        raw = raw.get('mizpah', raw)
    return layered(raw, 'engine')


def harness(config_path: str | Path) -> tuple[dict[str, Any], Path]:
    """The harness config the engine config names, with the user's seats over it, and the committed file's path."""
    config_path = Path(config_path).resolve()
    raw = json.loads(config_path.read_text())
    harness_path = (config_path.parent/raw.get('mizpah', raw)['harness_config']).resolve()
    return layered(json.loads(harness_path.read_text()), 'harness'), harness_path


def places(config_path: str | Path, widget_library: str) -> dict[str, str]:
    """What the committed config's ${NAME} placeholders stand for on this machine."""
    return dict(REPO=str((Path(config_path).resolve().parent/'..'/'..').resolve()),
                VENV=sys.prefix, PYTHON_HOME=sys.base_prefix, PYTHON_LINK=python_link(),
                WIDGET_LIBRARY=str(Path(widget_library).expanduser()), HOME=str(Path.home()))


def python_link() -> str:
    """The interpreter home as the venv's `python` link names it. uv links a venv through a minor-version alias
    (cpython-3.12-… → cpython-3.12.13-…); the sandbox must see the path the link names as well as the real one."""
    link = Path(sys.prefix)/'bin'/'python'
    try:
        target = Path(os.readlink(link))
    except OSError:
        return sys.base_prefix
    return str(target.parent.parent) if target.is_absolute() else sys.base_prefix


def expand(value: Any, names: dict[str, str]) -> Any:
    """${NAME} and a leading ~ in every string of a JSON value."""
    if isinstance(value, str):
        for name, text in names.items():
            value = value.replace('${'+name+'}', text)
        return str(Path(value).expanduser()) if value.startswith('~') else value
    if isinstance(value, list):
        return [expand(item, names) for item in value]
    if isinstance(value, dict):
        return {key: expand(item, names) for key, item in value.items()}
    return value


def tool(name_or_path: str | None, default: str) -> str:
    """A program the engine runs: an absolute path as given, else the one beside this interpreter, else PATH."""
    given = name_or_path or default
    if os.path.isabs(given):
        return given
    beside = Path(sys.executable).parent/given
    if beside.exists():
        return str(beside)
    return shutil.which(given) or given
