import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm_provider_profiles import (  # noqa: E402
    ApiKeyAuth,
    DeviceCodeFlow,
    OAuthPkceFlow,
    ProfileRegistry,
    ProviderProfile,
    auth_from_dict,
    profile_from_dict,
)

PKCE = OAuthPkceFlow(authorization_endpoint="https://auth.example.org/authorize", token_endpoint="https://auth.example.org/token",
                     client_id="app_1", redirect_port=1455, scopes=("openid", "offline_access"),
                     extra_authorization_params={"prompt": "login"}, account_claim_namespace="https://example.org/auth",
                     account_claim_key="account_id")
PROFILE = ProviderProfile(
    name="example_sub", display_name="Example Subscription", auth=PKCE, api_base_url="https://api.example.org/backend",
    completion_path="/responses", wire="responses", static_headers={"originator": "example-client"},
    credential_headers={"Authorization": "Bearer {token}", "x-account-id": "{account_id}"},
    models=("m-large", "m-small"), default_model="m-large", context_window=200000,
)


def test_redirect_uri_and_kind() -> None:
    assert PKCE.redirect_uri == "http://localhost:1455/auth/callback"
    assert PKCE.kind == "oauth_pkce" and DeviceCodeFlow().kind == "device_code" and ApiKeyAuth().kind == "api_key"


def test_headers_render_from_token_and_metadata() -> None:
    assert PROFILE.headers_for("tok", {"account_id": "acct-1"}) == {
        "originator": "example-client", "Authorization": "Bearer tok", "x-account-id": "acct-1"}
    assert PROFILE.headers_for("tok") == {"originator": "example-client", "Authorization": "Bearer tok"}


def test_roundtrip_through_dict() -> None:
    data = PROFILE.to_dict()
    assert data["auth"]["kind"] == "oauth_pkce" and data["models"] == ["m-large", "m-small"]
    restored = profile_from_dict(data)
    assert restored == PROFILE
    device = profile_from_dict({"name": "d", "display_name": "D", "api_base_url": "https://x.example", "completion_path": "/v1/r",
                                "auth": {"kind": "device_code", "device_authorization_endpoint": "https://x.example/d",
                                         "token_endpoint": "https://x.example/t", "client_id": "c", "scopes": ["a"]}})
    assert isinstance(device.auth, DeviceCodeFlow) and device.auth.scopes == ("a",)
    key = auth_from_dict({"kind": "api_key", "environment_variable": "EXAMPLE_KEY", "unknown": 1})
    assert isinstance(key, ApiKeyAuth) and key.environment_variable == "EXAMPLE_KEY"


def test_models_url() -> None:
    assert PROFILE.models_url is None
    listed = ProviderProfile("n", "N", ApiKeyAuth(), "https://api.example.org/v1/", "/chat/completions", models_path="/models")
    assert listed.models_url == "https://api.example.org/v1/models"
    with pytest.raises(ValueError):
        ProviderProfile("n", "N", ApiKeyAuth(), "https://api.example.org", "/x", models_path="models")


def test_validation() -> None:
    with pytest.raises(ValueError):
        ProviderProfile("n", "N", ApiKeyAuth(), "api.example.org", "/x")
    with pytest.raises(ValueError):
        ProviderProfile("n", "N", ApiKeyAuth(), "https://api.example.org", "x")
    with pytest.raises(ValueError):
        ProviderProfile("n", "N", ApiKeyAuth(), "https://api.example.org", "/x", wire="grpc")
    with pytest.raises(ValueError):
        ProviderProfile("n", "N", ApiKeyAuth(), "https://api.example.org", "/x", token_count="guess")
    with pytest.raises(ValueError):
        ProviderProfile("n", "N", ApiKeyAuth(), "https://api.example.org", "/x", models=("a",), default_model="b")
    with pytest.raises(ValueError):
        auth_from_dict({"kind": "magic"})
    with pytest.raises(ValueError):
        profile_from_dict({"name": "n", "display_name": "N", "api_base_url": "https://a.example", "completion_path": "/x"})


def test_registry() -> None:
    other = ProviderProfile("keyed", "Keyed", ApiKeyAuth(environment_variable="K"), "https://api.example.org", "/v1/chat/completions")
    registry = ProfileRegistry([PROFILE, other])
    assert registry.names() == ["example_sub", "keyed"]
    assert registry.get("keyed") is other
    assert [p.name for p in registry.with_auth_kind("oauth_pkce")] == ["example_sub"]
    with pytest.raises(KeyError, match="unknown provider"):
        registry.get("nope")
    replaced = ProviderProfile("keyed", "Keyed 2", ApiKeyAuth(), "https://api.example.org", "/v2")
    registry.register(replaced)
    assert registry.get("keyed").display_name == "Keyed 2"
    assert ProfileRegistry.from_dicts([PROFILE.to_dict()]).get("example_sub") == PROFILE
