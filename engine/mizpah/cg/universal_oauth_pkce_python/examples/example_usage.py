from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.oauth_pkce import OAuthPkceConfig
from src.oauth_pkce import build_authorization_url
from src.oauth_pkce import build_refresh_token_request
from src.oauth_pkce import build_token_exchange_request
from src.oauth_pkce import create_pkce_authorization


config = OAuthPkceConfig(
    authorization_endpoint="https://tenant.auth.us-east-2.amazoncognito.com/oauth2/authorize",
    token_endpoint="https://tenant.auth.us-east-2.amazoncognito.com/oauth2/token",
    revoke_endpoint="https://tenant.auth.us-east-2.amazoncognito.com/oauth2/revoke",
    client_id="cognito-client-id",
    redirect_uri="http://localhost:7071/auth/callback",
    scopes=("email", "openid", "profile"),
)

authorization = create_pkce_authorization()
auth_url = build_authorization_url(config, authorization)
token_request = build_token_exchange_request(
    config,
    code="authorization-code-from-callback",
    code_verifier=authorization.code_verifier,
)
refresh_request = build_refresh_token_request(
    config,
    refresh_token="refresh-token-from-initial-exchange",
)

print("1. Open browser to:")
print(auth_url)
print()
print("2. Exchange callback code with:")
print(token_request.method, token_request.url)
print(token_request.body)
print()
print("3. Refresh the Cognito session with:")
print(refresh_request.method, refresh_request.url)
print(refresh_request.body)
