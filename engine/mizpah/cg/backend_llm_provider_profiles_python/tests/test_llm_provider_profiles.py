import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm_provider_profiles import (  # noqa: E402
    ApiKeyAuth,
    AuthorizedUserFileAuth,
    DeviceCodeFlow,
    NoAuth,
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


def test_no_auth_and_efforts() -> None:
    local = profile_from_dict({"name": "local", "display_name": "Local", "api_base_url": "http://127.0.0.1:8080",
                               "completion_path": "/v1/chat/completions", "auth": {"kind": "none"},
                               "reasoning_efforts": ["low", "high"], "default_reasoning_effort": "high"})
    assert isinstance(local.auth, NoAuth) and local.auth.kind == "none"
    assert local.reasoning_efforts == ("low", "high") and local.to_dict()["reasoning_efforts"] == ["low", "high"]
    assert local.headers_for("") == {}
    with pytest.raises(ValueError):
        ProviderProfile("n", "N", NoAuth(), "http://x", "/v1", reasoning_efforts=("low",), default_reasoning_effort="max")


def test_base_url_template_and_file_auth() -> None:
    per_user = ProviderProfile("p", "P", DeviceCodeFlow(pkce=True), "https://{resource_url}/v1", "/chat/completions",
                               models_path="/models", token_metadata_fields=("resource_url",))
    assert per_user.auth.pkce and per_user.token_metadata_fields == ("resource_url",)
    assert per_user.base_url_for({"resource_url": "portal.example.org"}) == "https://portal.example.org/v1"
    assert per_user.base_url_for({"resource_url": "https://portal.example.org/"}) == "https://portal.example.org/v1"
    assert per_user.base_url_for({}) == "https://{resource_url}/v1"  # unfilled: left for the caller to notice
    assert per_user.models_url_for({"resource_url": "portal.example.org"}) == "https://portal.example.org/v1/models"
    plain = ProviderProfile("q", "Q", ApiKeyAuth(), "https://api.example.org/v1", "/x")
    assert plain.base_url_for({"resource_url": "ignored"}) == "https://api.example.org/v1"
    filed = auth_from_dict({"kind": "authorized_user_file", "path": "~/.config/tool/adc.json",
                            "token_endpoint": "https://oauth.example.org/token", "environment_variable": "TOOL_CREDENTIALS"})
    assert isinstance(filed, AuthorizedUserFileAuth) and filed.expected_type == "authorized_user"
    assert profile_from_dict(dict(per_user.to_dict())) == per_user


def test_absolute_models_url_and_prefix() -> None:
    p = ProviderProfile("v", "V", ApiKeyAuth(), "https://{region}.example.org/v1/projects/p/endpoints/openapi", "/chat/completions",
                        models_path="https://{region}.example.org/v1/publishers/models", model_id_prefix="vendor/")
    assert p.models_url_for({"region": "eu"}) == "https://eu.example.org/v1/publishers/models"
    assert p.base_url_for({"region": "eu"}) == "https://eu.example.org/v1/projects/p/endpoints/openapi"
    assert p.model_id_prefix == "vendor/"
    follows = ProviderProfile("w", "W", ApiKeyAuth(), "https://eu.example.org/v1/projects/p/endpoints/openapi", "/chat/completions",
                              models_path="https://{base_host}/v1/publishers/models")
    assert follows.models_url == "https://eu.example.org/v1/publishers/models"
    with pytest.raises(ValueError):
        ProviderProfile("v", "V", ApiKeyAuth(), "https://x.example", "/c", models_path="ftp://x")


def test_template_values_are_settings() -> None:
    p = ProviderProfile("v", "V", ApiKeyAuth(), "https://{region}.example.org/v1/projects/{project}/endpoints/openapi", "/chat/completions",
                        models_path="https://{base_host}/v1/publishers/models", template_values={"region": "eu"})
    assert p.unfilled_fields() == ["project"]
    assert p.base_url_for() == "https://eu.example.org/v1/projects/{project}/endpoints/openapi"
    filled = profile_from_dict(dict(p.to_dict(), template_values={"region": "eu", "project": "p1"}))
    assert filled.unfilled_fields() == []
    assert filled.base_url_for() == "https://eu.example.org/v1/projects/p1/endpoints/openapi"
    assert filled.models_url == "https://eu.example.org/v1/publishers/models"
    assert filled.base_url_for({"project": "from-token"}) == "https://eu.example.org/v1/projects/from-token/endpoints/openapi"
    headed = profile_from_dict(dict(filled.to_dict(), credential_headers={"Authorization": "Bearer {token}", "x-project": "{project}"},
                                    renamed_fields={"max_tokens": "max_completion_tokens"}))
    assert headed.headers_for("t") == {"Authorization": "Bearer t", "x-project": "p1"}
    assert headed.renamed_fields == {"max_tokens": "max_completion_tokens"}


def test_messages_wire_is_a_dialect() -> None:
    assert ProviderProfile("m", "M", ApiKeyAuth(), "https://api.example.org/v1", "/messages", wire="messages").wire == "messages"


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
