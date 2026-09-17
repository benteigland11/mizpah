"""
HMAC-SHA256 stamp signing for the Cartograph cloud trust model.

When a widget is pushed to the cloud, the local client signs its validation
stamp.  The server stores the signature alongside the stamp; on install the
client can verify the stamp hasn't been tampered with.

The signing key is set via CARTOGRAPH_SIGNING_KEY env var.  In a cloud
deployment the server uses a different (private) key for server-side
verification, but locally any consistent key works for round-trip testing.

All functions use stdlib only (hmac, hashlib, json, os) — no extra deps.
"""

import hashlib
import hmac
import json
import os


_ENV_KEY = "CARTOGRAPH_SIGNING_KEY"
_PLACEHOLDER_KEY = "cartograph-local-dev"  # not secret — just for local testing


class MissingSigningKeyError(RuntimeError):
    """Raised when a stamp needs to be signed but no real signing key is
    available. The CLI catches this in the publish path and turns it into
    an actionable "you need to login" message instead of letting a
    placeholder-signed stamp hit the server (which would surface as the
    confusing 403 "Invalid stamp signature")."""


def _signing_key() -> bytes:
    # Prefer per-user key from credentials (set during OAuth login).
    try:
        from .auth import get_signing_key
        key = get_signing_key()
        if key:
            return key.encode()
    except Exception:
        pass
    # Explicit env override — used by CI, tests, and server-side work.
    env_key = os.environ.get(_ENV_KEY)
    if env_key:
        return env_key.encode()
    # No per-user key AND no env override. In the past this silently fell
    # back to a placeholder, producing an "Invalid stamp signature" at the
    # server that looked like a server bug. Now we fail loud instead.
    raise MissingSigningKeyError(
        "No signing key available. Run `cartograph login` to authenticate, "
        "or set CARTOGRAPH_SIGNING_KEY for CI/test environments."
    )


def _canonical(stamp: dict) -> bytes:
    """Deterministic JSON serialisation for signing (sorted keys, no whitespace)."""
    # Exclude any existing 'signature' field so signing is idempotent
    clean = {k: v for k, v in stamp.items() if k != "signature"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()


def sign_stamp(stamp: dict) -> str:
    """Return an HMAC-SHA256 hex digest over the stamp's canonical form."""
    return hmac.new(_signing_key(), _canonical(stamp), hashlib.sha256).hexdigest()


def verify_stamp(stamp: dict) -> bool:
    """Return True if the stamp's signature matches our own signing key.

    Only meaningful for stamps this account signed (HMAC keys are per-user
    symmetric): sync pulls of your own widgets. Raises MissingSigningKeyError
    when no key is available, so callers can distinguish "can't verify"
    from "verified false".
    """
    sig = stamp.get("signature") if isinstance(stamp, dict) else None
    if not sig:
        return False
    return hmac.compare_digest(sign_stamp(stamp), sig)


# Fields the cloud registry requires in every published stamp.
# Defined here so the CLI and cloud stay in sync automatically.
STAMP_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "fingerprint",
    "language",
    "validated_at",
    "engine_version",
})


