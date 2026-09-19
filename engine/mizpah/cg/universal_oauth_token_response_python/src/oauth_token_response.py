from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any


@dataclass(frozen=True)
class OAuthTokenRecord:
    access_token: str
    token_type: str
    expires_at: datetime | None = None
    refresh_token: str | None = None
    scopes: tuple[str, ...] = ()
    id_token: str | None = None
    raw: dict[str, Any] | None = None

    def is_expired(self, now: datetime | None = None, *, skew_seconds: int = 60) -> bool:
        if self.expires_at is None:
            return False
        reference = _utc(now) if now is not None else datetime.now(timezone.utc)
        return self.expires_at <= reference + timedelta(seconds=skew_seconds)

    def safe_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "token_type": self.token_type,
            "scopes": list(self.scopes),
            "has_access_token": bool(self.access_token),
            "has_refresh_token": self.refresh_token is not None,
            "has_id_token": self.id_token is not None,
        }
        if self.expires_at is not None:
            metadata["expires_at"] = self.expires_at.isoformat()
        return metadata

    def secret_metadata(self) -> dict[str, Any]:
        metadata = self.safe_metadata()
        metadata["access_token"] = self.access_token
        if self.refresh_token is not None:
            metadata["refresh_token"] = self.refresh_token
        if self.id_token is not None:
            metadata["id_token"] = self.id_token
        return metadata


def normalize_oauth_token_response(
    payload: dict[str, Any],
    *,
    received_at: datetime | None = None,
    default_token_type: str = "bearer",
) -> OAuthTokenRecord:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dictionary")

    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("access_token is required")

    token_type = str(payload.get("token_type") or default_token_type).strip().lower()
    if not token_type:
        raise ValueError("token_type is required")

    expires_at = _resolve_expiry(payload, received_at=received_at)
    return OAuthTokenRecord(
        access_token=access_token,
        token_type=token_type,
        expires_at=expires_at,
        refresh_token=_optional_str(payload.get("refresh_token")),
        scopes=_parse_scopes(payload.get("scope") or payload.get("scopes")),
        id_token=_optional_str(payload.get("id_token")),
        raw=dict(payload),
    )


def oauth_token_response(payload: dict[str, Any]) -> OAuthTokenRecord:
    return normalize_oauth_token_response(payload)


def _resolve_expiry(payload: dict[str, Any], *, received_at: datetime | None) -> datetime | None:
    if payload.get("expires_at") is not None:
        return _parse_datetime(payload["expires_at"])

    expires_in = payload.get("expires_in")
    if expires_in is None:
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError) as exc:
        raise ValueError("expires_in must be an integer number of seconds") from exc
    if seconds < 0:
        raise ValueError("expires_in cannot be negative")
    reference = _utc(received_at) if received_at is not None else datetime.now(timezone.utc)
    return reference + timedelta(seconds=seconds)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str):
        raise ValueError("expires_at must be an ISO datetime string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _parse_scopes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        parts = [str(part) for part in value]
    else:
        raise ValueError("scope must be a string or collection")
    return tuple(part.strip() for part in parts if part.strip())
