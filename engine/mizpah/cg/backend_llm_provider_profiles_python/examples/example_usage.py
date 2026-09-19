"""Define two provider profiles as data, resolve them, and render request headers."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm_provider_profiles import ProfileRegistry

PROFILES = [
    {
        "name": "example_subscription",
        "display_name": "Example Subscription (browser sign-in)",
        "auth": {
            "kind": "oauth_pkce",
            "authorization_endpoint": "https://auth.example.org/oauth/authorize",
            "token_endpoint": "https://auth.example.org/oauth/token",
            "client_id": "app_example_public_client",
            "redirect_port": 1455,
            "scopes": ["openid", "profile", "email", "offline_access"],
            "account_claim_namespace": "https://api.example.org/auth",
            "account_claim_key": "account_id",
        },
        "api_base_url": "https://example.org/backend-api/client",
        "completion_path": "/responses",
        "wire": "responses",
        "static_headers": {"originator": "example-client", "Content-Type": "application/json"},
        "credential_headers": {"Authorization": "Bearer {token}", "x-account-id": "{account_id}"},
        "models": ["model-large", "model-small"],
        "default_model": "model-large",
        "context_window": 200000,
    },
    {
        "name": "example_device",
        "display_name": "Example Device (code sign-in)",
        "auth": {
            "kind": "device_code",
            "device_authorization_endpoint": "https://auth.example.org/oauth/device/code",
            "token_endpoint": "https://auth.example.org/oauth/token",
            "client_id": "example-device-client",
            "scopes": ["openid", "offline_access"],
        },
        "api_base_url": "https://api.example.org/v1",
        "completion_path": "/responses",
        "wire": "responses",
        "credential_headers": {"Authorization": "Bearer {token}"},
        "models": ["model-4"],
    },
]

registry = ProfileRegistry.from_dicts(PROFILES)
print("providers:", registry.names())
profile = registry.get("example_subscription")
print("auth flow:", profile.auth.kind, "->", profile.auth.redirect_uri)
print("completion url:", profile.api_base_url + profile.completion_path)
print("headers:", json.dumps(profile.headers_for("access-token-123", {"account_id": "acct-7"}), indent=2))
print("device providers:", [p.name for p in registry.with_auth_kind("device_code")])
