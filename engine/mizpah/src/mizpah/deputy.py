"""The Deputy: one persistent seat across every project, talking to the Administrator.

The Deputy drafts briefs with the person, pulls paper up on the desk for them to look at, and cleans up drafts
they no longer want. It is a focused session like a worker's — same harness, same compaction, same sandbox —
with one difference that is structural rather than policed: its `/work` is the gyms root, where its drafts live
beside every other gym, and each gym whose brief is issued is bound read-only over its place there; every other
live project it can see is mounted read-only too. It cannot write a live brief, start a loop or sign anything;
those are the Administrator's, on the desk. Change requests come from the
controllers and go up to the person; the Deputy never drafts one.

State: `<state dir>/deputy/` — the session (`state.sqlite3`, `events/`), `turns.jsonl` (what was said, both
ways, as the app shows it), `showing.json` (the paper it last pulled up). One conversation, resumed on every
`say`; a change of model or sandbox bindings archives the session and starts a fresh one, the turn log kept.

    python -m mizpah.deputy --config <engine config> say "<text>"    → {"reply": ..., "showing": {...}, ...}
    python -m mizpah.deputy --config <engine config> status
    python -m mizpah.deputy --config <engine config> reset
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any

from . import draft as draft_module, init as init_module
from .worker import (
    FocusedSession, ModelClient, ModelTransportError, ReviewPolicy, SandboxedShell, SessionPolicy, SessionSettings,
    ShellConfig, ShellLimits, client_for, load_config, observe_model, string,
)
from cg.bp_focused_agent_session_python.src.focused_agent_session import ContextCapacityExceeded, GenerationRetryExceeded

DEFAULT_TURN_CAP = 40


def deputy_root() -> Path:
    env = os.environ.get('MIZPAH_DEPUTY_ROOT')
    if env:
        root = Path(env)
    else:
        try:
            from cg.infra_app_paths_python.src.app_paths import resolve_app_paths
            root = resolve_app_paths('mizpah').state_dir/'deputy'
        except ImportError:
            root = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local'/'state')/'mizpah'/'deputy'
    root.mkdir(parents=True, exist_ok=True)
    return root


# The Deputy's verbs, typed because they are new and undiscoverable; everything else is bash and `terra --help`.
DEPUTY_TOOLS: tuple[dict[str, Any], ...] = (
    dict(name='draft_new', description='Start a draft: a gym under /work with an empty brief (title and mission set, '
         'status draft). Then add needs, deliverables, non-goals and the budget with `terra brief set` from inside '
         '/work/<slug> in bash, a few entries per call, and name the environment it runs in with `terra brief set '
         '--environment <name>` — `python -m mizpah.draft environments` lists the saved ones and what each provides; '
         'without one the run has Python and a shell and nothing else.',
         command='python -m mizpah.draft new {slug} --title {title} --mission {mission}',
         parameters=dict(type='object', properties=dict(slug=string('kebab-case name, e.g. ornith-landing'),
                                                        title=string('the brief title, a few words'),
                                                        mission=string('one or two sentences: what is built or found out, and how it is proved')),
                         required=['slug', 'title', 'mission'])),
    dict(name='draft_show', description='Pull a draft up on the desk beside the conversation so the Administrator reads '
         'the sheet itself. Do it after every change to a draft.',
         command='python -m mizpah.draft show {slug}',
         parameters=dict(type='object', properties=dict(slug=string('the draft to show')), required=['slug'])),
    dict(name='draft_discard', description='Remove a draft the Administrator no longer wants, with everything in it. '
         'Ask once first.',
         command='python -m mizpah.draft discard {slug}',
         parameters=dict(type='object', properties=dict(slug=string('the draft to remove')), required=['slug'])),
)


def deputy_spec(config: dict[str, Any]) -> dict[str, Any]:
    """The model behind the seat: `deputy` in the harness config, else the controller's."""
    return config.get('deputy') or config['controller']


def read_roots(config: dict[str, Any]) -> list[str]:
    """What the Deputy may read besides its own drafts: every issued gym (bound read-only over its place in
    /work), the brief library, and whatever the config adds."""
    gyms = init_module.gyms_root()
    roots = [str(p) for p in sorted(gyms.iterdir()) if p.is_dir() and not p.name.startswith('.')
             and not draft_module.is_draft(p)]
    try:
        from .briefs import store_dir
        roots.append(str(store_dir(config)))
    except (KeyError, OSError):
        pass
    for extra in (config['mizpah'].get('deputy') or {}).get('read_roots') or []:
        path = Path(extra).expanduser()
        if path.is_dir():
            roots.append(str(path))
    return sorted(set(roots))


def shell_for(config: dict[str, Any], root: Path) -> SandboxedShell:
    sandbox = config['mizpah']['sandbox']
    gyms = init_module.gyms_root()
    scratch = root/'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    environment = dict(sandbox['environment'], TERRA_DIRNAME='.mizpah', MIZPAH_GYMS='/work', HOME='/work/.home')
    binds = tuple(dict.fromkeys(list(sandbox['read_only_binds'])+read_roots(config)))
    shell = ShellConfig(**(config['shell'] | dict(
        scratch_root=str(scratch), limits=ShellLimits(**config['shell']['limits']), read_only_binds=binds,
        environment=environment, share_network=False, services=None,
        refused_paths=tuple(sandbox.get('refused_paths') or ()),
        refused_patterns=((r'(>>?|\btee\b|-i)\s*[^|;&]*\.mizpah/brief\.json', 'a brief is written with `terra brief set`, never as a file'),
                          (r'\bterra\s+brief\s+set\b[^|;&]*--status\s+active', 'issuing a brief is the Administrator\'s signature, on the desk'),
                          (r'\bmizpah\.(loop|worker|init)\b|\bmizpah\.draft\s+authorize\b', 'loops start on the Administrator\'s signature, never from this seat')),
        workspace_dir=str(gyms), cache_dirs=(), state_dirs=('.tool-output', '.session-history', '.home'))))
    return SandboxedShell(shell)


PROMPT_DIR = Path(__file__).parent/'deputy_prompt'


def policy_text(config: dict[str, Any] | None = None) -> str:
    """The system prompt, composed from `deputy_prompt/*.md` in name order (README.md aside), one blank line
    between files. One file per subject — the role, what a brief becomes, each part of the brief, the tools — so
    an edit is one subject. `deputy_prompt_dir` in the engine config points elsewhere for an experiment."""
    folder = PROMPT_DIR
    if config is not None and (config['mizpah'].get('deputy_prompt_dir')):
        folder = Path(config['mizpah_config_path']).parent/config['mizpah']['deputy_prompt_dir']
    parts = [p.read_text().strip() for p in sorted(folder.glob('*.md')) if p.name.lower() != 'readme.md']
    parts = [p for p in parts if p]
    if not parts:
        raise SystemExit('no prompt files under '+str(folder))
    return '\n\n'.join(parts)+'\n'


def settings_for(config: dict[str, Any], assignment: str) -> SessionSettings:
    spec = deputy_spec(config)
    policy = policy_text(config)
    return SessionSettings(assignment, policy, None, spec['generation'], SessionPolicy(**config['session_policy']),
        ReviewPolicy(**config['review_policy']), None, False, config['guidance_prefix'],
        maximum_generation_retries=config.get('maximum_generation_retries', 0),
        worker_tools=('bash', 'read'), command_tools=DEPUTY_TOOLS,
        **{key: config[key] for key in ('maximum_tool_argument_characters', 'maximum_read_lines') if key in config})


def _bindings(config: dict[str, Any], root: Path) -> tuple[ModelClient, SandboxedShell]:
    return client_for(deputy_spec(config), observe_model(root), config), shell_for(config, root)


def _archive(root: Path, why: str) -> None:
    """Put the session aside (its journal stays readable) and note the break in the turn log."""
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    aside = root/('session.'+stamp)
    aside.mkdir()
    for name in ('state.sqlite3', 'events', 'scratch'):
        if (root/name).exists():
            shutil.move(str(root/name), str(aside/name))
    _turn(root, 'system', 'The Deputy\'s memory was reset: '+why+'. The conversation above is on record; the seat starts fresh.')


def _turn(root: Path, role: str, text: str, **extra: Any) -> dict[str, Any]:
    line = dict(at=time.time(), role=role, text=text, **extra)
    with (root/'turns.jsonl').open('a') as handle:
        handle.write(json.dumps(line)+'\n')
    return line


def turns(root: Path) -> list[dict[str, Any]]:
    path = root/'turns.jsonl'
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def open_or_create(config: dict[str, Any], root: Path, text: str) -> tuple[FocusedSession, bool]:
    """The one session, resumed under whatever the seat runs on now. A model or sandbox that changed since the
    last turn is taken up by `rebind`: the new model writes the working memory from the old window and continues
    on it. The record notes the change; nothing is archived. The prompt files are re-read on every turn, so an
    edit to one reaches the standing seat at its next request."""
    worker, shell = _bindings(config, root)
    generation = dict(deputy_spec(config)['generation'])
    if (root/'state.sqlite3').exists():
        before = _model_on_record(root)
        try:
            session = FocusedSession.rebind(root, worker=worker, shell=shell, generation=generation,
                                            worker_system=policy_text(config))
        except ValueError as refused:
            if 'pending' not in str(refused):
                raise
            # A model call torn by an outage was never discarded: open under the old bindings, drop it (the
            # rest of the turn is the person's to say again), then rebind.
            torn = FocusedSession.open(root, worker=worker, shell=shell) if not _bindings_changed(root, worker, shell) else None
            if torn is None:
                raise
            discarded = torn.discard_pending()
            if discarded:
                (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')
            session = FocusedSession.rebind(root, worker=worker, shell=shell, generation=generation,
                                            worker_system=policy_text(config))
        now = generation.get('model') or ''
        if before and now and before != now:
            _turn(root, 'system', 'Now on '+now+' (was '+before+'); the Deputy carries its memory over.')
        _note_model(root, now)
        return session, False
    _note_model(root, generation.get('model') or '')
    return FocusedSession.create(root, settings_for(config, text), worker=worker, shell=shell), True


def _bindings_changed(root: Path, worker: Any, shell: Any) -> bool:
    """Whether a plain `open` would refuse: the saved model or sandbox bindings differ from these."""
    try:
        FocusedSession.open(root, worker=worker, shell=shell)
        return False
    except ValueError:
        return True


def _model_on_record(root: Path) -> str:
    try:
        return (root/'model').read_text().strip()
    except OSError:
        return ''


def _note_model(root: Path, model: str) -> None:
    if model and model != _model_on_record(root):
        (root/'model').write_text(model+'\n')


SHOW_COMMAND = re.compile(r'mizpah\.draft show (\S+)')
DISCARD_COMMAND = re.compile(r'mizpah\.draft discard (\S+)')


def showing_after(session: FocusedSession, since: int) -> dict[str, Any] | None | bool:
    """What the desk should show after this turn: the last draft shown (None to clear it after a discard,
    False when nothing about the desk changed). Read off the journal, so the model's words never decide it."""
    events = session.journal.read('session')[since:]
    result: dict[str, Any] | None | bool = False
    outcomes = {e.payload.get('call_id'): e.payload for e in events if e.event_type == 'tool_outcome'}
    for event in events:
        if event.event_type != 'command_tool':
            continue
        outcome = outcomes.get(event.payload.get('call_id')) or {}
        if outcome.get('status') != 'ok' or '"status": "ok"' not in (outcome.get('stdout') or ''):
            continue
        shown = SHOW_COMMAND.search(event.payload.get('command', ''))
        if shown:
            result = dict(draft=shown.group(1).strip('\'"'))
        gone = DISCARD_COMMAND.search(event.payload.get('command', ''))
        if gone and isinstance(result, dict) and result.get('draft') == gone.group(1).strip('\'"'):
            result = None
        elif gone and result is False:
            result = None
    return result


def say(config: dict[str, Any], text: str, *, turn_cap: int | None = None) -> dict[str, Any]:
    root = deputy_root()
    if not text.strip():
        raise SystemExit(json.dumps(dict(status='error', error='nothing said')))
    cap = turn_cap or (config['mizpah'].get('deputy') or {}).get('turn_cap') or DEFAULT_TURN_CAP
    (root/'STOP').unlink(missing_ok=True)   # a stop is for one turn; a stale one must not end the next
    _turn(root, 'user', text)
    session, fresh = open_or_create(config, root, text)
    since = len(session.journal.read('session'))
    before = session.progress.turns
    started = time.time()
    activity = root/'activity.json'

    def note_activity(io: dict[str, Any] | None) -> None:
        # What the seat is doing right now, for the office to show: the command running, or 'thinking'.
        activity.write_text(json.dumps(dict(at=time.time(), **(io or {})))+'\n')

    session.on_activity = note_activity
    note_activity(None)
    try:
        if not fresh:
            status = session.status()
            if status['status'] == 'blocked':
                _archive(root, status.get('blocked_reason') or 'the session was blocked')
                session, fresh = open_or_create(config, root, text)
                since, before = 0, 0
            elif status['phase'] == 'complete':
                session.continue_with(text)
            else:
                session.interject(text)
        outcome = session.run(maximum_worker_turns=cap, stop_when=lambda: (root/'STOP').exists())
    except ModelTransportError as error:
        from . import ops
        ops.record_outage(root, 'deputy', deputy_spec(config), error, 1)
        discarded = session.discard_pending()   # the torn call is not replayed; the next turn starts clean
        if discarded:
            (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')
        line = _turn(root, 'system', 'The Deputy\'s model did not answer: '+str(error)[:300], error=True)
        return dict(status='error', error=str(error), turn=line)
    except (ContextCapacityExceeded, GenerationRetryExceeded) as error:
        line = _turn(root, 'system', 'The Deputy could not finish this turn: '+str(error)[:300], error=True)
        return dict(status='error', error=str(error), turn=line)
    finally:
        session.shell.close_network()
        activity.unlink(missing_ok=True)
    reply = outcome.get('final_text') or ''
    if outcome.get('status') == 'stopped':
        (root/'STOP').unlink(missing_ok=True)
        reply = reply or '(stopped — '+str(outcome['completed_worker_turns']-before)+' tool calls in; say something to continue)'
    elif outcome.get('status') == 'paused' and not reply:
        reply = '(still working — '+str(outcome['completed_worker_turns'])+' tool calls and no answer yet; say something to continue)'
    desk = showing_after(session, since)
    showing_path = root/'showing.json'
    if desk is None:
        showing_path.unlink(missing_ok=True)
    elif desk is not False:
        showing_path.write_text(json.dumps(desk)+'\n')
    current = json.loads(showing_path.read_text()) if showing_path.exists() else None
    line = _turn(root, 'deputy', reply, showing=current, seconds=round(time.time()-started, 1),
                 tool_calls=outcome['completed_worker_turns']-before, stopped=outcome.get('status') == 'stopped')
    return dict(status='ok', reply=reply, showing=current, turn=line, session=outcome.get('status'))


def stop(config: dict[str, Any]) -> dict[str, Any]:
    """End the turn under way at its next step: the tool running finishes, nothing further starts, and the
    reply so far is what goes on record. A no-op when nothing is running."""
    root = deputy_root()
    (root/'STOP').touch()
    return dict(status='ok', root=str(root))


def status(config: dict[str, Any]) -> dict[str, Any]:
    root = deputy_root()
    showing_path = root/'showing.json'
    out = dict(status='ok', root=str(root), turns=len(turns(root)), session=(root/'state.sqlite3').exists(),
               showing=json.loads(showing_path.read_text()) if showing_path.exists() else None,
               model=deputy_spec(config).get('generation', {}).get('model'), gyms=str(init_module.gyms_root()),
               drafts=[draft_module.summary(p) for p in draft_module.listing()])
    return out


def reset(config: dict[str, Any]) -> dict[str, Any]:
    root = deputy_root()
    if (root/'state.sqlite3').exists():
        _archive(root, 'reset by the Administrator')
    return dict(status='ok', root=str(root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='mizpah.deputy', description=__doc__.split('\n\n')[0])
    parser.add_argument('--config', type=Path, required=True, help='the engine config (config.<name>.json)')
    sub = parser.add_subparsers(dest='verb', required=True)
    p = sub.add_parser('say')
    p.add_argument('text')
    p.add_argument('--turn-cap', type=int)
    sub.add_parser('status')
    sub.add_parser('stop', help='end the turn under way at its next step')
    sub.add_parser('reset')
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.verb == 'say':
        out = say(config, args.text, turn_cap=args.turn_cap)
    elif args.verb == 'status':
        out = status(config)
    elif args.verb == 'stop':
        out = stop(config)
    else:
        out = reset(config)
    print(json.dumps(out))
    return 0 if out.get('status') == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
