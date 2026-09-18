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
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from typing import Any

from cg.bp_focused_agent_session_python.src import (
    ControllerSettings, EndpointConfig, FocusedSession, ModelClient, ReviewPolicy, SandboxedShell, SessionPolicy,
    SessionSettings, ShellConfig, ShellLimits, llama_model_client,
)

# Workspace paths that never go back into the project: the harness's own, and the
# playbook copy, which is harvested separately and only after green.
PLAYBOOK_PREFIX = '.playbook'
WRITEBACK_EXCLUDE = ('.tool-output/', '.session-history', PLAYBOOK_PREFIX+'/')
# Project paths that never enter the worker's workspace.
PACK_EXCLUDE = ('.git', '.venv', '__pycache__')
# Library procedures that are the loop's own method, not a worker's to rewrite or get credit for.
BOOTSTRAP_PROCEDURES = ('mizpah-resolve-unknown',)
# A bucket is the mode of work, not just its price (points 3 / 8 / 21 on the route).
BUCKET_MODES = {'low': 'implement, the path is known', 'medium': 'validate, weigh a couple of options then conclude',
                'high': 'explore, several options in parallel before choosing'}
# The forced bootstrap walk (scaffolding); with it off, the worker is told where method and parts live and left to it.
BOOTSTRAP_WALK = ('Your first act on every task is `playbook open mizpah-resolve-unknown`: it writes the whole method to '
                  '`.playbook/open/mizpah-resolve-unknown.md`. Read that file once and follow it in order; it tells you '
                  'where domain procedures and Cartograph parts come in. Open a domain procedure the same way; '
                  '`playbook start <id> --step N` re-reads one step if you need it.')
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
    return dict(harness, mizpah=config, harness_config_path=str(harness_path))


def terra(config: dict[str, Any], project: Path, *args: str) -> dict[str, Any]:
    """Run one JSON-printing terra command against the project and return its data."""
    process = subprocess.run([config['mizpah']['terra'], *args], cwd=project, capture_output=True, text=True)
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
    base = project/'.terra'/'map' if map_id in (None, 'global') else project/'.terra'/'map'/'sessions'/map_id
    return json.loads((base/'unknowns'/(unknown_id+'.json')).read_text())


def read_known(project: Path, known_id: str, map_id: str | None = None) -> dict[str, Any] | None:
    base = project/'.terra'/'map' if map_id in (None, 'global') else project/'.terra'/'map'/'sessions'/map_id
    path = base/'knowns'/(known_id+'.json')
    return json.loads(path.read_text()) if path.exists() else None


def open_task_map(config: dict[str, Any], project: Path, task: dict[str, Any]) -> str:
    """A session map for the task with a copy of its unknown; unknowns do not read through."""
    map_id = task_map_id(task)
    if not (project/'.terra'/'map'/'sessions'/map_id).exists():
        terra(config, project, 'map', 'create', map_id, '--purpose', 'route task '+task['id'], '--parent', 'global')
    for unknown_id in task_unknown_ids(task):
        unknown = read_unknown(project, unknown_id)
        if (project/'.terra'/'map'/'sessions'/map_id/'unknowns'/(unknown['id']+'.json')).exists():
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
        if (project/'.terra'/'map'/'probes'/probe_id/'probe.json').exists():
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
    return tuple(sorted(p.name for p in (project/'.terra'/'map'/'probes').iterdir() if p.is_dir() and p.name not in own))


def probe_inputs(project: Path, task: dict[str, Any]) -> dict[str, list[str]]:
    """Unknown id → the knowns its scaffolded probe declares as inputs (only those that declare any)."""
    result = {}
    for uid in task_unknown_ids(task):
        meta = project/'.terra'/'map'/'probes'/(uid+'_probe')/'probe.json'
        if meta.exists():
            declared = json.loads(meta.read_text()).get('inputs') or {}
            if declared:
                result[uid] = sorted(declared)
    return result


def known_ids_named(project: Path, unknown: dict[str, Any]) -> list[str]:
    """Known ids that appear as words in the unknown's claim, evidence or notes, in order of first mention."""
    text = ' '.join(str(unknown.get(k) or '') for k in ('claim', 'evidence_needed', 'notes'))
    return [word for word in dict.fromkeys(re.findall(r'[a-z][a-z0-9_]*', text))
            if '_' in word and read_known(project, word) is not None]


def render_assignment(task: dict[str, Any], unknowns: list[dict[str, Any]], map_id: str,
                      inputs: dict[str, list[str]] | None = None) -> str:
    """The task, its unknowns and the map: nothing about method and nothing from the brief.
    `inputs` maps an unknown id to the knowns its probe declares; the worker reads them from ctx["inputs"]."""
    lines = ['Route task `'+task['id']+'` (bucket '+task['bucket']+': '+BUCKET_MODES.get(task['bucket'], '')+'): '+task['title'],
             'It resolves '+('one unknown' if len(unknowns) == 1 else str(len(unknowns))+' unknowns')+':']
    for unknown in unknowns:
        lines += describe_unknown(unknown)
    acceptance = [a for a in task.get('acceptance') or [] if not str(a).startswith('unknown:')]
    if acceptance:
        lines.append('Acceptance: '+'; '.join(acceptance))
    lines.append('Your map is `'+map_id+'` (TERRA_MAP is set): probes are shared, but the unknowns, your runs and '
                 'the knowns you graduate live there.')
    ids = [u['id'] for u in unknowns]
    lines.append('Your probes already exist, one per unknown, each measuring only its own quantity: '+
                 ', '.join('`.terra/map/probes/'+i+'_probe/`' for i in ids)+'. For each, write its `measure.py` '
                 '(a few lines returning {"<unknown id>": value}), validate, run. Do not create other probes.')
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
    brief = json.loads((project/'.terra'/'brief.json').read_text())
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


def pack_workspace(project: Path, playbook_store: Path | None = None) -> bytes:
    """The project tree plus a copy of the playbook store, as the harness's relative tar."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        for path in sorted(project.rglob('*')):
            relative = path.relative_to(project)
            if any(part in PACK_EXCLUDE for part in relative.parts) or relative.parts[0] == PLAYBOOK_PREFIX:
                continue
            if path.is_symlink() or path.is_file() or path.is_dir():
                archive.add(path, arcname=relative.as_posix(), recursive=False)
        if playbook_store is not None and playbook_store.is_dir():
            for path in sorted(playbook_store.glob('*.json')):
                archive.add(path, arcname=PLAYBOOK_PREFIX+'/playbook/procedures/'+path.name, recursive=False)
    return buffer.getvalue()


def _members(snapshot: bytes) -> dict[str, bytes]:
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
    terra_dir = '.terra/'
    written: list[str] = []

    def put(name: str) -> None:
        target = project/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[name])
        written.append(name)

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
        elif name.startswith(terra_dir+'map/knowns/') and name.endswith('.json'):
            known = json.loads(files[name])
            if (known.get('adopted_from') or {}).get('map') == map_id:
                put(name)
                adopted_runs.update(known.get('run_ids') or [])
        elif name.startswith(terra_dir+'map/unknowns/') and name[len(terra_dir+'map/unknowns/'):-5] in task_unknown_ids(task):
            if json.loads(files[name]).get('status') == 'resolved':
                put(name)
    for name in sorted(files):
        if name.startswith(terra_dir+'map/runs/') and name.split('/')[3] in adopted_runs:
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


def harvest_playbook(snapshot: bytes, store: Path, config: dict[str, Any],
                     allowed: tuple[str, ...] | None = None) -> dict[str, list[str]]:
    """Procedures new or changed in the workspace copy; installed only if they validate.

    With `allowed`, only those ids are considered: the procedures this session followed or created.
    Anything else the worker touched stays in its workspace."""
    installed, rejected, ignored = [], [], []
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
            else:
                rejected.append(target.stem+': '+(check.stderr or check.stdout).strip()[:300])
                if backup is None:
                    target.unlink()
                else:
                    target.write_bytes(backup)
    return dict(installed=installed, rejected=rejected, ignored=ignored)


def worker_blocked(project: Path, task: dict[str, Any]) -> str | None:
    """The reason if the worker itself blocked its task through Terra; None otherwise."""
    route = json.loads((project/'.terra'/'route.json').read_text())
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


def task_gate(config: dict[str, Any], project: Path, task: dict[str, Any], map_id: str,
              root: Path | None = None) -> dict[str, Any]:
    """Mechanical verdict: the route, the project map, Terra's gate, and every widget the task touched."""
    problems = []
    if root is not None:
        problems += widget_problems(config, project, root)
    route = json.loads((project/'.terra'/'route.json').read_text())
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
    """Domain procedure ids the worker opened with `playbook start`/`load` (the bootstrap excluded)."""
    used: list[str] = []
    for name, args, _ in session_calls(root):
        words = (args.get('command') or '').split() if name == 'bash' else []
        if (len(words) >= 3 and words[0] == 'playbook' and words[1] in ('start', 'load')
                and words[2] not in used and words[2] not in BOOTSTRAP_PROCEDURES):
            used.append(words[2])
    return used


def procedures_created(root: Path) -> list[str]:
    created: list[str] = []
    for name, args, result in session_calls(root):
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
    for unknown in unknowns:
        query = re.sub(r'[^a-z0-9 ]', ' ', (unknown.get('claim') or '').lower()).replace(' is unknown', '')
        try:
            out = sp.run([config['mizpah']['cartograph'], 'search', query, '--language', 'python', '--top-k', '3',
                          '--local-only'], cwd=project, capture_output=True, text=True, timeout=60, env=env).stdout
            for hit in (json.loads(out).get('local') or {}).get('widgets') or []:
                if hit.get('relevance_score', 0) >= 0.5:
                    seen.setdefault(hit['id'], (hit.get('description') or '').split('. ')[0][:120])
        except (ValueError, OSError, sp.SubprocessError):
            continue
    return ['`'+k+'` — '+v for k, v in seen.items()]


def green_message(gate: dict[str, Any], unknown_id: str | list[str], used: list[str] = (), cost: dict[str, Any] | None = None) -> str:
    if isinstance(unknown_id, list):
        unknown_id = ', '.join(unknown_id)
    paid = ''
    if cost:
        rows = ['  - while on '+repr(step)+': '+', '.join(f'{n}× {k}' for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
                for step, counts in cost.items()]
        paid = (' Calls that were refused or failed, by the step you were on:\n'+'\n'.join(rows)+
                '\n Where a step led you into those, the step is what needs rewriting.')
    if used:
        library = ('You followed '+', '.join('`'+u+'`' for u in used)+'. Improve that procedure with `playbook edit-step` '
                   'or `playbook add-step` where its steps fell short of what you actually had to do; create a new '
                   'procedure only if your method was genuinely different, not a rewording.')
    else:
        library = ('You followed only the loop\'s own bootstrap (`mizpah-resolve-unknown`), which is not yours to copy or '
                   'rewrite. If your method was specific to this kind of source or artifact (what you read, which widget, '
                   'how the reading was taken), `playbook create <id> --title ... --description ... --tags ...` a '
                   'procedure for that and `playbook add-step` one step at a time, each step one action with the exact '
                   'commands; if it was nothing but the bootstrap, create nothing and reply "none".')
    return ('Gate green: known '+unknown_id+' '+('are' if ',' in unknown_id else 'is')+' on the project map. The widgets '
            'your probes call under cg/ are checked in for you once `cartograph validate` passes; do not build or '
            'extract anything now — the reading is taken and the parts it needed already exist. One thing to record, so '
            'the next worker starts where you finished: the method. Record what you actually followed so the next worker '
            'finds it with `playbook search`, naming the widgets it should install. '+library+paid+
            ' Then `playbook validate <id>` and reply with the procedure id and nothing else.\n')


def client_for(spec: dict[str, Any], observer: Any) -> ModelClient:
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


def bindings(config: dict[str, Any], root: Path, map_id: str, checkins: bool | None = None) -> tuple[ModelClient, ModelClient | None, SandboxedShell]:
    observe = observe_model(root)
    worker = client_for(config['worker'], observe)
    if checkins is None:
        checkins = config['mizpah']['scaffolding']['checkins']
    checkin = client_for(config['controller'], observe) if checkins else None
    scratch = root/'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    sandbox = config['mizpah']['sandbox']
    environment = dict(sandbox['environment'], TERRA_MAP=map_id, XDG_DATA_HOME='/work/'+PLAYBOOK_PREFIX)
    shell = ShellConfig(**(config['shell'] | dict(scratch_root=str(scratch), limits=ShellLimits(**config['shell']['limits']),
                                                 read_only_binds=tuple(sandbox['read_only_binds']), environment=environment,
                                                 share_network=bool(sandbox.get('share_network', False)))))
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


def focus_globs(unknowns: list[dict[str, Any]]) -> tuple[str, ...]:
    """Probes, widget sources, and every artifact this task's unknowns say it creates."""
    globs = ['.terra/map/probes/*/probe.py', '.terra/map/probes/*/measure.py', 'cg/*/src/*.py']
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
    dict(name='playbook_open', description='Write a whole procedure to .playbook/open/<id>.md in the workspace; read '
         'that file once and follow it in order. Use for the bootstrap and for any domain procedure a search finds.',
         command='playbook open {id}',
         parameters=dict(type='object', properties=dict(id=string('procedure id from search')), required=['id'])),
    dict(name='playbook_create', description='Create a new procedure (after the gate is green, when your method was '
         'specific to this kind of source or artifact and no existing procedure captures it). Then add its steps one '
         'at a time with playbook_add_step, each one action with the exact commands, and `playbook validate <id>`.',
         command='playbook create {id} --title {title} --description {description} {tags}',
         parameters=dict(type='object', properties=dict(id=string('kebab-case procedure id'), title=string('short title'),
                                                        description=string('when to use it and what it produces, one or two sentences'),
                                                        tags=string('comma-separated tags, 3-5', flag='--tags')),
                         required=['id', 'title', 'description'])),
    dict(name='playbook_add_step', description='Append one step to a procedure you created or followed: a short title '
         'and one imperative `do` with the exact commands.',
         command='playbook add-step {id} --title {title} --do {do}',
         parameters=dict(type='object', properties=dict(id=string('procedure id'), title=string('short step title'),
                                                        do=string('one action, with the exact commands')),
                         required=['id', 'title', 'do'])),
    dict(name='playbook_edit_step', description='Rewrite one step of a procedure where it fell short of what you '
         'actually had to do; target it by its current title.',
         command='playbook edit-step {id} --title {title} --do {do}',
         parameters=dict(type='object', properties=dict(id=string('procedure id'), title=string('current step title'),
                                                        do=string('the new imperative, with the exact commands')),
                         required=['id', 'title', 'do'])),
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
        **{key: config[key] for key in ('worker_tools', 'maximum_tool_argument_characters', 'maximum_write_characters',
                                        'maximum_edit_characters', 'maximum_read_lines') if key in config})


def run_task(config: dict[str, Any], project: Path, root: Path, task_id: str | None = None) -> dict[str, Any]:
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
        worker_client, checkin, shell = bindings(config, root, map_id)
        try:
            session = FocusedSession.open(root, worker=worker_client, shell=shell, controller=checkin)
        except ValueError:
            # A session created with check-ins keeps its reviewer binding even after the toggle went off.
            checkin = client_for(config['controller'], observe_model(root))
            session = FocusedSession.open(root, worker=worker_client, shell=shell, controller=checkin)
        discarded = session.discard_pending()  # a killed run leaves an uncommitted call; nothing is replayed
        # The config is the operator's current judgment; a resumed session renders its next request under it.
        wanted = {k: config['session_policy'][k] for k in FocusedSession.RETUNABLE if k in config['session_policy']}
        current = {k: getattr(session.settings.session_policy, k) for k in wanted}
        if wanted != current:
            session.retune(**{k: v for k, v in wanted.items() if current[k] != v})
        if discarded:
            (root/'discarded.jsonl').open('a').write(json.dumps(discarded)+'\n')
    else:
        task = pick_task(config, project, task_id)
        map_id = open_task_map(config, project, task)
        unknowns = [read_unknown(project, uid, map_id) for uid in task_unknown_ids(task)]
        assignment = render_assignment(task, unknowns, map_id, probe_inputs(project, task))
        parts = library_parts(config, project, unknowns)
        if parts:
            assignment += ('The library already has parts near this work; install and extend one where it nearly fits '
                           'rather than creating a near-duplicate:\n'+'\n'.join('  - '+p for p in parts)+'\n')
        worker_client, checkin, shell = bindings(config, root, map_id)
        reference = render_reference(project, task, unknowns)
        session = FocusedSession.create(root, build_settings(config, assignment, reference, unknowns), worker=worker_client,
                                        shell=shell, controller=checkin, initial_workspace=pack_workspace(project, store))
        probes_before = protected_probes(project, task)
        (root/'task.json').write_text(json.dumps(dict(task=task, unknowns=unknowns, map=map_id, assignment=assignment,
                                                      reference=reference, probes_before=probes_before), indent=1))
    estimate = settings['turn_budget'][task['bucket']]   # the bucket's effort estimate, in turns: soft
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
        status = session.run(maximum_worker_turns=boundary)
        written = writeback(session.workspace(), project, task, map_id, probes_before)
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
        if status['status'] == 'paused':
            overruns += 1
            rounds.append(dict(turns=status['completed_worker_turns'], session=status['status'], gate='effort',
                               overruns=overruns))
            if status['completed_worker_turns'] >= cap:
                break
            session.interject(effort_message(task, estimate, status['completed_worker_turns'], overruns))
            continue
        gate = task_gate(config, project, task, map_id, root)
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
        session.continue_with(green_message(gate, task_unknown_ids(task), procedures_used(root), tool_fight(root)))
        status = session.run(maximum_worker_turns=budget-status['completed_worker_turns'])
        session.prune_workspaces()
        widgets = harvest_widgets(session.workspace(), root, config)
        playbook = harvest_playbook(session.workspace(), store, config,
                                    allowed=tuple(procedures_used(root)+procedures_created(root)))
        rounds.append(dict(turns=status['completed_worker_turns'], session=status['status'], gate='playbook',
                           final_text=status['final_text'], playbook=playbook, widgets=widgets))
    verdict = 'complete' if gate['ok'] else ('blocked_by_worker' if blocked_reason is not None else 'incomplete')
    result = dict(task=task['id'], unknown=task['map_id'], unknowns=task_unknown_ids(task), map=map_id, resumed=resuming,
                  verdict=verdict,
                  blocked_reason=blocked_reason, problems=gate['problems'], knowns=gate['knowns'],
                  runs=gate['runs'], foreign_violations=gate.get('foreign_violations', []),
                  turns=status['completed_worker_turns'], turn_budget=budget, turn_estimate=estimate, overruns=overruns,
                  session=status['status'],
                  handoffs=status['handoffs'], checkins=status['controller_reviews'], held_guidance=status['held_guidance'],
                  checkin_document=session.project_document(), rounds=rounds, playbook=playbook, widgets=widgets)
    (root/'result.json').write_text(json.dumps(result, indent=1))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True, help='A Terra project directory (.terra inside)')
    parser.add_argument('--root', type=Path, required=True, help='New session directory')
    parser.add_argument('--task', help='Route task id; default is the next pickable task')
    args = parser.parse_args()
    result = run_task(load_config(args.config), args.project, args.root, args.task)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['verdict'] == 'complete' else 1)


if __name__ == '__main__':
    main()
