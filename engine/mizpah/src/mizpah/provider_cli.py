"""mizpah-provider: sign in to, inspect, and sign out of hosted providers.

Every command prints one JSON object so the app can drive it. ``login`` prints
the prompt (a URL, or a code and a URL) as soon as it is known, on its own
line, then blocks until the flow completes and prints the status.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from cg.bp_subscription_provider_session_python.src.subscription_provider_session import LoginError, LoginPrompt

from mizpah.providers import (available_models, credential_path, local_profile, missing_client_id, registry, session_for,
                              shipped_names)


LLAMA_ONLY_GENERATION_KEYS = ('reasoning_format', 'reasoning_budget_tokens', 'chat_template_kwargs', 'top_k', 'min_p', 'seed')


def _config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    raw = json.loads(path.read_text())
    return raw if 'mizpah' in raw else {'mizpah': raw}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)


def cmd_list(args: argparse.Namespace) -> int:
    config = _config(args.config)
    store_path = credential_path(config)
    shipped = shipped_names()
    rows = []
    for name in registry(config).names():
        session = session_for(name, config)
        status = session.status()
        status['blocked'] = missing_client_id(session.profile)
        status['api_base_url'] = session.profile.api_base_url
        status['custom'] = name not in shipped
        rows.append(status)
    _emit(dict(credentials_file=str(store_path), providers=rows))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = session_for(args.provider, _config(args.config))
    status = session.status()
    status['blocked'] = missing_client_id(session.profile)
    status['api_base_url'] = session.profile.api_base_url
    _emit(status)
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    config = _config(args.config)
    session = session_for(args.provider, config, open_browser=not args.no_browser)
    blocked = missing_client_id(session.profile)
    if blocked:
        _emit(dict(event='error', provider=args.provider, error=blocked))
        return 2
    api_key = None
    if session.profile.auth.kind == 'api_key':
        variable = session.profile.auth.environment_variable
        api_key = args.api_key or os.environ.get(variable)
        if not api_key:
            _emit(dict(event='error', provider=args.provider, error=f'pass --api-key or set {variable}'))
            return 2

    def on_prompt(prompt: LoginPrompt) -> None:
        _emit(dict(event='prompt', provider=args.provider, kind=prompt.kind, url=prompt.url, user_code=prompt.user_code,
                   browser_opened=prompt.browser_opened, browser_note=prompt.browser_note))

    try:
        session.login(on_prompt=on_prompt, api_key=api_key)
    except (LoginError, ValueError, TimeoutError) as error:
        _emit(dict(event='error', provider=args.provider, error=f'{type(error).__name__}: {error}'))
        return 1
    _emit(dict(event='signed_in', **session.status()))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """Models a profile offers, with what the engine knows about each."""
    session = session_for(args.provider, _config(args.config))
    profile = session.profile
    rows, source = available_models(session)
    _emit(dict(provider=profile.name, default_model=profile.default_model, context_window=profile.context_window,
               wire=profile.wire, models=[r['id'] for r in rows], details=rows, live=source == 'live', source=source))
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    """Point a harness config's worker and/or controller at a provider, model and effort.

    Rewrites the harness config the engine config names (or --harness directly). A hosted profile
    becomes provider "subscription"; a local llama.cpp profile (no auth, tokenize counting) becomes
    provider "llama_client" so the native guarded client keeps its exact counting. The model is set,
    reasoning_effort is set or removed, and llama-only sampling keys are dropped for hosted providers.
    """
    config = _config(args.config)
    session = session_for(args.provider, config)
    profile = session.profile
    model = args.model or profile.default_model
    rows, source = available_models(session)
    if source == 'signed_out':
        _emit(dict(event='error', provider=args.provider, error=f'sign in to {profile.display_name} first; its models are listed from the account'))
        return 2
    if source == 'unreachable':
        _emit(dict(event='error', provider=args.provider, error=f'{profile.display_name} is not answering at {profile.api_base_url}'))
        return 2
    known = {r['id']: r for r in rows}
    if known and model not in known:
        _emit(dict(event='error', provider=args.provider, error=f'{model!r} is not one of {sorted(known)}'))
        return 2
    if model is None:
        _emit(dict(event='error', provider=args.provider, error='no model named and the profile has no default'))
        return 2
    efforts = known[model]['efforts'] if model in known else list(profile.reasoning_efforts)
    effort = args.effort
    if effort in (None, '') and model in known:
        effort = known[model]['default_effort']
    if effort == 'none':
        effort = None
    if effort is not None and efforts and effort not in efforts:
        _emit(dict(event='error', provider=args.provider, error=f'effort {effort!r} is not one of {efforts} for {model}'))
        return 2
    if effort is not None and not efforts:
        effort = None  # the model does not take an effort; do not send one
    harness_path = args.harness
    if harness_path is None:
        if args.config is None:
            _emit(dict(event='error', error='pass --config (the engine config) or --harness (the harness config)'))
            return 2
        harness_path = (args.config.parent/json.loads(args.config.read_text())['harness_config']).resolve()
    harness = json.loads(harness_path.read_text())
    roles = ('worker', 'controller') if args.role == 'both' else (args.role,)
    native = profile.auth.kind == 'none' and profile.token_count == 'tokenize_endpoint'
    for role in roles:
        spec = harness.setdefault(role, {})
        endpoint = spec.setdefault('endpoint', {})
        endpoint.update(base_url=profile.api_base_url, completion_path=profile.completion_path,
                        timeout_seconds=profile.timeout_seconds)
        for key, value in dict(maximum_response_bytes=16000000, headers={'Content-Type': 'application/json'},
                               template_path='/apply-template', tokenize_path='/tokenize',
                               tokenizer_add_special=True, tokenizer_parse_special=True).items():
            endpoint.setdefault(key, value)
        generation = spec.setdefault('generation', {})
        if native:
            spec['provider'] = 'llama_client'
            spec.pop('subscription', None)
            spec.setdefault('known_issues', {'repetition': {'identical_tool_calls': 8}})
        else:
            spec['provider'] = 'subscription'
            spec['subscription'] = profile.name
            spec.pop('known_issues', None)
            for key in LLAMA_ONLY_GENERATION_KEYS:
                generation.pop(key, None)
        generation['model'] = model
        if effort is None:
            generation.pop('reasoning_effort', None)
        else:
            generation['reasoning_effort'] = effort
    harness_path.write_text(json.dumps(harness, indent=2)+'\n')
    _emit(dict(event='using', provider=profile.name, model=model, effort=effort, roles=list(roles),
               transport='llama_client' if native else 'subscription', harness_config=str(harness_path)))
    return 0


def _write_override(config_path: Path | None, name: str, override: dict[str, Any] | None) -> dict[str, Any]:
    """Set (or with None, delete) mizpah.providers.<name> in the engine config; returns the raw file."""
    if config_path is None:
        _emit(dict(event='error', error='this command needs --config (the engine config to write into)'))
        raise SystemExit(2)
    raw = json.loads(config_path.read_text())
    target = raw['mizpah'] if 'mizpah' in raw else raw
    overrides = target.setdefault('providers', {})
    if override is None:
        overrides.pop(name, None)
    else:
        overrides[name] = override
    config_path.write_text(json.dumps(raw, indent=2)+'\n')
    return raw


def cmd_add_local(args: argparse.Namespace) -> int:
    """Add a server on this machine as a provider: a full no-auth profile in the engine config."""
    if args.name in shipped_names():
        _emit(dict(event='error', provider=args.name, error='that name is a shipped provider; pick another'))
        return 2
    try:
        profile = local_profile(args.name, args.base_url, args.kind, args.display_name)
    except ValueError as error:
        _emit(dict(event='error', provider=args.name, error=str(error)))
        return 2
    _write_override(args.config, args.name, profile)
    session = session_for(args.name, _config(args.config))
    status = session.status()
    status['api_base_url'] = session.profile.api_base_url
    status['custom'] = True
    _emit(dict(event='added', **status))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """Remove a provider the person added (shipped ones cannot be removed, only overridden)."""
    if args.provider in shipped_names():
        _emit(dict(event='error', provider=args.provider, error='shipped providers cannot be removed'))
        return 2
    config = _config(args.config)
    if args.provider not in registry(config).names():
        _emit(dict(event='error', provider=args.provider, error='no such provider'))
        return 2
    session_for(args.provider, config).logout()
    _write_override(args.config, args.provider, None)
    _emit(dict(event='removed', provider=args.provider))
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    """Override a profile field in the engine config (mizpah.providers.<name>), e.g. a local server's address."""
    if args.config is None:
        _emit(dict(event='error', error='configure needs --config (the engine config to write the override into)'))
        return 2
    raw = json.loads(args.config.read_text())
    target = raw['mizpah'] if 'mizpah' in raw else raw
    override = target.setdefault('providers', {}).setdefault(args.provider, {})
    if args.base_url:
        if not args.base_url.startswith(('http://', 'https://')):
            _emit(dict(event='error', provider=args.provider, error='base url must start with http:// or https://'))
            return 2
        override['api_base_url'] = args.base_url.rstrip('/')
    if args.default_model:
        override['default_model'] = args.default_model
    if args.client_id:
        override.setdefault('auth', {})['client_id'] = args.client_id
    args.config.write_text(json.dumps(raw, indent=2)+'\n')
    session = session_for(args.provider, _config(args.config))
    _emit(dict(event='configured', provider=args.provider, override=override, **{k: v for k, v in session.status().items()
                                                                                  if k in ('signed_in', 'reachable', 'reason')}))
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    session = session_for(args.provider, _config(args.config))
    _emit(dict(event='signed_out', provider=args.provider, removed=session.logout()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='mizpah-provider', description=__doc__)
    parser.add_argument('--config', type=Path, default=None, help='engine config (for mizpah.providers overrides)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list').set_defaults(run=cmd_list)
    status = commands.add_parser('status'); status.add_argument('provider'); status.set_defaults(run=cmd_status)
    login = commands.add_parser('login'); login.add_argument('provider')
    login.add_argument('--no-browser', action='store_true', help='print the URL; do not launch a browser')
    login.add_argument('--api-key', default=None, help='for api_key providers; else read from the profile environment variable')
    login.set_defaults(run=cmd_login)
    logout = commands.add_parser('logout'); logout.add_argument('provider'); logout.set_defaults(run=cmd_logout)
    models = commands.add_parser('models'); models.add_argument('provider'); models.set_defaults(run=cmd_models)
    use = commands.add_parser('use', help='point the harness config at a provider and model')
    use.add_argument('provider'); use.add_argument('model', nargs='?', default=None)
    use.add_argument('--role', choices=('worker', 'controller', 'both'), default='both')
    use.add_argument('--effort', default=None, help="reasoning effort when the model takes one; 'none' removes it")
    use.add_argument('--harness', type=Path, default=None, help='harness config to rewrite (default: the one --config names)')
    use.set_defaults(run=cmd_use)
    configure = commands.add_parser('configure', help='override a profile field in the engine config')
    configure.add_argument('provider')
    configure.add_argument('--base-url', default=None); configure.add_argument('--default-model', default=None)
    configure.add_argument('--client-id', default=None)
    configure.set_defaults(run=cmd_configure)
    add_local = commands.add_parser('add-local', help='add a server on this machine as a provider')
    add_local.add_argument('name', help='short id, e.g. my_llama')
    add_local.add_argument('--base-url', required=True)
    add_local.add_argument('--kind', choices=('llama', 'openai'), default='openai',
                           help='llama: llama.cpp server with exact /tokenize counting; openai: any /v1/chat/completions server')
    add_local.add_argument('--display-name', default=None)
    add_local.set_defaults(run=cmd_add_local)
    remove = commands.add_parser('remove', help='remove a provider you added'); remove.add_argument('provider')
    remove.set_defaults(run=cmd_remove)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == '__main__':
    sys.exit(main())
