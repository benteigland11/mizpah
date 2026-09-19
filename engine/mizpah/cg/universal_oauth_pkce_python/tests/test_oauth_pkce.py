from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.oauth_pkce import OAuthPkceConfig
from src.oauth_pkce import build_authorization_url
from src.oauth_pkce import build_refresh_token_request
from src.oauth_pkce import build_revoke_token_request
from src.oauth_pkce import build_token_exchange_request
from src.oauth_pkce import create_pkce_authorization
from src.oauth_pkce import parse_oauth_callback


CONFIG = OAuthPkceConfig(
    authorization_endpoint="https://auth.example.com/oauth2/authorize",
    token_endpoint="https://auth.example.com/oauth2/token",
    revoke_endpoint="https://auth.example.com/oauth2/revoke",
    client_id="client-123",
    redirect_uri="http://localhost:7071/auth/callback",
    scopes=("openid", "profile"),
)


def test_build_authorization_url_contains_pkce_fields() -> None:
    authorization = create_pkce_authorization()
    url = build_authorization_url(CONFIG, authorization)
    assert "client_id=client-123" in url
    assert "code_challenge_method=S256" in url
    assert authorization.state in url


def test_parse_oauth_callback_validates_state_and_path() -> None:
    callback = parse_oauth_callback(
        "http://localhost:7071/auth/callback?code=abc&state=expected",
        expected_path="/auth/callback",
        expected_state="expected",
    )
    assert callback.code == "abc"


def test_parse_oauth_callback_rejects_bad_state() -> None:
    try:
        parse_oauth_callback(
            "http://localhost:7071/auth/callback?code=abc&state=wrong",
            expected_path="/auth/callback",
            expected_state="expected",
        )
    except ValueError as exc:
        assert "state mismatch" in str(exc).lower()
    else:
        raise AssertionError("Expected parse_oauth_callback to reject invalid state")


def test_build_token_exchange_request() -> None:
    request = build_token_exchange_request(CONFIG, code="abc", code_verifier="verifier")
    assert request.url == CONFIG.token_endpoint
    assert request.method == "POST"
    assert "authorization_code" in request.body


def test_build_refresh_and_revoke_requests() -> None:
    refresh = build_refresh_token_request(CONFIG, refresh_token="refresh-1")
    revoke = build_revoke_token_request(CONFIG, token="refresh-1")
    assert "refresh_token" in refresh.body
    assert revoke.url == CONFIG.revoke_endpoint
