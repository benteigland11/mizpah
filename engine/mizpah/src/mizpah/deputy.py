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

from . import draft as draft_module, init as init_module, prompts
from .worker import (
    FocusedSession, ModelClient, ModelTransportError, NetworkPolicy, ReviewPolicy, SandboxedShell, SessionPolicy,
    SessionSettings, ShellConfig, ShellLimits, client_for, load_config, observe_model, string,
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


# The Deputy's verbs. Every one is a host operation on the gyms directory — a draft made, written, shown,
# discarded, a brief read — so they run in this process (`host: True`), never through a sandbox: a turn's tool
# calls cost milliseconds, not a bwrap and a systemd unit each. The seat has no bash and no file tools; building
# an environment is a worker's job in its own sandbox, and the Deputy only writes that brief.
DEPUTY_TOOLS: tuple[dict[str, Any], ...] = (
    dict(name='draft_new', host=True, description='Set up a gym: a training ground in a named environment, with an empty '
         'brief (title and mission set, status draft). Every gym has an environment: the one named, or when none is '
         'named the default (the line from the host under each message says which is the default and what each '
         'provides). Settle it with the Administrator first when the work needs more than the default.',
         parameters=dict(type='object', properties=dict(slug=string('kebab-case name, e.g. ornith-landing'),
                                                        title=string('the brief title, a few words'),
                                                        mission=string('one or two sentences: what is built or found out, and how it is proved'),
                                                        environment=string('a saved environment name; omit for the default')),
                         required=['slug', 'title', 'mission'])),
    dict(name='draft_write', host=True, description="Write a draft's brief in one call: needs, deliverables, non-goals, budget, "
         'and a new mission or environment if they change. A list given replaces that list on the sheet whole (send '
         'the full list, reworded where needed); a list left out stays as it is. It puts the sheet on the desk.',
         parameters=dict(type='object', properties=dict(
             slug=string('the draft'),
             title=string('a new title, if it changes'),
             mission=string('a new mission, if it changes'),
             needs=dict(type='array', items=dict(type='string'), description="every need, in order, in the Administrator's words"),
             deliverables=dict(type='array', items=dict(type='string'), description='every deliverable: one artifact by path, what it holds, the needs it draws on by number'),
             non_goals=dict(type='array', items=dict(type='string'), description='constraints on method, the forbidden term in backticks'),
             budget_points=dict(type='integer', description='total effort in route points'),
             budget_notes=string('one sentence: the horizon and why'),
             environment=string('a saved environment name, if it changes')),
             required=['slug'])),
    dict(name='environment_new', host=True, description='Start a new saved environment: an empty directory under /work '
         'named for it, with its record. Then set it up in bash at /work/<name> — a venv, packages, downloaded programs, '
         'wrappers in bin/ (the method is in your instructions) — and finish it with environment_finish. Every gym set '
         'up in it afterwards gets the directory mounted read-only with its env set and its note told to the worker.',
         parameters=dict(type='object', properties=dict(name=string("the environment's name, e.g. lean (lowercase, digits, - and _)"),
                                                        note=string('one line for now: what it will provide; the full note comes at finish')),
                         required=['name', 'note'])),
    dict(name='environment_finish', host=True, description='Finish (or revise) an environment: the note the next worker reads '
         'verbatim — each tool, how to call it, where its data is ($BASE/…), what it cannot do — and the env with '
         '$BASE paths. It checks the environment is usable from any gym (relocatable, has a note) and says what is wrong.',
         parameters=dict(type='object', properties=dict(name=string('the environment'),
                                                        note=string('the full note, one paragraph'),
                                                        env=dict(type='object', additionalProperties=dict(type='string'),
                                                                 description='NAME -> value, with $BASE for the environment\'s own path')),
                         required=['name', 'note'])),
    dict(name='brief_show', host=True, description='Read a brief as it stands — any gym, draft or issued — and pull it up on '
         'the desk. Reading is always the Administrator\'s to ask for; changing an issued brief is not yours.',
         parameters=dict(type='object', properties=dict(slug=string('the gym')), required=['slug'])),
    dict(name='draft_discard', host=True, description='Remove a draft the Administrator no longer wants, with everything in it. '
         'Ask once first.',
         parameters=dict(type='object', properties=dict(slug=string('the draft to remove')), required=['slug'])),
)


def _verb(fn: Any) -> Any:
    """A draft function as a handler: its refusals (SystemExit carrying JSON) become the error the model reads."""
    def call(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return fn(**args)
        except SystemExit as refused:
            try:
                return json.loads(str(refused.code if refused.code is not None else refused))
            except (ValueError, TypeError):
                return dict(status='error', error=str(refused))
        except TypeError as error:
            return dict(status='error', error='bad arguments: '+str(error))
    return call


def _brief_show(slug: str) -> dict[str, Any]:
    project = draft_module.draft_dir(slug)
    brief = draft_module.brief_of(project)
    if not brief:
        raise SystemExit(json.dumps(dict(status='error', error='no gym named '+slug+'; drafts: '+', '.join(p.name for p in draft_module.listing()))))
    keep = ('title', 'status', 'mission', 'environment', 'needs', 'deliverables', 'non_goals', 'budget_points', 'budget_notes',
            'enablers', 'phases')
    return dict(status='ok', showing=dict(draft=slug), brief={k: brief.get(k) for k in keep if brief.get(k) not in (None, [], '')})


def _environment_new(name: str, note: str) -> dict[str, Any]:
    from . import bases
    try:
        made = bases.create(name, note=note)
    except FileExistsError:
        raise SystemExit(json.dumps(dict(status='error', error='an environment named '+name+' exists; environment_finish revises it, or pick another name')))
    except ValueError as error:
        raise SystemExit(json.dumps(dict(status='error', error=str(error))))
    return dict(status='ok', name=name, path='/work/'+name, host_path=made['path'],
                next='set it up in bash under /work/'+name+' (venv/, tools/, bin/), then environment_finish')


def _environment_finish(name: str, note: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    from . import bases
    try:
        bases.update(name, note=note, env=env)
        report = bases.check(name)
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(json.dumps(dict(status='error', error=str(error))))
    return dict(status='ok' if report['ok'] else 'error', **({} if report['ok'] else dict(error='; '.join(report['problems']))), **report)


def handlers() -> dict[str, Any]:
    return dict(draft_new=_verb(draft_module.new), draft_write=_verb(draft_module.write), brief_show=_verb(_brief_show),
                draft_discard=_verb(draft_module.discard), environment_new=_verb(_environment_new),
                environment_finish=_verb(_environment_finish))


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
    """The Deputy's one use of a shell: setting up environments. `/work` is the environments directory — every base
    is a directory there the Deputy may fill (a venv, downloaded programs, wrappers) — with the package hosts
    reachable, so a toolchain is downloaded once and every gym set up in that environment gets it mounted. Drafts
    are never touched from here; they are host verbs. The gyms are not mounted at all."""
    sandbox = config['mizpah']['sandbox']
    from . import bases
    bases_root = bases.bases_root()
    scratch = root/'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    environment = dict(sandbox['environment'], MIZPAH_BASES='/work', HOME='/work/.home',
                       PIP_CACHE_DIR='/work/.cache/pip', PLAYWRIGHT_BROWSERS_PATH='/work/.cache/playwright-download')
    binds = tuple(dict.fromkeys(sandbox['read_only_binds']))
    network = None
    share = bool(sandbox.get('share_network', False))
    if sandbox.get('network'):
        # The host's proxy, with the package hosts an environment build fetches from.
        network = NetworkPolicy(**(sandbox['network'] | dict(allowed_domains=list(dict.fromkeys(
            list(sandbox['network'].get('allowed_domains') or [])+list(draft_module.BUILD_DOMAINS))))))
        share = False
    shell = ShellConfig(**(config['shell'] | dict(
        scratch_root=str(scratch), limits=ShellLimits(**config['shell']['limits']), read_only_binds=binds,
        environment=environment, share_network=share, services=None, network=network,
        # The engine's own documents and prompt are not its to read: a seat that reads them spends its turns
        # orienting instead of working (one turn grepped the docs for "environment gym" and read its own prompt).
        refused_paths=tuple(sandbox.get('refused_paths') or ())+tuple(
            str(Path(p).parent.parent.parent/d) for p in [config['mizpah_config_path']] for d in ('docs', 'app', 'engine')),
        refused_patterns=((r'\bmizpah\.(loop|worker|init|draft|deputy)\b|\bterra\b', 'drafts and briefs are verbs, not commands; this shell is for environments'),),
        workspace_dir=str(bases_root), cache_dirs=('.cache',), state_dirs=('.tool-output', '.session-history', '.home'))))
    return SandboxedShell(shell)


PROMPTS_DIR = Path(__file__).parents[4]/'prompts'   # the repo's, when no config names one


def policy_text(config: dict[str, Any] | None = None) -> str:
    """The system prompt: the pieces `prompts/order_deputy.txt` lists, composed like the other seats'
    (`mizpah.prompts.compose`) from the config's `prompts_dir`."""
    folder = Path(config['prompts_dir']) if config is not None and config.get('prompts_dir') else PROMPTS_DIR
    return prompts.compose('deputy', folder)


def settings_for(config: dict[str, Any], assignment: str) -> SessionSettings:
    spec = deputy_spec(config)
    policy = policy_text(config)
    return SessionSettings(assignment, policy, None, spec['generation'], SessionPolicy(**config['session_policy']),
        ReviewPolicy(**config['review_policy']), None, False, config['guidance_prefix'],
        maximum_generation_retries=config.get('maximum_generation_retries', 0),
        worker_tools=('bash',), command_tools=DEPUTY_TOOLS,
        maximum_tool_argument_characters=max(int(config.get('maximum_tool_argument_characters') or 0), 8000),
        **{key: config[key] for key in ('maximum_read_lines',) if key in config})


def _bindings(config: dict[str, Any], root: Path) -> tuple[ModelClient, SandboxedShell]:
    return client_for(deputy_spec(config), observe_model(root), config), shell_for(config, root)


def _archive(root: Path, why: str) -> Path:
    """Put the session aside (its journal stays readable) and note the break in the turn log."""
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    aside = root/('session.'+stamp)
    aside.mkdir()
    for name in ('state.sqlite3', 'events', 'scratch'):
        if (root/name).exists():
            shutil.move(str(root/name), str(aside/name))
    _turn(root, 'system', 'The Deputy\'s memory was reset: '+why+'. The conversation above is on record; the seat starts fresh.')
    return aside


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
        # A killed process (the app closed mid-turn) leaves a call unanswered; the rebind drops it and continues.
        session = FocusedSession.rebind(root, worker=worker, shell=shell, generation=generation,
                                        worker_system=policy_text(config), discard_pending=True, handlers=handlers())
        now = generation.get('model') or ''
        if before and now and before != now:
            _turn(root, 'system', 'Now on '+now+' (was '+before+'); the Deputy carries its memory over.')
        _note_model(root, now)
        return session, False
    _note_model(root, generation.get('model') or '')
    return FocusedSession.create(root, settings_for(config, text), worker=worker, shell=shell, handlers=handlers()), True


def _model_on_record(root: Path) -> str:
    try:
        return (root/'model').read_text().strip()
    except OSError:
        return ''


def _note_model(root: Path, model: str) -> None:
    if model and model != _model_on_record(root):
        (root/'model').write_text(model+'\n')


SHOW_COMMAND = re.compile(r'^(?:draft_show|draft_write|brief_show) \{.*?"slug": "([^"]+)"')   # a write or a read puts the sheet on the desk
DISCARD_COMMAND = re.compile(r'^draft_discard \{.*?"slug": "([^"]+)"')


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


def situation(root: Path) -> str:
    """What the desk holds, appended to each line the model hears: the drafts by slug and title, and the one on the
    desk. Read off disk by the host, so the seat never spends turns surveying /work to learn what it has."""
    drafts = [draft_module.summary(p) for p in draft_module.listing()]
    showing_path = root/'showing.json'
    shown = None
    if showing_path.exists():
        try:
            shown = (json.loads(showing_path.read_text()) or {}).get('draft')
        except ValueError:
            shown = None
    envs = draft_module.environments()
    lines = ['[Desk, from the host: environments: '+', '.join(e['name']+(' (default)' if e['default'] else '') for e in envs)
             +'; '+('drafts: '+'; '.join(
        d['slug']+' ('+(d['title'] or 'untitled')+', '+str(d['needs'])+' needs, '+str(d['deliverables'])+' deliverables'
        +(', env '+d['environment'] if d.get('environment') else '')+')' for d in drafts) if drafts else 'no drafts')
        +('; on the desk: '+shown if shown else '; the desk is clear')+']']
    return '\n'.join(lines)


def say(config: dict[str, Any], text: str, *, turn_cap: int | None = None) -> dict[str, Any]:
    root = deputy_root()
    if not text.strip():
        raise SystemExit(json.dumps(dict(status='error', error='nothing said')))
    heard = text.rstrip()+'\n\n'+situation(root)   # the record keeps the words as typed; the model hears the desk too
    cap = turn_cap or (config['mizpah'].get('deputy') or {}).get('turn_cap') or DEFAULT_TURN_CAP
    (root/'STOP').unlink(missing_ok=True)   # a stop is for one turn; a stale one must not end the next
    # The person's line goes on record once the seat is open: a seat that fails to open leaves the error
    # on the record instead of a line nobody answered.
    try:
        session, fresh = open_or_create(config, root, heard)
    except Exception as error:  # noqa: BLE001 — whatever it was, the person sees it where they spoke
        line = _turn(root, 'system', 'The seat could not be opened: '+str(error)[:300], error=True)
        return dict(status='error', error=str(error), turn=line)
    _turn(root, 'user', text)
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
            if status.get('pending_io') is not None:
                # The last turn was cut off mid-call (the app closed, the process was killed). The torn call is
                # dropped and the seat continues from where it was; nothing about its memory changes.
                discarded = session.discard_pending()
                if discarded:
                    (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')
                    _turn(root, 'system', 'The previous turn was cut off before it finished; its last '
                          +('command' if discarded.get('kind') == 'tool' else 'model call')+' was discarded and the seat continues.')
                status = session.status()
            if status['status'] == 'blocked':
                # Only a session the harness itself blocked (the window cannot fit, retries spent) starts over.
                _archive(root, status.get('blocked_reason') or 'the session was blocked')
                session, fresh = open_or_create(config, root, heard)
                since, before = 0, 0
            elif status['phase'] == 'complete':
                session.continue_with(heard)
            elif status['phase'] == 'worker':
                session.interject(heard)
            else:
                # Cut off inside a tool batch or a handoff: run that boundary through first, then the person's line
                # lands as guidance at the next worker step.
                session.run(maximum_worker_turns=1, stop_when=lambda: False)
                if session.state['phase'] == 'complete':
                    session.continue_with(heard)
                else:
                    session.interject(heard)
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
    """Clear everything: the seat's memory and the conversation on the desk. Nothing is deleted — the session
    and the turn log go aside under `session.<stamp>/`, readable on disk — but the office starts empty."""
    root = deputy_root()
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    aside = _archive(root, 'reset by the Administrator') if (root/'state.sqlite3').exists() else root/('session.'+stamp)
    aside.mkdir(exist_ok=True)
    for name in ('turns.jsonl', 'showing.json', 'activity.json', 'STOP'):
        if (root/name).exists():
            shutil.move(str(root/name), str(aside/name))
    return dict(status='ok', root=str(root), aside=str(aside))


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
