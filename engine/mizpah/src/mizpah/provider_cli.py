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

from mizpah.providers import credential_path, missing_client_id, registry, session_for


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
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == '__main__':
    sys.exit(main())
