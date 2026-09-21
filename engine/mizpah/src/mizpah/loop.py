"""The cycle driver: controller routes, workers close tasks, controller evaluates, repeat.

Controller and worker never share context. The driver only sequences them and records
what each returned; every judgment lives in Terra's files (the gate) or in the person's
hands (the proposal queue). A worker that runs out of budget leaves its task blocked with
the gate's delta as the reason, so the next controller step sees it as state, not as a
transcript.
"""
from __future__ import annotations

import argparse
import os
import threading
import json
from pathlib import Path
import time
import traceback
from typing import Any

from . import briefs, capabilities, controller, ops, worker, priorart
from . import layout
from . import init as init_module
from .worker import terra
from cg.infra_app_paths_python.src.app_paths import resolve_app_paths
from cg.infra_atomic_file_write_python.src.atomic_file_write import atomic_write_text


def ensure_brief_map(config: dict[str, Any], project: Path, log: Path) -> str:
    """The brief's map, `b_<slug>` under global, exists before anything is minted on it. Legacy projects stay on
    global. Task maps parent the brief map, so `known adopt --from t_x` lands on the brief, and only what is
    promoted deliberately reaches global — a repository's tenth brief does not read the first nine's readings."""
    map_id = layout.brief_map(project)
    if map_id == 'global' or (layout.state(project)/'map'/'sessions'/map_id).exists():
        return map_id
    try:
        brief = terra(config, project, 'brief', 'show')
        terra(config, project, 'map', 'create', map_id, '--parent', 'global', '--purpose', 'brief: '+str(brief.get('title') or map_id))
    except RuntimeError as error:
        with log.open('a') as handle:
            handle.write(json.dumps(dict(at=time.time(), where='brief_map', error=str(error)[:300]))+'\n')
    return map_id


def pickable(config: dict[str, Any], project: Path, root: Path | None = None) -> list[dict[str, Any]]:
    """Tasks to run next: this agent's own in-progress tasks with a session on disk first (a killed run
    resumes where it paused), then the route's pickable ones."""
    tasks = terra(config, project, 'route', 'next')['tasks']
    resumable = [t for t in tasks if root is not None and t.get('status') == 'in_progress'
                 and t.get('owner_agent') == config['mizpah']['agent'] and (root/'tasks'/t['id']/'state.sqlite3').exists()]
    if root is not None:
        # Done on the route with a session that never reported (killed after `route complete`, in the review or
        # the write-up): resumed first, so the write-up lands instead of being skipped for the next task.
        unreported = [t for t in terra(config, project, 'route', 'status')['tasks'] if t.get('status') == 'done'
                      and t.get('owner_agent') == config['mizpah']['agent'] and (root/'tasks'/t['id']/'state.sqlite3').exists()
                      and not (root/'tasks'/t['id']/'result.json').exists()]
        resumable = unreported+resumable
    return resumable+controller.ready_order(project, tasks)


def blocked(config: dict[str, Any], project: Path) -> list[dict[str, Any]]:
    return [t for t in terra(config, project, 'route', 'status')['tasks'] if t['status'] == 'blocked']


def settle_partial_block(config: dict[str, Any], project: Path, task_id: str, reason: str) -> dict[str, Any] | None:
    """A worker that resolved some of a task's unknowns and blocked on the rest has finished the part it could.

    Terra strands every dependent of a blocked task until a lead re-points it; here the lead is the eval, which
    only speaks in unknowns, tasks and proposals. So the driver settles the block the way a lead would: the
    task completes citing the knowns it did produce, and each unresolved unknown is marked blocked with the
    worker's reason in its notes — open state the eval sees and re-routes or proposes on. A task that resolved
    nothing stays blocked (the run stops on it, as before).
    """
    task = next((t for t in terra(config, project, 'route', 'status')['tasks'] if t['id'] == task_id), None)
    if task is None or task.get('status') != 'blocked':
        return None
    ids = [task.get('map_id')]+[a.removeprefix('unknown:') for a in task.get('acceptance') or [] if a.startswith('unknown:')]
    records = {u['id']: u.get('record') or {} for u in terra_list(config, project, 'unknown', 'list', '--json')}
    resolved = [u for u in ids if records.get(u, {}).get('status') == 'resolved']
    unresolved = [u for u in ids if u in records and records[u].get('status') != 'resolved']
    if not resolved or not unresolved:
        return None
    for uid in unresolved:
        notes = str(records[uid].get('notes') or '')
        terra(config, project, 'unknown', 'status', uid, 'blocked', '--notes', notes+'; blocked: '+reason[:600])
    evidence = ('partial: resolved '+', '.join(resolved)+'; blocked on '+', '.join(unresolved)+' — '+reason[:400])
    knowns = [k for u in resolved for k in ('--known', str(records[u].get('resolved_by') or u).removeprefix('known:'))]
    try:
        terra(config, project, 'route', 'complete', task_id, '--evidence', evidence, *knowns)
    except RuntimeError:
        terra(config, project, 'route', 'complete', task_id, '--evidence', evidence, '--freehand', evidence[:200])
    return dict(task=task_id, resolved=resolved, unresolved=unresolved)


def terra_list(config: dict[str, Any], project: Path, *args: str) -> list[dict[str, Any]]:
    """A terra verb that prints a JSON array (unknown list --json)."""
    import subprocess
    process = subprocess.run([config['mizpah']['terra'], *args], cwd=project, capture_output=True, text=True,
                             env=dict(os.environ, **layout.terra_env(project)))
    text = process.stdout.strip()
    start = text.find('[')
    return json.loads(text[start:]) if start >= 0 else []


def commit_procedures(store: Path, message: str, author: str = 'mizpah worker <worker@mizpah>') -> bool:
    """The playbook store is a git repository: every change to a procedure is a commit a person can read,
    diff and restore. The loop commits once per work order, in that work order's name; the app commits
    once per save. Returns whether anything was committed. Never fails the run."""
    import subprocess
    try:
        if not store.is_dir():
            return False
        if not (store/'.git').exists():
            subprocess.run(['git', 'init', '-q'], cwd=store, check=True, capture_output=True)
            (store/'.gitignore').write_text('.*.tmp\n')
        subprocess.run(['git', 'add', '-A'], cwd=store, check=True, capture_output=True)
        staged = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=store)
        if staged.returncode == 0:
            return False
        subprocess.run(['git', '-c', 'user.name=mizpah', '-c', 'user.email=mizpah@local',
                        'commit', '-q', '--author', author, '-m', message], cwd=store, check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def library_tick(config: dict[str, Any], store: Path) -> dict[str, Any]:
    """A green gate is the playbook's clock: the ledger sees new procedures and retires what nobody has
    touched for long enough (use it or lose it). Bootstrap procedures never retire. Never fails the run."""
    import subprocess
    try:
        out = subprocess.run([config['mizpah']['playbook'], 'library', 'tick', '--protect', 'mizpah-'],
                             capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, XDG_DATA_HOME=str(store.parent.parent)))
        return json.loads(out.stdout) if out.returncode == 0 else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _crew(spec: dict[str, Any]) -> dict[str, Any]:
    """A role's model, both as the report labels it and as its parts."""
    gen = spec.get('generation') or {}
    base_url = (spec.get('endpoint') or {}).get('base_url')
    model = gen.get('model') or spec.get('model')
    if not model and base_url:
        # A local server knows which file it loaded; the config only knows the address.
        import urllib.request
        try:
            with urllib.request.urlopen(base_url.rstrip('/')+'/v1/models', timeout=3) as r:
                data = (json.load(r).get('data') or [{}])[0].get('id') or ''
                model = data.rsplit('/', 1)[-1].removesuffix('.gguf') or None
        except Exception:  # noqa: BLE001 — a crew label never blocks a run
            model = None
    return dict(label=ops.model_label(spec), provider=spec.get('provider'), model=model,
                effort=gen.get('reasoning_effort'), base_url=base_url)


def registry_path() -> Path:
    """Every run this machine starts, one line each, wherever its root lives: how a front end finds them.

    Lives in the per-user *state* directory of the platform (XDG on Linux, AppData\\Local on Windows,
    Library/Application Support on macOS) — the same place the app's own path widget resolves to.
    """
    return resolve_app_paths('mizpah').state_dir/'runs.jsonl'


HEARTBEAT_SECONDS = 10


def _write_run_record(root: Path, record: dict[str, Any]) -> None:
    """run.json is read by a front end while we write it: replace atomically, never leave it torn."""
    atomic_write_text(root/'run.json', json.dumps(record, indent=1))


class _Heartbeat:
    """Stamps heartbeat_at into run.json every HEARTBEAT_SECONDS while the loop runs, so a reader on any
    platform can tell a live loop from a dead one without asking the OS about our pid."""

    def __init__(self, root: Path, record: dict[str, Any]) -> None:
        self.root, self.record = root, record
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='mizpah-heartbeat', daemon=True)

    def start(self) -> '_Heartbeat':
        self.beat()
        self._thread.start()
        return self

    def beat(self) -> None:
        self.record['heartbeat_at'] = time.time()
        try:
            _write_run_record(self.root, self.record)
        except OSError:
            pass  # a missed beat is tolerated by readers; the next one lands

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            self.beat()

    def stop(self) -> None:
        self._stop.set()


def _register(record: dict[str, Any]) -> None:
    try:
        path = registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.open('a').write(json.dumps(dict(root=record['root'], project=record['project'], started_at=record['started_at']))+'\n')
    except OSError:
        pass  # a registry that cannot be written never stops a run


def _loop_alive(root: Path) -> bool:
    """Another run root on this machine whose loop process is still running (its services are not orphans)."""
    import subprocess
    probe = subprocess.run(['pgrep', '-f', 'mizpah.loop .*--root '+str(root)+'( |$)'], capture_output=True, text=True)
    return probe.returncode == 0


def install_registered_enablers(config: dict[str, Any], project: Path, log: Path) -> list[dict[str, Any]]:
    """A declared enabler the registry holds packed is installed at its path and ready before the first route step."""
    outcomes = []
    try:
        brief = terra(config, project, 'brief', 'show')
    except RuntimeError:
        return outcomes
    for enabler in brief.get('enablers') or []:
        if not isinstance(enabler, dict) or str(enabler.get('status') or 'needed') in ('ready', 'graduated'):
            continue
        try:
            doc = capabilities.install(config, project, enabler)
        except (OSError, KeyError, ValueError) as error:
            with log.open('a') as handle:
                handle.write(json.dumps(dict(at=time.time(), where='enabler-install:'+str(enabler.get('id')), error=str(error)[:500]))+'\n')
            continue
        if doc is None:
            continue
        try:
            args = ['brief', 'enabler', enabler['id'], 'graduated' if doc.get('graduates_to') else 'ready', '--path', str(enabler.get('path') or doc.get('path'))]
            if doc.get('graduates_to'):
                args += ['--graduates-to', str(doc['graduates_to'])]
            terra(config, project, *args)
        except RuntimeError as error:
            with log.open('a') as handle:
                handle.write(json.dumps(dict(at=time.time(), where='enabler-install:'+str(enabler.get('id')), error=str(error)[:500]))+'\n')
            continue
        outcomes.append(dict(enabler=enabler['id'], status='installed_from_registry', widget=doc.get('graduates_to'), by=doc.get('project')))
    return outcomes


def advance_enabler(config: dict[str, Any], project: Path, task: dict[str, Any], result: dict[str, Any], log: Path) -> dict[str, Any]:
    """A complete enabler task makes its enabler ready at its path; a widget harvested from the task graduates it."""
    eid = str(task['enabler_id'])
    outcome: dict[str, Any] = dict(enabler=eid, status='ready')
    try:
        brief = terra(config, project, 'brief', 'show')
        path = next((e.get('path') for e in brief.get('enablers') or [] if e.get('id') == eid), '') or ''
        widgets = [w for r in (result.get('rounds') or []) for k in ('checked_in', 'unchanged')
                   for w in ((r.get('widgets') or {}).get(k) or [])]
        widgets = widgets or [w for k in ('checked_in', 'unchanged') for w in ((result.get('widgets') or {}).get(k) or [])]
        args = ['brief', 'enabler', eid, 'ready']+(['--path', path] if path else [])
        terra(config, project, *args)
        if widgets:
            terra(config, project, 'brief', 'enabler', eid, 'graduated', '--graduates-to', widgets[0])
            outcome.update(status='graduated', widget=widgets[0])
        # The registry: what this project can now do, for the next brief that declares the same instrument.
        enabler = next((e for e in terra(config, project, 'brief', 'show').get('enablers') or [] if e.get('id') == eid), None)
        if enabler:
            # The procedures the enabler task installed or created ship in the pack: the method with the tool.
            procedures = [pid for r in (result.get('rounds') or []) for pid in ((r.get('playbook') or {}).get('installed') or [])]
            procedures += ((result.get('playbook') or {}).get('installed') or [])
            try:
                capabilities.record(config, project, enabler, widget=widgets[0] if widgets else None, procedures=procedures)
            except (OSError, KeyError) as error:   # no store configured: the brief still holds the graduation
                outcome['registry_error'] = str(error)[:200]
    except RuntimeError as error:
        outcome['error'] = str(error)[:300]
        with log.open('a') as handle:
            handle.write(json.dumps(dict(at=time.time(), where='enabler:'+eid, error=str(error)[:500]))+'\n')
    return outcome


def open_proposals(project: Path) -> list[dict[str, Any]]:
    try:
        return [p for p in json.loads((project/layout.dirname(project)/'brief.json').read_text()).get('proposals') or []
                if p.get('status') in (None, 'open', 'pending')]
    except (OSError, ValueError):
        return []


def blocking_proposal_open(project: Path) -> bool:
    return any(str(p.get('summary') or '').startswith('[blocking]') for p in open_proposals(project))


def phase_open(config: dict[str, Any], project: Path) -> bool:
    """A phase the map has not met is still open: the controller's "done" is not the brief's."""
    from . import phases
    return phases.current(terra(config, project, 'brief', 'show')) is not None


def close_phase(config: dict[str, Any], project: Path, record: dict[str, Any], log: Path) -> bool:
    """Close the current phase if the map says it is met; records the outcome on the cycle; never raises."""
    try:
        outcome = controller.close_ready_phase(config, project)
    except Exception as error:  # noqa: BLE001
        with log.open('a') as handle:
            handle.write(json.dumps(dict(at=time.time(), where='phase', error=str(error)[:500]))+'\n')
        return False
    if outcome is None:
        return False
    record.setdefault('phases', []).append(outcome)
    return bool(outcome.get('closed'))


def failing_step(config: dict[str, Any], project: Path, journal: Path, mode: str, log: Path) -> dict[str, Any]:
    """A controller step that records its own failure instead of ending the run."""
    try:
        return controller.step(config, project, journal, mode)
    except Exception as error:  # noqa: BLE001 — the run must outlive one bad step
        with log.open('a') as handle:
            handle.write(json.dumps(dict(at=time.time(), where='controller:'+mode, error=str(error)[:500],
                                         trace=traceback.format_exc()[-2000:]))+'\n')
        return dict(mode=mode, applied=dict(unknowns=[], tasks=[], proposals=[], rebucket=[], unblock=[], retype=[]), refused=[],
                    why='', error=str(error)[:300])


def run(config: dict[str, Any], project: Path, root: Path, *, max_cycles: int, max_tasks: int,
        deadline_hours: float | None = None, max_consecutive_errors: int = 3) -> dict[str, Any]:
    """Unattended-safe: one task's crash blocks that task; repeated crashes stop the run; a deadline ends it."""
    project, root = project.resolve(), root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    layout.bind(project)                       # this process's terra calls find the project's tree and brief map
    init_module.apply_project_config(config, project)   # the project's own sandbox settings, if it has any
    journal = root/'controller.jsonl'
    log = root/'errors.jsonl'
    ensure_brief_map(config, project, log)
    started = time.time()
    deadline = None if deadline_hours is None else started+deadline_hours*3600
    cycles: list[dict[str, Any]] = []
    tasks_run = 0
    errors = 0
    stalled_evals = 0
    health = ops.Health(config, root)
    config['mizpah']['run_root'] = str(root)   # the controller's outage wait records health here too
    ops.mark_loop(root)   # every process under this loop carries the root; a leak is traced back by it
    # The pointer from a session root back to its project, and whether the loop is alive: what a
    # front end needs to find runs on disk without guessing from directory names.
    run_record = dict(project=str(project), root=str(root), pid=os.getpid(), started_at=started,
                      config=str(config.get('harness_config_path', '')), mizpah_config=str(config.get('mizpah_config_path', '')),
                      ended_at=None, stop=None,
                      # who is on the job: how a front end names the controller and the worker
                      crew=dict(controller=_crew(config.get('controller') or {}),
                                worker=_crew(config.get('worker') or {})))
    heartbeat = _Heartbeat(root, run_record).start()   # writes run.json now, then every HEARTBEAT_SECONDS
    _register(run_record)
    # A previous run of this root killed without its finally leaves services running; they are its, so reap them.
    orphans = ops.sweep_services(live_roots=[r for r in Path(root).parent.glob('*') if r.is_dir() and r != root
                                             and (r/'loop.json').exists() and _loop_alive(r)])
    if orphans:
        (root/'services.jsonl').open('a').write(json.dumps(dict(at=time.time(), swept=orphans))+'\n')

    def live_roots() -> list[Path]:
        return [r for r in Path(root).parent.glob('*') if r.is_dir() and r != root and (r/'loop.json').exists() and _loop_alive(r)]

    def reap(where: str) -> list[dict[str, Any]]:
        # Processes a task left behind on the host (a probe's browser outliving a killed probe) are killed at
        # every boundary and counted: a widget that keeps things open shows up as a number, not as lost RAM.
        try:
            leaks = ops.reap_leaks(root, live_roots(), where=where)
        except Exception as error:  # noqa: BLE001 — reaping never ends the run
            with log.open('a') as handle:
                handle.write(json.dumps(dict(at=time.time(), where='reap', error=str(error)[:300]))+'\n')
            return []
        if leaks:
            print('reaped '+str(len(leaks))+' leaked process(es) after '+where+': '
                  +', '.join(l['comm']+' pid '+str(l['pid'])+' '+str(l['rss_mb'])+' MB' for l in leaks[:6]), flush=True)
        return leaks
    reap('start')
    # A task the previous run blocked on its own plumbing ("driver error: …") is ours to retry, not the
    # worker's or the brief's: release it now that the engine has had its chance to be fixed.
    for t in blocked(config, project):
        if str(t.get('blocked_reason') or '').startswith(controller.DRIVER_BLOCK):
            try:
                terra(config, project, 'route', 'unblock', t['id'])
                with log.open('a') as handle:
                    handle.write(json.dumps(dict(at=time.time(), where='retry', task=t['id'], reason=t.get('blocked_reason')))+'\n')
            except RuntimeError as error:
                with log.open('a') as handle:
                    handle.write(json.dumps(dict(at=time.time(), where='retry:'+t['id'], error=str(error)[:300]))+'\n')
    # SIGTERM (a plain `kill`) should still run the finally blocks that stop services and write the report.
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    def report(stop_reason: str | None = None) -> None:
        try:
            ops.write_report(config, project, root, cycles+([record] if record and record not in cycles else []), stop_reason, started)
        except Exception as error:  # noqa: BLE001 — the report never ends the run
            with log.open('a') as handle:
                handle.write(json.dumps(dict(at=time.time(), where='report', error=str(error)[:300]))+'\n')
    record: dict[str, Any] = {}
    stop = 'max_cycles'

    def out_of_time() -> bool:
        return deadline is not None and time.time() > deadline

    try:
        installed = install_registered_enablers(config, project, log)
        for cycle in range(1, max_cycles+1):
            record = dict(cycle=cycle, tasks=[], evals=[], started_at=time.time())
            if installed:
                record['enablers'], installed = installed, []
            if not pickable(config, project, root):
                record['route'] = failing_step(config, project, journal, 'route', log)
                if record['route'].get('error'):
                    errors += 1
            while tasks_run < max_tasks and not out_of_time():
                if not health.disk_ok(root, project):
                    stop = 'disk_high'
                    break
                if ops.stop_requested(root):
                    stop = 'stopped_by_operator'
                    break
                if blocking_proposal_open(project):
                    # The controller judged the brief's flaw fatal to the remaining work: a person decides first.
                    stop = 'proposals_pending'
                    break
                ready = pickable(config, project, root)
                if not ready:
                    break
                task = ready[0]
                tasks_run += 1
                try:
                    # One session root per task, so a re-bucketed task resumes its own session.
                    result = worker.run_task(config, project, root/'tasks'/task['id'], task['id'])
                    if result.get('verdict') == 'complete':
                        ticked = library_tick(config, Path(config['mizpah']['playbook_store']).expanduser())
                        if ticked.get('retired'):
                            result['playbook'] = dict(result.get('playbook') or {}, retired=ticked['retired'])
                            (root/'tasks'/task['id']/'result.json').write_text(json.dumps(result, indent=1))
                    # Whatever the worker filed or improved in the playbook lands as one commit in its name.
                    commit_procedures(Path(config['mizpah']['playbook_store']).expanduser(),
                                      'work order '+task['id']+' · '+project.name+' ('+result.get('verdict', '?')+')',
                                      author='worker '+str(run_record['crew']['worker'].get('model') or 'model')+' <worker@mizpah>')
                except Exception as error:  # noqa: BLE001
                    errors += 1
                    with log.open('a') as handle:
                        handle.write(json.dumps(dict(at=time.time(), where='task:'+task['id'], error=str(error)[:500],
                                                     trace=traceback.format_exc()[-2000:]))+'\n')
                    record['tasks'].append(dict(task=task['id'], verdict='error', error=str(error)[:300]))
                    try:
                        terra(config, project, 'route', 'block', task['id'], '--reason', 'driver error: '+str(error)[:300])
                    except RuntimeError:
                        pass
                    if errors >= max_consecutive_errors:
                        stop = 'driver_failing'
                        break
                    continue
                errors = 0
                record['tasks'].append({k: result[k] for k in ('task', 'unknown', 'unknowns', 'verdict', 'blocked_reason', 'turns',
                                                               'resumed', 'checkins', 'held_guidance', 'problems', 'playbook', 'widgets')
                                        if k in result} | dict(reviewer_doubts=result.get('reviewer_doubts') or []))
                leaked = reap(task['id'])
                if leaked:
                    record['tasks'][-1]['leaked'] = [dict(comm=l['comm'], rss_mb=l['rss_mb'], age_s=l['age_s']) for l in leaked]
                # A task that rewrote a file the map depends on left knowns stale: the host re-takes them now,
                # before the eval sees a red gate it would otherwise spend a worker session clearing.
                try:
                    refreshed = worker.refresh_stale(config, project, root)
                except Exception as error:  # noqa: BLE001 — a refresh never ends the run
                    refreshed = dict(refreshed=[], changed=[], failed=['refresh: '+str(error)[:200]])
                    with log.open('a') as handle:
                        handle.write(json.dumps(dict(at=time.time(), where='refresh', error=str(error)[:500]))+'\n')
                if any(refreshed.values()):
                    record['tasks'][-1]['refreshed'] = refreshed
                if result['verdict'] == 'stopped':
                    stop = 'stopped_by_operator'
                    break
                if task.get('enabler_id') and result['verdict'] == 'complete':
                    record.setdefault('enablers', []).append(advance_enabler(config, project, task, result, log))
                if result['verdict'] == 'incomplete':
                    reason = ('worker budget exhausted' if result['session'] != 'complete' else 'gate rounds exhausted'
                              )+' at '+str(result['turns'])+' turns (safety cap; the worker never blocked itself); gate: '+'; '.join(result['problems'])[:400]
                    current = next((t for t in terra(config, project, 'route', 'status')['tasks'] if t['id'] == task['id']), {})
                    if current.get('status') == 'done':
                        # Terra let it complete but the Mizpah gate is red (typically a known not adopted).
                        # A done task is terminal; the open unknowns it left behind are unrouted state the
                        # eval step re-mints, so nothing to do here but record it.
                        record.setdefault('done_but_red', []).append(dict(task=task['id'], problems=result['problems'][:4]))
                    else:
                        try:
                            terra(config, project, 'route', 'block', task['id'], '--reason', reason)
                        except RuntimeError as error:
                            with log.open('a') as handle:
                                handle.write(json.dumps(dict(at=time.time(), where='block:'+task['id'], error=str(error)[:500]))+'\n')
                elif result['verdict'] == 'blocked_by_worker':
                    # A worker block that resolved some unknowns is settled (task done citing them, the rest marked
                    # blocked with the reason) so dependents can run; a block that resolved nothing stays blocked.
                    try:
                        settled = settle_partial_block(config, project, task['id'], str(result['blocked_reason'] or ''))
                    except RuntimeError as error:
                        settled = None
                        with log.open('a') as handle:
                            handle.write(json.dumps(dict(at=time.time(), where='settle:'+task['id'], error=str(error)[:500]))+'\n')
                    if settled:
                        record.setdefault('settled', []).append(settled)
                # The controller works between tasks: it sees the new known as state and may
                # mint the next unknowns while the route still has work. Its writes are safe
                # against the worker's entitlement writeback.
                record['evals'].append(failing_step(config, project, journal, 'eval', log))
                (root/'loop.json').write_text(json.dumps(dict(cycles=cycles+[record], tasks_run=tasks_run), indent=1))
                report()
            if stop in ('driver_failing', 'disk_high', 'stopped_by_operator', 'proposals_pending'):
                cycles.append(record)
                break
            if not record['evals']:
                record['evals'].append(failing_step(config, project, journal, 'eval', log))
            record['eval'] = record['evals'][-1]
            cycles.append(record)
            (root/'loop.json').write_text(json.dumps(dict(cycles=cycles, tasks_run=tasks_run), indent=1))
            if out_of_time():
                stop = 'deadline'
                break
            minted = record['eval']['applied']
            if not any(minted[k] for k in ('unknowns', 'tasks', 'rebucket', 'unblock', 'retype')) and not pickable(config, project, root):
                if close_phase(config, project, record, log):
                    # The eval looked and minted nothing (a suite resolved false would have drawn a repair task) and
                    # the map says every entry is resolved: the phase is met and the next cycle routes the next one.
                    # Closing before the eval let improve's change phase shut on a failing suite (2026-09-19).
                    stalled_evals = 0
                    continue
                if blocked(config, project):
                    stop = 'blocked'
                elif minted['proposals'] or open_proposals(project):
                    # Open proposals keep a project from wrapping up: what was done is done, but the brief is not met.
                    stop = 'proposals_pending'
                elif record['eval'].get('done') is True and not phase_open(config, project):
                    stop = 'nothing_owed'
                elif stalled_evals < 1:
                    # One empty eval is one bad draw (each step is a fresh window): a second cycle gets a route
                    # step and another eval before a person is asked to decide.
                    stalled_evals += 1
                    continue
                else:
                    # The controller routed nothing twice and would not say the brief is met: a person decides.
                    stop = 'controller_stalled'
                break
            stalled_evals = 0
            if tasks_run >= max_tasks:
                stop = 'max_tasks'
                break
    except KeyboardInterrupt:
        # SIGTERM or Ctrl-C: the task's session paused where it was (it reopens there); services stopped in
        # run_task's finally; the report says so.
        stop = 'interrupted'
    reap('end')
    report(stop)
    try:
        if not priorart.benchmark(project):   # a benchmark run is measured, not remembered
            briefs.record(config, project, stop, cycles, session=root)   # the controller's library grows by one completed run
    except Exception as error:  # noqa: BLE001
        with log.open('a') as handle:
            handle.write(json.dumps(dict(at=time.time(), where='briefs.record', error=str(error)[:300]))+'\n')
    if stop not in ('nothing_owed', 'max_cycles', 'max_tasks'):
        ops.notify(config, root, project.name+' stopped: '+stop,
                   str(tasks_run)+' tasks in '+str(round((time.time()-started)/3600, 2))+' h; report at '+str(root/'report.md'))
    adopted = None
    if stop == 'nothing_owed' and config['mizpah'].get('builds_base'):
        # An environment gym went green: its tree is the base now, and any gym may be set up in it.
        from . import bases
        try:
            adopted = bases.adopt(project, str(config['mizpah']['builds_base']), replace=True)
            with log.open('a') as handle:
                handle.write(json.dumps(dict(at=time.time(), where='bases.adopt', base=adopted['name'], path=adopted['path']))+'\n')
        except (OSError, ValueError) as error:
            with log.open('a') as handle:
                handle.write(json.dumps(dict(at=time.time(), where='bases.adopt', error=str(error)[:300]))+'\n')
            ops.notify(config, root, project.name+': environment not adopted', str(error)[:200])
    result = dict(stop=stop, cycles=cycles, tasks_run=tasks_run, hours=round((time.time()-started)/3600, 2), leaks=ops.leak_summary(root),
                  **(dict(adopted_base=dict(name=adopted['name'], path=adopted['path'])) if adopted else {}),
                  usage=ops.usage_summary(root),
                  blocked=[dict(id=t['id'], reason=t.get('blocked_reason')) for t in blocked(config, project)],
                  open_proposals=terra(config, project, 'brief', 'show').get('open_proposals'))
    (root/'loop.json').write_text(json.dumps(result, indent=1))
    heartbeat.stop()
    run_record.update(ended_at=time.time(), stop=stop)
    _write_run_record(root, run_record)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=None,
                        help='session root (default: <project>/.mizpah/sessions/<timestamp>)')
    parser.add_argument('--max-cycles', type=int, default=3)
    parser.add_argument('--max-tasks', type=int, default=6, help='Safety cap; the brief budget is what bounds tasks')
    parser.add_argument('--deadline-hours', type=float, default=None)
    parser.add_argument('--note', default=None, help='the person\'s reply to the run\'s last notice: put before the controller at its next briefing')
    args = parser.parse_args()
    root = args.root or layout.sessions(args.project)/time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    if args.note:
        root.mkdir(parents=True, exist_ok=True)
        with (root/controller.OPERATOR_NOTES).open('a') as handle:
            handle.write(json.dumps(dict(at=time.time(), text=args.note))+'\n')
    result = run(worker.load_config(args.config), args.project, root, max_cycles=args.max_cycles,
                 max_tasks=args.max_tasks, deadline_hours=args.deadline_hours)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
