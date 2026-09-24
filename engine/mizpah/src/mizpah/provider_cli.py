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

from mizpah import user_config
from mizpah.providers import (available_models, credential_path, local_profile, missing_client_id, registry, session_for,
                              shipped_names)


LLAMA_ONLY_GENERATION_KEYS = ('reasoning_format', 'reasoning_budget_tokens', 'chat_template_kwargs', 'top_k', 'min_p', 'seed')
# The subscription backends reject sampling knobs on reasoning models ('Unsupported parameter: temperature'):
# a hosted seat gets the model and its effort, nothing else.
SUBSCRIPTION_UNSUPPORTED_GENERATION_KEYS = LLAMA_ONLY_GENERATION_KEYS + ('temperature', 'top_p')


def _config(path: Path | None) -> dict[str, Any]:
    """The engine config with this machine's layer over it (the layer alone without --config)."""
    return {'mizpah': user_config.engine(path)}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)


def _setting_names(profile: Any) -> list[str]:
    """{field}s in the address that are settings (not credential facts), known or still needed."""
    import re
    names = re.findall(r'{([a-zA-Z_][a-zA-Z0-9_]*)}', profile.api_base_url)
    return [n for i, n in enumerate(names) if n != 'base_host' and n not in profile.token_metadata_fields and n not in names[:i]]


def _endpoint_view(config: dict[str, Any], session: Any) -> dict[str, Any]:
    """The editable shape of a provider's endpoint: profile fields plus the llama counting routes."""
    profile = session.profile
    override = ((config.get('mizpah') or {}).get('providers') or {}).get(profile.name) or {}
    return dict(api_base_url=profile.base_url_for(), address_template=profile.api_base_url,
                settings={name: profile.template_values.get(name, '') for name in _setting_names(profile)},
                needs=profile.unfilled_fields(),
                completion_path=profile.completion_path, models_path=profile.models_path,
                wire=profile.wire, context_window=profile.context_window, timeout_seconds=profile.timeout_seconds,
                static_headers=dict(profile.static_headers), tokenize_path=override.get('tokenize_path', '/tokenize'),
                template_path=override.get('template_path', '/apply-template'), local_kind=override.get('local_kind'))


def cmd_list(args: argparse.Namespace) -> int:
    config = _config(args.config)
    store_path = credential_path(config)
    shipped = shipped_names()
    rows = []
    for name in registry(config).names():
        session = session_for(name, config)
        status = session.status()
        status['blocked'] = missing_client_id(session.profile)
        status['api_base_url'] = session.profile.base_url_for()
        status['notes'] = session.profile.notes
        status['custom'] = name not in shipped
        status['endpoint'] = _endpoint_view(config, session)
        rows.append(status)
    _emit(dict(credentials_file=str(store_path), providers=rows))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = session_for(args.provider, _config(args.config))
    status = session.status()
    status['blocked'] = missing_client_id(session.profile)
    status['api_base_url'] = session.profile.base_url_for()
    status['notes'] = session.profile.notes
    status['endpoint'] = _endpoint_view(_config(args.config), session)
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
    """Point a harness config's worker, controller and/or deputy at a provider, model and effort.

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
    if model is None:
        model = rows[0]['id'] if rows else None
    if model is None:
        _emit(dict(event='error', provider=args.provider, error='name a model; the provider listed none'))
        return 2
    if source == 'live' and model not in known:
        _emit(dict(event='error', provider=args.provider, error=f'{model!r} is not one the account listed: {sorted(known)}'))
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
    # --project: the choice is this task's, not the user's. The same role spec goes under models.<role>
    # in the project's .mizpah/config.json; the loop layers it over the user config when it starts.
    project = getattr(args, 'project', None)
    harness_path = args.harness
    to_user = project is None and harness_path is None
    if to_user:
        if args.config is None:
            _emit(dict(event='error', error='pass --config (the engine config) or --harness (the harness config)'))
            return 2
        # The user's choice, not the repository's: the seats go to the user's layer (user_config).
        harness, _ = user_config.harness(args.config)
    elif project is not None:
        from . import init as init_module, layout
        state = layout.state(Path(project))
        cfg_path = state/'config.json'
        project_cfg = init_module.project_config(Path(project))
        harness = project_cfg.setdefault('models', {})
    elif not to_user:
        harness = json.loads(harness_path.read_text())
    roles = ('worker', 'controller') if args.role == 'both' else ('worker', 'controller', 'deputy') if args.role == 'all' else (args.role,)
    native = profile.auth.kind == 'none' and profile.token_count == 'tokenize_endpoint'
    override = ((config.get('mizpah') or {}).get('providers') or {}).get(profile.name) or {}
    for role in roles:
        spec = harness.setdefault(role, {})
        endpoint = spec.setdefault('endpoint', {})
        # A seat in conversation cannot wait a worker's fifteen minutes on a hung call: the Deputy's turn is a
        # few short calls, and a stall should surface in minutes and be discarded.
        endpoint.update(base_url=profile.api_base_url, completion_path=profile.completion_path,
                        timeout_seconds=min(profile.timeout_seconds, 180) if role == 'deputy' else profile.timeout_seconds,
                        template_path=override.get('template_path', '/apply-template'),
                        tokenize_path=override.get('tokenize_path', '/tokenize'),
                        headers={'Content-Type': 'application/json', **profile.static_headers})
        for key, value in dict(maximum_response_bytes=16000000, tokenizer_add_special=True, tokenizer_parse_special=True).items():
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
            for key in SUBSCRIPTION_UNSUPPORTED_GENERATION_KEYS:
                generation.pop(key, None)
        generation['model'] = model
        # The model's window rides on the seat: the engine fits the session's capacity to it (init.fit_windows).
        if profile.context_window:
            spec['context_window'] = int(profile.context_window)
        else:
            spec.pop('context_window', None)
        if effort is None:
            generation.pop('reasoning_effort', None)
        else:
            generation['reasoning_effort'] = effort
    if project is not None:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(project_cfg, indent=1)+'\n')
        _emit(dict(event='using', provider=profile.name, model=model, effort=effort, roles=list(roles),
                   transport='llama_client' if native else 'subscription', project=str(project), project_config=str(cfg_path)))
        return 0
    if to_user:
        written = user_config.update_section('harness', {role: harness[role] for role in roles})
    else:
        harness_path.write_text(json.dumps(harness, indent=2)+'\n')
        written = harness_path
    _emit(dict(event='using', provider=profile.name, model=model, effort=effort, roles=list(roles),
               transport='llama_client' if native else 'subscription', harness_config=str(written)))
    return 0


def _write_override(config_path: Path | None, name: str, override: dict[str, Any] | None) -> dict[str, Any]:
    """Set (or with None, delete) providers.<name> in the user's layer; returns that layer's engine section."""
    providers = dict(user_config.section('engine').get('providers') or {})
    if override is None:
        providers.pop(name, None)
    else:
        providers[name] = override
    user_config.update_section('engine', {'providers': providers})
    return user_config.section('engine')


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
    _emit(dict(event='added', kind=profile['local_kind'], **status))
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


EDITABLE_FIELDS = {
    # profile fields a person may set on any provider; the value's JSON type is checked by the profile itself
    'api_base_url': str, 'completion_path': str, 'models_path': str, 'wire': str, 'default_model': str,
    'context_window': int, 'timeout_seconds': float, 'static_headers': dict, 'credential_headers': dict,
    'reasoning_efforts': list, 'default_reasoning_effort': str, 'models': list, 'display_name': str,
    # the two llama.cpp counting routes; not profile fields, read by `use` for the native client
    'tokenize_path': str, 'template_path': str, 'template_values': dict,
    # dotted: auth.client_id, auth.scopes, ...
}


def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _apply_set(override: dict[str, Any], key: str, value: Any) -> None:
    head, _, rest = key.partition('.')
    if rest:
        _apply_set(override.setdefault(head, {}), rest, value)
        return
    if value is None or value == '':
        override.pop(head, None)
    else:
        override[head] = value


def cmd_configure(args: argparse.Namespace) -> int:
    """Override profile fields in the engine config (mizpah.providers.<name>).

    ``--set key=value`` takes any profile field (dotted for auth.*); the value is JSON when it parses,
    else a string; an empty value removes the override. The result must still be a valid profile, or
    nothing is written.
    """
    target = user_config.engine(args.config)
    override = json.loads(json.dumps(dict(target.get('providers') or {}).get(args.provider) or {}))
    if args.base_url:
        args.set = [f'api_base_url={args.base_url}'] + (args.set or [])
    if args.default_model:
        args.set = [f'default_model={args.default_model}'] + (args.set or [])
    if args.client_id:
        args.set = [f'auth.client_id={args.client_id}'] + (args.set or [])
    for item in args.set or []:
        key, sep, value = item.partition('=')
        if not sep or not key:
            _emit(dict(event='error', provider=args.provider, error=f'--set wants key=value, got {item!r}'))
            return 2
        parsed = _parse_value(value)
        head = key.split('.', 1)[0]
        if head not in EDITABLE_FIELDS and head != 'auth':
            _emit(dict(event='error', provider=args.provider, error=f'{key!r} is not a field you can set; one of {sorted(EDITABLE_FIELDS)} or auth.*'))
            return 2
        if key == 'api_base_url' and isinstance(parsed, str):
            if not parsed.startswith(('http://', 'https://')):
                _emit(dict(event='error', provider=args.provider, error='base url must start with http:// or https://'))
                return 2
            parsed = parsed.rstrip('/')
        if key in ('completion_path', 'models_path', 'tokenize_path', 'template_path') and isinstance(parsed, str) and parsed and not parsed.startswith('/'):
            parsed = '/' + parsed
        _apply_set(override, key, parsed)
    target['providers'] = dict(target.get('providers') or {}, **{args.provider: override})
    try:
        session = session_for(args.provider, {'mizpah': target})
    except (ValueError, TypeError, KeyError) as error:
        _emit(dict(event='error', provider=args.provider, error=f'that would not be a valid profile: {error}'))
        return 2
    _write_override(args.config, args.provider, override)
    _emit(dict(event='configured', provider=args.provider, override=override, **{k: v for k, v in session.status().items()
                                                                                  if k in ('signed_in', 'reachable', 'reason')}))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Does the provider answer in the shape the engine expects? Lists models, then runs one tiny completion.

    Reports each step's HTTP status and the first bytes of any error body, so a wrong path, header or
    field shows up as text rather than as a failed session later.
    """
    import time
    config = _config(args.config)
    session = session_for(args.provider, config)
    profile = session.profile
    report: dict[str, Any] = dict(event='check', provider=profile.name, address=profile.base_url_for(), steps=[])
    status = session.status()
    if not status['signed_in']:
        report['steps'].append(dict(step='credential', ok=False, detail=status.get('reason') or 'not signed in'))
        _emit(report)
        return 1
    rows, source = available_models(session)
    report['steps'].append(dict(step='models', ok=source == 'live', source=source, count=len(rows),
                                url=profile.models_url_for(session.credential().metadata),
                                sample=[r['id'] for r in rows[:5]]))
    model = args.model or (rows[0]['id'] if rows else None)
    if model is None:
        report['steps'].append(dict(step='completion', ok=False, detail='no model to try; pass --model'))
        _emit(report)
        return 1
    payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Reply with the single word OK.'}], 'max_tokens': 8}
    effort = next((r for r in rows if r['id'] == model), {}).get('default_effort')
    if effort:
        payload['reasoning_effort'] = effort
    transport = session.transport()
    started = time.monotonic()
    response = transport(profile.completion_path, payload, timeout_seconds=args.timeout)
    step: dict[str, Any] = dict(step='completion', model=model, status=response.status,
                                seconds=round(time.monotonic() - started, 2), ok=False)
    if response.error:
        step['detail'] = response.error
        step['body'] = response.body[:400]
    else:
        try:
            chat = json.loads(response.body)
            choice = chat['choices'][0]
            step['ok'] = True
            step['content'] = (choice['message'].get('content') or '')[:80]
            step['finish_reason'] = choice.get('finish_reason')
            step['usage'] = chat.get('usage')
            step['wire'] = profile.wire
        except (ValueError, KeyError, IndexError, TypeError) as error:
            step['detail'] = f'response is not a single-choice chat completion: {type(error).__name__}: {error}'
            step['body'] = response.body[:400]
    report['steps'].append(step)
    report['ok'] = all(s['ok'] for s in report['steps'])
    _emit(report)
    return 0 if report['ok'] else 1


def cmd_current(args: argparse.Namespace) -> int:
    """The seats a run would use on this machine: the committed harness config with the user's layer over it."""
    if args.config is None:
        _emit(dict(event='error', error='current needs --config (the engine config)'))
        return 2
    harness, _ = user_config.harness(args.config)
    _emit(dict(seats={role: harness.get(role) for role in ('worker', 'controller', 'deputy')},
               user_config=str(user_config.path())))
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
    commands.add_parser('current', help="the seats a run would use: repo defaults with this machine's choices over them"
                        ).set_defaults(run=cmd_current)
    status = commands.add_parser('status'); status.add_argument('provider'); status.set_defaults(run=cmd_status)
    login = commands.add_parser('login'); login.add_argument('provider')
    login.add_argument('--no-browser', action='store_true', help='print the URL; do not launch a browser')
    login.add_argument('--api-key', default=None, help='for api_key providers; else read from the profile environment variable')
    login.set_defaults(run=cmd_login)
    logout = commands.add_parser('logout'); logout.add_argument('provider'); logout.set_defaults(run=cmd_logout)
    models = commands.add_parser('models'); models.add_argument('provider'); models.set_defaults(run=cmd_models)
    use = commands.add_parser('use', help='point the harness config at a provider and model')
    use.add_argument('provider'); use.add_argument('model', nargs='?', default=None)
    use.add_argument('--role', choices=('worker', 'controller', 'deputy', 'both', 'all'), default='both')
    use.add_argument('--project', type=Path, default=None,
                     help='make the choice this task\'s only: written to its .mizpah/config.json, layered over your config when it runs')
    use.add_argument('--effort', default=None, help="reasoning effort when the model takes one; 'none' removes it")
    use.add_argument('--harness', type=Path, default=None, help='harness config to rewrite (default: the one --config names)')
    use.set_defaults(run=cmd_use)
    configure = commands.add_parser('configure', help='override a profile field in the engine config')
    configure.add_argument('provider')
    configure.add_argument('--base-url', default=None); configure.add_argument('--default-model', default=None)
    configure.add_argument('--client-id', default=None)
    configure.add_argument('--set', action='append', default=None, metavar='KEY=VALUE',
                           help='any profile field, dotted for auth.*; JSON value when it parses; empty removes')
    configure.set_defaults(run=cmd_configure)
    add_local = commands.add_parser('add-local', help='add a server on this machine as a provider')
    add_local.add_argument('name', help='short id, e.g. my_llama')
    add_local.add_argument('--base-url', required=True)
    add_local.add_argument('--kind', choices=('auto', 'llama', 'openai'), default='auto',
                           help='auto asks the server; llama: llama.cpp with exact /tokenize counting; openai: any /v1/chat/completions server')
    add_local.add_argument('--display-name', default=None)
    add_local.set_defaults(run=cmd_add_local)
    remove = commands.add_parser('remove', help='remove a provider you added'); remove.add_argument('provider')
    remove.set_defaults(run=cmd_remove)
    check = commands.add_parser('check', help='list models and run one tiny completion; report shapes')
    check.add_argument('provider'); check.add_argument('--model', default=None)
    check.add_argument('--timeout', type=float, default=120.0)
    check.set_defaults(run=cmd_check)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == '__main__':
    sys.exit(main())
