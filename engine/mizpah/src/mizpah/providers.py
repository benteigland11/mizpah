"""Hosted providers for the engine: profiles, the credential file, and a ModelClient.

The composition lives in ``bp-subscription-provider-session``; this module is
the product wiring: which profiles ship, where the credential file is, how
config overrides a profile, and how a signed-in session becomes the
``ModelClient`` the controller and worker already use.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from cg.backend_persistent_model_session_python.src.persistent_model_session import EndpointConfig, ModelClient
from cg.bp_subscription_provider_session_python.src.subscription_provider_session import (
    CredentialStore,
    ProviderSession,
)
from cg.backend_llm_provider_profiles_python.src.llm_provider_profiles import (
    ProfileRegistry,
    ProviderProfile,
    profile_from_dict,
)

SHIPPED_PROFILES = Path(__file__).with_name('providers.json')
CLIENT_ID_ENVIRONMENT = {'xai_grok': 'GROK_OAUTH2_CLIENT_ID', 'openai_chatgpt': 'MIZPAH_OPENAI_CLIENT_ID',
                         'github_copilot': 'MIZPAH_GITHUB_CLIENT_ID'}


def credential_path(config: dict[str, Any] | None = None) -> Path:
    """``mizpah.credentials_file`` in the config, else XDG data home."""
    configured = ((config or {}).get('mizpah') or {}).get('credentials_file')
    if configured:
        return Path(configured).expanduser()
    base = Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local'/'share')
    return base/'mizpah'/'credentials.json'


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def registry(config: dict[str, Any] | None = None, environ: dict[str, str] | None = None) -> ProfileRegistry:
    """Shipped profiles, then ``mizpah.providers`` overrides by name, then client ids from the environment."""
    env = os.environ if environ is None else environ
    shipped = {item['name']: item for item in json.loads(SHIPPED_PROFILES.read_text())['profiles']}
    overrides = ((config or {}).get('mizpah') or {}).get('providers') or {}
    for name, override in overrides.items():
        shipped[name] = _merge(shipped.get(name, {'name': name}), override)
    profiles = []
    for name, data in shipped.items():
        variable = CLIENT_ID_ENVIRONMENT.get(name)
        if variable and env.get(variable) and isinstance(data.get('auth'), dict):
            data = _merge(data, {'auth': {'client_id': env[variable]}})
        profiles.append(profile_from_dict(data))
    return ProfileRegistry(profiles)


def session_for(name: str, config: dict[str, Any] | None = None, *, open_browser: bool = True) -> ProviderSession:
    profile = registry(config).get(name)
    return ProviderSession(profile, CredentialStore(credential_path(config)), open_browser=open_browser)


def available_models(session: ProviderSession) -> tuple[list[str], bool]:
    """The live list when signed in and the provider has one (authoritative; the profile's default
    first if it is there), else the profile's static catalogue. The flag says which."""
    static = list(session.profile.models)
    if not session.status()['signed_in'] or session.profile.models_url is None:
        return static, False
    try:
        live = session.list_models()
    except Exception:
        return static, False
    if not live:
        return static, False
    default = session.profile.default_model
    ordered = ([default] if default in live else []) + [m for m in live if m != default]
    return ordered, True


def missing_client_id(profile: ProviderProfile) -> str | None:
    """A sentence for the user when a flow cannot start, else None."""
    client_id = getattr(profile.auth, 'client_id', None)
    if profile.auth.kind in ('oauth_pkce', 'device_code') and not client_id:
        variable = CLIENT_ID_ENVIRONMENT.get(profile.name, '<none>')
        return (f'{profile.display_name} needs an OAuth client id: set {variable}, or mizpah.providers.{profile.name}'
                f'.auth.client_id in the engine config. {profile.notes}'.strip())
    return None


class HostedModelClient(ModelClient):
    """A ModelClient over a subscription session: counting and capabilities come from the profile."""

    def __init__(self, session: ProviderSession, spec: dict[str, Any], observer: Any = None) -> None:
        self.session = session
        endpoint = dict(session.endpoint(), maximum_response_bytes=16_000_000, template_path='/apply-template',
                        tokenize_path='/tokenize', tokenizer_add_special=True, tokenizer_parse_special=True)
        endpoint.update({k: v for k, v in (spec.get('endpoint') or {}).items() if k in ('timeout_seconds', 'maximum_response_bytes')})
        super().__init__(EndpointConfig(**endpoint), transport=session.transport(), observer=observer)

    def count(self, payload: dict[str, Any], purpose: str) -> dict[str, Any]:
        estimate = self.session.count(payload)
        if self.observer:
            self.observer('model_request', dict(request_id='estimate', path=None, purpose=purpose+':estimate', body=None,
                                                timeout_seconds=None, transport=estimate))
        return dict(prompt='', **estimate)

    def capabilities(self, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
        profile = self.session.profile
        return dict(vision=True, audio=False, video=False, template={}, model=profile.default_model,
                    context=profile.context_window, error=None)


def hosted_model_client(spec: dict[str, Any], config: dict[str, Any], observer: Any = None) -> ModelClient:
    """``spec['provider'] == 'subscription'``: ``spec['subscription']`` names the profile."""
    name = spec.get('subscription')
    if not name:
        raise ValueError("endpoint spec with provider 'subscription' needs a 'subscription' profile name")
    session = session_for(name, config, open_browser=False)
    if not session.status()['signed_in']:
        raise RuntimeError(f'not signed in to {name}; run: mizpah-provider login {name}')
    return HostedModelClient(session, spec, observer)
