from datetime import datetime
from datetime import timezone

import pytest

from src.oauth_token_response import normalize_oauth_token_response
from src.oauth_token_response import oauth_token_response


def test_normalizes_token_endpoint_response_with_relative_expiry() -> None:
    received_at = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)

    record = normalize_oauth_token_response(
        {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "id_token": "id-1",
            "expires_in": "3600",
            "scope": "calendar.read calendar.write",
        },
        received_at=received_at,
    )

    assert record.access_token == "access-1"
    assert record.token_type == "bearer"
    assert record.refresh_token == "refresh-1"
    assert record.scopes == ("calendar.read", "calendar.write")
    assert record.expires_at == datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc)
    assert record.is_expired(datetime(2026, 5, 8, 12, 59, tzinfo=timezone.utc)) is True


def test_safe_metadata_omits_raw_secrets() -> None:
    record = oauth_token_response(
        {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "scope": ["read", "write"],
            "expires_at": "2026-05-08T13:00:00Z",
        }
    )

    safe = record.safe_metadata()
    assert safe["has_access_token"] is True
    assert safe["has_refresh_token"] is True
    assert safe["scopes"] == ["read", "write"]
    assert "access_token" not in safe
    assert "refresh_token" not in safe

    secret = record.secret_metadata()
    assert secret["access_token"] == "access-1"
    assert secret["refresh_token"] == "refresh-1"


def test_rejects_invalid_provider_payloads() -> None:
    with pytest.raises(ValueError, match="access_token"):
        normalize_oauth_token_response({"expires_in": 60})

    with pytest.raises(ValueError, match="expires_in"):
        normalize_oauth_token_response({"access_token": "a", "expires_in": "soon"})

    with pytest.raises(ValueError, match="scope"):
        normalize_oauth_token_response({"access_token": "a", "scope": 123})
