"""
Cartograph configuration.

Reads global config from <data_dir>/config.toml. Falls back to defaults
if the file is missing or malformed.

Example config.toml:

    [publish]
    auto_publish = true
    visibility = "public"
    governance = "open"
"""

import json
import os
import urllib.error
import urllib.request

from .net_tls import registry_ssl_context

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

# Public registry is hardcoded - never queried for /info
_PUBLIC_REGISTRY_URL = "https://api.cartograph.tools"
_PUBLIC_REGISTRY_PREFIX = "cg"

_DEFAULTS = {
    "library": {
        "show_unavailable": True,
        "cloud": True,
        "auto_update": True,
        "search_priority": "local",
    },
    "publish": {
        "auto_publish": False,
        "visibility": "public",
        "governance": "protected",
    },
}

# Flat CLI key -> (section, toml_key, type, choices, description)
_SCHEMA = {
    "auto-publish": ("publish", "auto_publish", "bool", None,
                     "Auto-publish to cloud on every checkin"),
    "visibility":   ("publish", "visibility", "str", ["public", "private"],
                     "Default visibility for published widgets"),
    "governance":   ("publish", "governance", "str", ["open", "protected"],
                     "Default contribution governance model"),
    "cloud":            ("library", "cloud", "bool", None,
                         "Enable cloud registry integration"),
    "auto-update":      ("library", "auto_update", "bool", None,
                         "Check for new Cartograph CLI releases and recommend upgrades"),
    "show-unavailable": ("library", "show_unavailable", "bool", None,
                         "Show widgets for languages not installed on this machine"),
    "publish-registry": ("publish", "registry", "str", None,
                         "Default registry prefix for --publish on local widgets (e.g. myorg). "
                         "Defaults to the public registry (cg) if not set. "
                         "Also ranks that registry first in search results."),
    "search-priority":  ("library", "search_priority", "str", ["local", "registry"],
                         "Which pool fills the search budget first: your local library "
                         "or the registry pool"),
}


def cloud_enabled() -> bool:
    """Check if cloud registry integration is enabled."""
    return load_config().get("library", {}).get("cloud", True)


def _config_path() -> str:
    """Return path to global config.toml."""
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), "config.toml")


def load_config() -> dict:
    """Load global config.toml, merging with defaults."""
    config = {section: dict(values) for section, values in _DEFAULTS.items()}

    path = _config_path()
    if not os.path.isfile(path) or tomllib is None:
        return config

    try:
        with open(path, "rb") as f:
            user = tomllib.load(f)
    except Exception:
        return config

    for section, values in user.items():
        if section in config and isinstance(values, dict):
            config[section].update(values)
        else:
            config[section] = values

    return config


def _is_path_key(key: str) -> tuple[bool, str]:
    """Recognize the dynamic 'paths.<binary>' namespace.

    Returns (is_path_key, binary_name). Binary names are free-form so we
    can't enumerate them in _SCHEMA; instead we validate the prefix and
    let the [paths] section in config.toml store whatever the user sets.
    """
    if not key.startswith("paths."):
        return False, ""
    binary = key[len("paths."):]
    return bool(binary) and "/" not in binary and "\\" not in binary, binary


def get_path_override(binary_name: str) -> str | None:
    """Return the configured override path for a binary, or None.

    Reads the [paths] section of config.toml. The resolver uses this to
    short-circuit PATH lookup when the user has explicitly pointed at a
    binary on disk.
    """
    config = load_config()
    paths = config.get("paths", {})
    if not isinstance(paths, dict):
        return None
    value = paths.get(binary_name)
    return value if isinstance(value, str) and value else None


def get_value(key: str):
    """Get a config value by flat key (e.g. 'auto-publish')."""
    is_path, binary = _is_path_key(key)
    if is_path:
        return get_path_override(binary), None
    if key not in _SCHEMA:
        return None, f"Unknown setting: '{key}'. Run 'cartograph config list' to see available settings."
    section, toml_key, _, _, _ = _SCHEMA[key]
    config = load_config()
    value = config.get(section, {}).get(toml_key)
    # publish-registry defaults to the public registry (cg) when unset, so
    # readers see the effective target instead of an ambiguous empty string.
    if key == "publish-registry" and not value:
        value = _PUBLIC_REGISTRY_PREFIX
    return value, None


def set_value(key: str, raw_value: str):
    """Set a config value by flat key. Writes config.toml."""
    is_path, binary = _is_path_key(key)
    if is_path:
        value = raw_value.strip()
        if not value:
            return "Path override cannot be empty. Use 'cartograph config unset paths.<binary>' to clear."
        config = load_config()
        config.setdefault("paths", {})
        config["paths"][binary] = value
        path = _config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write_toml(path, config)
        return None
    if key not in _SCHEMA:
        return f"Unknown setting: '{key}'. Run 'cartograph config list' to see available settings."
    section, toml_key, typ, choices, _ = _SCHEMA[key]

    if typ == "bool":
        if raw_value.lower() in ("true", "1", "yes"):
            value = True
        elif raw_value.lower() in ("false", "0", "no"):
            value = False
        else:
            return f"Invalid boolean: '{raw_value}'. Use true or false."
    else:
        value = raw_value

    if choices and value not in choices:
        return f"Invalid value: '{value}'. Choose from: {', '.join(choices)}"

    config = load_config()
    if section not in config:
        config[section] = {}
    config[section][toml_key] = value

    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_toml(path, config)
    return None


_DISPLAY_DEFAULTS = {
    # Keys whose effective default differs from _DEFAULTS (usually because
    # `None` has a special "fall back to public" meaning in the code but is
    # confusing to display). Surface the human-facing default here.
    "publish-registry": _PUBLIC_REGISTRY_PREFIX,
}


def visibility_has_effect() -> tuple[bool, str]:
    """Whether the `visibility` setting is currently load-bearing.

    The `cg` community registry treats every widget as public, so setting
    visibility while publish-registry=cg is a no-op. Self-hosted and
    third-party registries honor it normally. Returns (has_effect, active
    publish-registry prefix) so callers can phrase the right warning.
    """
    config = load_config()
    target = config.get("publish", {}).get("registry") or _PUBLIC_REGISTRY_PREFIX
    return (target != _PUBLIC_REGISTRY_PREFIX, target)


def _known_engine_binaries() -> list[tuple[str, str]]:
    """Return (binary_name, owning_engine_name) pairs for every binary any
    registered engine declares in its toolchain. Used by list_values() to
    advertise discoverable paths.<binary> overrides even before the user
    sets them."""
    try:
        from .languages.registry import supported_languages, get_engine
    except Exception:
        return []
    pairs = []
    seen = set()
    for lang in supported_languages():
        engine = get_engine(lang)
        if engine is None:
            continue
        for binary in getattr(engine, "toolchain", {}) or {}:
            if binary in seen:
                continue
            seen.add(binary)
            pairs.append((binary, lang))
    return sorted(pairs)


def config_keys() -> list[str]:
    """Return all settable config keys (excluding the dynamic paths.* namespace).

    Public accessor so downstream surfaces (MCP server, agent tooling) can
    derive their key enums from the CLI instead of hardcoding a copy that
    drifts when new settings land.
    """
    return sorted(_SCHEMA)


def list_values() -> list[dict]:
    """Return all config keys with current values and defaults."""
    config = load_config()
    result = []
    for key in sorted(_SCHEMA):
        section, toml_key, typ, choices, description = _SCHEMA[key]
        current = config.get(section, {}).get(toml_key)
        default = _DEFAULTS.get(section, {}).get(toml_key)
        if default is None and key in _DISPLAY_DEFAULTS:
            default = _DISPLAY_DEFAULTS[key]
        result.append({
            "key": key,
            "value": current,
            "default": default,
            "type": typ,
            "choices": choices,
            "description": description,
        })
    # paths.<binary> namespace. Two sources:
    #   1. binaries every registered engine declares — show up even when
    #      unset so agents/humans can discover the override surface.
    #   2. extra binaries the user has already overridden that aren't in any
    #      engine's toolchain (unusual, but don't hide them).
    paths = config.get("paths", {}) or {}
    engine_binaries = _known_engine_binaries()
    advertised = set()
    for binary, lang in engine_binaries:
        advertised.add(binary)
        result.append({
            "key": f"paths.{binary}",
            "value": paths.get(binary),
            "default": None,
            "type": "path",
            "choices": None,
            "description": f"Override PATH lookup for '{binary}' ({lang} engine)",
        })
    for binary in sorted(paths):
        if binary in advertised:
            continue
        result.append({
            "key": f"paths.{binary}",
            "value": paths[binary],
            "default": None,
            "type": "path",
            "choices": None,
            "description": f"Override PATH lookup for '{binary}' during validation and scanning",
        })
    return result


def _write_toml(path: str, config: dict):
    """Write config dict as TOML (minimal writer - no dependency needed)."""
    lines = []
    for section, values in sorted(config.items()):
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in sorted(values.items()):
            if v is None:
                continue
            elif isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, str):
                # Escape backslashes and quotes so Windows paths like
                # C:\Program Files\Nim\bin\nim.exe round-trip correctly.
                escaped = v.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{k} = "{escaped}"')
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Registry management
# ---------------------------------------------------------------------------

def _registries_path() -> str:
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), "registries.json")


def get_registries() -> list[dict]:
    """Return user-configured registries (excludes the public Cartograph registry)."""
    try:
        with open(_registries_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_registries(registries: list[dict]) -> None:
    path = _registries_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registries, f, indent=2)


def _fetch_registry_info(url: str) -> dict:
    """Fetch /info from a registry. Returns dict with at least 'prefix', or {'error': ...}."""
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/info",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5, context=registry_ssl_context()) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"Registry /info returned HTTP {e.code}"}
    except Exception as e:
        return {"error": f"Could not reach registry: {e}"}


def add_registry(url: str, prefix: str | None = None) -> tuple[str | None, str | None, bool]:
    """Add a registry. Auto-fetches prefix from /info if not provided.

    Returns (prefix, error, needs_prefix).
    - Success:      (prefix, None, False)
    - Hard error:   (None, message, False)  e.g. duplicate, reserved prefix
    - Soft failure: (None, message, True)   /info unreachable or missing prefix field
    """
    url = url.rstrip("/")

    if url == _PUBLIC_REGISTRY_URL:
        return None, "That is the public Cartograph registry (prefix: cg). It is always available.", False

    # Always try /info first - server's prefix wins over any user-provided value
    info = _fetch_registry_info(url)
    if "error" not in info:
        server_prefix = info.get("prefix")
        if server_prefix:
            prefix = server_prefix  # server wins, even if --prefix was passed
    elif prefix is None:
        # /info unreachable and no fallback prefix provided
        return None, info["error"], True

    if not prefix:
        return None, f"Registry at {url} did not return a prefix.", True

    if prefix == _PUBLIC_REGISTRY_PREFIX:
        return None, f"Prefix '{_PUBLIC_REGISTRY_PREFIX}' is reserved for the public Cartograph registry.", False

    registries = get_registries()
    for reg in registries:
        if reg["prefix"] == prefix:
            return None, f"Prefix '{prefix}' already configured for {reg['url']}. Remove it first.", False
        if reg["url"] == url:
            return None, f"Registry {url} is already configured as '{reg['prefix']}'.", False

    registries.append({"prefix": prefix, "url": url})
    _save_registries(registries)
    return prefix, None, False


def get_registry_url_for_prefix(prefix: str) -> str | None:
    """Return the URL for a given registry prefix, or None if not found.

    The public prefix ('cg') always resolves to the public registry URL.
    """
    if prefix == _PUBLIC_REGISTRY_PREFIX:
        return _PUBLIC_REGISTRY_URL
    for reg in get_registries():
        if reg["prefix"] == prefix:
            return reg["url"]
    return None


def remove_registry(prefix: str) -> str | None:
    """Remove a registry by prefix. Returns error string or None on success."""
    if prefix == _PUBLIC_REGISTRY_PREFIX:
        return f"Cannot remove the public Cartograph registry (prefix: {_PUBLIC_REGISTRY_PREFIX})."

    registries = get_registries()
    new_registries = [r for r in registries if r["prefix"] != prefix]
    if len(new_registries) == len(registries):
        return f"No registry with prefix '{prefix}' found."

    _save_registries(new_registries)
    return None
