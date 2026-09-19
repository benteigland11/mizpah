"""Read claims from a JWT without verifying it.

Right when the token came straight from its issuer over TLS and the reader
is the party it was issued to: the transport already vouched for it, and a
signature check would need keys the client has no business fetching. Wrong
anywhere a token arrives from a third party. The function names say
``unverified`` so the call site cannot pretend otherwise.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Any

UNVERIFIED_HEADER_KEYS = ("alg", "typ", "kid")


class MalformedJwt(ValueError):
    """The value is not three base64url segments carrying JSON objects."""


def _decode_segment(segment: str, name: str) -> dict[str, Any]:
    padded = segment + "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError) as error:
        raise MalformedJwt(f"{name} segment is not base64url JSON") from error
    if not isinstance(decoded, dict):
        raise MalformedJwt(f"{name} segment is not a JSON object")
    return decoded


def split_jwt(token: str) -> tuple[str, str, str]:
    parts = token.strip().split(".")
    if len(parts) != 3 or not all(parts[:2]):
        raise MalformedJwt("expected header.payload.signature")
    return parts[0], parts[1], parts[2]


def unverified_header(token: str) -> dict[str, Any]:
    return _decode_segment(split_jwt(token)[0], "header")


def unverified_claims(token: str) -> dict[str, Any]:
    """The payload as a dict. Nothing about it has been checked."""
    return _decode_segment(split_jwt(token)[1], "payload")


def claim(claims: dict[str, Any], path: str, default: Any = None, *, separator: str = "/") -> Any:
    """Look a claim up by path, descending into nested objects.

    Namespaced claims are often full URLs (``https://issuer.example/auth``),
    so the first segment is matched as a whole key before any splitting.
    """
    if path in claims:
        return claims[path]
    node: Any = claims
    for key in path.split(separator):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def namespaced_claim(claims: dict[str, Any], namespace: str, key: str, default: Any = None) -> Any:
    """``claims[namespace][key]`` where ``namespace`` is itself a full key."""
    scope = claims.get(namespace)
    if not isinstance(scope, dict):
        return default
    return scope.get(key, default)


def expires_at(claims: dict[str, Any]) -> datetime | None:
    value = claims.get("exp")
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def is_expired(claims: dict[str, Any], now: datetime | None = None, *, skew_seconds: int = 0) -> bool:
    expiry = expires_at(claims)
    if expiry is None:
        return False
    reference = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return expiry.timestamp() <= reference.timestamp() + skew_seconds
