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


def evidence(session: Any) -> bytes:
    """The workspace as tar bytes whichever mode the shell runs in: what harvest and the checklists read."""
    snapshot = getattr(session, 'workspace_snapshot', None)
    return snapshot() if callable(snapshot) else session.workspace()


def state_of(workspace: Any) -> bytes:
    """The snapshot-managed part (all of it in snapshot mode; the state tar in bind mode): what write-back reads."""
    return workspace if isinstance(workspace, (bytes, bytearray)) else bytes(getattr(workspace, 'state', b''))
# Library procedures that are the loop's own method, not a worker's to rewrite or get credit for.
BOOTSTRAP_PROCEDURES = ('mizpah-resolve-unknown',)
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


def load_config(path: str | Path) -> dict[str, Any]:
    """Mizpah config layered over the harness config it names; paths resolve from each file."""
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    harness_path = (path.parent/config['harness_config']).resolve()
    harness = json.loads(harness_path.read_text())
    config['worker_policy'] = (path.parent/config['worker_policy_file']).read_text()
    # Scaffolding is method the host imposes; each piece is a toggle so a model that can orchestrate
    # can be run without it and compared. Verification guards are not toggles.
    scaffolding = dict(bootstrap=True, small_edits=True, checkins=True, command_tools=True) | (config.get('scaffolding') or {})
    config['scaffolding'] = scaffolding
    if not scaffolding['bootstrap']:
        config['worker_policy'] = config['worker_policy'].replace(BOOTSTRAP_WALK, FREE_METHOD, 1)
        assert FREE_METHOD in config['worker_policy'], 'worker policy no longer carries the bootstrap walk sentence'
    config['route_policy'] = (path.parent/config['route_policy_file']).read_text()
    config['eval_policy'] = (path.parent/config['eval_policy_file']).read_text()
    config['checkin_policy'] = (path.parent/config['checkin_policy_file']).read_text()  # tool-less; no v10 text
    config['playbook_store'] = str(Path(config['playbook_store']).expanduser())
    config['widget_library'] = str(Path(config['widget_library']).expanduser())
    return dict(harness, mizpah=config, harness_config_path=str(harness_path), mizpah_config_path=str(Path(path).resolve()))


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


def open_task_map(config: dict[str, Any], project: Path, task: dict[str, Any]) -> str:
    """A session map for the task with a copy of its unknown; unknowns do not read through."""
    map_id = task_map_id(task)
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
        terra(config, project, *args)
    scaffold_probes(config, project, task)
    return map_id


def scaffold_probes(config: dict[str, Any], project: Path, task: dict[str, Any]) -> list[str]:
    """One probe per unknown, created by the host before the worker starts, each declaring its single
    quantity. The worker writes measure.py for each; a probe that measures several things cannot exist."""
    made = []
    for unknown_id in task_unknown_ids(task):
        probe_id = unknown_id+'_probe'
        if (project/layout.dirname(project)/'map'/'probes'/probe_id/'probe.json').exists():
            continue
        unknown = read_unknown(project, unknown_id)
        args = ['probe', 'create', probe_id, '--purpose', unknown['claim'][:200], '--kind', 'run', '--measure', unknown_id]
        # An unknown that names knowns ("a comparison of wind_alert_count and gale_reading_count")
        # gets them as declared inputs, so the probe compares against the map through ctx["inputs"]
        # and Terra refuses a measure() that re-derives or hardcodes them instead.
        for known_id in known_ids_named(project, unknown):
            if known_id != unknown_id:
                args += ['--input', known_id+'=known:'+known_id]
        terra(config, project, *args)
        made.append(probe_id)
    return made


def protected_probes(project: Path, task: dict[str, Any]) -> tuple[str, ...]:
    """Probes that predate this task and are not its own: instruments other knowns cite, not this worker's to change.
    The task's scaffolded probes exist before the session starts, but they are exactly what the worker fills in."""
    own = {uid+'_probe' for uid in task_unknown_ids(task)}
    return tuple(sorted(p.name for p in (project/layout.dirname(project)/'map'/'probes').iterdir() if p.is_dir() and p.name not in own))


def probe_inputs(project: Path, task: dict[str, Any]) -> dict[str, list[str]]:
    """Unknown id → the knowns its scaffolded probe declares as inputs (only those that declare any)."""
    result = {}
    for uid in task_unknown_ids(task):
        meta = project/layout.dirname(project)/'map'/'probes'/(uid+'_probe')/'probe.json'
        if meta.exists():
            declared = json.loads(meta.read_text()).get('inputs') or {}
            if declared:
                result[uid] = sorted(declared)
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


def render_assignment(task: dict[str, Any], unknowns: list[dict[str, Any]], map_id: str,
                      inputs: dict[str, list[str]] | None = None, state_dirname: str = layout.STATE_DIRNAME) -> str:
    """The task, its unknowns and the map: nothing about method and nothing from the brief.
    `inputs` maps an unknown id to the knowns its probe declares; the worker reads them from ctx["inputs"]."""
    lines = ['Route task `'+task['id']+'` (bucket '+task['bucket']+': '+BUCKET_MODES.get(task['bucket'], '')+'): '+task['title'],
             'It resolves '+('one unknown' if len(unknowns) == 1 else str(len(unknowns))+' unknowns')+':']
    for unknown in unknowns:
        lines += describe_unknown(unknown)
    acceptance = [a for a in task.get('acceptance') or [] if not str(a).startswith('unknown:')]
    if acceptance:
        lines.append('Acceptance: '+'; '.join(acceptance))
    if task.get('enabler_id'):
        lines.append('This task builds the enabler `'+str(task['enabler_id'])+'`: an instrument the brief needs before its '
                     'readings can be taken, which is a widget. Search the widget library first (`cartograph search`, '
                     'several terms): an installed widget that does the job is the instrument — install it at the path the '
                     'unknown names and the unknown reads true when its validate passes. Only when nothing fits, create it, '
                     'give it tests, and it is checked in after green. The instrument is never itself a finding: the reading '
                     'is that it exists at its path and validates. An enabler is packed as a small repo from its path: leave '
                     'a README there that is its interface (what to call, with what, what comes back) — the next project '
                     'installs the directory and reads only that.')
    lines.append('Your map is `'+map_id+'` (TERRA_MAP is set): probes are shared, but the unknowns, your runs and '
                 'the knowns you graduate live there.')
    ids = [u['id'] for u in unknowns]
    lines.append('Your probes already exist, one per unknown, each measuring only its own quantity: '+
                 ', '.join('`'+state_dirname+'/map/probes/'+i+'_probe/`' for i in ids)+'. For each, write its `measure.py` '
                 '(a few lines returning {"<unknown id>": value}), validate, run. Do not create other probes. '
                 'A probe runs with the project root as its working directory: open files and run commands by '
                 'relative path; do not derive the root from `__file__` (measure.py is four levels down).')
    for uid, known_ids in (inputs or {}).items():
        lines.append('`'+uid+'_probe` declares the map knowns '+', '.join('`'+k+'`' for k in known_ids)+
                     ' as inputs: its measure() gets their values in ctx["inputs"] and compares against them.')
    lines.append('Done means `terra known adopt <known> --from '+map_id+'` succeeded for '+
                 ('it' if len(ids) == 1 else 'each of '+', '.join(ids))+' and then '
                 '`terra route complete '+task['id']+' --run <run_id>'+''.join(' --known '+i for i in ids)+'` succeeded.')
    return '\n'.join(lines)+'\n'


def unknown_notes(unknown: dict[str, Any]) -> dict[str, str]:
    """`cites need:1; source orders.csv` → {'cites': 'need:1', 'source': 'orders.csv'}."""
    result: dict[str, str] = {}
    for part in (unknown.get('notes') or '').split(';'):
        key, _, value = part.strip().partition(' ')
        if key in ('cites', 'source', 'creates') and value.strip():
            result[key] = value.strip()
    return result


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
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        for path in sorted(project.rglob('*')):
            relative = path.relative_to(project)
            if any(part in PACK_EXCLUDE for part in relative.parts) or relative.parts[0] == PLAYBOOK_PREFIX:
                continue
            if only and relative.parts[0] not in only:
                continue
            if any(tuple(relative.parts[:len(e)]) == e for e in excluded):
                continue
            if path.is_symlink() or path.is_file() or path.is_dir():
                archive.add(path, arcname=relative.as_posix(), recursive=False)
        if playbook_store is not None and playbook_store.is_dir():
            for path in sorted(playbook_store.glob('*.json')):
                archive.add(path, arcname=PLAYBOOK_PREFIX+'/playbook/procedures/'+path.name, recursive=False)
    return buffer.getvalue()


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


def harvest_playbook(snapshot: bytes, store: Path, config: dict[str, Any],
                     allowed: tuple[str, ...] | None = None) -> dict[str, list[str]]:
    """Procedures new or changed in the workspace copy; installed only if they validate.

    With `allowed`, only those ids are considered: the procedures this session followed or created.
    Anything else the worker touched stays in its workspace."""
    with _store_lock(store):
        return _harvest_playbook(snapshot, store, config, allowed)


def _harvest_playbook(snapshot: bytes, store: Path, config: dict[str, Any],
                      allowed: tuple[str, ...] | None = None) -> dict[str, list[str]]:
    installed, rejected, ignored = [], [], []
    created, improved = [], []
    prefix = PLAYBOOK_PREFIX+'/playbook/procedures/'
    with tarfile.open(fileobj=io.BytesIO(snapshot), mode='r:') as archive:
        for member in archive:
            if not (member.isfile() and member.name.startswith(prefix) and member.name.endswith('.json')):
                continue
            data = archive.extractfile(member).read()
            target = store/Path(member.name).name
            if (target.stem in BOOTSTRAP_PROCEDURES or any(target.stem.startswith(b+'-') or target.stem.startswith(b+'_')
                                                            for b in BOOTSTRAP_PROCEDURES)
                    or (allowed is not None and target.stem not in allowed)):
                if not target.exists() or target.read_bytes() != data:
                    ignored.append(target.stem)
                continue
            if target.exists() and target.read_bytes() == data:
                continue
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
            else:
                rejected.append(target.stem+': '+(check.stderr or check.stdout).strip()[:300])
                if backup is None:
                    target.unlink()
                else:
                    target.write_bytes(backup)
    return dict(installed=installed, rejected=rejected, ignored=ignored, created=created, improved=improved)


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


def harvest_widgets(snapshot: bytes, root: Path, config: dict[str, Any]) -> dict[str, list[str]]:
    with _store_lock(Path(config['mizpah']['widget_library'])):
        return _harvest_widgets(snapshot, root, config)


def _harvest_widgets(snapshot: bytes, root: Path, config: dict[str, Any]) -> dict[str, list[str]]:
    """Widgets the session created or changed: validated and checked into the local library after green.

    Never published. A widget the library already holds at identical source is left alone.
    """
    import shutil
    import subprocess as sp
    import tempfile
    result: dict[str, list[str]] = dict(checked_in=[], rejected=[], unchanged=[])
    files = _members(snapshot)
    library = Path(config['mizpah']['widget_library'])
    for directory in widgets_touched(root):
        prefix = 'cg/'+directory+'/'
        members = {name: data for name, data in files.items() if name.startswith(prefix)
                   and '/.venv/' not in name and '__pycache__' not in name}
        if prefix+'widget.json' not in members:
            continue
        try:
            widget_id = json.loads(members[prefix+'widget.json'])['meta']['id']
        except (ValueError, KeyError, TypeError):
            result['rejected'].append(directory+': widget.json has no meta.id')
            continue
        shipped = library/widget_id
        if shipped.is_dir():
            same = all((shipped/name[len(prefix):]).exists() and (shipped/name[len(prefix):]).read_bytes() == data
                       for name, data in members.items() if not name.endswith('changelog.json'))
            if same:
                result['unchanged'].append(widget_id)
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


NESTING_LIMIT = 3   # walks open at once: the third says the unknown is several unknowns


def open_checklists(snapshot: bytes) -> list[str]:
    """Every procedure the worker opened is a commitment: each step ticked `[x]` (done) or `[-]` (not needed)
    before the gate can be green. Checklists live under .playbook/open/ in the workspace, one per walk.

    Depth is bounded at the map, not the window: with NESTING_LIMIT walks open at once, the task is told that
    the unknown it holds is several unknowns and to block naming the readings the inner walks would produce;
    the eval mints them, each one procedure deep, and this task resumes with them as inputs."""
    problems: list[str] = []
    walks = open_walks(snapshot)
    if len(walks) >= NESTING_LIMIT:
        problems.append(str(len(walks))+' procedure walks are open at once ('+'; '.join(w.split(':')[0].rsplit('/', 1)[-1] for w in walks)
                        +'): a walk nested this deep means the unknown is several unknowns. Finish the innermost if it is '
                        'one or two steps from done; otherwise `terra route block` this task naming the readings the inner '
                        'walks would produce as unknowns of their own — the route mints them, they are measured one '
                        'procedure deep, and this task resumes with them on the map')
    for name, data in sorted(_members(snapshot).items()):
        if not name.startswith(PLAYBOOK_PREFIX+'/open/') or not name.endswith('.md'):
            continue
        text = data.decode('utf-8', errors='replace')
        open_steps = [ln.strip()[6:].strip('* ') for ln in text.splitlines() if ln.strip().startswith('- [ ]')]
        if open_steps:
            problems.append('checklist '+name+' has '+str(len(open_steps))+' unticked step(s): '+
                            '; '.join(o[:60] for o in open_steps[:4])+(' …' if len(open_steps) > 4 else '')+
                            ' — tick each `[x]` (done) or `[-]` (not needed)')
    return problems


def checklist_skips(snapshot: bytes) -> dict[str, list[str]]:
    """Steps a worker ticked `[-]` (not needed) per opened procedure: the signal the green phase hands back,
    so the worker decides whether each was not needed on this walk or not needed in general."""
    skips: dict[str, list[str]] = {}
    for name, data in sorted(_members(snapshot).items()):
        if not name.startswith(PLAYBOOK_PREFIX+'/open/') or not name.endswith('.md'):
            continue
        procedure = name.split('/')[-1].split('--')[0].removesuffix('.md')
        for ln in data.decode('utf-8', errors='replace').splitlines():
            if ln.strip().startswith('- [-]'):
                skips.setdefault(procedure, []).append(ln.strip()[6:].strip('* '))
    return skips


def task_gate(config: dict[str, Any], project: Path, task: dict[str, Any], map_id: str,
              root: Path | None = None) -> dict[str, Any]:
    """Mechanical verdict: the route, the project map, Terra's gate, and every widget the task touched."""
    problems = []
    if root is not None:
        problems += widget_problems(config, project, root)
    route = json.loads((project/layout.dirname(project)/'route.json').read_text())
    entry = next((t for t in route['tasks'] if t['id'] == task['id']), None)
    if entry is None:
        return dict(ok=False, problems=['task vanished from the route'], knowns=[], runs=[])
    if entry['status'] != 'done':
        problems.append('route task '+task['id']+' is '+entry['status']+', not done')
    evidence = entry.get('evidence') or []
    last = evidence[-1] if evidence else {}
    runs, knowns = list(last.get('runs') or []), list(last.get('knowns') or [])
    if last.get('freehand'):
        problems.append('task completed freehand ('+last['freehand']+'); freehand is not evidence')
    if last.get('skip_gate'):
        problems.append('gate override recorded ('+str(last['skip_gate'])+'); overrides are not evidence')
    if entry['status'] == 'done' and not runs and not knowns:
        problems.append('completion cites no run or known')
    unknown_ids = task_unknown_ids(task)
    for unknown_id in unknown_ids:
        if unknown_id not in knowns and entry['status'] == 'done':
            problems.append('completion does not cite known '+unknown_id)
    for known_id in dict.fromkeys(knowns+unknown_ids):
        local = read_known(project, known_id, map_id)
        adopted = read_known(project, known_id)
        if local is None:
            problems.append('known '+known_id+' has not been graduated on map '+map_id+': once its measure.py '
                            'validates, `terra known ladder '+known_id+'` runs, links, graduates, promotes and adopts it')
            continue
        if adopted is None or (adopted.get('adopted_from') or {}).get('map') != map_id:
            derived = local.get('confidence_derived') or 'low'
            n = (local.get('stats') or {}).get('n') or 0
            if CONFIDENCE_RANK.get(local.get('confidence') or 'low', 0) >= CONFIDENCE_RANK['med']:
                problems.append('known '+known_id+' is ready on '+map_id+' (confidence '+str(local.get('confidence'))+
                                ', n='+str(n)+') but not yet on the project map: run `terra known adopt '+known_id+
                                ' --from '+map_id+'`')
            else:
                problems.append('known '+known_id+' is on '+map_id+' (n='+str(n)+', confidence '+derived+
                                ') below the adoption bar: `terra known ladder '+known_id+'` takes the remaining '
                                'readings, promotes and adopts in one call')
            continue
        if CONFIDENCE_RANK.get(adopted.get('confidence') or 'low', 0) < CONFIDENCE_RANK['med']:
            problems.append('adopted known '+known_id+' is confidence '+str(adopted.get('confidence')))
    for unknown_id in unknown_ids:
        project_unknown = read_unknown(project, unknown_id)
        if project_unknown.get('status') != 'resolved':
            problems.append('project unknown '+unknown_id+' is '+str(project_unknown.get('status')))
    problems += vacuous_truth_problems(project, unknown_ids)
    problems += duplicate_reading_problems(project, unknown_ids)
    resynced = readopt_retaken(config, project, map_id, unknown_ids)
    if resynced:
        (root/'resynced.jsonl').open('a').write(json.dumps(dict(at=time.time(), readopted=resynced))+'\n') if root else None
    problems += artifact_agreement_problems(project, unknown_ids)
    problems += unread_input_problems(project, unknown_ids)
    gate = terra(config, project, 'gate')
    own_ids = set(knowns) | set(runs) | set(unknown_ids)
    for violation in gate.get('violations') or []:
        if violation.get('map_id') == map_id or violation.get('id') in own_ids:
            line = 'terra gate: '+str(violation.get('why') or violation.get('kind'))
            if line not in problems:  # the same unknown is open on both the task map and the project map
                problems.append(line)
    return dict(ok=not problems, problems=problems, knowns=knowns, runs=runs,
                foreign_violations=[v for v in gate.get('violations') or []
                                    if v.get('map_id') != map_id and v.get('id') not in own_ids])


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
        refused_paths=tuple(sandbox.get('refused_paths') or ()), refused_patterns=REFUSED_PATTERNS) | bound)))
    try:
        # Bind mode: the evidence tree without the caches; the caches are bound read-only by the detached run.
        workspace = pack_workspace(project, exclude=cache_dirs(config)) if bind_mode(config) else pack_workspace(project)
        for known_id in known_ids:
            known = read_known(project, known_id)
            if known is None:
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
        refused_paths=tuple(sandbox.get('refused_paths') or ()), refused_patterns=REFUSED_PATTERNS) | bound)))
    state_dir = layout.dirname(project)
    try:
        workspace = pack_workspace(project, exclude=cache_dirs(config)) if bind_mode(config) else pack_workspace(project)
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
    lines = ['Gate red. Missing:']+['- '+p for p in gate['problems']]
    if keep:
        lines.append('Keep: '+'; '.join(dict.fromkeys(keep))+'. Add what is missing; do not start over.')
    return '\n'.join(lines)+'\n'


def session_calls(root: Path) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(tool, arguments, result) for every worker call in the session journal."""
    calls = []
    journal = root/'events'/'session.jsonl'
    if not journal.exists():
        return calls
    for line in journal.read_text().splitlines():
        event = json.loads(line)
        if event.get('event_type') != 'worker_turn':
            continue
        results = {r['call_id']: r['result'] for r in event['payload'].get('tool_results') or []}
        for call in event['payload']['response'].get('tool_calls') or []:
            arguments = call['function']['arguments']
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
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


def tool_fight(root: Path) -> dict[str, Any]:
    """What the worker paid in refused or failed calls, grouped by the procedure step it was on."""
    step = '(no procedure step)'
    cost: dict[str, dict[str, int]] = {}
    for name, args, result in session_calls(root):
        command = args.get('command') or '' if name == 'bash' else ''
        if command.startswith('playbook start ') and result.get('exit_code') == 0:
            title = command.split('--title', 1)[1].strip().strip('"\'') if '--title' in command else command.split()[2]
            step = title[:60]
            continue
        failed = result.get('status') in ('rejected', 'error') or (name == 'bash' and result.get('exit_code') not in (0, None))
        if failed:
            bucket = cost.setdefault(step, {})
            key = name if name != 'bash' else ('bash '+command.split()[0] if command.split() else 'bash')
            bucket[key] = bucket.get(key, 0)+1
    return cost


def effort_message(task: dict[str, Any], estimate: int, turns: int, overruns: int) -> str:
    """The estimate is spent; the worker, not the host, judges whether to continue."""
    return ('Effort check: this task was bucketed '+task['bucket']+' ('+BUCKET_MODES.get(task['bucket'], '')+') and you have used '+str(turns)+' turns, '
            +('past' if overruns == 1 else str(overruns)+'× past')+' that estimate. Nothing has been decided for you. '
            'Judge your own effort honestly: if the readings are within reach with the approach you are on, continue; '
            'if the approach is not working, change it; if the source cannot be read as the unknown asks, '
            '`terra route block '+task['id']+' --reason "..."` and stop. Do not pad or fake. Reply by acting.\n')


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


def uncovered_by_procedures(config: dict[str, Any], used: list[str], unknowns: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Per followed procedure, the task's unknowns none of its steps mention: the pressure a wider brief puts on a method.

    An unknown counts as covered when two stems of its id or claim occur in the procedure's title, description
    or steps. The eval mints unknowns a procedure never anticipated; each one it leaves uncovered is a step the
    worker took without the procedure's help — and the step the next worker should find written down."""
    import subprocess as sp
    out: dict[str, list[str]] = {}
    for procedure_id in used:
        try:
            text = sp.run([config['mizpah']['playbook'], 'load', procedure_id], capture_output=True, text=True, timeout=30).stdout
            doc = json.loads(text[text.find('{'):])
        except (ValueError, OSError, sp.SubprocessError):
            continue
        body = ' '.join([str(doc.get('title') or ''), str(doc.get('description') or '')]
                        +[str(st.get('title') or '')+' '+str(st.get('do') or '') for st in doc.get('steps') or []]).lower().replace('-', ' ')
        missing = []
        for u in unknowns:
            words = {w for w in re.findall(r'[a-z0-9]{4,}', (str(u.get('id') or '')+' '+str(u.get('claim') or '')).lower().replace('_', ' '))
                     if w not in STOP}
            if shared_stems(words, body) < 2:
                missing.append(str(u.get('id')))
        if missing:
            out[procedure_id] = missing
    return out


def refusal_message(refused: list[tuple[str, str]]) -> str:
    """The library's reasons for refusing what the worker built, and the one chance to fix them."""
    lines = ['The gate is green and your work is done; one thing remains. The library refused what you built, for these '
             'reasons — fix them and it is checked in; leave them and the work stays only in this project:']
    for kind, reason in refused:
        lines.append('- '+kind+' '+reason)
    lines.append('A widget must validate (`cartograph validate cg/<dir>`): tests under tests/ that pass, no project names or '
                 'paths in src/, every dependency declared. A procedure must validate (`playbook validate <id>`). '
                 'Fix, validate, then reply that you are done; do not start other work.')
    return '\n'.join(lines)


def green_message(gate: dict[str, Any], unknown_id: str | list[str], used: list[str] = (), cost: dict[str, Any] | None = None,
                  skips: dict[str, list[str]] | None = None, uncovered: dict[str, list[str]] | None = None) -> str:
    if isinstance(unknown_id, list):
        unknown_id = ', '.join(unknown_id)
    paid = ''
    if uncovered:
        rows = ['  - '+proc+': '+', '.join(ids) for proc, ids in uncovered.items()]
        paid += (' Unknowns this task resolved that the procedure you followed has no step for:\n'+'\n'.join(rows)+
                 '\n For each, `playbook add-step` the step you actually took (the reading, the widget, the exact command) '
                 'where it belongs in the walk; a procedure grows by the unknowns that stretched it.')
    if skips:
        rows = ['  - '+proc+': '+'; '.join(steps) for proc, steps in skips.items()]
        paid += (' Steps you marked `[-]` not needed:\n'+'\n'.join(rows)+
                 '\n For each, decide: not needed on this walk (leave the procedure alone) or not needed in general '
                 '(`playbook edit-step` to narrow it, or remove it). A step every walk skips is noise for the next worker.')
    if cost:
        rows = ['  - while on '+repr(step)+': '+', '.join(f'{n}× {k}' for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
                for step, counts in cost.items()]
        paid = (' Calls that were refused or failed, by the step you were on:\n'+'\n'.join(rows)+
                '\n Where a step led you into those, the step is what needs rewriting.')
    linking = ('The procedure lives in the store and changes only through `playbook edit-step` / `add-step` / `remove-step`; the '
               'checklist under `.playbook/open/` is a rendered copy for ticking — rewriting its step text changes nothing. '
               'Procedures compose by linking, and a link is the first-class way to reuse one: a step that says "now walk '
               'procedure X" is written `playbook add-step <id> --title ... --do "<why here>" --procedure <X>`, and open '
               'renders it as the command to open X. Never copy another procedure\'s steps into yours — link the step to '
               'it. Search before you write (`playbook search`, a create is refused without one): where a procedure '
               'already covers part of what you did, your procedure links it for that part and adds only what was new.')
    if used:
        library = ('You followed '+', '.join('`'+u+'`' for u in used)+'. Improve that procedure with `playbook edit-step` '
                   'or `playbook add-step` where its steps fell short of what you actually had to do; where a part of it '
                   'is really another procedure (one that exists, or one you now create for that part), make that step a '
                   'link with `--procedure`. Create a new procedure only if your method was genuinely different, not a '
                   'rewording. '+linking)
    else:
        library = ('You followed only the loop\'s own bootstrap (`mizpah-resolve-unknown`), which is not yours to copy or '
                   'rewrite. If your method was specific to this kind of source or artifact (what you read, which widget, '
                   'how the reading was taken), `playbook create <id> --title ... --description ... --tags ...` a '
                   'procedure for that and `playbook add-step` one step at a time, each step one action with the exact '
                   'commands; if it was nothing but the bootstrap, create nothing and reply "none". '+linking)
    return ('Gate green: known '+unknown_id+' '+('are' if ',' in unknown_id else 'is')+' on the project map. The widgets '
            'your probes call under cg/ are checked in for you once `cartograph validate` passes; do not build or '
            'extract anything now — the reading is taken and the parts it needed already exist. One thing to record, so '
            'the next worker starts where you finished: the method. Record what you actually followed so the next worker '
            'finds it with `playbook search`, naming the widgets it should install. '+library+paid+
            ' Then `playbook validate <id>` and reply with the procedure id and nothing else.\n')


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
                                                 refused_patterns=REFUSED_PATTERNS, network=network) | bound))
    return worker, checkin, SandboxedShell(shell)


def checkin_settings(config: dict[str, Any]) -> ControllerSettings:
    """The v10 review contract (cadence, budgets, document edits) with the Mizpah check-in policy."""
    c = config['controller']
    return ControllerSettings(config['mizpah']['checkin_policy'], c['generation'], c['context_capacity'],
        config['mizpah'].get('checkin_output_tokens', 2048),
        c['maximum_model_calls'], c['maximum_tool_calls'], c['maximum_tool_output_characters'],
        c.get('output_headroom_tokens', 1), c.get('input_target_tokens'), c.get('recent_review_exchanges', 2),
        c.get('investigation_budgets'), c.get('maximum_document_edits_per_review'),
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
    """Probes, widget sources, and every artifact this task's unknowns say it creates."""
    globs = [d+'/map/probes/*/probe.py' for d in (layout.STATE_DIRNAME, layout.LEGACY_DIRNAME)] \
        + [d+'/map/probes/*/measure.py' for d in (layout.STATE_DIRNAME, layout.LEGACY_DIRNAME)] + ['cg/*/src/*.py']
    for unknown in unknowns:
        creates = unknown_notes(unknown).get('creates')
        if creates:
            globs.append(creates)
            globs.append(creates.rstrip('/')+'/*')
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
         'run id and every known id. Refused while a known is missing.',
         command='terra route complete {task} --run {run} {knowns}',
         parameters=dict(type='object', properties=dict(task=string('task id'), run=string('a run id of this task'),
                                                        knowns=dict(type='array', items=dict(type='string'), flag='--known',
                                                                    description='every known id the task carries')),
                         required=['task', 'run', 'knowns'])),
    dict(name='terra_route_block', description='The honest exit: the source cannot be read as the unknown asks, or '
         'the question is not answerable from this workspace. Say what you needed and could not read; then stop.',
         command='terra route block {task} --reason {reason}',
         parameters=dict(type='object', properties=dict(task=string('task id'), reason=string('what you needed and could not read')),
                         required=['task', 'reason'])),
    dict(name='playbook_open', description='Write a whole procedure as a checklist to .playbook/open/<id>--<for>.md '
         'in the workspace; read that file once, follow it in order, tick steps off. One copy per walk: say what '
         'this walk is for. Use for the bootstrap and for any domain procedure a search finds.',
         command='playbook open {id} --for {purpose}',
         parameters=dict(type='object', properties=dict(id=string('procedure id from search'),
                                                        purpose=string('what this walk is for: the unknown(s), artifact or source')),
                         required=['id', 'purpose'])),
    dict(name='playbook_create', description='Create a new procedure (after the gate is green, when your method was '
         'specific to this kind of source or artifact and no existing procedure captures it). Then add its steps one '
         'at a time with playbook_add_step, each one action with the exact commands, and `playbook validate <id>`.',
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
    (r'(>>?|\btee\b|-i)\s*[^|;&]*\.(terra|mizpah)/(brief|route)\.json', 'the brief moves by proposal and the route by terra route; neither is a file to write'),
    (r'(>>?|\btee\b|-i)\s*[^|;&]*\.(terra|mizpah)/map/(knowns|runs|unknowns)/', 'knowns, runs and unknowns are born by terra commands, never by writing their files'),
    (r'(>>?|\btee\b|-i|\bmv\b|\bcp\b)\s*[^|;&]*\.playbook/playbook/procedures/', 'the store is not a file to write: improving a procedure is `playbook edit-step` / `add-step`'),
    (r'\bsystemctl\b|\bsystemd-run\b|\bloginctl\b', 'the host\'s service manager is outside the sandbox; services start with `svc start`'),
    (r'\bcurl\b[^|;&]*(/stop\b|/shutdown\b|/slots\b)', 'the model server is not yours to signal'),
)


def build_settings(config: dict[str, Any], assignment: str, reference: str,
                   unknowns: list[dict[str, Any]] = ()) -> SessionSettings:
    # The check-in controller reviews on the v10 cadence against the task reference; routing and
    # project eval are separate steps in controller.py and never enter the session.
    # Check-ins are scaffolding: ~115 reviews across two runs issued no correction and held on the two
    # fabrications the structure later caught. With Terra constraining the trajectory they are a toggle.
    checkins = config['mizpah']['scaffolding']['checkins']
    return SessionSettings(assignment, config['mizpah']['worker_policy'], reference if checkins else None,
        config['worker']['generation'], SessionPolicy(**config['session_policy']), ReviewPolicy(**config['review_policy']),
        checkin_settings(config) if checkins else None,
        config['review_on_completion'], config['guidance_prefix'],
        maximum_generation_retries=config.get('maximum_generation_retries', 0),
        write_existing_files=not config['mizpah']['scaffolding']['small_edits'], edit_requires_read=config['mizpah']['scaffolding']['small_edits'],
        command_tools=COMMAND_TOOLS if config['mizpah']['scaffolding'].get('command_tools', True) else (),
        repeated_failure_rollover=config['mizpah'].get('repeated_failure_rollover'),
        repeated_success_rollover=config['mizpah'].get('repeated_success_rollover'),
        review_focus_globs=focus_globs(list(unknowns)), review_focus_characters=12000,
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

    def stop_requested() -> bool:
        return any(p.exists() for p in stop_files)

    while True:
        try:
            return session.run(maximum_worker_turns=maximum_worker_turns, stop_when=stop_requested)
        except RejectedGeneration:
            raise
        except ModelTransportError as error:
            # The wait starts at the outage, not at entry: one call to run() spans a whole burst of turns.
            outages += 1
            from . import ops
            ops.record_outage(root, 'worker', config['worker'], error, outages)
            if outages > 5:
                raise
            checker = health or ops.Health(config, root)
            if not checker.wait_for_model((config['worker'].get('endpoint') or {}).get('base_url'), wait_seconds=wait_seconds):
                raise
            discarded = session.discard_pending()
            if discarded:
                (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')


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


def _run_task(config: dict[str, Any], project: Path, root: Path, task_id: str | None, holder: dict[str, Any]) -> dict[str, Any]:
    """Pick (or resume), open the task map, run until green or the backstop, harvest the playbook, report.

    A root that already holds a session is resumed: the task was re-bucketed after its worker
    ran out of budget, and the same session continues from where it paused on the new budget.
    """
    project, root = project.resolve(), root.resolve()
    settings = config['mizpah']
    store = Path(settings['playbook_store'])
    resuming = (root/'state.sqlite3').exists()
    if resuming:
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
        if discarded:
            (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')
        # A resumed worker is told where it stood, not left to its compacted memory of it: every procedure it
        # opened and did not finish, with the next unticked step, so it picks the walk up rather than starting
        # another one (or forgetting the first — the gate would hold the task on it either way).
        left_open = open_walks(evidence(session))
        if left_open:
            session.continue_with('Resuming this task after a pause. You left these procedure walks open; continue each '
                                  'from its next step (tick `[x]` done or `[-]` not needed as you go) before anything else:\n'
                                  +'\n'.join('  - '+w for w in left_open)+'\n')
    else:
        task = pick_task(config, project, task_id)
        map_id = open_task_map(config, project, task)
        unknowns = [read_unknown(project, uid, map_id) for uid in task_unknown_ids(task)]
        assignment = render_assignment(task, unknowns, map_id, probe_inputs(project, task), layout.dirname(project))
        parts = library_parts(config, project, unknowns)
        if parts:
            assignment += ('The library already has parts near this work; install and extend one where it nearly fits '
                           'rather than creating a near-duplicate:\n'+'\n'.join('  - '+p for p in parts)+'\n')
        methods = procedure_parts(config, task, unknowns)
        if methods:
            assignment += ('The playbook already has methods near this work; search for them, open the best with '
                           '`playbook open <id> --for ...` and follow it before working the method out yourself:\n'
                           +'\n'.join('  - '+m for m in methods)+'\n')
        from . import bases
        assignment += bases.enabler_text(config)   # the gym's environment base, when it has one
        worker_client, checkin, shell = bindings(config, root, map_id, project=project)
        holder['shell'] = shell
        reference = render_reference(project, task, unknowns)
        # Bind mode: only the state directories are packed in; the tree is the project directory itself.
        initial = pack_workspace(project, store, only=state_dirs(project)) if bind_mode(config) else pack_workspace(project, store)
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
    rounds: list[dict[str, Any]] = []
    gate: dict[str, Any] = dict(ok=False, problems=['not run'], knowns=[], runs=[])
    playbook: dict[str, list[str]] = dict(installed=[], rejected=[], ignored=[])
    widgets: dict[str, list[str]] = dict(checked_in=[], rejected=[], unchanged=[])
    status = session.status()
    blocked_reason = None
    overruns = 0
    stalled = 0
    while stalled < settings['gate_rounds']:
        remaining = cap-status['completed_worker_turns']
        if remaining <= 0:
            break
        # Run to the next estimate boundary; the worker judges its own effort there.
        boundary = min(remaining, max(1, estimate*(overruns+1)-status['completed_worker_turns']))
        status = run_through_outages(session, config, root, maximum_worker_turns=boundary)
        written = writeback(state_of(session.workspace()), project, task, map_id, probes_before)
        refused = [w for w in written if w.startswith('refused:')]
        if refused:
            (root/'writeback.jsonl').open('a').write(json.dumps(dict(turns=status['completed_worker_turns'], refused=refused))+'\n')
        session.prune_workspaces()
        blocked_reason = worker_blocked(project, task)
        if blocked_reason is not None:
            # The honest exit: the worker says the source cannot be read as asked. That is state
            # for the controller (a proposal, usually), not a red round for the worker.
            rounds.append(dict(turns=status['completed_worker_turns'], session=status['status'], gate='blocked',
                               blocked_reason=blocked_reason, final_text=status['final_text']))
            break
        if status['status'] == 'stopped':
            # The operator's STOP file: the session paused at a turn boundary and reopens where it is; the task
            # stays in_progress on the route so the next run resumes it.
            rounds.append(dict(turns=status['completed_worker_turns'], session='stopped', gate='stopped'))
            break
        if status['status'] == 'paused':
            overruns += 1
            rounds.append(dict(turns=status['completed_worker_turns'], session=status['status'], gate='effort',
                               overruns=overruns))
            if status['completed_worker_turns'] >= cap:
                break
            session.interject(effort_message(task, estimate, status['completed_worker_turns'], overruns))
            continue
        gate = task_gate(config, project, task, map_id, root)
        if gate['ok'] and config['mizpah'].get('remeasure', True):
            # Green on the worker's evidence is not yet green: the host takes the readings itself.
            checked = remeasure(config, project, root, [k for k in gate['knowns'] if k in task_unknown_ids(task)] or task_unknown_ids(task))
            if checked:
                gate = dict(gate, ok=False, problems=checked)
        unticked = open_checklists(evidence(session))
        if unticked:
            gate = dict(gate, ok=False, problems=gate['problems']+unticked)
        previous = rounds[-1].get('problems') if rounds else None
        # A red round that changed nothing counts against gate_rounds; a red round with fewer
        # problems is progress and costs nothing. The worker keeps deciding.
        stalled = stalled+1 if previous is not None and len(gate['problems']) >= len(previous) else 0
        rounds.append(dict(turns=status['completed_worker_turns'], session=status['status'], gate=gate['ok'],
                           problems=gate['problems'], final_text=status['final_text']))
        if status['status'] != 'complete' or gate['ok']:
            break
        session.continue_with(red_message(gate, project, map_id, task_unknown_ids(task)))
    budget = cap
    if gate['ok'] and budget-status['completed_worker_turns'] > 0:
        # Only after green: the method goes into the library, and only through the harvest.
        followed = procedures_used(root)
        session.continue_with(green_message(gate, task_unknown_ids(task), followed, tool_fight(root),
                                            checklist_skips(evidence(session)),
                                            uncovered_by_procedures(config, followed, unknowns)))
        status = run_through_outages(session, config, root, maximum_worker_turns=budget-status['completed_worker_turns'])
        session.prune_workspaces()
        widgets = harvest_widgets(evidence(session), root, config)
        playbook = harvest_playbook(evidence(session), store, config,
                                    allowed=tuple(procedures_used(root)+procedures_created(root)))
        deps = declare_artifact_deps(config, project, unknowns)
        rounds.append(dict(turns=status['completed_worker_turns'], session=status['status'], gate='playbook',
                           final_text=status['final_text'], playbook=playbook, widgets=widgets, artifact_deps=deps))
        # One repair round: the library refused something the worker built, for reasons it can act on (a
        # missing test, a project name in src/, a hardcoded path). Without this the worker never saw the
        # validator's words and real work did not compound.
        # Repair rounds continue while each one fixes something (the refused count falls) and turns remain; a
        # round that fixes nothing ends it — the same rule as red gate rounds. Fix one of two, and you get
        # another go at the other.
        refused = [('widget', r) for r in widgets['rejected']]+[('procedure', r) for r in playbook['rejected']]
        while refused and status['status'] == 'complete' and budget-status['completed_worker_turns'] > 0:
            session.continue_with(refusal_message(refused))
            status = run_through_outages(session, config, root, maximum_worker_turns=budget-status['completed_worker_turns'])
            session.prune_workspaces()
            again_w = harvest_widgets(evidence(session), root, config)
            again_p = harvest_playbook(evidence(session), store, config,
                                       allowed=tuple(procedures_used(root)+procedures_created(root)))
            for key in ('checked_in', 'unchanged'):
                widgets[key] = sorted(set(widgets[key]) | set(again_w[key]))
            widgets['rejected'] = list(again_w['rejected'])
            for key in ('installed', 'created', 'improved', 'ignored'):
                playbook[key] = sorted(set(playbook.get(key) or []) | set(again_p.get(key) or []))
            playbook['rejected'] = list(again_p['rejected'])
            rounds.append(dict(turns=status['completed_worker_turns'], session=status['status'], gate='library_repair',
                               final_text=status['final_text'], playbook=again_p, widgets=again_w))
            still = [('widget', r) for r in widgets['rejected']]+[('procedure', r) for r in playbook['rejected']]
            if len(still) >= len(refused):
                break   # nothing fixed this round: the worker has had its say
            refused = still
    verdict = ('complete' if gate['ok'] else 'blocked_by_worker' if blocked_reason is not None
               else 'stopped' if status['status'] == 'stopped' else 'incomplete')
    result = dict(task=task['id'], unknown=task['map_id'], unknowns=task_unknown_ids(task), map=map_id, resumed=resuming,
                  verdict=verdict,
                  blocked_reason=blocked_reason, problems=gate['problems'], knowns=gate['knowns'],
                  runs=gate['runs'], foreign_violations=gate.get('foreign_violations', []),
                  turns=status['completed_worker_turns'], turn_budget=budget, turn_estimate=estimate, overruns=overruns,
                  session=status['status'],
                  handoffs=status['handoffs'], checkins=status['controller_reviews'], held_guidance=status['held_guidance'],
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
