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
import json
from pathlib import Path
import time
import traceback
from typing import Any

from . import briefs, controller, ops, worker
from .worker import terra


def pickable(config: dict[str, Any], project: Path, root: Path | None = None) -> list[dict[str, Any]]:
    """Tasks to run next: this agent's own in-progress tasks with a session on disk first (a killed run
    resumes where it paused), then the route's pickable ones."""
    tasks = terra(config, project, 'route', 'next')['tasks']
    resumable = [t for t in tasks if root is not None and t.get('status') == 'in_progress'
                 and t.get('owner_agent') == config['mizpah']['agent'] and (root/'tasks'/t['id']/'state.sqlite3').exists()]
    return resumable+[t for t in tasks if t.get('pickable') and t.get('map_id')]


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
    process = subprocess.run([config['mizpah']['terra'], *args], cwd=project, capture_output=True, text=True)
    text = process.stdout.strip()
    start = text.find('[')
    return json.loads(text[start:]) if start >= 0 else []


def registry_path() -> Path:
    """Every run this machine starts, one line each, wherever its root lives: how a front end finds them."""
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local'/'state')
    return base/'mizpah'/'runs.jsonl'


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


def failing_step(config: dict[str, Any], project: Path, journal: Path, mode: str, log: Path) -> dict[str, Any]:
    """A controller step that records its own failure instead of ending the run."""
    try:
        return controller.step(config, project, journal, mode)
    except Exception as error:  # noqa: BLE001 — the run must outlive one bad step
        with log.open('a') as handle:
            handle.write(json.dumps(dict(at=time.time(), where='controller:'+mode, error=str(error)[:500],
                                         trace=traceback.format_exc()[-2000:]))+'\n')
        return dict(mode=mode, applied=dict(unknowns=[], tasks=[], proposals=[], rebucket=[]), refused=[],
                    why='', error=str(error)[:300])


def run(config: dict[str, Any], project: Path, root: Path, *, max_cycles: int, max_tasks: int,
        deadline_hours: float | None = None, max_consecutive_errors: int = 3) -> dict[str, Any]:
    """Unattended-safe: one task's crash blocks that task; repeated crashes stop the run; a deadline ends it."""
    project, root = project.resolve(), root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    journal = root/'controller.jsonl'
    log = root/'errors.jsonl'
    started = time.time()
    deadline = None if deadline_hours is None else started+deadline_hours*3600
    cycles: list[dict[str, Any]] = []
    tasks_run = 0
    errors = 0
    stalled_evals = 0
    health = ops.Health(config, root)
    config['mizpah']['run_root'] = str(root)   # the controller's outage wait records health here too
    # The pointer from a session root back to its project, and whether the loop is alive: what a
    # front end needs to find runs on disk without guessing from directory names.
    run_record = dict(project=str(project), root=str(root), pid=os.getpid(), started_at=started,
                      config=str(config.get('harness_config_path', '')), ended_at=None, stop=None)
    (root/'run.json').write_text(json.dumps(run_record, indent=1))
    _register(run_record)
    # A previous run of this root killed without its finally leaves services running; they are its, so reap them.
    orphans = ops.sweep_services(live_roots=[r for r in Path(root).parent.glob('*') if r.is_dir() and r != root
                                             and (r/'loop.json').exists() and _loop_alive(r)])
    if orphans:
        (root/'services.jsonl').open('a').write(json.dumps(dict(at=time.time(), swept=orphans))+'\n')
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
        for cycle in range(1, max_cycles+1):
            record = dict(cycle=cycle, tasks=[], evals=[], started_at=time.time())
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
                ready = pickable(config, project, root)
                if not ready:
                    break
                task = ready[0]
                tasks_run += 1
                try:
                    # One session root per task, so a re-bucketed task resumes its own session.
                    result = worker.run_task(config, project, root/'tasks'/task['id'], task['id'])
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
                                                               'resumed', 'checkins', 'held_guidance', 'problems', 'playbook', 'widgets')})
                if result['verdict'] == 'stopped':
                    stop = 'stopped_by_operator'
                    break
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
            if stop in ('driver_failing', 'disk_high', 'stopped_by_operator'):
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
            if not any(minted[k] for k in ('unknowns', 'tasks', 'rebucket')) and not pickable(config, project, root):
                if blocked(config, project):
                    stop = 'blocked'
                elif minted['proposals']:
                    stop = 'proposals_pending'
                elif record['eval'].get('done') is True:
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
    report(stop)
    try:
        briefs.record(config, project, stop, cycles)   # the controller's library grows by one finished run
    except Exception as error:  # noqa: BLE001
        with log.open('a') as handle:
            handle.write(json.dumps(dict(at=time.time(), where='briefs.record', error=str(error)[:300]))+'\n')
    if stop not in ('nothing_owed', 'max_cycles', 'max_tasks'):
        ops.notify(config, root, project.name+' stopped: '+stop,
                   str(tasks_run)+' tasks in '+str(round((time.time()-started)/3600, 2))+' h; report at '+str(root/'report.md'))
    result = dict(stop=stop, cycles=cycles, tasks_run=tasks_run, hours=round((time.time()-started)/3600, 2),
                  blocked=[dict(id=t['id'], reason=t.get('blocked_reason')) for t in blocked(config, project)],
                  open_proposals=terra(config, project, 'brief', 'show').get('open_proposals'))
    (root/'loop.json').write_text(json.dumps(result, indent=1))
    run_record.update(ended_at=time.time(), stop=stop)
    (root/'run.json').write_text(json.dumps(run_record, indent=1))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--max-cycles', type=int, default=3)
    parser.add_argument('--max-tasks', type=int, default=6, help='Safety cap; the brief budget is what bounds tasks')
    parser.add_argument('--deadline-hours', type=float, default=None)
    args = parser.parse_args()
    result = run(worker.load_config(args.config), args.project, args.root, max_cycles=args.max_cycles,
                 max_tasks=args.max_tasks, deadline_hours=args.deadline_hours)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
