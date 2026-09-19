"""What a client needs to know about an LLM provider, as data.

A profile says how to get a credential (which OAuth flow, or an API key),
where to send completions, which wire dialect the endpoint speaks, which
headers a credential turns into, and which models exist. Everything is a
plain frozen dataclass so a profile can round-trip through JSON config; a
registry resolves them by name. No provider constants live here - the
caller supplies them (see the example for the shape).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping

WIRE_DIALECTS = ("chat_completions", "responses")
TOKEN_COUNT_STRATEGIES = ("tokenize_endpoint", "usage_calibrated")
AUTH_KINDS = ("oauth_pkce", "device_code", "api_key")


@dataclass(frozen=True)
class OAuthPkceFlow:
    kind: str = field(default="oauth_pkce", init=False)
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    client_id: str = ""
    redirect_path: str = "/auth/callback"
    redirect_host: str = "localhost"
    redirect_port: int = 0
    scopes: tuple[str, ...] = ()
    extra_authorization_params: dict[str, str] = field(default_factory=dict)
    account_claim_namespace: str | None = None
    account_claim_key: str | None = None

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.redirect_host}:{self.redirect_port}{self.redirect_path}"


@dataclass(frozen=True)
class DeviceCodeFlow:
    kind: str = field(default="device_code", init=False)
    device_authorization_endpoint: str = ""
    token_endpoint: str = ""
    client_id: str = ""
    scopes: tuple[str, ...] = ()
    extra_authorization_params: dict[str, str] = field(default_factory=dict)
    extra_token_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ApiKeyAuth:
    kind: str = field(default="api_key", init=False)
    environment_variable: str = ""
    header_name: str = "Authorization"
    header_format: str = "Bearer {key}"


AuthSpec = OAuthPkceFlow | DeviceCodeFlow | ApiKeyAuth


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    display_name: str
    auth: AuthSpec
    api_base_url: str
    completion_path: str
    wire: str = "chat_completions"
    static_headers: dict[str, str] = field(default_factory=dict)
    credential_headers: dict[str, str] = field(default_factory=dict)
    models: tuple[str, ...] = ()
    default_model: str | None = None
    token_count: str = "usage_calibrated"
    context_window: int | None = None
    timeout_seconds: float = 600.0
    notes: str = ""

    def __post_init__(self) -> None:
        if self.wire not in WIRE_DIALECTS:
            raise ValueError(f"wire must be one of {WIRE_DIALECTS}")
        if self.token_count not in TOKEN_COUNT_STRATEGIES:
            raise ValueError(f"token_count must be one of {TOKEN_COUNT_STRATEGIES}")
        if not self.api_base_url.startswith(("http://", "https://")):
            raise ValueError("api_base_url must be absolute")
        if not self.completion_path.startswith("/"):
            raise ValueError("completion_path must begin with a slash")
        if self.default_model is not None and self.models and self.default_model not in self.models:
            raise ValueError("default_model must be one of models")

    def headers_for(self, access_token: str, metadata: Mapping[str, Any] | None = None) -> dict[str, str]:
        """Static headers plus credential headers rendered from the token and its metadata.

        ``credential_headers`` values are format strings over ``token`` and any
        metadata key, e.g. ``{"Authorization": "Bearer {token}", "x-account": "{account_id}"}``.
        A header whose fields are missing is left out rather than sent half-rendered.
        """
        values: dict[str, Any] = dict(metadata or {})
        values["token"] = access_token
        rendered = dict(self.static_headers)
        for name, template in self.credential_headers.items():
            try:
                rendered[name] = template.format(**values)
            except (KeyError, IndexError):
                continue
        return rendered

    def to_dict(self) -> dict[str, Any]:
        data = _jsonable(asdict(self))
        data["auth"] = _jsonable(asdict(self.auth))
        return data


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    return {key: list(value) if isinstance(value, tuple) else value for key, value in data.items()}


def auth_from_dict(data: Mapping[str, Any]) -> AuthSpec:
    kind = data.get("kind")
    classes = {"oauth_pkce": OAuthPkceFlow, "device_code": DeviceCodeFlow, "api_key": ApiKeyAuth}
    if kind not in classes:
        raise ValueError(f"auth.kind must be one of {AUTH_KINDS}")
    cls = classes[kind]
    allowed = {f.name for f in fields(cls) if f.init}
    kwargs = {key: value for key, value in data.items() if key in allowed}
    for key in ("scopes",):
        if key in kwargs and not isinstance(kwargs[key], tuple):
            kwargs[key] = tuple(kwargs[key])
    return cls(**kwargs)


def profile_from_dict(data: Mapping[str, Any]) -> ProviderProfile:
    allowed = {f.name for f in fields(ProviderProfile)}
    kwargs = {key: value for key, value in data.items() if key in allowed}
    if "auth" not in kwargs:
        raise ValueError("profile needs an auth block")
    kwargs["auth"] = auth_from_dict(kwargs["auth"])
    if "models" in kwargs:
        kwargs["models"] = tuple(kwargs["models"])
    return ProviderProfile(**kwargs)


class ProfileRegistry:
    """Profiles by name. Registering a name twice replaces it."""

    def __init__(self, profiles: tuple[ProviderProfile, ...] | list[ProviderProfile] = ()) -> None:
        self._profiles: dict[str, ProviderProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: ProviderProfile) -> None:
        self._profiles[profile.name] = profile

    def get(self, name: str) -> ProviderProfile:
        try:
            return self._profiles[name]
        except KeyError:
            raise KeyError(f"unknown provider {name!r}; known: {', '.join(self.names()) or 'none'}") from None

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def with_auth_kind(self, kind: str) -> list[ProviderProfile]:
        return [profile for profile in self._profiles.values() if profile.auth.kind == kind]

    @classmethod
    def from_dicts(cls, items: list[Mapping[str, Any]]) -> ProfileRegistry:
        return cls([profile_from_dict(item) for item in items])
