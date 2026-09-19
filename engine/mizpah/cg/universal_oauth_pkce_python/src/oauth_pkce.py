from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import secrets
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlparse


@dataclass(frozen=True)
class OAuthPkceConfig:
    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...] = ()
    revoke_endpoint: str | None = None


@dataclass(frozen=True)
class PkceAuthorization:
    code_verifier: str
    code_challenge: str
    state: str


@dataclass(frozen=True)
class OAuthCallback:
    code: str
    state: str


@dataclass(frozen=True)
class OAuthRequestDescriptor:
    url: str
    method: str
    headers: dict[str, str]
    body: str


def generate_code_verifier(length_bytes: int = 96) -> str:
    token = secrets.token_urlsafe(length_bytes)
    return token[:128]


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_oauth_state(length_bytes: int = 16) -> str:
    return secrets.token_hex(length_bytes)


def create_pkce_authorization(
    *,
    verifier_length_bytes: int = 96,
    state_length_bytes: int = 16,
) -> PkceAuthorization:
    code_verifier = generate_code_verifier(verifier_length_bytes)
    return PkceAuthorization(
        code_verifier=code_verifier,
        code_challenge=generate_code_challenge(code_verifier),
        state=generate_oauth_state(state_length_bytes),
    )


def build_authorization_url(config: OAuthPkceConfig, authorization: PkceAuthorization) -> str:
    query = urlencode(
        {
            "client_id": config.client_id,
            "response_type": "code",
            "redirect_uri": config.redirect_uri,
            "scope": " ".join(config.scopes),
            "code_challenge": authorization.code_challenge,
            "code_challenge_method": "S256",
            "state": authorization.state,
        }
    )
    separator = "&" if "?" in config.authorization_endpoint else "?"
    return f"{config.authorization_endpoint}{separator}{query}"


def parse_oauth_callback(
    callback_url: str,
    *,
    expected_path: str,
    expected_state: str,
) -> OAuthCallback:
    parsed = urlparse(callback_url)
    if parsed.path != expected_path:
        raise ValueError(f"Unexpected callback path: {parsed.path}")

    params = parse_qs(parsed.query, keep_blank_values=False)
    error = _first(params, "error")
    if error:
        description = _first(params, "error_description") or error
        raise ValueError(f"OAuth provider returned an error: {description}")

    code = _first(params, "code")
    state = _first(params, "state")
    if not code:
        raise ValueError("Missing authorization code.")
    if state != expected_state:
        raise ValueError("OAuth state mismatch.")
    return OAuthCallback(code=code, state=state)


def build_token_exchange_request(
    config: OAuthPkceConfig,
    *,
    code: str,
    code_verifier: str,
) -> OAuthRequestDescriptor:
    return OAuthRequestDescriptor(
        url=config.token_endpoint,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": config.client_id,
                "code": code,
                "redirect_uri": config.redirect_uri,
                "code_verifier": code_verifier,
            }
        ),
    )


def build_refresh_token_request(
    config: OAuthPkceConfig,
    *,
    refresh_token: str,
) -> OAuthRequestDescriptor:
    return OAuthRequestDescriptor(
        url=config.token_endpoint,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": config.client_id,
                "refresh_token": refresh_token,
            }
        ),
    )


def build_revoke_token_request(
    config: OAuthPkceConfig,
    *,
    token: str,
) -> OAuthRequestDescriptor:
    if not config.revoke_endpoint:
        raise ValueError("OAuth config does not define a revoke endpoint.")
    return OAuthRequestDescriptor(
        url=config.revoke_endpoint,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode(
            {
                "client_id": config.client_id,
                "token": token,
            }
        ),
    )


def _first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    return values[0]
