"""The worker: one Terra route task, one focused session, held open until the gate is green.

The worker never sees the brief. Its assignment is the task text plus the unknown it
resolves; its tools are the harness's bash/read/write/edit under the v10 bounds; the
sandbox carries the terra and playbook CLIs read-only. It works on its own task map
(a Terra session map under the project map): runs, the unknown and the known it births
land there, and the only way onto the project map is `terra known adopt`, which enforces
Terra's admission bar. The gate is mechanical, read from Terra's files after each time
the model stops; red is fed back as the delta and the same session continues, green
unlocks the one thing a worker may only do after green — writing the method it followed
into the playbook — and the playbook store is copied into the workspace, so only what is
harvested after green ever reaches the real library.
"""
from __future__ import annotations

import argparse
import io
import json
from dataclasses import asdict, replace
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import time
from typing import Any

from . import layout, ops

from cg.bp_focused_agent_session_python.src import (
    ControllerSettings, EndpointConfig, FocusedSession, ModelClient, ReviewPolicy, SandboxedShell, SessionPolicy,
    NetworkPolicy, ServiceLimits, SessionSettings, ShellConfig, ShellLimits, llama_model_client,
)
from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError, RejectedGeneration

# Workspace paths that never go back into the project: the harness's own, and the
# playbook copy, which is harvested separately and only after green.
PLAYBOOK_PREFIX = '.playbook'
WRITEBACK_EXCLUDE = ('.tool-output/', '.session-history', PLAYBOOK_PREFIX+'/')
# Project paths that never enter the worker's workspace.
PACK_EXCLUDE = ('.git', '.venv', '__pycache__')
# Bind mode: the project directory is /work; these stay snapshot-managed (tmpfs over the bind, tar in, write-back
# out with the map's entitlements), so a worker's reach into `.terra/` is exactly what it is in snapshot mode.
STATE_DIRS = (PLAYBOOK_PREFIX, '.svc', '.tool-output', '.session-history')


def state_dirs(project: Path) -> tuple[str, ...]:
    """The project's state directory (its `.mizpah` or legacy `.terra`) and the worker's own: in bind mode
    these stay snapshot-managed under a tmpfs over the bind."""
    return (layout.dirname(project),)+STATE_DIRS


def bind_mode(config: dict[str, Any]) -> bool:
    return str(config['mizpah']['sandbox'].get('workspace') or 'snapshot') == 'bind'


def cache_dirs(config: dict[str, Any]) -> tuple[str, ...]:
    return tuple(config['mizpah']['sandbox'].get('cache_dirs') or ())


def scratch_dirs(config: dict[str, Any]) -> tuple[str, ...]:
    """The worker's scratch inside /work: host-backed, uncapped, never packed. `workspace_bytes` used to
    size the sandbox's whole writable disk as well as the evidence a tar may carry; a media build needs a
    great deal of the first and none of the second."""
    return tuple(config['mizpah']['sandbox'].get('scratch_dirs') or ())


def evidence(session: Any) -> bytes:
    """The workspace as tar bytes whichever mode the shell runs in: what harvest and the checklists read."""
    snapshot = getattr(session, 'workspace_snapshot', None)
    return snapshot() if callable(snapshot) else session.workspace()


def state_of(workspace: Any) -> bytes:
    """The snapshot-managed part (all of it in snapshot mode; the state tar in bind mode): what write-back reads."""
    return workspace if isinstance(workspace, (bytes, bytearray)) else bytes(getattr(workspace, 'state', b''))
# Library procedures that are the loop's own method, not a worker's to rewrite or get credit for.
BOOTSTRAP_PROCEDURES = ('mizpah-resolve-unknown', 'mizpah-build-environment')
# A bucket is the mode of work, not just its price (points 3 / 8 / 21 on the route).
BUCKET_MODES = {'low': 'implement, the path is known', 'medium': 'validate, weigh a couple of options then conclude',
                'high': 'explore, several options in parallel before choosing'}
# The forced bootstrap walk (scaffolding); with it off, the worker is told where method and parts live and left to it.
BOOTSTRAP_WALK = ('Your first act on every task is `playbook open mizpah-resolve-unknown --for "<the task>"`: it writes '
                  'the whole method as a checklist under `.playbook/open/`. Read that file once and follow it in order, '
                  'ticking steps off; it tells you where domain procedures and Cartograph parts come in. Open a domain '
                  'procedure the same way, one copy per thing you walk it for.')
FREE_METHOD = ('Method lives in the playbook (`playbook search <words>`, then `playbook start <id>` walks a procedure one step '
               'at a time; `mizpah-resolve-unknown` is the generic one) and parts live in Cartograph (`cartograph search '
               '<words>`; a widget you build is checked in after green). Look before you build; how you order the work is yours.')
CONFIDENCE_RANK = dict(low=0, med=1, high=2)


# Whole-file bounds: a widget module, a probe with its helpers, a page of prose — one call each. The 60K-era caps
# (2000/1500/1500) rejected two edits in the first five turns of a benchmark attempt on a model with the room.
WHOLE_FILE_BOUNDS = (('maximum_write_characters', 20000), ('maximum_edit_characters', 12000), ('maximum_tool_argument_characters', 24000))


def load_config(path: str | Path) -> dict[str, Any]:
    """Mizpah config layered over the harness config it names; paths resolve from each file."""
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    harness_path = (path.parent/config['harness_config']).resolve()
    harness = json.loads(harness_path.read_text())
    from . import prompts as _prompts
    prompts_dir = (path.parent/config.get('prompts_dir', '../../prompts')).resolve()
    config['prompts_dir'] = str(prompts_dir)
    _prompts.set_messages_dir(prompts_dir)
    config['worker_policy'] = _prompts.compose('worker', prompts_dir)
    # The window's messages are pieces too: the config's handoff_prompt / resume_prefix give way to them.
    sp = harness.setdefault('session_policy', {})
    sp['handoff_prompt'] = _prompts.message('handoff_worker')
    sp['reflect_prompt'] = _prompts.message('reflect_worker')
    sp.setdefault('reflect_turns', -1)   # as many turns as the reflection needs; the buffer ends it
    sp['resume_prefix'] = _prompts.message('resume_worker')
    harness['guidance_prefix'] = (prompts_dir/'messages'/'correction_worker.md').read_text()   # a template; the harness fills it
    # Scaffolding is method the host imposes; each piece is a toggle so a model that can orchestrate
    # can be run without it and compared. Verification guards are not toggles.
    # tick_guards: a tick rides on the step's work, never in a loop or a list (off 2026-09-22: the walk's plan is
    # the read, and after it the model closes steps by its own judgement; on for a model that needs the rail).
    scaffolding = dict(bootstrap=True, checkins=True, command_tools=True, tick_guards=False) | (config.get('scaffolding') or {})
    scaffolding.pop('small_edits', None)   # retired: whole files for every model
    config['scaffolding'] = scaffolding
    # Whole files at once, for every model: the write/edit caps are at least a whole widget module. Small edits
    # were a 60K-window rule (a truncated payload lost the model its place) and are retired (2026-09-21); a model
    # writes a coherent forty lines in one call where the rule took fourteen (pedal gym, 2026-09-20).
    for key, limit in WHOLE_FILE_BOUNDS:
        harness[key] = max(int(harness.get(key) or 0), limit)
    config['controller_policy'] = _prompts.compose('controller', prompts_dir)   # one controller, one loop: no route/eval modes
    config['checkin_policy'] = _prompts.compose('reviewer', prompts_dir)
    config['playbook_store'] = str(Path(config['playbook_store']).expanduser())
    config['widget_library'] = str(Path(config['widget_library']).expanduser())
    return dict(harness, mizpah=config, harness_config_path=str(harness_path), mizpah_config_path=str(Path(path).resolve()))


SMALL_EDITS = ('Every file is built in pieces, and the tools enforce it: `write` creates a file once, as a skeleton — imports, '
               'signatures, docstrings, `pass` bodies, or a short file — and `edit` adds one function body, one branch or one '
               'test per call, a few lines each. Anything longer is refused unexecuted, so never attempt it: when a function '
               'would be long, first add the small helpers it needs, one per edit, then a body that only calls them. To change '
               'something substantially, read it, delete the block with bash (`sed -i \'START,ENDd\' FILE`), then rebuild it the '
               'same way — skeleton, then pieces. A scaffold stub you must replace (a widget\'s `src`/`tests`/`examples`) is '
               '`rm`\'d and rebuilt like that; only a probe\'s `measure.py` — a few lines by design, one probe per unknown — is '
               'written whole.')
WHOLE_FILES = ('Write a file whole when you know what goes in it (`write` replaces an existing file too), and `edit` for a '
               'change to part of one. A scaffold stub you must replace (a widget\'s `src`/`tests`/`examples`) is overwritten '
               'with `write`.')


def terra(config: dict[str, Any], project: Path, *args: str) -> dict[str, Any]:
    """Run one JSON-printing terra command against the project and return its data."""
    process = subprocess.run([config['mizpah']['terra'], *args], cwd=project, capture_output=True, text=True,
                             env=dict(os.environ, **layout.terra_env(project)))
    text = process.stdout.strip()
    start = text.find('{')
    if start < 0:
        # A few verbs print prose (map create); the exit code is their contract.
        if process.returncode != 0:
            raise RuntimeError('terra '+' '.join(args)+' failed: '+(process.stderr or text)[:500])
        return dict(text=text)
    payload = json.loads(text[start:])
    if payload.get('status') != 'success':
        raise RuntimeError('terra '+' '.join(args)+' failed: '+json.dumps(payload.get('error')))
    return payload['data']


def pick_task(config: dict[str, Any], project: Path, task_id: str | None = None) -> dict[str, Any]:
    """The route's next pickable task, or the named one; started under the worker's agent id."""
    tasks = terra(config, project, 'route', 'next')['tasks']
    if task_id is not None:
        tasks = [task for task in tasks if task['id'] == task_id]
        # A task this agent already started is resumed, not started again: a killed run leaves it in_progress.
        mine = [t for t in tasks if t.get('status') == 'in_progress' and t.get('owner_agent') == config['mizpah']['agent']]
        if mine:
            return mine[0]
        # Done on the route but its session never finished (killed after `route complete`, in the review or
        # the write-up): resumed too, so the write-up lands; `route next` does not list done tasks.
        done = [t for t in terra(config, project, 'route', 'status')['tasks'] if t['id'] == task_id and t.get('status') == 'done']
        if done:
            return done[0]
    pickable = [task for task in tasks if task.get('pickable')]
    if not pickable:
        raise RuntimeError('No pickable route task'+(' '+task_id if task_id else ''))
    task = pickable[0]
    if task.get('map_id') is None:
        raise RuntimeError('Task '+task['id']+' resolves no unknown; the worker only takes claim-shaped tasks')
    terra(config, project, 'route', 'start', task['id'], '--agent', config['mizpah']['agent'])
    return task


def task_map_id(task: dict[str, Any]) -> str:
    return 't_'+task['id']


def task_unknown_ids(task: dict[str, Any]) -> list[str]:
    """The unknown in map_id plus any `unknown:<id>` acceptance entries: every unknown this task resolves."""
    ids = [task['map_id']] if task.get('map_id') else []
    for entry in task.get('acceptance') or []:
        if isinstance(entry, str) and entry.startswith('unknown:') and entry[8:] not in ids:
            ids.append(entry[8:])
    return ids


def read_unknown(project: Path, unknown_id: str, map_id: str | None = None) -> dict[str, Any]:
    base = layout.map_root(project) if map_id is None else (project/layout.dirname(project)/'map' if map_id == 'global'
                                                            else project/layout.dirname(project)/'map'/'sessions'/map_id)
    return json.loads((base/'unknowns'/(unknown_id+'.json')).read_text())


def read_known(project: Path, known_id: str, map_id: str | None = None) -> dict[str, Any] | None:
    base = layout.map_root(project) if map_id is None else (project/layout.dirname(project)/'map' if map_id == 'global'
                                                            else project/layout.dirname(project)/'map'/'sessions'/map_id)
    path = base/'knowns'/(known_id+'.json')
    return json.loads(path.read_text()) if path.exists() else None


def open_task_map(config: dict[str, Any], project: Path, task: dict[str, Any], map_id: str | None = None) -> str:
    """A session map for the task with a copy of its unknown; unknowns do not read through. A task that continues
    another's workspace keeps that workspace's map (`map_id`): the readings it re-takes land beside the ones it
    took, and the sandbox binding (TERRA_MAP) does not change under a session that is being resumed — a new map
    per task left an orphan map with an open unknown that kept the gate red for a task working on the old one."""
    map_id = map_id or task_map_id(task)
    if not (project/layout.dirname(project)/'map'/'sessions'/map_id).exists():
        terra(config, project, 'map', 'create', map_id, '--purpose', 'route task '+task['id'], '--parent', layout.brief_map(project))
    for unknown_id in task_unknown_ids(task):
        unknown = read_unknown(project, unknown_id)
        if (project/layout.dirname(project)/'map'/'sessions'/map_id/'unknowns'/(unknown['id']+'.json')).exists():
            continue
        args = ['--map', map_id, 'unknown', 'create', unknown['id'], '--claim', unknown['claim'],
                '--evidence', unknown['evidence_needed'], '--type', unknown['type']]
        if unknown.get('quantity'):
            args += ['--quantity', unknown['quantity']]
        if unknown.get('unit'):
            args += ['--unit', unknown['unit']]
        if unknown.get('notes'):
            args += ['--notes', unknown['notes']]  # carries `cites need:N; source ...` for the check-in reference
        args += formula_args(unknown)
        terra(config, project, *args)
    return map_id


def composed(known: dict[str, Any]) -> bool:
    """A formula known is computed from other knowns, not read by a probe: the re-measure takes its inputs again and
    skips it. It asked the target's probe for a reading of the target, so every composed target failed the gate
    ("probe … did not produce a reading for target_…", turn before the music on GPT-6 Sol, 2026-09-22)."""
    return known.get('type') == 'formula'


def formula_args(unknown: dict[str, Any]) -> list[str]:
    """A formula unknown's expression and variables, in the form `terra unknown create` takes them. The copy onto
    a task map carried claim, evidence and type only, so the first target a controller composed (a turn-ahead
    gym on GPT-6 Sol, 2026-09-22) was refused on the task map and the driver died three times."""
    if unknown.get('type') != 'formula':
        return []
    args = ['--expression', str(unknown.get('expression') or '')]
    for name, spec in (unknown.get('vars') or {}).items():
        if isinstance(spec, dict) and spec.get('known_id'):
            args += ['--var', f"{name}=known:{spec['known_id']}"]
        elif isinstance(spec, dict) and spec.get('quantity'):
            args += ['--var', f"{name}={spec['quantity']}" + (f":{spec['kind']}" if spec.get('kind') else '')]
        elif isinstance(spec, str):
            args += ['--var', f'{name}={spec}']
    return args



def protected_probes(project: Path, task: dict[str, Any]) -> tuple[str, ...]:
    """Probes that predate this work order: instruments other knowns cite, not this worker's to change. Its own
    are the ones it creates (a probe is a general measurement the worker names; none is scaffolded for it)."""
    probes = project/layout.dirname(project)/'map'/'probes'
    return tuple(sorted(p.name for p in probes.iterdir() if p.is_dir())) if probes.is_dir() else ()


def probe_inputs(project: Path, task: dict[str, Any]) -> dict[str, list[str]]:
    """Unknown id → the map knowns its claim names: a probe for it declares them as inputs (`--input k=known:k`)
    and compares through ctx["inputs"] rather than re-deriving them."""
    result = {}
    for uid in task_unknown_ids(task):
        try:
            unknown = read_unknown(project, uid)
        except FileNotFoundError:
            continue
        named = [k for k in known_ids_named(project, unknown) if k != uid]
        if named:
            result[uid] = named
    return result


def known_ids_named(project: Path, unknown: dict[str, Any]) -> list[str]:
    """Known ids the unknown names: by id, or by the id's words ("the minimum passing parallel count" names
    min_parallel_count). The loose match over-declares now and then; an extra input in ctx costs nothing, while a
    reading that re-derives what the map holds cost the drone brief its whole second phase (p=4 hardcoded)."""
    text = ' '.join(str(unknown.get(k) or '') for k in ('claim', 'evidence_needed', 'notes'))
    exact = [word for word in dict.fromkeys(re.findall(r'[a-z][a-z0-9_]*', text))
             if '_' in word and read_known(project, word) is not None]
    words = set(re.findall(r'[a-z][a-z0-9_]*', text.lower()))
    own = set(known_words(str(unknown.get('id') or '')))
    loose = [path.stem for path in sorted((layout.map_root(project)/'knowns').glob('*.json'))
             if path.stem not in exact and path.stem != unknown.get('id') and names_known(words, path.stem)
             and not set(known_words(path.stem)) <= own]
    return exact+loose


def prior_readings(project: Path, unknowns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """For each unknown that already has a known on the brief's map — a repair or a re-measure — what it read."""
    out: dict[str, dict[str, Any]] = {}
    for unknown in unknowns:
        known = read_known(project, unknown['id'])
        if known is None:
            continue
        stats = known.get('stats') or {}
        out[unknown['id']] = dict(value=known.get('value'), rate=stats.get('rate'), n=stats.get('n'),
                                  run=known.get('primary_run_id'), probes=list(known.get('probe_ids') or []))
    return out


def render_assignment(task: dict[str, Any], unknowns: list[dict[str, Any]], map_id: str,
                      inputs: dict[str, list[str]] | None = None, state_dirname: str = layout.STATE_DIRNAME,
                      prior: dict[str, dict[str, Any]] | None = None, brief: dict[str, Any] | None = None) -> str:
    """The task, its unknowns and the map: nothing about method, and of the brief only the entries each unknown
    cites. `inputs` maps an unknown id to the knowns its probe declares; the worker reads them from ctx["inputs"].
    `prior` maps an unknown id to what it last read, when this task is a repair or a re-measure: the delta is
    put in front of the worker on its first turn (a repair task was told "make it pass" and not what failed,
    2026-09-20). `brief` supplies the cited entries' text: a builder told to make "the requested three-section
    piano passage" had never been told what was requested (changing-meter gym, 2026-09-21) — the request lives
    in the need the unknown cites, and the worker gets that line, not the brief."""
    lines = ['Route task `'+task['id']+'` (bucket '+task['bucket']+': '+BUCKET_MODES.get(task['bucket'], '')+'): '+task['title'],
             'It resolves '+('one unknown' if len(unknowns) == 1 else str(len(unknowns))+' unknowns')+':']
    for unknown in unknowns:
        lines += describe_unknown(unknown)
        cited = cited_entries(brief, unknown) if brief else []
        if cited:
            lines.append('  what the brief asks for, in its words (the entries this unknown serves):')
            lines += ['    '+ref+' — '+text for ref, text in cited]
        last = (prior or {}).get(unknown['id'])
        if last:
            value = last.get('value')
            verdict = ('false' if str(value) in ('0.0', '0', 'False', 'false') else 'true' if str(value) in ('1.0', '1', 'True', 'true')
                       else str(value))
            lines.append('  LAST READING: '+verdict+(' (rate '+str(last['rate'])+' over '+str(last['n'])+')' if last.get('rate') is not None else '')
                         +(', run `'+str(last['run'])+'`' if last.get('run') else '')
                         +(', by probe '+', '.join('`'+p+'`' for p in last['probes']) if last.get('probes') else '')
                         +('. This task exists because of that reading: read the probe\'s measure.py first to see exactly '
                            'what it counted, change the thing it reads so the count passes, then take the reading again. '
                            'Do not change the probe to make it pass.' if verdict == 'false' else
                            '. The thing it reads has changed since: take the reading again with the same probe.'))
    non_goals = [str(n) for n in ((brief or {}).get('non_goals') or []) if str(n).strip()]
    if non_goals:
        # Every work order carries the brief's non-goals: the lines the work must not cross, whatever the task.
        lines.append('Not asked for, by the brief (do not build, render or spend turns on these):')
        lines += ['  - '+n for n in non_goals]
    acceptance = [a for a in task.get('acceptance') or [] if not str(a).startswith('unknown:')]
    if acceptance:
        lines.append('Acceptance: '+'; '.join(acceptance))
    if task.get('enabler_id'):
        lines.append('This work order builds the enabler `'+str(task['enabler_id'])+'`: an instrument the brief needs before '
                     'its readings can be taken — a widget, installed at the path the unknown names, read true when it validates.')
    made = [unknown_notes(u)['creates'] for u in unknowns if unknown_notes(u).get('creates')] if not task.get('enabler_id') else []
    if made:
        lines.append('This work order makes '+', '.join('`'+m+'`' for m in made)+'.')
    lines.append('Your task map is `'+map_id+'` (TERRA_MAP is set). A probe runs with the project root as its working '
                 'directory: relative paths; do not derive the root from `__file__`.')
    ids = [u['id'] for u in unknowns]
    for uid, known_ids in (inputs or {}).items():
        lines.append('A probe for `'+uid+'` may declare the map knowns '+', '.join('`'+k+'`' for k in known_ids)+
                     ' as inputs: its measure() gets their values in ctx["inputs"].')
    lines.append('Done: `terra known adopt <known> --from '+map_id+'` for '+('it' if len(ids) == 1 else 'each of '+', '.join(ids))
                 +', then `terra route complete '+task['id']+' --run <run_id>'+''.join(' --known '+i for i in ids)+'`.')
    return '\n'.join(lines)+'\n'


def unknown_notes(unknown: dict[str, Any]) -> dict[str, str]:
    """`cites need:1; source orders.csv` → {'cites': 'need:1', 'source': 'orders.csv'}."""
    result: dict[str, str] = {}
    for part in (unknown.get('notes') or '').split(';'):
        key, _, value = part.strip().partition(' ')
        if key in ('cites', 'source', 'creates', 'also') and value.strip():
            result[key] = value.strip()
    return result


def cited_entries(brief: dict[str, Any], unknown: dict[str, Any]) -> list[tuple[str, str]]:
    """(ref, text) for every brief entry the unknown cites — the primary cite and the `also` list — in order,
    skipping references the brief no longer has."""
    notes = unknown_notes(unknown)
    refs = [notes.get('cites', '')]+[r.strip() for r in re.split(r'[|,]', notes.get('also', '')) if r.strip()]
    out: list[tuple[str, str]] = []
    for ref in refs:
        kind, _, index = ref.partition(':')
        entries = brief.get({'need': 'needs', 'deliverable': 'deliverables', 'non_goal': 'non_goals'}.get(kind, '')) or []
        if index.isdigit() and 1 <= int(index) <= len(entries) and (ref, str(entries[int(index)-1])) not in out:
            out.append((ref, str(entries[int(index)-1])))
    return out


def describe_unknown(unknown: dict[str, Any]) -> list[str]:
    notes = unknown_notes(unknown)
    lines = ['Unknown `'+unknown['id']+'` ('+unknown['type']+(', quantity '+unknown['quantity'] if unknown.get('quantity') else '')+
             (', unit '+unknown['unit'] if unknown.get('unit') else '')+'):',
             '  claim: '+unknown['claim'],
             '  evidence that resolves it: '+unknown['evidence_needed']]
    if notes.get('creates'):
        lines.append('  it is about an artifact this task builds: '+notes['creates'])
    elif notes.get('source'):
        lines.append('  read it from: '+notes['source'])
    if unknown.get('probe_id'):
        lines.append('  an instrument already exists: probe `'+unknown['probe_id']+'`')
    elif unknown.get('_measure_exists'):
        # The record may not name it (a reopened unknown), but the map holds its probe with a measure written:
        # every repair task tonight rewrote one that was sitting there.
        lines.append('  an instrument already exists: probe `'+unknown['id']+'_probe` has a measure.py on the map — read it '
                     'and run it; write a new one only if it reads the wrong thing')
    return lines


def render_reference(project: Path, task: dict[str, Any], unknowns: list[dict[str, Any]]) -> str:
    """What the check-in controller holds: the brief entries cited, the task, the unknowns. Nothing else."""
    brief = json.loads((project/layout.dirname(project)/'brief.json').read_text())
    lines = []
    seen = set()
    for unknown in unknowns:
        cites = unknown_notes(unknown).get('cites', '')
        kind, _, index = cites.partition(':')
        entries = brief.get('needs' if kind == 'need' else 'deliverables') or []
        cited = entries[int(index)-1] if index.isdigit() and 1 <= int(index) <= len(entries) else None
        line = 'Brief entry served: '+(cites+' — '+str(cited) if cited else '(unknown '+unknown['id']+' cites no brief entry)')
        if line not in seen:
            seen.add(line)
            lines.append(line)
    lines.append('Mission: '+str(brief.get('mission')))
    for non_goal in brief.get('non_goals') or []:
        # Constraints on method are the reference too; the worker never sees them, the reviewer holds them and
        # states the delta when the work crosses one ("the page pulls a bundler; the brief says none").
        lines.append('Non-goal: '+str(non_goal))
    lines.append('Route task `'+task['id']+'` (bucket '+task['bucket']+'): '+task['title'])
    for unknown in unknowns:
        lines += describe_unknown(unknown)
    lines += ['The typed quantity is the contract; the prose describes it. A probe that returns a constant, or a '
              'value not read from the source (or, for an artifact the task builds, not read by running or reading '
              'that artifact), resolves nothing.',
              'Resolution is a stamped probe run whose measure is that quantity, a known adopted to the project map, '
              'and the route task completed citing them.',
              'After the gate is green the host asks the worker to record the method it followed in the playbook '
              '(`playbook create` / `add-step` / `edit-step`); that work is in scope then and is not a departure.']
    return '\n'.join(lines)+'\n'


def shared_workspaces(root: Path) -> Path:
    """Where a loop's tasks share their seed: `<session>/workspaces/`, beside `tasks/`. The seed (the state
    directories plus the playbook store, 4.5 MB) is the same for every task until a procedure is minted, and
    the harness stores snapshots by content, so one copy serves the run instead of one per task."""
    return root.parent.parent/'workspaces' if root.parent.name == 'tasks' else root/'workspaces'


def pack_workspace(project: Path, playbook_store: Path | None = None, *, only: tuple[str, ...] = (),
                   exclude: tuple[str, ...] = ()) -> bytes:
    """The project tree plus a copy of the playbook store, as the harness's relative tar.
    `only`: pack just these top-level directories (bind mode packs the state directories, the tree is bound).
    `exclude`: relative directories left out (caches, in a re-measurement pack)."""
    buffer = io.BytesIO()
    # The project's own sessions (loops, journals, tasks) never travel: they are the host's record, not the
    # worker's workspace, and they are large.
    excluded = tuple(PurePosixPath(e).parts for e in exclude)+((layout.dirname(project), layout.SESSIONS_DIRNAME),)
    venvs: list[tuple[str, ...]] = []   # any directory holding pyvenv.cfg is an environment, whatever its name
    with tarfile.open(fileobj=buffer, mode='w:', dereference=False) as archive:
        for path in sorted(project.rglob('*')):
            relative = path.relative_to(project)
            if any(part in PACK_EXCLUDE for part in relative.parts) or relative.parts[0] == PLAYBOOK_PREFIX:
                continue
            if only and relative.parts[0] not in only:
                continue
            if any(tuple(relative.parts[:len(e)]) == e for e in excluded+tuple(venvs)):
                continue
            if path.is_dir() and not path.is_symlink() and (path/'pyvenv.cfg').exists():
                venvs.append(tuple(relative.parts))
                continue
            if path.is_symlink():
                # A link that leaves the tree (a venv's bin/python -> the base interpreter) cannot travel: the
                # sandbox refuses the whole tar for one such member (score-video's re-measure, 2026-09-20).
                link = os.readlink(path)
                if os.path.isabs(link) or '..' in link.split('/'):
                    continue
                archive.add(path, arcname=relative.as_posix(), recursive=False)
            elif path.is_file():
                # Stored as an independent regular file even when hard-linked (uv links site-packages from its
                # cache): tarfile would write a link entry, which the sandbox refuses.
                info = archive.gettarinfo(str(path), arcname=relative.as_posix())
                info.type = tarfile.REGTYPE
                info.linkname = ''
                info.size = path.stat().st_size   # gettarinfo gives a hard link size 0
                with path.open('rb') as handle:
                    archive.addfile(info, handle)
            elif path.is_dir():
                archive.add(path, arcname=relative.as_posix(), recursive=False)
        if playbook_store is not None and playbook_store.is_dir():
            # Retired procedures (use it or lose it) do not travel: a worker cannot find what it does not have.
            # The ledger itself does, so touches made in the sandbox are stamped on the same clock.
            retired = retired_procedures(playbook_store)
            for path in sorted(playbook_store.glob('*.json')):
                if path.stem in retired:
                    continue
                archive.add(path, arcname=PLAYBOOK_PREFIX+'/playbook/procedures/'+path.name, recursive=False)
    return buffer.getvalue()


LIBRARY_LEDGER = '.library.json'


def retired_procedures(store: Path) -> set[str]:
    """Ids the playbook's ledger has retired; empty when there is no ledger."""
    try:
        data = json.loads((store/LIBRARY_LEDGER).read_text())
        return {i for i, e in (data.get('items') or {}).items() if isinstance(e, dict) and e.get('status') == 'retired'}
    except (OSError, ValueError):
        return set()


def _members(snapshot: bytes) -> dict[str, bytes]:
    if not snapshot:
        return {}   # an empty state part is no members, not a torn archive (tarfile calls it "empty file")
    with tarfile.open(fileobj=io.BytesIO(snapshot), mode='r:') as archive:
        return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}


def writeback(snapshot: bytes, project: Path, task: dict[str, Any], map_id: str,
              protected_probes: tuple[str, ...] = ()) -> list[str]:
    """Write only what this worker is entitled to change; the rest of the project is not its snapshot's to overwrite.

    Entitled: everything outside `.terra/`; its task map; probes (global by Terra's design); knowns it adopted and
    their runs; its own unknown once resolved; its own route entry, merged into the live route file. A controller
    working on the project meanwhile keeps its unknowns, tasks and proposals.
    """
    files = _members(snapshot)
    terra_dir = layout.dirname(project)+'/'
    written: list[str] = []

    def put(name: str) -> None:
        target = project/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[name])
        written.append(name)

    # The project map the task adopts to: global, or the brief's map under sessions/ (its knowns, unknowns and
    # runs live there, not under map/ — the first brief-map run lost its adopted knowns to this rule).
    brief_map = layout.brief_map(project)
    project_map = terra_dir+'map/' if brief_map == 'global' else terra_dir+'map/sessions/'+brief_map+'/'
    adopted_runs: set[str] = set()
    for name in sorted(files):
        if name.startswith(WRITEBACK_EXCLUDE):
            continue
        if not name.startswith(terra_dir):
            put(name)
        elif name.startswith(terra_dir+'map/probes/'):
            probe = name.split('/')[3]
            if probe in protected_probes:
                # An instrument another task's known cites is not this worker's to change.
                existing = project/name
                if not existing.exists() or existing.read_bytes() != files[name]:
                    written.append('refused:'+name)
                continue
            put(name)
        elif name.startswith(terra_dir+'map/sessions/'+map_id+'/'):
            put(name)
        elif name.startswith(project_map+'knowns/') and name.endswith('.json'):
            known = json.loads(files[name])
            if (known.get('adopted_from') or {}).get('map') == map_id:
                put(name)
                adopted_runs.update(known.get('run_ids') or [])
        elif name.startswith(project_map+'unknowns/') and name[len(project_map+'unknowns/'):-5] in task_unknown_ids(task):
            if json.loads(files[name]).get('status') == 'resolved':
                put(name)
    for name in sorted(files):
        if name.startswith(project_map+'runs/') and name[len(project_map+'runs/'):].split('/')[0] in adopted_runs:
            put(name)
    route_name = terra_dir+'route.json'
    if route_name in files:
        mine = next((t for t in json.loads(files[route_name])['tasks'] if t['id'] == task['id']), None)
        live_path = project/route_name
        live = json.loads(live_path.read_text())
        if mine is not None:
            live['tasks'] = [mine if t['id'] == task['id'] else t for t in live['tasks']]
            live_path.write_text(json.dumps(live, indent=2, sort_keys=True)+'\n')
            written.append(route_name+'#'+task['id'])
    return written


class _store_lock:
    """One harvest into a shared store at a time: two loops finishing tasks together must not race on the
    same procedure or widget id (last writer wins silently otherwise). A flock on a file beside the store."""

    def __init__(self, store: Path) -> None:
        store.mkdir(parents=True, exist_ok=True)
        self.path = store/'.harvest.lock'

    def __enter__(self) -> None:
        import fcntl
        self.handle = self.path.open('a+')
        fcntl.flock(self.handle, fcntl.LOCK_EX)

    def __exit__(self, *_: Any) -> None:
        import fcntl
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


PLAYBOOK_BASE = 'playbook_base'   # under the task root: the store as the task received it, for three-way merges
WRITEUP_MARK = 'writeup.started'   # under the task root: the green message went out; a resume harvests, never re-asks
REFLECTED_MARK = 'reflected.at-green'   # the green reflection was asked for once; a resume never re-asks


def merge_procedure(base: dict[str, Any], theirs: dict[str, Any], yours: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Three-way merge of a procedure: the copy the task started from, the store now (another task's
    improvement), and the workspace copy. Steps merge by id — a step only one side changed takes that side; a
    step both changed the same way is fine; one both changed differently keeps theirs and is reported so the
    worker can reconcile. Added steps keep their place after the step they followed; a step you removed goes
    unless they changed it. Title, description and tags merge the same way. Two tasks improving the same
    procedure used to be last-writer-wins: the first task's steps vanished without a word."""
    conflicts: list[str] = []
    merged = dict(theirs)
    for field in ('title', 'description', 'tags'):
        b, t, y = base.get(field), theirs.get(field), yours.get(field)
        if y != b:
            if t == b or t == y:
                merged[field] = y
            else:
                conflicts.append(field+': theirs '+json.dumps(t, ensure_ascii=False)[:120]+' / yours '+json.dumps(y, ensure_ascii=False)[:120])
    b_steps = {str(st.get('id')): st for st in base.get('steps') or [] if isinstance(st, dict)}
    t_steps = {str(st.get('id')): st for st in theirs.get('steps') or [] if isinstance(st, dict)}
    y_steps = {str(st.get('id')): st for st in yours.get('steps') or [] if isinstance(st, dict)}
    order = [str(st.get('id')) for st in theirs.get('steps') or [] if isinstance(st, dict)]
    result: dict[str, dict[str, Any]] = {sid: dict(t_steps[sid]) for sid in order}
    yours_order = [str(st.get('id')) for st in yours.get('steps') or [] if isinstance(st, dict)]
    for position, sid in enumerate(yours_order):
        y = y_steps[sid]; b = b_steps.get(sid); t = t_steps.get(sid)
        if b is None and t is None:
            # Added by you: after the step you put it after, when that one survives; else at the end.
            after = next((p for p in reversed(yours_order[:position]) if p in order), None)
            order.insert(order.index(after)+1 if after is not None else len(order), sid)
            result[sid] = dict(y)
        elif b is not None and t is None:
            continue   # they removed it; a removal they made stands (their improvement is the base you land on)
        elif y != b:
            if t == b or t == y:
                result[sid] = dict(y)
            else:
                conflicts.append('step '+json.dumps(str(t.get('title') or sid), ensure_ascii=False)+': theirs '
                                 +json.dumps(str(t.get('do') or ''), ensure_ascii=False)[:160]+' / yours '
                                 +json.dumps(str(y.get('do') or ''), ensure_ascii=False)[:160])
    for sid in list(order):
        if sid in b_steps and sid not in y_steps and sid in t_steps and t_steps[sid] == b_steps[sid]:
            order.remove(sid); result.pop(sid, None)   # you removed it and they left it as it was
    merged['steps'] = [result[sid] for sid in order]
    return merged, conflicts


def procedure_merge_message(merges: list[dict[str, Any]]) -> str:
    """The merge round for procedures: what moved, what merged on its own, the steps both sides changed."""
    lines = ['The gate is green and your work is done; one thing remains. A procedure you improved was improved by another '
             'task while you worked. Their changes and yours were merged step by step and the merged copy is now the one '
             'in your store — except where you both changed the same thing differently, where theirs was kept:']
    for m in merges:
        lines.append('- `'+m['id']+'`: '+'; '.join(m['conflicts']))
    lines.append('For each: `playbook load <id>` to read the merged procedure, then `playbook edit-step <id> --title "…" --do "…"` '
                 'to carry what yours did onto their version of the step (keep what they added; say both things if both '
                 'are true), `playbook validate <id>`, then call `done`; do not start other work.')
    return '\n'.join(lines)


def workspace_procedures(config: dict[str, Any], project: Path) -> Path | None:
    """The worker's copy of the store, when it is a directory on the host (bind mode): where a merged procedure
    is written back so the worker's `playbook load`/`edit-step` see it."""
    path = project/PLAYBOOK_PREFIX/'playbook'/'procedures'
    return path if bind_mode(config) and path.is_dir() else None


def harvest_playbook(snapshot: bytes, store: Path, config: dict[str, Any],
                     allowed: tuple[str, ...] | None = None, base: Path | None = None,
                     workspace_store: Path | None = None) -> dict[str, list[Any]]:
    """Procedures new or changed in the workspace copy; installed only if they validate. With `base` (the store
    as the task received it) a procedure the store moved under is three-way merged; a step both sides changed
    differently comes back under `conflicts` with the merged copy written to `workspace_store` for a merge round.

    With `allowed`, only those ids are considered: the procedures this session followed or created.
    Anything else the worker touched stays in its workspace."""
    with _store_lock(store):
        return _harvest_playbook(snapshot, store, config, allowed, base, workspace_store)


def _harvest_playbook(snapshot: bytes, store: Path, config: dict[str, Any],
                      allowed: tuple[str, ...] | None = None, base: Path | None = None,
                      workspace_store: Path | None = None) -> dict[str, list[Any]]:
    installed, rejected, ignored = [], [], []
    created, improved, merged_ids, conflicts = [], [], [], []
    pending: list[tuple[Path, bytes]] = []
    prefix = PLAYBOOK_PREFIX+'/playbook/procedures/'
    with tarfile.open(fileobj=io.BytesIO(snapshot), mode='r:') as archive:
        for member in archive:
            if not (member.isfile() and member.name.startswith(prefix) and member.name.endswith('.json')):
                continue
            data = archive.extractfile(member).read()
            target = store/Path(member.name).name
            if target.name == LIBRARY_LEDGER:
                # The sandbox's ledger: fold its touches (searched, used) into the host's; never install it.
                _merge_library(config, store, data)
                continue
            if target.name.startswith('.'):
                continue
            if (target.stem in BOOTSTRAP_PROCEDURES or any(target.stem.startswith(b+'-') or target.stem.startswith(b+'_')
                                                            for b in BOOTSTRAP_PROCEDURES)
                    or (allowed is not None and target.stem not in allowed)):
                if not target.exists() or target.read_bytes() != data:
                    ignored.append(target.stem)
                continue
            if target.exists() and target.read_bytes() == data:
                continue
            started_from = base/target.name if base is not None else None
            if started_from is not None and started_from.exists() and target.exists() \
                    and target.read_bytes() != started_from.read_bytes():
                # The store moved under this task: merge onto theirs rather than write over it.
                if data == started_from.read_bytes():
                    continue   # you did not change it; theirs stands
                try:
                    merged, clashes = merge_procedure(json.loads(started_from.read_bytes()), json.loads(target.read_bytes()),
                                                      json.loads(data))
                except ValueError as error:
                    rejected.append(target.stem+': could not merge onto the library\'s newer copy: '+str(error)[:200]); continue
                data = json.dumps(merged, indent=2, ensure_ascii=False).encode()
                started_from.write_bytes(target.read_bytes())   # the next harvest of this task merges against theirs
                if workspace_store is not None:
                    (workspace_store/target.name).write_bytes(data)   # the worker's copy is the merged one now
                if clashes:
                    conflicts.append(dict(id=target.stem, conflicts=clashes))
                    continue   # the worker reconciles in a merge round; the merge lands then
                merged_ids.append(target.stem)
            pending.append((target, data))
    # Install in passes until nothing more validates: a procedure that links one created in the same task fails
    # validation until that one is in the store, and the archive is alphabetical. compose-romantic-piano-midi was
    # refused for linking shape-a-returning-rhythmic-cell, installed a moment later; the repair round then moved the
    # link to a procedure the task never opened, and the method landed an orphan (rhythm gym, 2026-09-22).
    failed: dict[str, str] = {}
    while pending:
        retry = []
        for target, data in pending:
            staged = target.with_suffix('.json.staged')
            staged.write_bytes(data)
            backup = target.read_bytes() if target.exists() else None
            staged.replace(target)
            # The store is <XDG_DATA_HOME>/playbook/procedures; validate reads it through that root.
            check = subprocess.run([config['mizpah']['playbook'], 'validate', target.stem], capture_output=True, text=True,
                                   env=dict(os.environ, XDG_DATA_HOME=str(store.parent.parent)))
            if check.returncode == 0:
                installed.append(target.stem)
                # New to the library, or an existing procedure improved: the notice tells them apart.
                (improved if backup is not None else created).append(target.stem)
                failed.pop(target.stem, None)
            else:
                failed[target.stem] = (check.stderr or check.stdout).strip()[:300]
                retry.append((target, data))
                if backup is None:
                    target.unlink()
                else:
                    target.write_bytes(backup)
        if len(retry) == len(pending):
            break   # a pass that installed nothing: what is left fails on its own
        pending = retry
    rejected += [stem+': '+why for stem, why in failed.items()]
    return dict(installed=installed, rejected=rejected, ignored=ignored, created=created, improved=improved,
                merged=merged_ids, conflicts=conflicts)


def worker_blocked(project: Path, task: dict[str, Any]) -> str | None:
    """The reason if the worker itself blocked its task through Terra; None otherwise."""
    route = json.loads((project/layout.dirname(project)/'route.json').read_text())
    entry = next((t for t in route['tasks'] if t['id'] == task['id']), None)
    if entry and entry['status'] == 'blocked' and entry.get('blocked_reason'):
        return str(entry['blocked_reason'])
    return None


def widgets_touched(root: Path) -> list[str]:
    """Widget directories under cg/ the session created or edited, from the journal."""
    touched: list[str] = []
    for name, args, result in session_calls(root):
        candidates: list[str] = []
        if name == 'bash':
            command = args.get('command') or ''
            if command.startswith('cartograph create') and result.get('exit_code') == 0:
                text = result.get('stdout') or ''
                start = text.find('"path": "')
                if start >= 0:
                    candidates.append(text[start+9:].split('"')[0].split('/work/')[-1])
            for token in command.replace('"', ' ').replace("'", ' ').split():
                if token.startswith('cg/') and not token.startswith('cg/*'):
                    candidates.append(token)
        elif name in ('write', 'edit') and result.get('status') == 'ok':
            candidates.append(str(args.get('path') or ''))
        for candidate in candidates:
            parts = candidate.strip('./').split('/')
            if len(parts) >= 2 and parts[0] == 'cg' and parts[1] and parts[1] not in touched:
                touched.append(parts[1])
    return touched


def _merge_library(config: dict[str, Any], store: Path, data: bytes) -> None:
    """`playbook library` owns the ledger's format; hand it the sandbox copy to merge."""
    try:
        travelled = json.loads(data)
    except ValueError:
        return
    script = ('import json, sys\nfrom playbook import library\n'
              'print(json.dumps(library.merge(json.loads(sys.stdin.read()))))')
    python = str(Path(config['mizpah']['playbook']).parent/'python')
    try:
        subprocess.run([python, '-c', script], input=json.dumps(travelled), capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, XDG_DATA_HOME=str(store.parent.parent)))
    except (OSError, subprocess.SubprocessError):
        pass


def harvest_widgets(snapshot: bytes, root: Path, config: dict[str, Any], project: Path | None = None) -> dict[str, list[Any]]:
    with _store_lock(Path(config['mizpah']['widget_library'])):
        return _harvest_widgets(snapshot, root, config, project)


UPSTREAM = '.upstream'   # where a merge round finds the library's current copy: cg/<dir>/.upstream/


def _harvest_widgets(snapshot: bytes, root: Path, config: dict[str, Any], project: Path | None = None) -> dict[str, list[Any]]:
    """Widgets the session created or changed: validated and checked into the local library after green.

    Never published. A widget the library already holds at identical source is left alone. A widget whose library
    version moved while the session worked on it (another task checked in an improvement) is not checked in — that
    would erase theirs; it is returned under `conflicts` with the library's copy staged at cg/<dir>/.upstream/ in the
    project, for a merge round.
    """
    import shutil
    import subprocess as sp
    import tempfile
    result: dict[str, list[Any]] = dict(checked_in=[], rejected=[], unchanged=[], conflicts=[], merged=[])
    files = _members(snapshot)
    library = Path(config['mizpah']['widget_library'])
    for directory in widgets_touched(root):
        prefix = 'cg/'+directory+'/'
        members = {name: data for name, data in files.items() if name.startswith(prefix)
                   and '/.venv/' not in name and '__pycache__' not in name and '/'+UPSTREAM+'/' not in name}
        if prefix+'widget.json' not in members:
            continue
        try:
            meta = json.loads(members[prefix+'widget.json'])['meta']
            widget_id = meta['id']
        except (ValueError, KeyError, TypeError):
            result['rejected'].append(directory+': widget.json has no meta.id')
            continue
        shipped = library/widget_id
        if shipped.is_dir():
            same = all((shipped/name[len(prefix):]).exists() and (shipped/name[len(prefix):]).read_bytes() == data
                       for name, data in members.items() if not _widget_meta(name[len(prefix):]))
            if same:
                result['unchanged'].append(widget_id)
                continue
            try:
                shipped_version = json.loads((shipped/'widget.json').read_text())['meta'].get('version')
            except (OSError, ValueError, KeyError):
                shipped_version = None
            base_version = meta.get('version')
            if shipped_version and base_version and shipped_version != base_version:
                # The base moved under this session: someone else's improvement is in the library. Merge onto it
                # three-way per file (base = the version this session started from, kept under history/; theirs =
                # the library now; yours = the workspace). Hunks that do not overlap — a function added here, a
                # test there, which is what parallel gyms mostly do — merge with no worker turn; a real overlap
                # goes to the worker with the markers in the file. Seventeen improvements were dropped tonight
                # because the merge was a prose diff for the worker to redo by hand.
                base_dir = shipped/'history'/str(base_version)
                merged, clashes = _merge_widget(base_dir, shipped, members, prefix)
                if merged is not None and not clashes:
                    members = merged
                    result.setdefault('merged', []).append(widget_id)
                else:
                    conflict = dict(id=widget_id, dir=directory, base=base_version, library=shipped_version,
                                    changes=_library_changes(shipped, base_version), diff=_widget_diff(shipped, members, prefix),
                                    clashes=clashes)
                    if project is not None:
                        staged = project/'cg'/directory/UPSTREAM
                        if staged.exists():
                            shutil.rmtree(staged)
                        shutil.copytree(shipped, staged, ignore=shutil.ignore_patterns('history', '__pycache__', '.venv'))
                        if merged is not None:
                            # The merged files with conflict markers where both sides changed the same lines: the
                            # worker resolves the markers rather than re-applying its work from a diff.
                            for name, data in merged.items():
                                if name[len(prefix):] in clashes:
                                    (project/name).parent.mkdir(parents=True, exist_ok=True)
                                    (project/name).write_bytes(data)
                    result['conflicts'].append(conflict)
                    continue
        with tempfile.TemporaryDirectory(prefix='mizpah-widget-') as temp:
            target = Path(temp)/'cg'/directory
            for name, data in members.items():
                path = target/name[len(prefix):]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            check = sp.run([config['mizpah']['cartograph'], 'validate', str(target)], capture_output=True, text=True, cwd=temp)
            if check.returncode != 0 or '"status": "success"' not in check.stdout:
                result['rejected'].append(widget_id+': '+(check.stdout or check.stderr).strip()[:300])
                continue
            reason = 'Mizpah worker '+('improved' if shipped.is_dir() else 'created')+' this widget for task '+root.name
            done = sp.run([config['mizpah']['cartograph'], 'checkin', str(target), '--reason', reason, '--no-publish',
                           '--bump', 'patch' if shipped.is_dir() else 'minor'], capture_output=True, text=True, cwd=temp)
            if done.returncode == 0 and '"status": "success"' in done.stdout:
                result['checked_in'].append(widget_id)
            elif 'identical content' in (done.stdout or ''):
                result['unchanged'].append(widget_id)  # installed and used as-is: nothing to record
            else:
                result['rejected'].append(widget_id+': checkin: '+(done.stdout or done.stderr).strip()[:300])
    return result


WIDGET_META = ('changelog.json', 'widget.json', 'library_notes', '.validation_stamp.json', UPSTREAM)


def _widget_meta(name: str) -> bool:
    """Files Cartograph writes about a widget, not the widget: the check-in bumps the version, `validate` stamps
    the copy it ran on, a merge round stages the library's copy. None of them is a change of the widget and none
    merges — a validation stamp both sides had written was a "conflict" that cost a nine-turn repair round."""
    return name in WIDGET_META or name.split('/')[0] in WIDGET_META


def _merge_widget(base_dir: Path, shipped: Path, members: dict[str, bytes], prefix: str) -> tuple[dict[str, bytes] | None, list[str]]:
    """Three-way merge of a widget's files: (merged members, files with an unresolved overlap). None when there
    is no base to merge from (the library keeps no history of that version). Files only one side touched take
    that side; `git merge-file` merges the rest and leaves markers where both changed the same lines. Metadata
    (widget.json, changelog.json) is theirs: the check-in bumps the version."""
    import subprocess
    import tempfile
    if not base_dir.is_dir():
        return None, []
    names = {n[len(prefix):] for n in members} | {str(p.relative_to(shipped)) for p in shipped.rglob('*')
                                                   if p.is_file() and 'history' not in p.parts and '__pycache__' not in p.parts and '.venv' not in p.parts}
    merged: dict[str, bytes] = {}
    clashes: list[str] = []
    for name in sorted(names):
        if _widget_meta(name):
            if (shipped/name).exists():
                merged[prefix+name] = (shipped/name).read_bytes()
            continue
        base = (base_dir/name).read_bytes() if (base_dir/name).exists() else None
        theirs = (shipped/name).read_bytes() if (shipped/name).exists() else None
        mine = members.get(prefix+name)
        if mine == theirs:
            if mine is not None:
                merged[prefix+name] = mine
            continue
        if theirs == base:                 # only you changed it (or added/removed it)
            if mine is not None:
                merged[prefix+name] = mine
            continue
        if mine == base:                   # only they changed it
            if theirs is not None:
                merged[prefix+name] = theirs
            continue
        if base is None or theirs is None or mine is None:
            # Both sides added the same new file, or one removed what the other changed: no base to merge on.
            clashes.append(name)
            merged[prefix+name] = mine if mine is not None else theirs
            continue
        with tempfile.TemporaryDirectory(prefix='mizpah-merge-') as temp:
            t = Path(temp)
            (t/'mine').write_bytes(mine); (t/'base').write_bytes(base); (t/'theirs').write_bytes(theirs)
            done = subprocess.run(['git', 'merge-file', '-p', '-L', 'yours', '-L', 'base', '-L', 'library',
                                   str(t/'mine'), str(t/'base'), str(t/'theirs')], capture_output=True)
            merged[prefix+name] = done.stdout
            if done.returncode != 0:       # the number of conflicts, or negative on error
                clashes.append(name)
    return merged, clashes


def _library_changes(shipped: Path, since: str) -> list[str]:
    """Changelog reasons the library gained after `since`, newest first."""
    try:
        log = json.loads((shipped/'changelog.json').read_text())
    except (OSError, ValueError):
        return []
    out = []
    for entry in log:
        if entry.get('version') == since:
            break
        out.append(str(entry.get('version'))+': '+str(entry.get('reason') or '')[:120])
    return out


def _widget_diff(shipped: Path, members: dict[str, bytes], prefix: str, limit: int = 160) -> str:
    """A unified diff, library copy → the session's copy, over src/ and tests/, capped."""
    import difflib
    lines: list[str] = []
    names = sorted({n[len(prefix):] for n in members if n[len(prefix):].startswith(('src/', 'tests/'))}
                   | {str(p.relative_to(shipped)) for sub in ('src', 'tests') for p in (shipped/sub).rglob('*') if p.is_file()})
    for name in names:
        theirs = (shipped/name).read_text(errors='replace').splitlines() if (shipped/name).exists() else []
        mine = members[prefix+name].decode('utf-8', 'replace').splitlines() if prefix+name in members else []
        if theirs == mine:
            continue
        lines += list(difflib.unified_diff(theirs, mine, 'library/'+name, 'yours/'+name, lineterm='', n=2))
    if len(lines) > limit:
        lines = lines[:limit]+['... ('+str(len(lines)-limit)+' more lines)']
    return '\n'.join(lines)


def merge_message(conflicts: list[dict[str, Any]]) -> str:
    """The one chance to bring an improvement onto a base that moved: what moved, the diff, what to keep."""
    lines = ['The gate is green and your work is done; one thing remains. A widget you improved was improved by another '
             'task while you worked, and the library holds their version now. Checking yours in as it is would erase '
             'theirs, so it was not. Merge yours onto theirs:']
    for c in conflicts:
        lines.append('- `'+c['id']+'` (cg/'+c['dir']+'): you started from '+str(c['base'])+', the library is at '+str(c['library'])
                     +('; since then: '+'; '.join(c['changes']) if c['changes'] else '')+'.')
        if c.get('clashes'):
            lines.append('  Merged three-way already, except these files where you and they changed the same lines — they '
                         'now hold conflict markers (`<<<<<<< yours` … `=======` … `>>>>>>> library`): '+', '.join(c['clashes'])
                         +'. Resolve each marker keeping both intentions, delete the markers.')
        lines.append('  Their copy is at `cg/'+c['dir']+'/'+UPSTREAM+'/` (read it; it is the base you must land on). '
                     'The diff from their copy to yours:')
        lines.append('```\n'+(c['diff'] or '(no difference under src/ or tests/)')+'\n```')
    lines.append('For each: start from their copy — copy `'+UPSTREAM+'/src`, `'+UPSTREAM+'/tests` and `'+UPSTREAM+'/widget.json` over '
                 'yours — then re-apply your improvement on top: keep every function and test they added, keep every one you '
                 'added, and where you both changed the same function, keep theirs and add what yours did as a parameter or a '
                 'new function rather than replacing it. Set nothing about version (the check-in bumps it). Delete the `'
                 +UPSTREAM+'/` directory, `cartograph validate cg/<dir>` (both sets of tests must pass), then call `done`; '
                 'do not start other work.')
    return '\n'.join(lines)


def widget_problems(config: dict[str, Any], project: Path, root: Path) -> list[str]:
    """Every widget this session created or edited must pass `cartograph validate`; the validator's words are the delta."""
    import subprocess as sp
    problems = []
    for directory in widgets_touched(root):
        target = project/'cg'/directory
        if not (target/'widget.json').exists():
            continue
        check = sp.run([config['mizpah']['cartograph'], 'validate', str(target)], capture_output=True, text=True, cwd=project)
        if check.returncode != 0 or '"status": "success"' not in check.stdout:
            text = (check.stdout or check.stderr).strip()
            try:
                payload = json.loads(text[text.find('{'):])
                text = payload.get('message') or payload.get('error') or text
                if payload.get('blocks'):
                    text += ' — '+'; '.join(str(b) for b in payload['blocks'][:3])
            except (ValueError, TypeError):
                pass
            problems.append('widget cg/'+directory+' does not validate: '+text[:300])
    return problems


def open_walks(snapshot: bytes) -> list[str]:
    """Each opened checklist with unticked steps, as `<file>: <ticked>/<total> ticked; next: <step>`."""
    walks: list[str] = []
    for name, data in sorted(_members(snapshot).items()):
        if not name.startswith(PLAYBOOK_PREFIX+'/open/') or not name.endswith('.md'):
            continue
        lines = [ln.strip() for ln in data.decode('utf-8', errors='replace').splitlines()]
        steps = [ln for ln in lines if ln.startswith('- [')]
        pending = [ln[6:].strip('* ') for ln in steps if ln.startswith('- [ ]')]
        if pending:
            walks.append(name+': '+str(len(steps)-len(pending))+'/'+str(len(steps))+' ticked; next: '+pending[0][:100])
    return walks


def walks_left(snapshot: bytes) -> list[dict[str, Any]]:
    """The open walks as records: procedure id, file, steps, unticked, next step title."""
    out: list[dict[str, Any]] = []
    for name, data in sorted(_members(snapshot).items()):
        if not name.startswith(PLAYBOOK_PREFIX+'/open/') or not name.endswith('.md'):
            continue
        lines = [ln.strip() for ln in data.decode('utf-8', errors='replace').splitlines()]
        steps = [ln for ln in lines if ln.startswith('- [')]
        pending = [ln[6:].strip('* ') for ln in steps if ln.startswith('- [ ]')]
        # A flat walk's footer says what the method still holds past this walk: the next work order.
        footer = next((ln for ln in lines if ln.startswith('continues:')), '')
        more = re.search(r'continues: (\d+) more', footer)
        start = re.search(r'--from (\d+)', footer)
        if pending or more:
            out.append(dict(procedure=name.rsplit('/', 1)[-1].split('--', 1)[0], file=name, steps=len(steps),
                            unticked=len(pending), next=pending[0][:100] if pending else '',
                            continues=int(more.group(1)) if more else 0, next_from=int(start.group(1)) if start else 0))
    return out


def open_checklists(snapshot: bytes) -> list[str]:
    """Every procedure the worker opened is a commitment: each step ticked `[x]` (done) or `[-]` (not needed,
    with its reason) before the gate can be green. Checklists live under .playbook/open/ in the workspace, one
    per walk. Several open at once is the normal shape of a procedure whose steps link other procedures (the
    voicing procedure opens pedal, phrase shaping, ritardando and validation); the old "three open means the
    unknown is several unknowns" line told a worker with four linked walks to block the task."""
    problems: list[str] = []
    for name, data in sorted(_members(snapshot).items()):
        if not name.startswith(PLAYBOOK_PREFIX+'/open/') or not name.endswith('.md'):
            continue
        text = data.decode('utf-8', errors='replace')
        open_steps = [ln.strip()[6:].strip('* ') for ln in text.splitlines() if ln.strip().startswith('- [ ]')]
        if open_steps:
            problems.append('checklist '+name+' has '+str(len(open_steps))+' unticked step(s): '+
                            '; '.join(o[:60] for o in open_steps[:4])+(' …' if len(open_steps) > 4 else '')+
                            ' — do each against the artifact and `playbook tick '+name+' --done N,M --note "..."`; steps that do not '
                            'apply here are `--skip K,L --because "..."`, and "already done" is not a reason')
    return problems


def task_gate(config: dict[str, Any], project: Path, task: dict[str, Any], map_id: str,
              root: Path | None = None) -> dict[str, Any]:
    """The verdict on a claim is Terra's gate on the work order's task map, plus the host's honesty checks on
    the claim — a probe that reads true on an empty file, an artifact that disagrees with the map, an input it
    was told to read and did not, a widget it touched that does not validate. What the route already refuses at
    `route complete` (a run and a known cited for every unknown, med or better, adopted) is not judged again;
    the loop used to reconstruct all of it here and disagree with the gate it was standing on."""
    problems = []
    if root is not None:
        problems += widget_problems(config, project, root)
    route = json.loads((project/layout.dirname(project)/'route.json').read_text())
    entry = next((t for t in route['tasks'] if t['id'] == task['id']), None)
    if entry is None:
        return dict(ok=False, problems=['task vanished from the route'], knowns=[], runs=[])
    evidence = entry.get('evidence') or []
    last = evidence[-1] if evidence else {}
    runs, knowns = list(last.get('runs') or []), list(last.get('knowns') or [])
    if entry['status'] != 'done':
        problems.append('route task '+task['id']+' is '+entry['status']+', not done: `terra route complete` has not succeeded')
    unknown_ids = task_unknown_ids(task)
    problems += vacuous_truth_problems(project, unknown_ids)
    problems += duplicate_reading_problems(project, unknown_ids)   # two knowns, one float, no shared input: a copy
    resynced = readopt_retaken(config, project, map_id, unknown_ids)
    if resynced:
        (root/'resynced.jsonl').open('a').write(json.dumps(dict(at=time.time(), readopted=resynced))+'\n') if root else None
    problems += artifact_agreement_problems(project, unknown_ids)
    problems += unread_input_problems(project, unknown_ids)
    gate = terra(config, project, 'gate', '--map', map_id)
    for violation in gate.get('violations') or []:
        problems.append('['+str(violation.get('kind'))+'] '+str(violation.get('id') or '')+': '+str(violation.get('why') or ''))
    return dict(ok=not problems, problems=problems, knowns=knowns, runs=runs)


def duplicate_reading_problems(project: Path, unknown_ids: list[str]) -> list[str]:
    """Two quantities that are different things cannot agree to twelve digits by chance: the light and dark body
    contrasts, and the accent's, all read 16.075361130695477 (palette2) — one computation read three times. A
    re-measurement reruns the same probe and reproduces it, so the gate has to notice the coincidence itself.
    Integers and round values are exempt (counts and booleans coincide honestly)."""
    values: dict[str, list[str]] = {}
    inputs: dict[str, dict[str, Any]] = {}
    for uid in unknown_ids:
        known = read_known(project, uid)
        if not known or (known.get('stats') or {}).get('kind') != 'number':
            continue
        value = extract_known_value(known)
        if not isinstance(value, float) or value == int(value) or round(value, 2) == value:
            continue
        values.setdefault(repr(value), []).append(uid)
        inputs[uid] = run_inputs(project, known)
    problems = []
    for value, ids in values.items():
        if len(ids) < 2:
            continue
        # Two contrasts of the same fill against white agree to every digit honestly (logo_mark7: both #19324a). The
        # record shows why only when each probe declares the input it read (a known: binding) and the bound values
        # coincide; an unexplained coincidence is still the same computation read twice.
        explained = all(inputs[i] for i in ids) and len({json.dumps(inputs[i], sort_keys=True) for i in ids}) == 1
        if explained:
            continue
        problems.append(', '.join(ids)+' all read exactly '+value+': different quantities do not agree to every digit — '
                        'each probe is reading the same computation (the same pair, the same scheme, the same file). '
                        'Give each its own inputs and re-take them; void the runs that repeat. If they truly share an input '
                        '(the same fill, the same file), make that input a known and declare it on both probes (probe init '
                        '--input name=known:<id>) so the record shows why they agree')
    return problems


def remeasure(config: dict[str, Any], project: Path, root: Path, known_ids: list[str]) -> list[str]:
    """The host takes each adopted reading again, in a sandbox the worker never touched, and compares.

    Nothing that runs in the worker's sandbox can be trusted not to have been shaped by it; the only check
    that costs the worker nothing to pass honestly and everything to pass dishonestly is an independent
    re-measurement. A fresh shell (own network namespace, no services) runs `terra probe run` for the known's
    probe on the project as written back; a reading that disagrees, or a probe that cannot run without the
    worker's ambient state (a browser it left on a port), is a problem the gate reports. Only the value is
    compared: numbers within tolerance, booleans and labels exactly.
    """
    problems: list[str] = []
    sandbox = config['mizpah']['sandbox']
    scratch = root/'remeasure'
    scratch.mkdir(parents=True, exist_ok=True)
    network = NetworkPolicy(**sandbox['network']) if sandbox.get('network') else None
    bound = dict(workspace_dir=str(project.resolve()), cache_dirs=cache_dirs(config), state_dirs=state_dirs(project)) if bind_mode(config) else {}
    shell = SandboxedShell(ShellConfig(**(config['shell'] | dict(
        scratch_root=str(scratch), limits=ShellLimits(**config['shell']['limits']),
        read_only_binds=tuple(sandbox['read_only_binds']), environment=dict(sandbox['environment'], **layout.terra_env(project)),
        share_network=bool(sandbox.get('share_network', False)) and network is None, network=network,
        refused_paths=tuple(sandbox.get('refused_paths') or ()), refused_patterns=REFUSED_PATTERNS,
        scratch_dirs=scratch_dirs(config)) | bound)))
    try:
        # Bind mode: the evidence tree without the caches; the caches are bound read-only by the detached run.
        workspace = pack_workspace(project, exclude=cache_dirs(config)+scratch_dirs(config)) if bind_mode(config) \
            else pack_workspace(project, exclude=scratch_dirs(config))
        for known_id in known_ids:
            known = read_known(project, known_id)
            if known is None or composed(known):
                continue
            probe_id = (known.get('probe_ids') or [None])[0] or (known.get('primary_run_id') or '').split('_', 1)[-1].rsplit('_', 1)[0]
            if not probe_id:
                problems.append('re-measure: known '+known_id+' names no probe')
                continue
            expected = extract_known_value(known)
            result = shell.run('terra probe run '+probe_id+' --to \'{"kind": "file"}\' --json 2>/dev/null; '
                               'cat '+layout.dirname(project)+'/map/probes/'+probe_id+'/_last_reading.json 2>/dev/null', workspace,
                               timeout_seconds=min(80, config['shell']['limits']['command_seconds']),
                               **(dict(detached=True) if bind_mode(config) else {}))
            reading = None
            text = result.stdout
            start = text.rfind('{"to"') if '{"to"' in text else text.rfind('{\n  "to"')
            try:
                doc = json.loads(text[start:]) if start >= 0 else {}
                reading = (doc.get('readings') or {}).get(known.get('quantity') or known_id)
            except ValueError:
                reading = None
            if reading is None:
                problems.append('re-measure: probe '+probe_id+' did not produce a reading for '+known_id+' when the host ran it '
                                'alone (exit '+str(result.exit_code)+'): '+(result.stderr or result.stdout).strip()[-200:]
                                +' — a reading must not depend on state only your session had (a service on a port, a file outside the project)')
                continue
            if not values_agree(known.get('type'), expected, reading):
                problems.append('re-measure: known '+known_id+' = '+str(expected)+' on the map but the host\'s own run of '
                                +probe_id+' read '+str(reading)+'; the reading is not reproducible')
    finally:
        shell.close()
    (root/'remeasure.jsonl').open('a').write(json.dumps(dict(at=time.time(), knowns=known_ids, problems=problems))+'\n')
    return problems


def refresh_stale(config: dict[str, Any], project: Path, root: Path) -> dict[str, list[str]]:
    """The host re-takes every global known that went stale because its inputs moved — a file it depends on
    was rewritten, a known its probe declared changed — and, when the fresh reading reproduces the value,
    links the run so the known is live again with its dependencies restamped. A reading that no longer
    agrees is left stale with the fresh value recorded for the eval: the artifact changed under it and the
    reading is owed again. Clearing a cascade cost headline3 a whole worker session (29 turns, still red)
    for what is a mechanical re-run; the worker never touches this path.

    The probe runs in a fresh sandbox exactly as re-measurement does; its run directory is harvested from the
    sandbox's tree into the project and linked by the host's own terra."""
    out: dict[str, list[str]] = dict(refreshed=[], changed=[], failed=[])
    listing = subprocess.run([config['mizpah']['terra'], 'known', 'list', '--json'], cwd=project, capture_output=True, text=True,
                             env=dict(os.environ, **layout.terra_env(project)))
    try:
        rows = json.loads(listing.stdout[listing.stdout.find('['):])
    except ValueError:
        out['failed'].append('known list: '+(listing.stderr or listing.stdout)[:200])
        return out
    stale = [r for r in rows if r.get('stale') and any(('file dep changed' in str(x) or 'declared input' in str(x))
                                                       for x in r.get('stale_reasons') or [])]
    if not stale:
        return out
    sandbox = config['mizpah']['sandbox']
    scratch = root/'refresh'
    scratch.mkdir(parents=True, exist_ok=True)
    network = NetworkPolicy(**sandbox['network']) if sandbox.get('network') else None
    bound = dict(workspace_dir=str(project.resolve()), cache_dirs=cache_dirs(config), state_dirs=state_dirs(project)) if bind_mode(config) else {}
    shell = SandboxedShell(ShellConfig(**(config['shell'] | dict(
        scratch_root=str(scratch), limits=ShellLimits(**config['shell']['limits']),
        read_only_binds=tuple(sandbox['read_only_binds']), environment=dict(sandbox['environment'], **layout.terra_env(project)),
        share_network=bool(sandbox.get('share_network', False)) and network is None, network=network,
        refused_paths=tuple(sandbox.get('refused_paths') or ()), refused_patterns=REFUSED_PATTERNS,
        scratch_dirs=scratch_dirs(config)) | bound)))
    state_dir = layout.dirname(project)
    try:
        workspace = pack_workspace(project, exclude=cache_dirs(config)+scratch_dirs(config)) if bind_mode(config) \
            else pack_workspace(project, exclude=scratch_dirs(config))
        for row in stale:
            known_id = str(row.get('id'))
            record = row.get('record') or {}
            probe_id = (record.get('probe_ids') or [None])[0]
            if not probe_id:
                out['failed'].append(known_id+': names no probe'); continue
            expected = extract_known_value(record)
            result = shell.run('terra probe run '+probe_id+' --to \'{"kind": "file"}\' --json 2>/dev/null; '
                               'echo; cat '+state_dir+'/map/probes/'+probe_id+'/_last_reading.json 2>/dev/null', workspace,
                               timeout_seconds=min(80, config['shell']['limits']['command_seconds']),
                               **(dict(detached=True) if bind_mode(config) else {}))
            text = result.stdout
            run_id = None
            m = re.search(r'"id": "(\d{8}T\d{6}Z_'+re.escape(probe_id)+r'_[0-9a-f]+)"', text)
            if m:
                run_id = m.group(1)
            start = text.rfind('{"to"') if '{"to"' in text else text.rfind('{\n  "to"')
            try:
                reading = ((json.loads(text[start:]) if start >= 0 else {}).get('readings') or {}).get(record.get('quantity') or known_id)
            except ValueError:
                reading = None
            if run_id is None or reading is None or not isinstance(result.workspace, (bytes, bytearray)):
                out['failed'].append(known_id+': the probe produced no run when the host ran it alone (exit '+str(result.exit_code)+')')
                continue
            if not values_agree(record.get('type'), expected, reading):
                out['changed'].append(known_id+' = '+str(expected)+' on the map, but its inputs moved and a fresh run reads '
                                      +str(reading)+': the reading is owed again')
                continue
            # Harvest the run directory from the sandbox's tree, then link it with the host's terra.
            prefix = layout.map_root(project).relative_to(project).as_posix()+'/runs/'+run_id+'/'
            members = {name: data for name, data in _members(result.workspace).items() if name.startswith(prefix)}
            if not members:
                out['failed'].append(known_id+': run '+run_id+' not found in the sandbox tree'); continue
            for name, data in members.items():
                target = project/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            try:
                terra(config, project, 'known', 'link-run', known_id, run_id)
                out['refreshed'].append(known_id)
            except RuntimeError as error:
                out['failed'].append(known_id+': link-run: '+str(error)[:200])
    finally:
        shell.close()
    (root/'refresh.jsonl').open('a').write(json.dumps(dict(at=time.time(), **out))+'\n')
    return out


def extract_known_value(known: dict[str, Any]) -> Any:
    stats = known.get('stats') or {}
    kind = stats.get('kind') or known.get('type')
    if kind == 'number':
        return stats.get('mean')
    if kind == 'boolean':
        return None if stats.get('rate') is None else stats['rate'] >= 0.5
    if kind == 'label':
        return stats.get('mode')
    return stats.get('value')


def values_agree(kind: str | None, expected: Any, reading: Any) -> bool:
    if expected is None or reading is None:
        return False
    if kind == 'boolean':
        return isinstance(reading, bool) and reading == expected
    if kind == 'label':
        return str(reading) == str(expected)
    try:
        a, b = float(expected), float(reading)
    except (TypeError, ValueError):
        return False
    return abs(a-b) <= max(1e-6, 0.02*abs(a))   # 2%: a re-run of a reading, not a different reading


def artifact_agreement_problems(project: Path, unknown_ids: list[str]) -> list[str]:
    """An artifact unknown records what the artifact printed; the map already holds what it should have printed.

    A number-typed artifact unknown names the known it must agree with (the anchor guard made sure of that);
    recording the printed value is not agreement, comparing it is. luna's sizing script printed hover_power
    340.6 W against the map's 281.8 W and the task completed: the probe declared the known as an input and
    discarded it. So the gate compares: the adopted value against every number known the unknown names, and
    disagreement with all of them is red, with both numbers in the message."""
    problems: list[str] = []
    known_ids = {p.stem for p in (layout.map_root(project)/'knowns').glob('*.json')}
    for uid in unknown_ids:
        try:
            unknown = read_unknown(project, uid)
        except (OSError, ValueError):
            continue
        notes = str(unknown.get('notes') or '')
        if not ('creates ' in notes or 'cites deliverable:' in notes) or unknown.get('type') != 'number':
            continue
        adopted = read_known(project, uid)
        if adopted is None:
            continue
        value = extract_known_value(adopted)
        text = str(unknown.get('claim') or '')+' '+str(unknown.get('evidence_needed') or '')
        anchors = [w for w in dict.fromkeys(re.findall(r'[a-z][a-z0-9_]*', text)) if w in known_ids and w != uid]
        numeric = [(a, extract_known_value(read_known(project, a) or {})) for a in anchors]
        numeric = [(a, v) for a, v in numeric if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not numeric or value is None:
            continue
        if not any(values_agree('number', v, value) for _, v in numeric):
            problems.append('artifact known '+uid+' = '+str(value)+' but the known'+('s' if len(numeric) > 1 else '')+' it must agree with '
                            +', '.join(a+' = '+str(round(v, 6)) for a, v in numeric)+' — the artifact prints a different number. '
                            'If the artifact is wrong (or the probe reads the wrong line), fix it, never the map. If the '
                            'artifact measures a different quantity than the known (the same name at another mass, '
                            'another point, another unit), do not bend it to the map: block the task naming both '
                            'quantities, so the brief can be made to name them apart')
    return problems



def run_inputs(project: Path, known: dict[str, Any]) -> dict[str, Any]:
    """The input snapshot of the known's primary run: what the probe declared it read from the map (empty when
    the probe declared nothing)."""
    run_id = str(known.get('primary_run_id') or next(iter(known.get('run_ids') or []), ''))
    if not run_id:
        return {}
    for base in [layout.map_root(project)/'runs']+sorted((project/layout.dirname(project)/'map'/'sessions').glob('*/runs')):
        meta = base/run_id/'meta.json'
        if meta.exists():
            try:
                doc = json.loads(meta.read_text())
            except ValueError:
                return {}
            return dict(doc.get('inputs') or {}) if doc.get('input_bindings') else {}
    return {}


def vacuous_truth_problems(project: Path, unknown_ids: list[str]) -> list[str]:
    """'Every mark.svg is valid' read true over zero files (logo_mark3): a universal that is true because there
    is nothing to check is a reading of nothing. When a boolean whose claim quantifies over things reads true and a
    number known of the same task reads 0, the true is refused with both named."""
    problems: list[str] = []
    zeros = []
    for uid in unknown_ids:
        known = read_known(project, uid)
        if known and (known.get('stats') or {}).get('kind') == 'number' and extract_known_value(known) == 0:
            zeros.append(uid)
    if not zeros:
        return problems
    for uid in unknown_ids:
        try:
            unknown = read_unknown(project, uid)
        except (OSError, ValueError):
            continue
        known = read_known(project, uid)
        if not known or unknown.get('type') != 'boolean' or extract_known_value(known) is not True:
            continue
        if re.search(r'\b(every|all|each|no)\b', str(unknown.get('claim') or '').lower()):
            problems.append(uid+' reads true ("'+str(unknown.get('claim') or '')[:80]+'") while '+', '.join(zeros)+' reads 0: a '
                            'universal over nothing is vacuous, not a reading. The things it quantifies over do not exist yet; '
                            'the true is refused until they do (or the unknown is about their absence, in which case say so '
                            'and block naming the deliverable that builds them)')
    return problems


def readopt_retaken(config: dict[str, Any], project: Path, map_id: str, unknown_ids: list[str]) -> list[str]:
    """A known the task re-took on its map (voided runs, new runs) is adopted again with --update, so the
    parent's copy carries the reading the worker now stands behind.

    The worker fixed its instrument, voided three false runs and linked five true ones on its task map; the
    gate kept comparing the host's fresh reading with global's stale copy and refused the task five times
    (catalog_pick1, 2026-09-19). The worker is entitled to its own knowns; the plumbing is the host's."""
    resynced: list[str] = []
    for uid in unknown_ids:
        own, parent = read_known(project, uid, map_id), read_known(project, uid)
        if not own or not parent or not own.get('adopted_to'):
            continue
        if list(own.get('run_ids') or []) == list(parent.get('run_ids') or []):
            continue
        try:
            terra(config, project, 'known', 'adopt', uid, '--from', map_id, '--update')
            resynced.append(uid)
        except RuntimeError:
            continue
    return resynced


def run_meta(project: Path, run_id: str) -> dict[str, Any]:
    for base in (layout.map_root(project)/'runs', *(project/layout.dirname(project)/'map'/'sessions').glob('*/runs')):
        path = base/run_id/'meta.json'
        if path.exists():
            try:
                return json.loads(path.read_text())
            except ValueError:
                return {}
    return {}


UNIT_WORDS = {'kg', 'g', 'w', 'wh', 'v', 'a', 'ah', 'min', 'mins', 's', 'ms', 'px', 'kb', 'mb', 'pct', 'ratio', 'count', 'id',
              'name', 'label', 'value', 'total', 'number', 'n'}


def known_words(known_id: str) -> list[str]:
    return [w for w in known_id.split('_') if len(w) > 2 and w not in UNIT_WORDS and not w[0].isdigit()]


def names_known(text_words: set[str], known_id: str) -> bool:
    """`min_parallel_count` is named by "the minimum passing parallel count": every content word of the id
    appears in the text, by prefix either way (min/minimum), so a probe cannot hardcode what the map holds."""
    parts = known_words(known_id)
    return bool(parts) and all(any(t.startswith(p) or p.startswith(t) for t in text_words if len(t) > 2) for p in parts)


def unread_input_problems(project: Path, unknown_ids: list[str]) -> list[str]:
    """A reading "at that parallel count" must read the count from the map, not decide it again.

    An unknown whose claim names another known — by id, or by the id's words ("the selected motor",
    "the minimum passing parallel count") — is a reading conditioned on it. Terra records what a run consumed
    (declared inputs, instrumented `known get`); when the known appears in none of the unknown's runs, the probe
    re-derived or hardcoded it: luna's survey took the branch count of the last function it walked while the map
    said `collect_map_status` (119 branches, recorded as 2); the drone's pack mass used `p=4` while the map's
    min_parallel_count was 2, and every phase-2 number followed. Red, with the fix spelled out."""
    problems: list[str] = []
    knowns: dict[str, dict[str, Any]] = {}
    for path in (layout.map_root(project)/'knowns').glob('*.json'):
        try:
            knowns[path.stem] = json.loads(path.read_text())
        except ValueError:
            continue
    if not knowns:
        return problems
    for uid in unknown_ids:
        try:
            unknown = read_unknown(project, uid)
        except (OSError, ValueError):
            continue
        text = str(unknown.get('claim') or '')+' '+str(unknown.get('evidence_needed') or '')
        words = set(re.findall(r'[a-z][a-z0-9_]*', text.lower()))
        own = set(known_words(uid))
        is_label = lambda k: knowns[k].get('type') == 'label' or (knowns[k].get('stats') or {}).get('kind') == 'label'  # noqa: E731
        # Exact ids always; labels also by their words (distinctive); numbers by words are only declared as inputs
        # at scaffold time (over-declaring is harmless there, refusing on a loose match is not).
        named = [k for k in knowns if k != uid and k not in unknown_ids
                 and (k in words or (is_label(k) and names_known(words, k) and not set(known_words(k)) <= own))]
        known = read_known(project, uid)
        if not known:
            continue
        meta_path = project/layout.dirname(project)/'map'/'probes'/(uid+'_probe')/'probe.json'
        measure_path = project/layout.dirname(project)/'map'/'probes'/(uid+'_probe')/'measure.py'
        if meta_path.exists() and measure_path.exists():
            try:
                declared = json.loads(meta_path.read_text()).get('inputs') or {}
            except ValueError:
                declared = {}
            source = measure_path.read_text(errors='replace')
            used = re.sub(r'_\s*=\s*ctx(\.get\(\s*)?\[?"inputs"\]?\)?', '', source)   # `_ = ctx["inputs"]` is not a read
            if declared and not re.search(r'inputs', used):
                problems.append(uid+'_probe declares '+', '.join(sorted(declared))+' as inputs but measure.py never reads '
                                'ctx["inputs"]: the reading was computed without the map values it depends on. Use them '
                                '(ctx["inputs"]["'+sorted(declared)[0]+'"]), re-run and re-graduate')
                continue
        if not named:
            continue
        consumed: set[str] = set()
        for run_id in known.get('run_ids') or []:
            meta = run_meta(project, run_id)
            consumed |= {str(v).removeprefix('known:') for v in (meta.get('input_bindings') or {}).values()}
            consumed |= set(meta.get('inputs') or {})
            consumed |= {str(r.get('known_id') or r.get('id') or '') for r in meta.get('known_reads') or [] if isinstance(r, dict)}
        missing = [n for n in named if n not in consumed]
        if missing:
            problems.append(uid+' names '+', '.join(missing)+' (= '+', '.join(repr(extract_known_value(knowns[m])) for m in missing)
                            +') but none of its runs read it: the probe decided that value again instead of taking it from '
                            'the map. Declare it as an input of the probe (probe.json "inputs": {"'+missing[0]+'": "known:'+missing[0]
                            +'"}) and use ctx["inputs"]["'+missing[0]+'"] in measure(); then re-run and re-graduate')
    return problems


def red_message(gate: dict[str, Any], project: Path, map_id: str, unknown_ids: list[str]) -> str:
    """The delta only: what is missing and what to keep (never a restatement of the task)."""
    keep = []
    for unknown_id in unknown_ids:
        local = read_known(project, unknown_id, map_id)
        if local:
            keep.append('known '+unknown_id+' on '+map_id+' with its '+str(len(local.get('run_ids') or []))+' linked run(s)')
        try:
            unknown = read_unknown(project, unknown_id, map_id)
        except FileNotFoundError:
            continue
        if unknown.get('probe_ids') or unknown.get('probe_id'):
            keep.append('probe '+', '.join(unknown.get('probe_ids') or [unknown['probe_id']]))
    from . import prompts as _prompts
    return _prompts.message('gate_red_worker', problems='\n'.join('- '+p for p in gate['problems']),
                            keep=('Keep: '+'; '.join(dict.fromkeys(keep))+'. Add what is missing; do not start over.') if keep else '')


def session_calls(root: Path) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(tool, arguments, result) for every worker call in the session journal."""
    calls = []
    journal = root/'events'/'session.jsonl'
    if not journal.exists():
        return calls
    for line in ops.jsonl_lines(journal):
        event = json.loads(line)
        if event.get('event_type') != 'worker_turn':
            continue
        results = {r['call_id']: r['result'] for r in event['payload'].get('tool_results') or []}
        for call in event['payload']['response'].get('tool_calls') or []:
            arguments = call['function']['arguments']
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
            except ValueError:
                # Arguments that were not JSON: the harness refused that call at the time; here it is a call with
                # no arguments, not a reason to fail the task.
                args = {}
            calls.append((call['function']['name'], args if isinstance(args, dict) else {}, results.get(call.get('id'), {})))
    return calls


def procedures_used(root: Path) -> list[str]:
    """Domain procedure ids the worker opened — `playbook start`/`load`/`open` at bash or the playbook_open tool
    (the bootstrap excluded)."""
    used: list[str] = []
    for name, args, _ in session_calls(root):
        words = (args.get('command') or '').split() if name == 'bash' else []
        if (len(words) >= 3 and words[0] == 'playbook' and words[1] in ('start', 'load', 'open')
                and words[2] not in used and words[2] not in BOOTSTRAP_PROCEDURES):
            used.append(words[2])
        elif name == 'playbook_open' and args.get('id') and args['id'] not in used and args['id'] not in BOOTSTRAP_PROCEDURES:
            used.append(str(args['id']))
    return used


def procedures_created(root: Path) -> list[str]:
    """Procedure ids the worker created — `playbook create` at bash or the playbook_create tool — that succeeded.
    A mint the harvest cannot see is a mint thrown away: every procedure Ornith wrote on 2026-09-19 went through
    the typed tool and was marked ignored."""
    created: list[str] = []
    for name, args, result in session_calls(root):
        if name == 'playbook_create':
            if result.get('exit_code') == 0 and args.get('id') and args['id'] not in created:
                created.append(str(args['id']))
            continue
        command = args.get('command') or '' if name == 'bash' else ''
        # `cd /work && playbook create <id> ...`, line continuations, chained commands: find the create anywhere.
        for match in re.finditer(r'(?:^|[;&|]\s*)playbook\s+create\s+([a-z0-9][a-z0-9_-]*)', command, re.MULTILINE):
            if result.get('exit_code') == 0 and match.group(1) not in created:
                created.append(match.group(1))
    return created


EDIT_VERBS = ('add-step', 'add-steps', 'edit-step', 'remove-step', 'move-step', 'edit')


def procedures_edited(root: Path) -> list[str]:
    """Procedure ids the worker changed by name (`playbook add-step <id>` and the other edit verbs, at bash or as a
    typed tool) in a call that succeeded, the bootstrap excluded. Linking a new method up from the general one is an
    add-step on a procedure the task never opened, and the harvest took only opened or created ids: the step
    that linked shape-a-returning-rhythmic-cell from compose-a-character-piece was ignored, and so was
    read-ternary-return-from-intervals' (Block C, 2026-09-22). A walk-scoped edit (`--walk`) is the opened one."""
    edited: list[str] = []
    for name, args, result in session_calls(root):
        ids = []
        if name.startswith('playbook_') and name[9:].replace('_', '-') in EDIT_VERBS:
            if result.get('exit_code') == 0 and args.get('id') and not args.get('walk'):
                ids = [str(args['id'])]
        elif name == 'bash' and result.get('exit_code') == 0:
            ids = re.findall(r'(?:^|[;&|]\s*)playbook\s+(?:'+'|'.join(re.escape(v) for v in EDIT_VERBS)+r')\s+([a-z0-9][a-z0-9_-]*)',
                             args.get('command') or '', re.MULTILINE)
        for pid in ids:
            if pid not in edited and pid not in BOOTSTRAP_PROCEDURES:
                edited.append(pid)
    return edited


def effort_message(task: dict[str, Any], estimate: int, turns: int, overruns: int) -> str:
    """The estimate is spent; the worker reports whether the bucket was wrong and judges whether to continue."""
    from . import prompts as _prompts
    return _prompts.message('effort_worker', bucket=task['bucket'], mode=BUCKET_MODES.get(task['bucket'], ''), turns=turns,
                            past=('past' if overruns == 1 else str(overruns)+'× past'), task=task['id'])


def library_parts(config: dict[str, Any], project: Path, unknowns: list[dict[str, Any]]) -> list[str]:
    """Widgets already in the library that fit this task's unknowns, found by the host so the worker sees them
    before it creates a near-duplicate: five tasks in a row minted overlapping CSV widgets without searching."""
    import subprocess as sp
    env = dict(os.environ, WIDGET_LIBRARY_PATH=config['mizpah']['widget_library'])
    seen: dict[str, str] = {}
    # One query made of the distinctive words across all claims finds the instrument for the task's kind of
    # source (a rendered page, a CSV); each claim's own query finds the atom for that reading.
    combined = ' '.join(distinctive_words([str(u.get('claim') or '') for u in unknowns]))
    for query in [combined]+[re.sub(r'[^a-z0-9 ]', ' ', (u.get('claim') or '').lower()).replace(' is unknown', '') for u in unknowns]:
        if not query.strip():
            continue
        words = {w for w in re.findall(r'[a-z0-9]{4,}', query) if w not in STOP}
        try:
            out = sp.run([config['mizpah']['cartograph'], 'search', query, '--language', 'python', '--top-k', '3',
                          '--local-only'], cwd=project, capture_output=True, text=True, timeout=60, env=env).stdout
            for hit in (json.loads(out).get('local') or {}).get('widgets') or []:
                # A relevance score alone let "render" pull in FreeCAD meshes and CFD contours for a web page;
                # a hit counts when it shares two real words with the claim, the same bar as procedure hits.
                text = (hit['id']+' '+str(hit.get('name') or '')+' '+str(hit.get('description') or '')).lower().replace('-', ' ')
                if hit.get('relevance_score', 0) >= 0.5 and shared_stems(words, text) >= 2:
                    seen.setdefault(hit['id'], (hit.get('description') or '').split('. ')[0][:120])
        except (ValueError, OSError, sp.SubprocessError):
            continue
    return ['`'+k+'` — '+v for k, v in seen.items()]


def procedure_parts(config: dict[str, Any], task: dict[str, Any], unknowns: list[dict[str, Any]]) -> list[str]:
    """Procedures already in the playbook near this task, found by the host so the worker opens one instead of
    working the method out: the sales2 run never searched once and minted a twin per task."""
    import subprocess as sp
    queries = [str(task.get('title') or ''), ' '.join(distinctive_words([str(u.get('claim') or '') for u in unknowns]))]
    queries += [re.sub(r'[^a-z0-9 ]', ' ', (u.get('claim') or '').lower()) for u in unknowns[:6]]
    seen: dict[str, tuple[float, str]] = {}
    for query in queries:
        if not query.strip():
            continue
        try:
            out = sp.run([config['mizpah']['playbook'], 'search', query, '--limit', '3'], capture_output=True, text=True,
                         timeout=60).stdout
            words = {w for w in re.findall(r'[a-z0-9]{4,}', query.lower()) if w not in STOP}
            for hit in json.loads(out[out.find('{'):]).get('hits') or []:
                if hit['id'] in BOOTSTRAP_PROCEDURES:
                    continue
                # Scores are not comparable across queries; a hit counts when it shares two real words with
                # the query ("character" alone matched a rigged-game procedure for a 70-character measure).
                text = ' '.join(str(hit.get(k) or '') for k in ('id', 'title', 'description', 'snippet')).lower().replace('-', ' ')
                if shared_stems(words, text) < 2:
                    continue
                score = float(hit.get('score') or 0)
                if score > seen.get(hit['id'], (0, ''))[0]:
                    seen[hit['id']] = (score, str(hit.get('title') or '')[:80])
        except (ValueError, OSError, sp.SubprocessError, KeyError):
            continue
    best = sorted(seen.items(), key=lambda kv: -kv[1][0])[:4]
    return ['`'+k+'` — '+v[1] for k, v in best]


def distinctive_words(texts: list[str], limit: int = 12) -> list[str]:
    """The words that recur across a task's claims, most frequent first: what the task is about, minus filler."""
    counts: dict[str, int] = {}
    for text in texts:
        for w in set(re.findall(r'[a-z0-9]{4,}', text.lower())):
            if w not in STOP:
                counts[w] = counts.get(w, 0)+1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def shared_stems(words: set[str], text: str) -> int:
    """How many of the query's real words the text carries, matching on a 5-letter stem (font/fonts, size/sizes)."""
    stems = {w[:5] for w in words}
    found = {t[:5] for t in re.findall(r'[a-z0-9]{4,}', text)}
    return len(stems & found)


# Filler only: the words that describe every Mizpah unknown. Domain words (page, rendered, viewport, csv)
# stay — they are what a search is for.
STOP = {'with', 'from', 'that', 'this', 'into', 'every', 'each', 'must', 'their', 'when', 'than', 'then', 'against',
        'known', 'unknown', 'reading', 'value', 'number', 'whether', 'count', 'current', 'currently', 'probe',
        'change', 'after', 'before', 'agreeing', 'prints', 'python3'}


def refusal_message(refused: list[tuple[str, str]]) -> str:
    """The library's reasons for refusing what the worker built, and the one chance to fix them."""
    from . import prompts as _prompts
    return _prompts.message('library_repair_worker', reasons='\n'.join('- '+kind+' '+reason for kind, reason in refused))


def green_message(gate: dict[str, Any], unknown_id: str | list[str], used: list[str] = (), cost: dict[str, Any] | None = None,
                  skips: dict[str, list[str]] | None = None, uncovered: dict[str, list[str]] | None = None,
                  made: list[str] = (), procedures: list[str] = (), widgets: list[str] = ()) -> str:
    """The library's one question after green, asked when it got nothing on one side or the other: the procedures
    this task created or improved, or the widgets it checked in. The worker answers by doing or by declining.
    (There is no write-up phase otherwise — the harvest of what was minted while working is the review.)"""
    if isinstance(unknown_id, list):
        unknown_id = ', '.join(unknown_id)
    held = ('widgets: '+(', '.join('`'+w+'`' for w in widgets) or 'none')
            +'; procedures created or improved: '+(', '.join('`'+p+'`' for p in procedures) or 'none')
            +('; walked: '+', '.join('`'+u+'`' for u in used) if used else ''))
    asks = []
    if not procedures:
        asks.append('Is there a method here — instincts the next worker on a work order like this should start from? '
                    'If so, `playbook create` it under the general procedure that should lead to it, each step a thing to '
                    'attend to; or improve the one you walked where it fell short. `playbook validate` once.')
    if not widgets:
        asks.append('Is there an instrument here — a reading a probe computed inline that another bench would call? '
                    'If so, make it a widget under `cg/` and validate it.')
    from . import prompts as _prompts
    return _prompts.message('library_asks_worker', known=unknown_id, made=(' and you made '+', '.join('`'+m+'`' for m in made) if made else ''),
                            held=held, asks='; and '.join(asks))


def client_for(spec: dict[str, Any], observer: Any, config: dict[str, Any] | None = None) -> ModelClient:
    if spec.get('provider') == 'subscription':
        from mizpah.providers import hosted_model_client
        return hosted_model_client(spec, config or {}, observer)
    endpoint = EndpointConfig(**spec['endpoint'])
    if spec.get('provider', 'direct_json') == 'llama_client':
        return llama_model_client(endpoint, known_issues=spec.get('known_issues'),
                                  diagnostic_characters=spec.get('diagnostic_characters', 8192), observer=observer)
    return ModelClient(endpoint, observer=observer)


def observe_model(root: Path):
    def observe(kind: str, value: dict[str, Any]) -> None:
        if kind == 'model_response' and value['purpose'] in ('worker', 'handoff', 'controller'):
            print(json.dumps(dict(event='model_response', role=value['purpose'], status=value['status'],
                                  seconds=value['elapsed_seconds'], error=value['error'])), file=sys.stderr, flush=True)
    return observe


def bindings(config: dict[str, Any], root: Path, map_id: str, checkins: bool | None = None,
             project: Path | None = None) -> tuple[ModelClient, ModelClient | None, SandboxedShell]:
    observe = observe_model(root)
    worker = client_for(config['worker'], observe, config)
    if checkins is None:
        checkins = config['mizpah']['scaffolding']['checkins']
    checkin = client_for(config['controller'], observe, config) if checkins else None
    scratch = root/'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    sandbox = config['mizpah']['sandbox']
    environment = dict(sandbox['environment'], **{**layout.terra_env(project), 'TERRA_MAP': map_id, 'XDG_DATA_HOME': '/work/'+PLAYBOOK_PREFIX})
    services = ServiceLimits(**sandbox['services']) if sandbox.get('services') else None
    network = NetworkPolicy(**sandbox['network']) if sandbox.get('network') else None
    bound = dict(workspace_dir=str(project.resolve()), cache_dirs=cache_dirs(config), state_dirs=state_dirs(project)) \
        if bind_mode(config) and project is not None else {}
    shell = ShellConfig(**(config['shell'] | dict(scratch_root=str(scratch), limits=ShellLimits(**config['shell']['limits']),
                                                 read_only_binds=tuple(sandbox['read_only_binds']), environment=environment,
                                                 share_network=bool(sandbox.get('share_network', False)), services=services,
                                                 refused_paths=tuple(sandbox.get('refused_paths') or ()),
                                                 refused_patterns=refused_patterns(config), network=network,
                                                 scratch_dirs=scratch_dirs(config)) | bound))
    return worker, checkin, SandboxedShell(shell)


def refused_patterns(config: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """The standing refusals, minus the install ban for an environment gym: building an environment is exactly
    installing into /work, with the package hosts its config allows."""
    patterns = REFUSED_PATTERNS + (TICK_PATTERNS if config['mizpah']['scaffolding'].get('tick_guards') else ())
    if config['mizpah'].get('builds_base'):
        return tuple(p for p in patterns if 'install' not in p[0])
    return patterns


def checkin_settings(config: dict[str, Any]) -> ControllerSettings:
    """The v10 review contract (cadence, budgets, document edits) with the Mizpah check-in policy."""
    c = config['controller']
    return ControllerSettings(config['mizpah']['checkin_policy'], c['generation'], c['context_capacity'],
        config['mizpah'].get('checkin_output_tokens', 2048),
        c['maximum_model_calls'], c['maximum_tool_calls'], c['maximum_tool_output_characters'],
        c.get('output_headroom_tokens', 1), c.get('input_target_tokens'), c.get('recent_review_exchanges', 2),
        c.get('investigation_budgets'), c.get('maximum_document_edits_per_review'),
        maximum_completion_corrections=config['mizpah'].get('checkin_completion_corrections', 2),
        plain_review=True, plain_recent_exchanges=config['mizpah'].get('checkin_recent_exchanges', 6))


def declare_artifact_deps(config: dict[str, Any], project: Path, unknowns: list[dict[str, Any]]) -> dict[str, list[str]]:
    """An adopted artifact known depends on the files that make the artifact: the file the task built and its
    sibling modules (a CLI's entry point lives next to the command). Terra stamps their hashes; a later task
    that changes one of them makes the known stale, and the eval sees it. Without this, `build_cli` rewrote
    the dispatch and `quality_ok` stayed green on the map while `python3 -m weather quality` was broken."""
    declared: dict[str, list[str]] = {}
    for unknown in unknowns:
        creates = unknown_notes(unknown).get('creates')
        if not creates or read_known(project, unknown['id']) is None:
            continue
        target = project/creates
        paths: list[Path] = []
        if target.is_file():
            paths.append(target)
            if target.suffix == '.py':
                paths += [p for p in target.parent.glob('*.py') if p != target]
        elif target.is_dir():
            paths += [p for p in target.rglob('*') if p.is_file()]
        specs = ['file:'+p.relative_to(project).as_posix() for p in sorted(paths)]
        if specs:
            try:
                terra(config, project, 'known', 'depend', unknown['id'], *[a for spec in specs for a in ('--on', spec)])
                declared[unknown['id']] = specs
            except RuntimeError:
                continue
    return declared


def focus_globs(unknowns: list[dict[str, Any]]) -> tuple[str, ...]:
    """What the reviewer reads, most decisive first: the probes (a worker names its own — none is scaffolded —
    so every probe on the map is in focus, and the harness follows each into the widgets it names), then the
    artifacts the unknowns say the work order creates. The focus budget follows the reviewer's window."""
    globs = [d+'/map/probes/*/measure.py' for d in (layout.STATE_DIRNAME, layout.LEGACY_DIRNAME)]
    for unknown in unknowns:
        creates = unknown_notes(unknown).get('creates')
        if creates:
            globs.append(creates)
            globs.append(creates.rstrip('/')+'/*')
    # Not the walks and not the widgets by default: walk honesty is Playbook's and the controller's; a widget's
    # honesty is the library's. The reviewer answers one question from two kinds of file.
    return tuple(dict.fromkeys(globs))


# Cartograph as typed tools: the surface is small and non-obvious (a worker spent nine turns hunting
# the library on disk because it did not know `inspect` existed). Each renders to the CLI command and
# runs through the sandbox like bash, so procedures and the journal keep one vocabulary.
def string(desc: str, **extra: Any) -> dict[str, Any]:
    return dict(type='string', description=desc, **extra)


# Typed only where bash measurably fails or the surface cannot be discovered (journal tally, 2026-09-18):
# cartograph search/inspect/install/create were never found (nine turns hunting the library on disk);
# terra route complete fumbled 6/16, known promote 7/30, unknown link-run 7/33 — `known ladder` replaces
# those rungs and is new, so it is undiscoverable; route block is the honest exit and must be visible;
# playbook add-step fumbled 8/26, edit-step 4/18, start 6/59, and `playbook --help` was called seven times.
# Everything else (probe run 5/61, probe validate, known adopt, cartograph validate, playbook search) stays bash.
COMMAND_TOOLS: tuple[dict[str, Any], ...] = (
    dict(name='cartograph_search', description='Search the widget library for an existing part before writing one: '
         'parsers, statistics, checks, tool wrappers. Returns ids with descriptions and relevance. Search is cheap; '
         'create without a prior search is refused.',
         command='cartograph search {query} --language {language} --top-k {top_k}',
         parameters=dict(type='object', properties=dict(query=string('what the part must do, in plain words'),
                                                        language=string('implementation language', default='python'),
                                                        top_k=dict(type='integer', description='how many hits', default=3)),
                         required=['query'])),
    dict(name='cartograph_inspect', description='Show a widget: its description, API, dependencies and (with source) '
         'the code and examples, straight from the library. Use it to decide between install and create.',
         command='cartograph inspect {widget_id} {source}',
         parameters=dict(type='object', properties=dict(widget_id=string('id from search, e.g. data-csv-mean-python'),
                                                        source=dict(type='boolean', description='include source files', flag='--source')),
                         required=['widget_id'])),
    dict(name='cartograph_install', description='Install a widget into this project under cg/<dir>/; then import it with '
         'sys.path.insert(0, "cg/<dir>") and from src.<module> import <fn> (read cg/<dir>/examples/ first).',
         command='cartograph install {widget_id}',
         parameters=dict(type='object', properties=dict(widget_id=string('id from search')), required=['widget_id'])),
    dict(name='cartograph_create', description='Scaffold a new widget under cg/ when no library part fits (refused '
         'without a prior search). Then rm the stub src file, write a skeleton, fill one function per edit, add '
         'tests and an example, and `cartograph validate cg/<dir>` in bash.',
         command='cartograph create {slug} --language {language} --domain {domain} --description {description} {tags}',
         parameters=dict(type='object', properties=dict(slug=string('kebab-case name, e.g. csv-column-mean'),
                                                        language=string('implementation language', default='python'),
                                                        domain=string('data | backend | frontend | infra | universal | ...', default='data'),
                                                        description=string('one sentence: what it does'),
                                                        tags=string('comma-separated tags, 3-5', flag='--tags')),
                         required=['slug', 'description'])),
    dict(name='terra_known_ladder', description='Take an unknown up the whole ladder in one call: runs its probe until '
         'the evidence meets the bar, links every run, graduates, promotes to med and adopts to the project map. '
         'Refusals name the rung that failed. Use after `terra probe validate <probe>` passes.',
         command='terra known ladder {unknown_id} {to} {confidence}',
         parameters=dict(type='object', properties=dict(unknown_id=string('the unknown id'),
                                                        to=string('run target JSON when the probe needs one, e.g. {"kind": "file"}', flag='--to'),
                                                        confidence=string('bar to reach', default='med', flag='--confidence')),
                         required=['unknown_id'])),
    dict(name='terra_route_complete', description='Close the task once every unknown it carries is adopted: cite one '
         'run id and every known id. Refused while a known is missing, and refused while a procedure walk you opened '
         'still has an unticked step — the walks are the method, closed before the claim.',
         command='terra route complete {task} --run {run} {knowns}',
         parameters=dict(type='object', properties=dict(task=string('task id'), run=string('a run id of this task'),
                                                        knowns=dict(type='array', items=dict(type='string'), flag='--known',
                                                                    description='every known id the task carries')),
                         required=['task', 'run', 'knowns'])),
    dict(name='done', description='Say you are done. This is the only way a stretch of work ends: after `terra route '
         'complete` reported success (summary: the run and known ids); after the write-up (summary: the procedure ids '
         'you touched); after answering a correction from the reviewer (summary: what you changed). The reviewer looks '
         'at this claim; nothing else brings it back. An edit that returned ok landed — do not make it again, call done.',
         command='echo done: {summary}',
         parameters=dict(type='object', properties=dict(summary=string('the ids, or one line on what you finished')),
                         required=['summary'])),
    dict(name='terra_route_block', description='The honest exit: the source cannot be read as the unknown asks, or '
         'the question is not answerable from this workspace. Say what you needed and could not read; then stop.',
         command='terra route block {task} --reason {reason}',
         parameters=dict(type='object', properties=dict(task=string('task id'), reason=string('what you needed and could not read')),
                         required=['task', 'reason'])),
    dict(name='playbook_open', description='Write a whole procedure as a checklist to .playbook/open/<id>--<for>.md '
         'in the workspace; read that file once, follow it in order, tick steps off. One copy per walk: say what '
         'this walk is for; a procedure already open here is handed back with its next step. Use for the bootstrap '
         'and for any domain procedure a search finds. Every step is done or skipped with its reason (`playbook tick`, bash); after the plan, as many at once as belong together.',
         command='playbook open {id} --for {purpose}',
         parameters=dict(type='object', properties=dict(id=string('procedure id from search'),
                                                        purpose=string('what this walk is for: the unknown(s), artifact or source')),
                         required=['id', 'purpose'])),
    dict(name='playbook_create', description='Create a new procedure (after the gate is green, when your method was '
         'specific to this kind of source or artifact and no existing procedure captures it). Then add its steps one '
         'at a time with playbook_add_step, each one action with the exact commands; `playbook validate <id>` once at the end.',
         command='playbook create {id} --title {title} --description {description} {tags}',
         parameters=dict(type='object', properties=dict(id=string('kebab-case procedure id'), title=string('short title'),
                                                        description=string('when to use it and what it produces, one or two sentences'),
                                                        tags=string('comma-separated tags, 3-5', flag='--tags')),
                         required=['id', 'title', 'description'])),
    dict(name='playbook_add_step', description='Append one step to a procedure you created or followed: a short title '
         'and one imperative `do` with the exact commands. When the step is really another procedure, pass its id as '
         '`procedure`: the step links it (open renders the command to walk it) instead of copying its steps.',
         command='playbook add-step {id} --title {title} --do {do} {procedure}',
         parameters=dict(type='object', properties=dict(id=string('procedure id'), title=string('short step title'),
                                                        do=string('one action, with the exact commands'),
                                                        procedure=dict(type='string', flag='--procedure',
                                                                       description='id of the procedure this step walks (optional)')),
                         required=['id', 'title', 'do'])),
    dict(name='playbook_edit_step', description='Rewrite one step of a procedure where it fell short of what you '
         'actually had to do; target it by its current title. `procedure` links another procedure to the step.',
         command='playbook edit-step {id} --title {title} --do {do} {procedure}',
         parameters=dict(type='object', properties=dict(id=string('procedure id'), title=string('current step title'),
                                                        do=string('the new imperative, with the exact commands'),
                                                        procedure=dict(type='string', flag='--procedure',
                                                                       description='id of the procedure this step walks (optional)')),
                         required=['id', 'title', 'do'])),
)


# Records only a tool may write. The writeback would drop hand edits anyway; refusing them at the tool saves the
# turns spent making them and the turns spent wondering why they did not take.
PROTECTED_PATHS = tuple(d+'/'+rel for d in (layout.STATE_DIRNAME, layout.LEGACY_DIRNAME)
                        for rel in ('brief.json', 'route.json', 'map/knowns/*', 'map/runs/*',
                                    'map/sessions/*/knowns/*', 'map/sessions/*/runs/*', 'map/unknowns/*')) + (
                   '.playbook/playbook/procedures/*',)   # the checklist copy under .playbook/open/ is ticked by editing it
REFUSED_PATTERNS = (
    (r'--skip-gate\b', 'the gate is not yours to skip: a red gate says what is missing, and a block says why you cannot'),
    (r'--freehand\b', 'a claim-shaped task completes on map evidence (--run/--known), never on prose'),
    (r'\bcartograph\s+(checkin|publish)\b', 'widgets are checked in by the harness after green, never by the worker'),
    (r'\bplaybook\s+(remove-step|edit)\s+mizpah-', 'the bootstrap procedure is not yours to rewrite'),
    (r'(>>?|\btee\b|(?<=\s)-i(?=\s))\s*[^|;&]*\.(terra|mizpah)/(brief|route)\.json', 'the brief moves by proposal and the route by terra route; neither is a file to write'),
    (r'(>>?|\btee\b|(?<=\s)-i(?=\s))\s*[^|;&]*\.(terra|mizpah)/map/(knowns|runs|unknowns)/', 'knowns, runs and unknowns are born by terra commands, never by writing their files'),
    (r'(>>?|\btee\b|(?<=\s)-i(?=\s)|\bmv\b|\bcp\b)\s*[^|;&]*\.playbook/playbook/procedures/', 'the store is not a file to write: improving a procedure is `playbook edit-step` / `add-step`'),
    (r'\bsystemctl\b|\bsystemd-run\b|\bloginctl\b', 'the host\'s service manager is outside the sandbox; services start with `svc start`'),
    (r'\b(pip3?|python3?\s+-m\s+pip|uv\s+pip|pipx|conda)\s+install\b', 'nothing installs in the sandbox: there is no pip and no network. The gym environment '
     'provides the toolchain and its packages (see the enablers of your task); a package it lacks is `terra route block` naming '
     'the package, so the person can add it to the environment'),
    (r'\bcurl\b[^|;&]*(/stop\b|/shutdown\b|/slots\b)', 'the model server is not yours to signal'),
)
# Scaffolding (`scaffolding.tick_guards`): the shape of a tick, on for a model that needs the rail.
TICK_PATTERNS = (
    # A tick is a claim like a reading: it comes from the step's own work, in the command that did it. Fifty
    # boxes in one loop is not a walk (attempt 4: `for n in 1..50; do playbook tick --done $n; done`, then route
    # complete).
    (r'\bfor\b[^|&]*\bdo\b[^|&]*\bplaybook\s+tick\b|\bwhile\b[^|&]*\bdo\b[^|&]*\bplaybook\s+tick\b|\bxargs\b[^|;&]*\bplaybook\s+tick\b',
     'a walk is not ticked in a loop: tick a step in the command that does it, with what it found'),
    (r'(\bplaybook\s+tick\b.*){4,}',
     'four or more ticks in one command is a list closed after the fact: tick a step in the command that does it'),
    (r'^\s*playbook\s+tick\b[^;&|\n]*((;|&&)\s*playbook\s+tick\b[^;&|\n]*)*\s*;?\s*$',
     'a tick rides on the command that does the step\'s work (the check, the edit\'s run, the reading), never alone: '
     'a walk ticked in tick-only turns is boxes, not a method'),
)


def review_cadence(config: dict[str, Any]) -> ReviewPolicy:
    """When the reviewer looks. At the worker's completion claim only: the periodic look was the v10 drift guard
    for a small model; on Luna it produced "stub not implemented yet" corrections mid-work and pulled workers back
    into measurement inside bookkeeping rounds. `checkin_periodic: true` restores it. (The bootstrap look at turn
    1 goes with it: there is nothing to review before the first turn.)"""
    return ReviewPolicy(**(config['review_policy'] if config['mizpah'].get('checkin_periodic', False)
                           else dict(config['review_policy'], update_interval=10**6, bootstrap_after_turns=10**6)))


def build_settings(config: dict[str, Any], assignment: str, reference: str,
                   unknowns: list[dict[str, Any]] = ()) -> SessionSettings:
    # What the reviewer may read of the focus files, from the reviewer's own window: three quarters of its
    # tokens in characters (~a quarter of the window at three characters a token), a quarter of that per
    # file. Fixed at 24000/6000 — a 60K number — the reviewer saw a 9 KB measure.py cut at 6000, could not
    # see the boolean its correction was about, and held the correction the worker had already answered.
    focus_budget = max(24000, int(config['controller']['context_capacity'])*3//4)
    # The check-in controller reviews on the v10 cadence against the task reference; routing and
    # project eval are separate steps in controller.py and never enter the session.
    # Check-ins are scaffolding: ~115 reviews across two runs issued no correction and held on the two
    # fabrications the structure later caught. With Terra constraining the trajectory they are a toggle.
    checkins = config['mizpah']['scaffolding']['checkins']
    return SessionSettings(assignment, config['mizpah']['worker_policy'], reference if checkins else None,
        config['worker']['generation'], SessionPolicy(**config['session_policy']),
        review_cadence(config),
        checkin_settings(config) if checkins else None,
        config['review_on_completion'], config['guidance_prefix'],
        maximum_generation_retries=config.get('maximum_generation_retries', 0),
        write_existing_files=True, edit_requires_read=False,
        command_tools=COMMAND_TOOLS if config['mizpah']['scaffolding'].get('command_tools', True) else (),
        final_tools=('done',),
        repeated_failure_rollover=config['mizpah'].get('repeated_failure_rollover'),
        repeated_success_rollover=config['mizpah'].get('repeated_success_rollover'),
        review_focus_globs=focus_globs(list(unknowns)), review_focus_characters=focus_budget, review_focus_file_characters=focus_budget//4,
        protected_paths=PROTECTED_PATHS,
        **{key: config[key] for key in ('worker_tools', 'maximum_tool_argument_characters', 'maximum_write_characters',
                                        'maximum_edit_characters', 'maximum_read_lines') if key in config})


def model_up(config: dict[str, Any]) -> bool:
    from mizpah.ops import model_up as reachable
    return reachable((config['worker'].get('endpoint') or {}).get('base_url'))


def run_through_outages(session: FocusedSession, config: dict[str, Any], root: Path, *, maximum_worker_turns: int,
                        wait_seconds: int = 300, health: Any = None) -> dict[str, Any]:
    """`session.run`, but a model server that goes away mid-call is waited for, not counted as a failure.

    A supervised server restarts in seconds; the session discards the torn call and continues. Only a server
    that stays down past `wait_seconds` surfaces as the transport error it is. Rejected generations are not
    outages and pass straight through.
    """
    import time
    outages = 0
    stop_files = (root/'STOP', root.parent/'STOP', root.parent.parent/'STOP')

    nudge = root/'nudge.md'
    burst_start, burst = session.status()['completed_worker_turns'], maximum_worker_turns

    def stop_requested() -> bool:
        return any(p.exists() for p in stop_files) or nudge.exists()

    while True:
        try:
            status = session.run(maximum_worker_turns=maximum_worker_turns, stop_when=stop_requested)
            if status['status'] == 'stopped' and nudge.exists() and not any(p.exists() for p in stop_files):
                # The person's word, left as nudge.md while the worker ran: the session pauses at the boundary it is
                # on, the note goes in as the next user message, and the burst resumes. No restart to say a sentence.
                text = nudge.read_text().strip()
                nudge.rename(root/'nudge.delivered.md')
                if text and status['phase'] == 'worker' and status['pending_io'] is None:
                    from . import prompts as _prompts
                    session.interject(_prompts.message('nudge_worker', text=text), label='the person')
                if burst is not None:   # the rest of the burst, not a fresh one
                    maximum_worker_turns = max(1, burst-(status['completed_worker_turns']-burst_start))
                continue
            return status
        except RejectedGeneration:
            raise
        except ModelTransportError as error:
            # The wait starts at the outage, not at entry: one call to run() spans a whole burst of turns.
            from . import ops
            if not ops.network_reachable(ops.provider_host(config['worker'], config)):
                # No route to the model's host: not the model's outage and not counted as one. Wait for the
                # network, discard the torn call, ask again.
                checker = health or ops.Health(config, root)
                ops.record_outage(root, 'worker', config['worker'], error, outages, task=root.name,
                                  action='the network is down; waiting for it, not counted')
                if not checker.wait_for_network(ops.provider_host(config['worker'], config)):
                    raise
                discarded = session.discard_pending()
                if discarded:
                    (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')
                if burst is not None:
                    try:
                        maximum_worker_turns = max(1, burst-(session.status()['completed_worker_turns']-burst_start))
                    except Exception:  # noqa: BLE001
                        pass
                continue
            outages += 1
            too_big = 'exceeded the configured byte limit' in str(error)
            try:
                turn = session.status()['completed_worker_turns']
            except Exception:  # noqa: BLE001
                turn = None
            delay = ops.backoff_seconds(outages, cap=wait_seconds)
            ops.record_outage(root, 'worker', config['worker'], error, outages, task=root.name, turn=turn,
                              waited_seconds=None if too_big else delay,
                              action=('the reply is discarded and the worker is asked again with a warning' if too_big
                                      else 'the torn call is discarded; the worker asks again after '+str(int(delay))+' s' if outages <= 5
                                      else 'the sixth in a row: the task fails'))
            if outages > 5:
                raise
            if not too_big:
                # A server that went away is waited for, with backoff; a reply that was too big is the worker's own
                # doing and the server is fine.
                checker = health or ops.Health(config, root)
                if not checker.wait_for_model((config['worker'].get('endpoint') or {}).get('base_url'), wait_seconds=wait_seconds, attempt=outages):
                    raise
            discarded = session.discard_pending()
            if discarded:
                (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')
            if burst is not None:
                # The rest of the burst, not a fresh one: a torn call at turn 138 restarted a 119-turn burst from
                # there, and the effort boundary owed at 240 moved to 257, then 302 after the next outage — the
                # worker on engrave (2026-09-22) never heard "estimate spent" a second time, and the morning's
                # "no effort check until turn 235" was the same slip.
                try:
                    maximum_worker_turns = max(1, burst-(session.status()['completed_worker_turns']-burst_start))
                except Exception:  # noqa: BLE001
                    pass
            if too_big:
                # The warning rides on the failure, to the one worker that needs it, when it can act (a sketch gym
                # streamed a four-thumbnail SVG as a write argument five times over, 2026-09-20). Not a standing
                # instruction: the delta, once.
                status = session.status()
                if status['phase'] == 'worker' and status['pending_io'] is None:
                    session.interject(label='reply too big', message='Your last reply was larger than the transport allows and was discarded; nothing in it '
                                      'was applied. A reply is a plan and a tool call, never the artifact: when the thing you '
                                      'are making is big (an SVG of strokes, a long data file), a widget under cg/ writes it '
                                      'from a short plan you give it (regions, directions, spacing, widths, a seed), and you '
                                      'look at the result by reading its render, not its source. Continue from the unchanged '
                                      'conversation with a smaller reply.')


def run_task(config: dict[str, Any], project: Path, root: Path, task_id: str | None = None) -> dict[str, Any]:
    """One task, one session; whatever the worker left running (a server, a browser) stops with it."""
    holder: dict[str, Any] = {}
    try:
        return _run_task(config, project, root, task_id, holder)
    finally:
        shell = holder.get('shell')
        if shell is not None:
            stopped = shell.stop_all()
            if stopped:
                (root/'services.jsonl').open('a').write(json.dumps(dict(at=time.time(), stopped=stopped))+'\n')
            shell.close_network()


def install_walk_widgets(config: dict[str, Any], project: Path, walk_id: str) -> list[str]:
    """Install the widgets a method names (its `widgets` field, across the procedures its walk inlines) into the
    project's cg/ before the worker's first turn. Returns the ids present afterwards; a failed install is left
    for the worker (its step says so)."""
    import subprocess as sp
    try:
        out = sp.run([config['mizpah']['playbook'], 'reach', walk_id], capture_output=True, text=True, timeout=60).stdout
        wanted = [str(w) for w in (json.loads(out[out.find('{'):]).get('widgets') or [])]
    except (ValueError, OSError, sp.SubprocessError):
        return []
    return install_widgets(config, project, wanted)


def install_widgets(config: dict[str, Any], project: Path, wanted: list[str]) -> list[str]:
    """Install library widgets into the project's cg/; the ids present afterwards (a failed install is left to the worker)."""
    import subprocess as sp
    env = dict(os.environ, WIDGET_LIBRARY_PATH=config['mizpah']['widget_library'])
    present: list[str] = []
    for wid in wanted:
        target = project/'cg'/wid.replace('-', '_')
        if target.is_dir():
            present.append(wid)
            continue
        try:
            done = sp.run([config['mizpah']['cartograph'], 'install', wid], cwd=project, capture_output=True, text=True, timeout=120, env=env)
            if done.returncode == 0 and target.is_dir():
                present.append(wid)
        except (OSError, sp.SubprocessError):
            continue
    return present


def assigned_widgets(task: dict[str, Any]) -> list[str]:
    """The widgets the controller named for this task (`widget:<id>` in its acceptance)."""
    return [e[len('widget:'):].strip() for e in task.get('acceptance') or [] if isinstance(e, str) and e.startswith('widget:')]


def assigned_walk(task: dict[str, Any]) -> tuple[str, int]:
    """The procedure walk the route named for this task (`walk:<id>@<from>` in its acceptance), or ('', 0)."""
    for entry in task.get('acceptance') or []:
        if isinstance(entry, str) and entry.startswith('walk:'):
            spec = entry[len('walk:'):].strip()
            pid, _, start = spec.partition('@')
            return pid.strip(), int(start) if start.strip().isdigit() else 0
    return '', 0




def _run_task(config: dict[str, Any], project: Path, root: Path, task_id: str | None, holder: dict[str, Any]) -> dict[str, Any]:
    """Pick (or resume), open the task map, run until green or the backstop, harvest the playbook, report.

    A root that already holds a session is resumed: the work order stopped short (paused, blocked, sent back,
    reopened) and the same session continues from where it paused.
    """
    project, root = project.resolve(), root.resolve()
    settings = config['mizpah']
    store = Path(settings['playbook_store'])
    resuming = (root/'state.sqlite3').exists()
    if resuming:
        # A work order that stopped short reopens its own worker where it left off; a new work order never inherits
        # another's window (continue_from is retired, 2026-09-21: what is on disk is the continuity).
        saved = json.loads((root/'task.json').read_text())
        task, unknowns, map_id = saved['task'], saved['unknowns'], saved['map']
        probes_before = tuple(saved.get('probes_before') or ())
        task = pick_task(config, project, task['id']) | dict(bucket=next(
            t['bucket'] for t in terra(config, project, 'route', 'status')['tasks'] if t['id'] == task['id']))
        worker_client, checkin, shell = bindings(config, root, map_id, project=project)
        holder['shell'] = shell
        try:
            session = FocusedSession.open(root, worker=worker_client, shell=shell, controller=checkin,
                                          shared_workspaces=shared_workspaces(root))
        except ValueError:
            # A session created with check-ins keeps its reviewer binding even after the toggle went off.
            checkin = client_for(config['controller'], observe_model(root), config)
            session = FocusedSession.open(root, worker=worker_client, shell=shell, controller=checkin,
                                          shared_workspaces=shared_workspaces(root))
        discarded = session.discard_pending()  # a killed run leaves an uncommitted call; nothing is replayed
        lifted = session.reset_generation_block()   # a degenerate window is retried in a fresh one, not re-raised
        if lifted:
            (root/'discarded.jsonl').open('a').write(json.dumps(dict(generation_block=lifted))+'\n')
        # The config is the operator's current judgment; a resumed session renders its next request under it.
        wanted = {k: config['session_policy'][k] for k in FocusedSession.RETUNABLE if k in config['session_policy']}
        current = {k: getattr(session.settings.session_policy, k) for k in wanted}
        if wanted != current:
            session.retune(**{k: v for k, v in wanted.items() if current[k] != v})
        # The reviewer's current policy text, memory and completion budget apply to a resumed session too: a
        # session saved before they existed would otherwise run its old text with no budget to the end.
        if session.controller_client is not None and session.settings.controller is not None:
            fresh = checkin_settings(config)
            held = session.settings.controller
            cadence = review_cadence(config)   # when the reviewer looks: the config's, not the session's
            if held.system_prompt != fresh.system_prompt or held.maximum_completion_corrections != fresh.maximum_completion_corrections \
                    or session.settings.review_policy != cadence:
                session.settings = replace(session.settings, review_policy=cadence,
                                           controller=replace(held, system_prompt=fresh.system_prompt,
                                                              maximum_completion_corrections=fresh.maximum_completion_corrections))
                session.state['settings'] = asdict(session.settings)
                session.state.setdefault('review_log', []).append(dict(turn=session.progress.turns, boundary='policy',
                                                                       operation='policy_changed', correction='resumed: current review policy', evidence=''))
        # The worker's own policy text too: a rule added while a worker was mid-task (the probe cost rule, 2026-09-21)
        # reached every new session and not the one it was written for. The system text changes, so the prefix
        # cache misses once; the wire view of the turns is untouched.
        if session.settings.worker_system != config['mizpah']['worker_policy']:
            session.settings = replace(session.settings, worker_system=config['mizpah']['worker_policy'])
            session.state['settings'] = asdict(session.settings)
            # The text on the wire is the session's first message, saved with it, not read from the settings.
            first = session.session.messages[0] if session.session.messages else None
            if isinstance(first, dict) and first.get('role') == 'system':
                first['content'] = config['mizpah']['worker_policy']
            session.state.setdefault('review_log', []).append(dict(turn=session.progress.turns, boundary='policy',
                                                                   operation='policy_changed', correction='resumed: current worker policy', evidence=''))
        # The payload bounds too: a session saved under the small-edit caps keeps rejecting whole files after the
        # config lifted them.
        bounds = {k: config.get(k) for k in ('maximum_tool_argument_characters', 'maximum_write_characters', 'maximum_edit_characters')}
        bounds |= dict(write_existing_files=True, edit_requires_read=False)
        if any(getattr(session.settings, k) != v for k, v in bounds.items()):
            session.settings = replace(session.settings, **bounds)
            session.state['settings'] = asdict(session.settings)
            from cg.bp_focused_agent_session_python.src.focused_agent_session import worker_tools
            session.session.tools = worker_tools(session.settings.worker_tools, session.settings, session.state.get("capabilities"))
        # The tools too: a session saved without `done` (or a tool since added) keeps looping for want of it.
        current_tools = COMMAND_TOOLS if config['mizpah']['scaffolding'].get('command_tools', True) else ()
        if tuple(t['name'] for t in session.settings.command_tools) != tuple(t['name'] for t in current_tools) \
                or tuple(session.settings.final_tools) != ('done',):
            session.settings = replace(session.settings, command_tools=current_tools, final_tools=('done',))
            session.state['settings'] = asdict(session.settings)
            note = ('Your tools changed while you were paused: `done` exists now. When a stretch of work is finished — '
                    'the task after `terra route complete` succeeded, the write-up after your edits validated, an answer '
                    'to a correction — call `done` with the ids. An edit that returned ok landed; do not make it again.')
            status_now = session.status()
            if status_now['phase'] == 'worker' and status_now['pending_io'] is None:
                session.interject(note, label='resumed: done tool added')
        # The person's word to this worker, left as `nudge.md` in the task root while the loop was paused: put
        # before it once, as the next user message, then kept as `nudge.delivered.md` for the record. The only
        # way in a person had was the controller's note; a worker an hour into decoding a PDF had nobody to say
        # "there is a cheaper reading".
        # The controller reopened this work order: its reading no longer stands. The reason is the first thing the
        # worker hears, as a continuation of the session it finished — the same window, its probes in hand.
        reopen = root/'reopen.md'
        if reopen.exists() and reopen.read_text().strip():
            status_now = session.status()
            from . import prompts as _prompts
            text = _prompts.message('reopened_worker', why=reopen.read_text().strip())
            if status_now['phase'] == 'complete':
                session.continue_with(text, label='reopened')
            elif status_now['phase'] == 'worker' and status_now['pending_io'] is None:
                session.interject(text, label='reopened')
            reopen.rename(root/'reopen.delivered.md')
        nudge = root/'nudge.md'
        if nudge.exists() and nudge.read_text().strip():
            status_now = session.status()
            from . import prompts as _prompts
            text = _prompts.message('nudge_worker', text=nudge.read_text().strip())
            if status_now['phase'] == 'complete':
                session.continue_with(text, label='the person')
            elif status_now['phase'] == 'worker' and status_now['pending_io'] is None:
                session.interject(text, label='the person')
            nudge.rename(root/'nudge.delivered.md')
        if not (root/PLAYBOOK_BASE).is_dir():
            # A task started before three-way merges has no base; the store as it is now is the best one there is
            # (what moved before this point is already in it; what moves after is merged).
            (root/PLAYBOOK_BASE).mkdir()
            for path in store.glob('*.json'):
                (root/PLAYBOOK_BASE/path.name).write_bytes(path.read_bytes())
        if discarded:
            (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')
        # A resumed worker is told where it stood, not left to its compacted memory of it: every procedure it
        # opened and did not finish, with the next unticked step, so it picks the walk up rather than starting
        # another one (or forgetting the first — the gate would hold the task on it either way).
        left_open = open_walks(evidence(session))
        if left_open:
            note = ('Resuming this task after a pause. You left these procedure walks open; continue each '
                    'from its next step (tick `[x]` done or `[-]` not needed as you go) before anything else:\n'
                    +'\n'.join('  - '+w for w in left_open)+'\n')
            status = session.status()
            # A session that had finished takes the note as a continuation; one paused mid-work (killed between
            # turns) takes it as an interjection; one mid-call or mid-review is left to finish its boundary.
            if status['phase'] == 'complete':
                session.continue_with(note, label='resumed: walks left open')
            elif status['phase'] == 'worker' and status['pending_io'] is None:
                session.interject(note, label='resumed: walks left open')
    else:
        task = pick_task(config, project, task_id)
        map_id = open_task_map(config, project, task)
        unknowns = [read_unknown(project, uid, map_id) for uid in task_unknown_ids(task)]
        try:
            brief = json.loads((project/layout.dirname(project)/'brief.json').read_text())
        except (OSError, ValueError):
            brief = {}
        for u in unknowns:
            u['_measure_exists'] = (project/layout.dirname(project)/'map'/'probes'/(u['id']+'_probe')/'measure.py').exists()
        assignment = render_assignment(task, unknowns, map_id, probe_inputs(project, task), layout.dirname(project),
                                       prior=prior_readings(project, unknowns), brief=brief)
        parts = library_parts(config, project, unknowns)
        if parts:
            assignment += ('The library already has parts near this work; install and extend one where it nearly fits '
                           'rather than creating a near-duplicate:\n'+'\n'.join('  - '+p for p in parts)+'\n')
        walk_id, walk_from = assigned_walk(task)
        if walk_id:
            # The route named the method: the worker opens it, flat, and does not search first. The method's
            # widgets are installed under cg/ before its first turn: a widget is a dependency of the method, not
            # a step of it (a step that said "install X" cost a tick and was satisfied by installing without using).
            installed = install_walk_widgets(config, project, walk_id)
            assignment += ('Your method is named: open it before anything else — `playbook open '+walk_id+' --for "'+task['id']+'"'
                           +(' --from '+str(walk_from) if walk_from else '')+'` — and walk it; it is one straight list of at '
                           'most 50 steps, each naming the procedure it came from. Search the playbook only if that walk '
                           'does not fit what is in front of you, and say so when you block or complete.\n')
            if installed:
                assignment += ('The method\'s widgets are installed under cg/ already: '+', '.join('`'+w+'`' for w in installed)
                               +' — call them; a step that says to install one is done when `cartograph validate cg/<dir>` passes.\n')
        else:
            methods = procedure_parts(config, task, unknowns)
            if methods:
                assignment += ('The playbook already has methods near this work; search for them, open the best with '
                               '`playbook open <id> --for ...` and follow it before working the method out yourself:\n'
                               +'\n'.join('  - '+m for m in methods)+'\n')
        named = assigned_widgets(task)
        if named:
            # The controller searched the library and named instruments for this work: installed like a method's.
            present = install_widgets(config, project, named)
            assignment += ('The route named widgets for this work, installed under cg/: '+', '.join('`'+w+'`' for w in present)
                           +(' (not installed: '+', '.join('`'+w+'`' for w in named if w not in present)+' — install it yourself)'
                             if len(present) < len(named) else '')
                           +'. Read each one\'s example and call it where it fits; extend it rather than write a near-duplicate.\n')
        from . import bases
        assignment += bases.enabler_text(config)   # the gym's environment base, when it has one
        if config['mizpah'].get('builds_base'):
            assignment += ('\nThis gym builds an environment: on green, /work becomes the base "'+str(config['mizpah']['builds_base'])
                           +'" that later gyms are set up in. Installing into /work is the work here (pip, downloads from the '
                           'hosts the gym\'s config allows). Open `mizpah-build-environment` with `playbook open` and follow '
                           'it: venv, tools, wrappers in bin/, relocatable shebangs, base.json; each need is one tool that '
                           'must run.\n')
        worker_client, checkin, shell = bindings(config, root, map_id, project=project)
        holder['shell'] = shell
        reference = render_reference(project, task, unknowns)
        # Bind mode: only the state directories are packed in; the tree is the project directory itself.
        initial = pack_workspace(project, store, only=state_dirs(project)) if bind_mode(config) \
            else pack_workspace(project, store, exclude=scratch_dirs(config))
        # The store as the task received it, for three-way merges at harvest (another task may improve the
        # same procedure meanwhile; without a base the last harvest wrote over the first).
        base_dir = root/PLAYBOOK_BASE
        base_dir.mkdir(exist_ok=True)
        for path in store.glob('*.json'):
            (base_dir/path.name).write_bytes(path.read_bytes())
        session = FocusedSession.create(root, build_settings(config, assignment, reference, unknowns), worker=worker_client,
                                        shell=shell, controller=checkin, initial_workspace=initial,
                                        shared_workspaces=shared_workspaces(root))
        probes_before = protected_probes(project, task)
        (root/'task.json').write_text(json.dumps(dict(task=task, unknowns=unknowns, map=map_id, assignment=assignment,
                                                      reference=reference, probes_before=probes_before), indent=1))
    # The bucket's effort estimate in turns, soft — plus an allowance per reading beyond the first: a probe is
    # written, validated, run, laddered and adopted, five or so turns each, and a low task carrying eight of them
    # met its 60-turn boundary twice and closed incomplete at 39 (measure_headline_candidates, inspect_mark_files).
    estimate = settings['turn_budget'][task['bucket']]+settings.get('turns_per_extra_reading', 8)*max(0, len(unknowns)-1)
    cap = settings.get('turn_cap', 400)                  # safety only; not a bucket, not a judgment
    origin = 0

    def turns(status: dict[str, Any]) -> int:
        return status['completed_worker_turns']-origin
    rounds: list[dict[str, Any]] = []
    gate: dict[str, Any] = dict(ok=False, problems=['not run'], knowns=[], runs=[])
    playbook: dict[str, list[str]] = dict(installed=[], rejected=[], ignored=[])
    widgets: dict[str, list[str]] = dict(checked_in=[], rejected=[], unchanged=[])
    status = session.status()
    blocked_reason = None
    # A resumed session has already passed the boundaries below its turn count: starting at zero re-fired
    # "estimate spent" twice, one turn apart, on an engrave resumed at 325 turns (2026-09-22).
    overruns = turns(status)//estimate
    stalled = 0
    while stalled < settings['gate_rounds']:
        remaining = cap-turns(status)
        if remaining <= 0:
            break
        # Run to the next estimate boundary; the worker judges its own effort there.
        boundary = min(remaining, max(1, estimate*(overruns+1)-turns(status)))
        status = run_through_outages(session, config, root, maximum_worker_turns=boundary)
        written = writeback(state_of(session.workspace()), project, task, map_id, probes_before)
        refused = [w for w in written if w.startswith('refused:')]
        if refused:
            (root/'writeback.jsonl').open('a').write(json.dumps(dict(turns=turns(status), refused=refused))+'\n')
        session.prune_workspaces()
        blocked_reason = worker_blocked(project, task)
        if blocked_reason is not None:
            # The honest exit: the worker says the source cannot be read as asked. That is state
            # for the controller (a proposal, usually), not a red round for the worker.
            rounds.append(dict(turns=turns(status), session=status['status'], gate='blocked',
                               blocked_reason=blocked_reason, final_text=status['final_text']))
            break
        if status['status'] == 'stopped':
            # The operator's STOP file: the session paused at a turn boundary and reopens where it is; the task
            # stays in_progress on the route so the next run resumes it.
            rounds.append(dict(turns=turns(status), session='stopped', gate='stopped'))
            break
        if status['status'] == 'paused':
            overruns += 1
            rounds.append(dict(turns=turns(status), session=status['status'], gate='effort',
                               overruns=overruns))
            if turns(status) >= cap:
                break
            session.interject(effort_message(task, estimate, turns(status), overruns), label='estimate spent')
            continue
        gate = task_gate(config, project, task, map_id, root)
        if gate['ok'] and config['mizpah'].get('remeasure', True):
            # Green on the worker's evidence is not yet green: the host takes the readings itself.
            checked = remeasure(config, project, root, [k for k in gate['knowns'] if k in task_unknown_ids(task)] or task_unknown_ids(task))
            if checked:
                gate = dict(gate, ok=False, problems=checked)
        unticked = open_checklists(evidence(session))
        evidence_ok = gate['ok']   # the readings are in; what is left, if anything, is bookkeeping
        if unticked:
            gate = dict(gate, ok=False, problems=gate['problems']+unticked)
        previous = rounds[-1].get('problems') if rounds else None
        # A red round that changed nothing counts against gate_rounds; a red round with fewer
        # problems is progress and costs nothing. The worker keeps deciding.
        stalled = stalled+1 if previous is not None and len(gate['problems']) >= len(previous) else 0
        rounds.append(dict(turns=turns(status), session=status['status'], gate=gate['ok'],
                           problems=gate['problems'], final_text=status['final_text']))
        if status['status'] != 'complete' or gate['ok']:
            break
        if evidence_ok:
            # The readings are in and the red is checklists only: the reviewer's question (are the probes honest)
            # has been answered; it held a worker 24 turns inside a round about seven unticked boxes.
            session.suspend_reviews('gate red on checklists only; the readings are in')
        else:
            session.resume_reviews()
        session.continue_with(red_message(gate, project, map_id, task_unknown_ids(task)),
                              label='gate red: '+('tick the walks you opened' if evidence_ok else 'repair round'))
    budget = cap
    if gate['ok']:
        # Green: the method goes into the library through the harvest — what the worker minted while it worked.
        # There is no write-up phase: a second task asking the worker to "record the method" ran 20–50 turns a
        # time and a second reviewer read it; the harvest's validators are the review. The worker is asked back
        # only when the library refused something (the validator's words), a base moved under it (a merge), or
        # it made an artifact and touched no procedure at all (one round to record what it did).
        (root/WRITEUP_MARK).write_text(str(turns(status)))
        session.suspend_reviews('gate green: the harvest is the review')
        # The reflection fires at the window's threshold, so a work order that never filled a window never had
        # the moment to record its method: the render task that distilled its own procedure did so only because
        # it ran long (2026-09-22). Green is the other moment it is owed, and the one the harvest reads.
        if not (root/REFLECTED_MARK).exists():
            from . import prompts as _prompts
            (root/REFLECTED_MARK).write_text(str(turns(status)))
            session.continue_with(_prompts.message('reflect_green_worker'), label='gate green: record the method')
            status = run_through_outages(session, config, root, maximum_worker_turns=max(1, budget-turns(status)))
            session.prune_workspaces()
        widgets = harvest_widgets(evidence(session), root, config, project)
        playbook = harvest_playbook(evidence(session), store, config,
                                    allowed=tuple(procedures_used(root)+procedures_created(root)+procedures_edited(root)),
                                    base=root/PLAYBOOK_BASE, workspace_store=workspace_procedures(config, project))
        deps = declare_artifact_deps(config, project, unknowns)
        rounds.append(dict(turns=turns(status), session=status['status'], gate='playbook',
                           final_text=status['final_text'], playbook=playbook, widgets=widgets, artifact_deps=deps))
        made = [unknown_notes(u)['creates'] for u in unknowns if unknown_notes(u).get('creates')]
        # One round to ask, when either side of the library got nothing from this task: the procedures it
        # created or improved, or the widgets it checked in. The worker decides — record the method, improve
        # the one it walked, mint the instrument, or say there is nothing general here and finish. Asked only
        # when both sides were empty, attempt 1 checked in five instruments over two tasks and no method: the
        # walk of the bootstrap procedure was the only procedure ever touched.
        unrecorded = (not (playbook['created'] or playbook['improved']) or not (widgets['checked_in'] or widgets['unchanged'])) \
            and status['status'] == 'complete' and budget-turns(status) > 0
        refused = [('widget', r) for r in widgets['rejected']]+[('procedure', r) for r in playbook['rejected']]
        conflicts = list(widgets.get('conflicts') or [])
        merges = list(playbook.get('conflicts') or [])
        # Rounds continue while each one fixes something (the count of problems falls) and turns remain; a round
        # that fixes nothing ends it — the same rule as red gate rounds.
        stuck = 0   # rounds in a row that fixed nothing; the second ends it (a validator's refusal can take two tries)
        while (refused or conflicts or merges or unrecorded) and status['status'] == 'complete' \
                and budget-turns(status) > 0:
            if conflicts:
                session.continue_with(merge_message(conflicts), label='library merge')
            elif merges:
                session.continue_with(procedure_merge_message(merges), label='library merge')
            elif refused:
                session.continue_with(refusal_message(refused), label='library repair: validator refused')
            else:
                session.continue_with(green_message(gate, task_unknown_ids(task), procedures_used(root), made=made,
                                                    procedures=sorted(set(playbook['created']+playbook['improved'])),
                                                    widgets=sorted(set(widgets['checked_in']+widgets['unchanged']))),
                                      label='gate green: the library asks')
            status = run_through_outages(session, config, root, maximum_worker_turns=budget-turns(status))
            session.prune_workspaces()
            again_w = harvest_widgets(evidence(session), root, config, project)
            again_p = harvest_playbook(evidence(session), store, config,
                                       allowed=tuple(procedures_used(root)+procedures_created(root)+procedures_edited(root)),
                                       base=root/PLAYBOOK_BASE, workspace_store=workspace_procedures(config, project))
            for key in ('checked_in', 'unchanged', 'merged'):
                widgets[key] = sorted(set(widgets.get(key) or []) | set(again_w.get(key) or []))
            widgets['rejected'] = list(again_w['rejected'])
            for key in ('installed', 'created', 'improved', 'ignored', 'merged'):
                playbook[key] = sorted(set(playbook.get(key) or []) | set(again_p.get(key) or []))
            playbook['rejected'] = list(again_p['rejected'])
            playbook['conflicts'] = list(again_p.get('conflicts') or [])
            rounds.append(dict(turns=turns(status), session=status['status'], gate='library_repair',
                               final_text=status['final_text'], playbook=again_p, widgets=again_w))
            still = [('widget', r) for r in widgets['rejected']]+[('procedure', r) for r in playbook['rejected']]
            still_conflicts = list(again_w.get('conflicts') or [])
            still_merges = list(again_p.get('conflicts') or [])
            still_unrecorded = unrecorded and (not (playbook['created'] or playbook['improved'])
                                               or not (widgets['checked_in'] or widgets['unchanged']))
            if len(still)+len(still_conflicts)+len(still_merges)+int(still_unrecorded) \
                    >= len(refused)+len(conflicts)+len(merges)+int(unrecorded):
                stuck += 1
                if stuck >= 2:
                    break   # two rounds fixed nothing: the worker has had its say
            else:
                stuck = 0
            refused, conflicts, merges, unrecorded = still, still_conflicts, still_merges, still_unrecorded
    verdict = ('complete' if gate['ok'] else 'blocked_by_worker' if blocked_reason is not None
               else 'stopped' if status['status'] == 'stopped' else 'incomplete')
    result = dict(task=task['id'], unknown=task['map_id'], unknowns=task_unknown_ids(task), map=map_id, resumed=resuming,
                  verdict=verdict,
                  blocked_reason=blocked_reason, problems=gate['problems'], knowns=gate['knowns'],
                  runs=gate['runs'],
                  turns=turns(status), turn_budget=budget, turn_estimate=estimate, overruns=overruns,
                  session=status['status'],
                  handoffs=status['handoffs'], checkins=status['controller_reviews'], held_guidance=status['held_guidance'],
                  # What the reviewer still doubted when its completion budget ran out: the reading stands, and the
                  # doubt goes to the controller as a candidate reading of its own rather than back to this worker.
                  reviewer_doubts=list(session.state.get('dropped_corrections') or []),
                  # The procedure walks this worker left open, for the controller: each is method still owed on
                  # this workspace, and a task of its own is how the route pays it over the brief
                  # rather than in one worker's window.
                  walks_open=walks_left(state_of(session.workspace())),
                  checkin_document=session.project_document(), rounds=rounds, playbook=playbook, widgets=widgets,
                  usage=ops.journal_usage(root/'events'/'session.jsonl'))
    (root/'result.json').write_text(json.dumps(result, indent=1))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True, help='A project directory (.mizpah or .terra inside)')
    parser.add_argument('--root', type=Path, required=True, help='New session directory')
    parser.add_argument('--task', help='Route task id; default is the next pickable task')
    args = parser.parse_args()
    result = run_task(load_config(args.config), args.project, args.root, args.task)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['verdict'] == 'complete' else 1)


if __name__ == '__main__':
    main()
