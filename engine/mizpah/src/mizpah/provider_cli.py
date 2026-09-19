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

from mizpah.providers import available_models, credential_path, missing_client_id, registry, session_for


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
    rows = []
    for name in registry(config).names():
        session = session_for(name, config)
        status = session.status()
        status['blocked'] = missing_client_id(session.profile)
        rows.append(status)
    _emit(dict(credentials_file=str(store_path), providers=rows))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = session_for(args.provider, _config(args.config))
    status = session.status()
    status['blocked'] = missing_client_id(session.profile)
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
    models, live = available_models(session)
    _emit(dict(provider=profile.name, default_model=profile.default_model, context_window=profile.context_window,
               wire=profile.wire, models=models, live=live))
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    """Point a harness config's worker and/or controller at a provider and model.

    Rewrites the harness config the engine config names (or --harness directly): the endpoint spec
    becomes provider "subscription", the model is set, and llama-only sampling keys are dropped.
    """
    config = _config(args.config)
    session = session_for(args.provider, config)
    profile = session.profile
    model = args.model or profile.default_model
    models, _ = available_models(session)
    if models and model not in models:
        _emit(dict(event='error', provider=args.provider, error=f'{model!r} is not one of {models}'))
        return 2
    harness_path = args.harness
    if harness_path is None:
        if args.config is None:
            _emit(dict(event='error', error='pass --config (the engine config) or --harness (the harness config)'))
            return 2
        harness_path = (args.config.parent/json.loads(args.config.read_text())['harness_config']).resolve()
    harness = json.loads(harness_path.read_text())
    roles = ('worker', 'controller') if args.role == 'both' else (args.role,)
    for role in roles:
        spec = harness.setdefault(role, {})
        spec['provider'] = 'subscription'
        spec['subscription'] = profile.name
        spec.pop('known_issues', None)
        endpoint = spec.setdefault('endpoint', {})
        endpoint.update(base_url=profile.api_base_url, completion_path=profile.completion_path,
                        timeout_seconds=profile.timeout_seconds)
        for key, value in dict(maximum_response_bytes=16000000, headers={'Content-Type': 'application/json'},
                               template_path='/apply-template', tokenize_path='/tokenize',
                               tokenizer_add_special=True, tokenizer_parse_special=True).items():
            endpoint.setdefault(key, value)
        generation = spec.setdefault('generation', {})
        for key in LLAMA_ONLY_GENERATION_KEYS:
            generation.pop(key, None)
        generation['model'] = model
    harness_path.write_text(json.dumps(harness, indent=2)+'\n')
    _emit(dict(event='using', provider=profile.name, model=model, roles=list(roles), harness_config=str(harness_path)))
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
    use.add_argument('--harness', type=Path, default=None, help='harness config to rewrite (default: the one --config names)')
    use.set_defaults(run=cmd_use)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == '__main__':
    sys.exit(main())
