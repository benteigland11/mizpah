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


def shipped_names() -> set[str]:
    return set(shipped_profiles())


LOCAL_KINDS = {
    # llama.cpp: exact /tokenize counting through the native guarded client
    'llama': dict(completion_path='/v1/chat/completions', models_path='/v1/models', token_count='tokenize_endpoint'),
    # anything else that speaks /v1/chat/completions without a key: Ollama, LM Studio, vLLM, ...
    'openai': dict(completion_path='/chat/completions', models_path='/models', token_count='usage_calibrated'),
}


def detect_local_kind(base_url: str, *, timeout_seconds: float = 3.0) -> str | None:
    """'llama' when the server answers llama.cpp's /tokenize, 'openai' when it answers at all, None when down.

    llama.cpp answers /tokenize (405 for GET is still an answer from that route); Ollama, LM Studio and
    vLLM have no such route and return 404, which is the tell.
    """
    import urllib.error
    import urllib.request
    root = base_url.rstrip('/')
    if root.endswith('/v1'):
        root = root[:-3]
    try:
        with urllib.request.urlopen(root + '/tokenize', timeout=timeout_seconds):
            return 'llama'
    except urllib.error.HTTPError as error:
        return 'openai' if error.code == 404 else 'llama'
    except OSError:
        return None


def local_profile(name: str, base_url: str, kind: str = 'auto', display_name: str | None = None) -> dict[str, Any]:
    """A complete no-auth profile for a server on this machine, as config data.

    ``kind`` 'auto' asks the server (see ``detect_local_kind``) and falls back to 'openai' when it is
    not answering yet; 'llama' or 'openai' force it.
    """
    if not base_url.startswith(('http://', 'https://')):
        raise ValueError('base url must start with http:// or https://')
    if kind == 'auto':
        kind = detect_local_kind(base_url) or 'openai'
    if kind not in LOCAL_KINDS:
        raise ValueError(f'kind must be auto or one of {sorted(LOCAL_KINDS)}')
    root = base_url.rstrip('/')
    if kind == 'llama' and root.endswith('/v1'):
        root = root[:-3]  # tokenize/props live at the server root; the profile adds /v1 to the chat path
    return dict(name=name, display_name=display_name or name, auth={'kind': 'none'}, api_base_url=root,
                wire='chat_completions', models=[], reasoning_efforts=[], context_window=None, timeout_seconds=900,
                local_kind=kind, **LOCAL_KINDS[kind])


_SHIPPED_CACHE: dict[str, Any] | None = None


def shipped_profiles() -> dict[str, dict[str, Any]]:
    """The shipped profile file, read once per process: a loop runs the profiles it started with. Reading it
    on every controller step let a half-edited file (someone adding a provider) fail every live eval with a
    validation error about a provider the loop never used (2026-09-20)."""
    global _SHIPPED_CACHE
    if _SHIPPED_CACHE is None:
        _SHIPPED_CACHE = {item['name']: item for item in json.loads(SHIPPED_PROFILES.read_text())['profiles']}
    return _SHIPPED_CACHE


def registry(config: dict[str, Any] | None = None, environ: dict[str, str] | None = None,
             only: str | None = None) -> ProfileRegistry:
    """Shipped profiles, then ``mizpah.providers`` overrides by name, then client ids from the environment.
    ``only`` builds (and so validates) one profile: a session for luna does not depend on Bedrock's shape."""
    env = os.environ if environ is None else environ
    shipped = dict(shipped_profiles())
    overrides = ((config or {}).get('mizpah') or {}).get('providers') or {}
    for name, override in overrides.items():
        shipped[name] = _merge(shipped.get(name, {'name': name}), override)
    profiles = []
    for name, data in shipped.items():
        if only is not None and name != only:
            continue
        variable = CLIENT_ID_ENVIRONMENT.get(name)
        if variable and env.get(variable) and isinstance(data.get('auth'), dict):
            data = _merge(data, {'auth': {'client_id': env[variable]}})
        profiles.append(profile_from_dict(data))
    return ProfileRegistry(profiles)


def session_for(name: str, config: dict[str, Any] | None = None, *, open_browser: bool = True) -> ProviderSession:
    profile = registry(config, only=name).get(name)
    return ProviderSession(profile, CredentialStore(credential_path(config)), open_browser=open_browser, user_agent='mizpah/0.1')


def available_models(session: ProviderSession) -> tuple[list[dict[str, Any]], str]:
    """Models the person may pick — ``{id, efforts, default_effort, context_window}`` each — and where
    they came from.

    ``live``: the provider listed them for this credential (authoritative; profile default first).
    ``signed_out``: the provider has a list endpoint but no credential yet — nothing is shown, since
    a static list cannot be verified. ``unreachable``: a no-auth endpoint that is not answering. ``no_list_endpoint``: the profile's static catalogue.
    ``list_failed:<why>``: signed in but the list call failed; the static catalogue as an unverified hint.
    A model the provider listed without an effort ladder inherits the profile's.
    """
    profile = session.profile

    def hint(identifier: str) -> dict[str, Any]:
        return {'id': identifier, 'efforts': list(profile.reasoning_efforts),
                'default_effort': profile.default_reasoning_effort, 'context_window': profile.context_window}

    static = [hint(m) for m in profile.models]
    if profile.models_url is None:
        return static, 'no_list_endpoint'
    status = session.status()
    if not status['signed_in']:
        return [], 'unreachable' if status['auth_kind'] == 'none' else 'signed_out'
    try:
        live = session.list_model_info()
    except Exception as error:  # noqa: BLE001 - any failure here means "hint only"
        return static, f'list_failed: {type(error).__name__}: {error}'
    if not live:
        return static, 'list_failed: empty list'
    rows = []
    for info in live:
        row = info.as_dict()
        if not row['efforts']:
            row['efforts'], row['default_effort'] = list(profile.reasoning_efforts), profile.default_reasoning_effort
        row['context_window'] = row['context_window'] or profile.context_window
        rows.append(row)
    default = profile.default_model
    return sorted(rows, key=lambda r: r['id'] != default), 'live'


SETTING_LABELS = {'project': 'your Google Cloud project id', 'region': 'a region', 'resource': 'your resource name'}


def missing_client_id(profile: ProviderProfile) -> str | None:
    """One sentence for the user when a flow cannot start, else None."""
    needed = profile.unfilled_fields()
    if needed:
        wants = ' and '.join(SETTING_LABELS.get(name, name) for name in needed)
        return f'{profile.display_name} needs {wants}.'
    client_id = getattr(profile.auth, 'client_id', None)
    if profile.auth.kind in ('oauth_pkce', 'device_code') and not client_id:
        variable = CLIENT_ID_ENVIRONMENT.get(profile.name, '<none>')
        return f'{profile.display_name} needs an OAuth client id: set {variable} in the environment.'
    return None


class HostedModelClient(ModelClient):
    """A ModelClient over a subscription session: counting and capabilities come from the profile."""

    def __init__(self, session: ProviderSession, spec: dict[str, Any], observer: Any = None) -> None:
        self.session = session
        endpoint = dict(session.endpoint(), maximum_response_bytes=16_000_000, template_path='/apply-template',
                        tokenize_path='/tokenize', tokenizer_add_special=True, tokenizer_parse_special=True)
        endpoint.update({k: v for k, v in (spec.get('endpoint') or {}).items() if k in ('timeout_seconds', 'maximum_response_bytes')})
        transport = session.transport()
        transport.default_timeout_seconds = endpoint.get('timeout_seconds')   # the config's bound reaches the wire
        super().__init__(EndpointConfig(**endpoint), transport=transport, observer=observer)

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
    status = session.status()
    if not status['signed_in']:
        if status['auth_kind'] == 'none':
            raise RuntimeError(f"{name} is not answering at {session.profile.api_base_url}: {status.get('reason')}")
        raise RuntimeError(f'not signed in to {name}; run: mizpah-provider login {name}')
    return HostedModelClient(session, spec, observer)
