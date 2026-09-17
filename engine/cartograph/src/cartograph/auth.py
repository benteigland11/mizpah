"""
Auth credential storage for the Cartograph cloud registry.

Credentials are stored in the platform user-config directory with 0o600
permissions so only the current user can read them.  Nothing in this module
makes network calls except the token refresh path.

Stored format:
  {"id_token": "...", "refresh_token": "...", "signing_key": "..."}

Environment overrides (useful for CI/scripts):
  CARTOGRAPH_TOKEN        — skip file storage entirely (raw ID token)
  CARTOGRAPH_REGISTRY_URL — override the default registry endpoint
"""

import base64
import json
import logging
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("cartograph")

_DEFAULT_REGISTRY_URL = "https://api.cartograph.tools"


def _credentials_path() -> str:
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), "credentials.json")


_CREDENTIALS_FILE = _credentials_path()
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
def _google_client_id() -> str:
    env = os.environ.get("GOOGLE_CLIENT_ID", "")
    if env:
        return env
    return _read_credentials().get("client_id", "")


def _google_client_secret() -> str:
    env = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if env:
        return env
    return _read_credentials().get("client_secret", "")


def get_registry_url() -> str:
    return os.environ.get("CARTOGRAPH_REGISTRY_URL", _DEFAULT_REGISTRY_URL)


def _registry_tokens_path() -> str:
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), "registry_tokens.json")


def _read_registry_tokens() -> dict:
    try:
        with open(_registry_tokens_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def store_registry_token(registry_url: str, token: str) -> None:
    """Store an API token for a company registry, keyed by URL."""
    tokens = _read_registry_tokens()
    tokens[registry_url.rstrip("/")] = token
    path = _registry_tokens_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def remove_registry_token(registry_url: str) -> bool:
    """Remove the stored token for a company registry. Returns True if one existed."""
    tokens = _read_registry_tokens()
    key = registry_url.rstrip("/")
    if key not in tokens:
        return False
    del tokens[key]
    with open(_registry_tokens_path(), "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)
    return True


def _credentials_lock_path() -> str:
    return _CREDENTIALS_FILE + ".lock"


def _auth_lock():
    """Exclusive lock for credential read-modify-write (refresh, login, logout)."""
    from cg.infra_interprocess_lock_python.src.interprocess_lock import file_lock
    dest = _CREDENTIALS_FILE
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return file_lock(_credentials_lock_path(), blocking=True)


def _read_credentials() -> dict:
    """Read the credentials file, or return empty dict."""
    try:
        with open(_CREDENTIALS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _decode_jwt_payload(token: str) -> dict:
    """Decode the payload of a JWT without verification (client-side only)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        # Add padding
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _is_token_expired(token: str) -> bool:
    """Check if a JWT ID token is expired (with 5min buffer)."""
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp")
    if not exp:
        return True
    return time.time() > (exp - 300)  # 5 minute buffer


def _refresh_id_token(refresh_token: str) -> str | None:
    """Use a refresh token to get a new ID token.

    Preferred path: POST JSON to the Cartograph registry ``/v1/auth/refresh``
    so the OAuth client secret never lives on the workstation. Legacy path:
    form-POST to a stored IdP token_url with client_id/secret (old logins).

    Returns the new id_token, or None on failure.
    """
    if not refresh_token:
        return None

    creds = _read_credentials()
    token_url = (creds.get("token_url") or "").strip()
    registry = get_registry_url().rstrip("/")
    # New logins point token_url at the registry refresh endpoint. If empty
    # or missing a client secret, always go through the registry.
    use_registry = (
        not token_url
        or token_url.rstrip("/").endswith("/v1/auth/refresh")
        or not _google_client_secret()
    )
    if use_registry:
        refresh_url = (
            token_url if token_url.rstrip("/").endswith("/v1/auth/refresh")
            else f"{registry}/v1/auth/refresh"
        )
        if not refresh_url.startswith("https://") and not refresh_url.startswith("http://127.0.0.1"):
            log.debug("Refusing token refresh against non-https endpoint")
            return None
        body = json.dumps({"refresh_token": refresh_token}).encode()
        req = urllib.request.Request(
            refresh_url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    else:
        # Legacy: direct IdP refresh with secrets stored from an older login.
        if not token_url.startswith("https://"):
            token_url = _GOOGLE_TOKEN_URL
        client_id = _google_client_id()
        client_secret = _google_client_secret()
        if not client_id:
            log.debug("Cannot refresh token: no client_id stored")
            return None
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret:
            form["client_secret"] = client_secret
        req = urllib.request.Request(
            token_url,
            data=urllib.parse.urlencode(form).encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    try:
        from .net_tls import registry_ssl_context
        with urllib.request.urlopen(req, timeout=10, context=registry_ssl_context()) as resp:
            result = json.loads(resp.read())
        new_id_token = result.get("id_token")
        if new_id_token:
            creds = _read_credentials()
            creds["id_token"] = new_id_token
            new_refresh = result.get("refresh_token")
            if new_refresh:
                creds["refresh_token"] = new_refresh
            # Drop any legacy secret once registry-mediated refresh works.
            if use_registry:
                creds["client_secret"] = None  # drop on write
                if not creds.get("token_url"):
                    creds["token_url"] = f"{registry}/v1/auth/refresh"
            _write_credentials(creds)
            log.debug("ID token refreshed successfully")
            return new_id_token
    except Exception as e:
        log.debug("Token refresh failed: %s", e)
    return None



def get_token(registry_url: str | None = None) -> str | None:
    """Return a valid token for the given registry, or None if not authenticated.

    For the public Cartograph registry (default), checks expiration and
    auto-refreshes via Google OAuth. For company registries, returns a stored
    API token from registry_tokens.json.
    """
    # Company registry: check per-registry token store
    if registry_url and registry_url.rstrip("/") != _DEFAULT_REGISTRY_URL:
        tokens = _read_registry_tokens()
        return tokens.get(registry_url.rstrip("/")) or None

    env = os.environ.get("CARTOGRAPH_TOKEN")
    if env:
        return env

    creds = _read_credentials()
    id_token = creds.get("id_token")
    if not id_token:
        return None

    if not _is_token_expired(id_token):
        return id_token

    # Serialize refresh so parallel CLI/MCP processes cannot truncate
    # credentials.json and drop the refresh_token (open(..., "w") race).
    with _auth_lock():
        creds = _read_credentials()
        id_token = creds.get("id_token")
        if id_token and not _is_token_expired(id_token):
            return id_token
        refresh_token = creds.get("refresh_token", "")
        refreshed = _refresh_id_token(refresh_token)
        if refreshed:
            return refreshed
        log.debug("ID token expired and refresh failed")
        return None


def get_signing_key() -> str | None:
    """Return the user's signing key from stored credentials."""
    env = os.environ.get("CARTOGRAPH_SIGNING_KEY")
    if env:
        return env
    creds = _read_credentials()
    return creds.get("signing_key") or None


def is_authenticated() -> bool:
    return get_token() is not None


def _write_credentials(creds: dict) -> None:
    """Write credentials dict to disk with restricted permissions.

    Replaces the live file via a sibling temp + ``os.replace`` so readers
    never see a truncated JSON document. Concurrent writers are serialized
    by ``_auth_lock``. A write that omits ``refresh_token`` / ``signing_key``
    keeps the existing values instead of blanking them.
    """
    dest = _CREDENTIALS_FILE
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    incoming = dict(creds)
    with _auth_lock():
        existing = _read_credentials()
        merged = dict(existing)
        for key, value in incoming.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        for key in ("refresh_token", "signing_key"):
            if not str(merged.get(key) or "").strip() and str(existing.get(key) or "").strip():
                merged[key] = existing[key]
        tmp = dest + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(merged, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(dest, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        except OSError as e:
            # Live file already came from a 0o600 tmp replace. Deleting it
            # would log the user out; leave it and keep going.
            log.debug("Could not chmod %s: %s", dest, e)


def save_credentials(id_token: str, refresh_token: str, signing_key: str,
                     client_id: str = "", client_secret: str = "",
                     token_url: str = "") -> None:
    """Persist OAuth credentials to disk.

    token_url is the issuer's token endpoint for refreshes - handed back
    by the registry at login so org tenants refresh against their own
    identity provider instead of Google.
    """
    if not id_token or not id_token.strip():
        raise ValueError("Received empty id_token - credentials not saved.")
    creds = {
        "id_token": id_token,
        "refresh_token": refresh_token,
        "signing_key": signing_key,
    }
    if client_id:
        creds["client_id"] = client_id
    if client_secret:
        creds["client_secret"] = client_secret
    if token_url:
        creds["token_url"] = token_url
    _write_credentials(creds)


def clear_token() -> None:
    """Remove stored credentials."""
    with _auth_lock():
        for path in (_CREDENTIALS_FILE, _CREDENTIALS_FILE + ".tmp"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                log.debug("Could not remove %s: %s", path, e)
    # Profile cache is auth-scoped — wipe it alongside credentials so the
    # next session doesn't read a stale handle for the previous account.
    from cg.universal_ttl_disk_cache_python.src import ttl_disk_cache
    try:
        ttl_disk_cache.clear(_profile_cache_root())
    except OSError as e:
        log.debug("Could not clear profile cache: %s", e)


def _profile_cache_root() -> str:
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), "profile_cache")


_PROFILE_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h
_PROFILE_CACHE_KEY = "profile"


def cached_whoami(ttl_seconds: int = _PROFILE_CACHE_TTL_SECONDS) -> dict:
    """Return the current user's profile, hitting a disk cache when fresh.

    Read-only callers (search, status) should prefer this over the live
    whoami to avoid a network round-trip per invocation. Mutating flows
    (publish, checkin, cloud adopt) should keep calling live whoami where
    stale identity could cause writes to land under the wrong handle.

    Returns {} when not authenticated. Returns whatever whoami() returned
    on cache miss (including its error shape) without writing the cache.
    """
    if not is_authenticated():
        return {}
    from cg.universal_ttl_disk_cache_python.src import ttl_disk_cache
    cache_root = _profile_cache_root()
    hit = ttl_disk_cache.get(cache_root, _PROFILE_CACHE_KEY)
    if hit:
        return hit
    from .cloud import whoami as _live_whoami
    profile = _live_whoami()
    if isinstance(profile, dict) and "error" not in profile and profile:
        try:
            ttl_disk_cache.set(
                cache_root, _PROFILE_CACHE_KEY, profile, ttl_seconds,
            )
        except (OSError, TypeError) as e:
            log.debug("Could not write profile cache: %s", e)
    return profile
