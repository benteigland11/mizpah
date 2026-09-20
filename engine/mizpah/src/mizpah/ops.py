"""What a loop needs to run unattended: a health check that heals what it can, a report a person reads in the
morning, and a way to say the few things that need saying now.

Config, under `ops` in the Mizpah config (all optional):
  model_unit            a user systemd unit for the model server; started when /health stays down
  restart_after_seconds how long the server may be down before the unit is started (default 60)
  restart_cooldown_seconds  no second start within this window (default 600)
  disk_high_percent     the run stops before a full disk corrupts it (default 92)
  notify_command        a shell command run with MIZPAH_TITLE and MIZPAH_BODY in its environment, for the
                        events that change what a person does next (run stopped, server down, disk high)

Everything here is derived from files the loop already writes; nothing is a second bookkeeping path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
import urllib.error
import urllib.request


def settings(config: dict[str, Any]) -> dict[str, Any]:
    return dict(model_unit=None, restart_after_seconds=60, restart_cooldown_seconds=600, disk_high_percent=92,
                notify_command=None) | dict(config['mizpah'].get('ops') or {})


# ---------------------------------------------------------------- health

def model_label(spec: dict[str, Any]) -> str:
    """How a report names the model: a local server by its address, a subscription by profile and model."""
    if spec.get('provider') == 'subscription':
        return str(spec.get('subscription'))+'/'+str((spec.get('generation') or {}).get('model') or '?')
    return str((spec.get('endpoint') or {}).get('base_url') or '?')


def model_up(base_url: str | None, timeout: float = 5.0) -> bool:
    """llama.cpp answers /health with 200 (ready) or 503 (loading); a hosted API has no /health and
    answers 404 or 401, which still means the host is reachable, and that is the question here."""
    if not base_url:
        return True   # a subscription client has no address of its own; its transport reports outages
    try:
        with urllib.request.urlopen(base_url.rstrip('/')+'/health', timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        return error.code != 503
    except OSError:
        return False


class Health:
    """One per run. `wait_for_model` is what the outage loops call instead of sleeping on /health alone."""

    def __init__(self, config: dict[str, Any], root: Path) -> None:
        self.config = config
        self.ops = settings(config)
        self.root = root
        self.log = root/'health.jsonl'
        self.last_restart = 0.0

    def _record(self, **fields: Any) -> None:
        self.log.open('a').write(json.dumps(dict(at=time.time(), **fields))+'\n')

    def wait_for_model(self, base_url: str | None, *, wait_seconds: float) -> bool:
        """True when the server answers within wait_seconds; starts its unit once it has been down long enough.

        A subscription client (no base_url) is waited for with plain backoff: nothing here can restart it."""
        if not base_url:
            time.sleep(min(60.0, wait_seconds))
            return True
        down_since = time.time()
        deadline = down_since+wait_seconds
        while time.time() <= deadline:
            if model_up(base_url):
                return True
            unit = self.ops['model_unit']
            if (unit and time.time()-down_since >= self.ops['restart_after_seconds']
                    and time.time()-self.last_restart >= self.ops['restart_cooldown_seconds']):
                self.last_restart = time.time()
                # An active unit that does not answer is wedged (llama-server after two SIGINTs sits in teardown
                # with its listener closed and never exits, so Restart= never fires): restart, not start.
                active = subprocess.run(['systemctl', '--user', 'is-active', unit], capture_output=True, text=True,
                                        timeout=30, check=False).stdout.strip() == 'active'
                verb = 'restart' if active else 'start'
                started = subprocess.run(['systemctl', '--user', verb, unit], capture_output=True, text=True,
                                         timeout=180, check=False)
                self._record(event='model_unit_'+verb+'ed', unit=unit, ok=started.returncode == 0,
                             error=(started.stderr or '').strip()[:300])
                notify(self.config, self.root, 'model server '+verb+'ed',
                       unit+' was down '+str(int(time.time()-down_since))+' s ('+('active but silent' if active else 'inactive')+'); '+verb+'ed it')
            time.sleep(5)
        self._record(event='model_down', base_url=base_url, waited=wait_seconds)
        notify(self.config, self.root, 'model server down', base_url+' did not answer for '+str(int(wait_seconds))+' s')
        return False

    def disk_ok(self, *paths: Path) -> bool:
        """False when any filesystem a run writes to is past the watermark; recorded and notified once each."""
        ok = True
        for path in paths:
            try:
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            percent = 100*usage.used/usage.total if usage.total else 0
            if percent >= self.ops['disk_high_percent']:
                ok = False
                key = 'disk_high:'+str(path)
                if not getattr(self, key, False):
                    setattr(self, key, True)
                    self._record(event='disk_high', path=str(path), percent=round(percent, 1))
                    notify(self.config, self.root, 'disk high', str(path)+' is '+str(round(percent, 1))+'% full')
        return ok


# ---------------------------------------------------------------- stop and sweep

def stop_requested(root: Path) -> bool:
    """A STOP file in the run root, or one or two levels up (a whole queue), pauses the loop at the next boundary."""
    return any((p/'STOP').exists() for p in (root, root.parent, root.parent.parent))


def sweep_services(live_roots: list[Path] = ()) -> list[str]:
    """Stop every `isolated-service-*` scope no live run owns: a loop killed with -9 leaves its browser running
    until the service lifetime, and nothing else reaps it."""
    listing = subprocess.run(['systemctl', '--user', 'list-units', '--type=service', '--type=scope', '--all',
                              '--no-legend', '--plain', 'isolated-service-*'], capture_output=True, text=True, timeout=30)
    units = [line.split()[0] for line in listing.stdout.splitlines() if line.strip()]
    owned: set[str] = set()
    for root in live_roots:
        for status in Path(root).glob('tasks/*/scratch/services/*/status.json'):
            try:
                owned.add(json.loads(status.read_text()).get('unit') or '')
            except (OSError, ValueError):
                continue
    stopped = []
    for unit in units:
        if unit in owned:
            continue
        subprocess.run(['systemctl', '--user', 'stop', unit], capture_output=True, timeout=30, check=False)
        stopped.append(unit)
    return stopped


# ---------------------------------------------------------------- leaked processes

LEAK_MARK = 'MIZPAH_LOOP'


def mark_loop(root: Path) -> None:
    """Every process this loop starts inherits the mark, down to a chromium a probe launched three forks
    deep: the mark is how a leak is traced back to its loop after its parent is gone."""
    os.environ[LEAK_MARK] = str(root)


def _proc(pid: int) -> dict[str, Any] | None:
    base = Path('/proc')/str(pid)
    try:
        environ = (base/'environ').read_bytes().split(b'\0')
        stat = (base/'stat').read_text()
        status = (base/'status').read_text()
    except OSError:
        return None
    mark = next((e[len(LEAK_MARK)+1:].decode(errors='replace') for e in environ if e.startswith(LEAK_MARK.encode()+b'=')), None)
    if mark is None:
        return None
    tail = stat[stat.rfind(')')+2:].split()
    comm = stat[stat.find('(')+1:stat.rfind(')')]
    ppid = int(tail[1])
    rss_kb = next((int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:')), 0)
    try:
        cmdline = (base/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
    except OSError:
        cmdline = comm
    ticks = os.sysconf('SC_CLK_TCK')
    try:
        uptime = float(Path('/proc/uptime').read_text().split()[0])
        age = uptime-int(tail[19])/ticks
    except (OSError, ValueError, IndexError):
        age = 0.0
    return dict(pid=pid, ppid=ppid, comm=comm, cmdline=cmdline[:200], root=mark, rss_mb=round(rss_kb/1024), age_s=round(age))


def leaked_processes(root: Path, live_roots: list[Path] = ()) -> list[dict[str, Any]]:
    """Marked processes whose parent is gone (reparented to PID 1): a probe killed by a timeout or a sandbox
    teardown leaves its browser behind on the host, and nothing else reaps it. A loop itself (detached by
    the app, so ppid 1 by design) is never a leak; a leak of another loop still alive is that loop's to reap
    at its own cycle boundary."""
    live = {str(Path(r).resolve()) for r in live_roots}
    mine = str(Path(root).resolve())
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        info = _proc(int(entry.name))
        if info is None or info['ppid'] != 1 or 'mizpah.loop' in info['cmdline']:
            continue
        if info['root'] != mine and info['root'] in live:
            continue
        found.append(info)
    return found


def reap_leaks(root: Path, live_roots: list[Path] = (), *, where: str = '') -> list[dict[str, Any]]:
    """Kill leaked processes (TERM, then KILL) and record each in <root>/leaks.jsonl: the count is a number the
    report shows, so a widget that keeps things open is seen, not just cleaned up after."""
    import signal
    leaks = leaked_processes(root, live_roots)
    for info in leaks:
        try:
            os.kill(info['pid'], signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time()+3
    for info in leaks:
        while time.time() < deadline and Path('/proc', str(info['pid'])).exists():
            time.sleep(0.1)
        if Path('/proc', str(info['pid'])).exists():
            try:
                os.kill(info['pid'], signal.SIGKILL)
            except OSError:
                pass
            info['forced'] = True
    if leaks:
        with (Path(root)/'leaks.jsonl').open('a') as handle:
            for info in leaks:
                handle.write(json.dumps(dict(at=time.time(), where=where, **info))+'\n')
    return leaks


def leak_summary(root: Path) -> dict[str, Any]:
    """What the run leaked so far, by command: the report's line and the number a person watches."""
    rows = _read_jsonl(Path(root)/'leaks.jsonl')
    by_comm: dict[str, int] = {}
    for row in rows:
        by_comm[str(row.get('comm'))] = by_comm.get(str(row.get('comm')), 0)+1
    return dict(count=len(rows), by_comm=by_comm, rss_mb=sum(int(r.get('rss_mb') or 0) for r in rows))


# ---------------------------------------------------------------- notify

def notify(config: dict[str, Any], root: Path, title: str, body: str) -> None:
    """The few events that change what a person does next. Logged always; sent when a command is configured."""
    (root/'notify.jsonl').open('a').write(json.dumps(dict(at=time.time(), title=title, body=body))+'\n')
    command = settings(config)['notify_command']
    if not command:
        return
    try:
        subprocess.run(command, shell=True, timeout=30, check=False, capture_output=True,
                       env=dict(os.environ, MIZPAH_TITLE=title, MIZPAH_BODY=body))
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- report

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def write_report(config: dict[str, Any], project: Path, root: Path, cycles: list[dict[str, Any]],
                 stop: str | None, started: float) -> Path:
    """root/report.md: the run as a person would want to read it, rebuilt after every task and eval."""
    lines = ['# '+project.name+' — '+('running' if stop is None else 'stopped: '+stop),
             '', 'engine '+_engine_version()+' · model '+model_label(config['worker'])
             +' · '+str(round((time.time()-started)/3600, 2))+' h · '
             +str(sum(len(c.get('tasks') or []) for c in cycles))+' tasks', '']
    leaks = leak_summary(root)
    if leaks['count']:
        lines.append('**Leaked processes reaped: '+str(leaks['count'])+'** ('
                     +', '.join(k+' ×'+str(v) for k, v in sorted(leaks['by_comm'].items()))+', '+str(leaks['rss_mb'])
                     +' MB) — a task left these running on the host after its probe ended; see leaks.jsonl')
        lines.append('')
    lines.append('## Tasks')
    for cycle in cycles:
        for task in cycle.get('tasks') or []:
            playbook = task.get('playbook') or {}
            widgets = task.get('widgets') or {}
            flags = []
            if task.get('blocked_reason'):
                flags.append('blocked: '+str(task['blocked_reason'])[:160])
            if playbook.get('installed'):
                flags.append('procedures +'+', '.join(playbook['installed']))
            if playbook.get('rejected'):
                flags.append('procedures rejected '+str(len(playbook['rejected'])))
            for key in ('checked_in', 'rejected'):
                if widgets.get(key):
                    flags.append('widgets '+key+' '+', '.join(str(w) for w in widgets[key]))
            if task.get('problems'):
                flags.append('gate: '+'; '.join(str(p)[:80] for p in task['problems'][:2]))
            lines.append('- `'+str(task.get('task'))+'` '+str(task.get('verdict'))+' in '+str(task.get('turns'))+' turns'
                         +(' — '+' · '.join(flags) if flags else ''))
        for step in [cycle.get('route')]+list(cycle.get('evals') or []):
            if not step:
                continue
            applied = step.get('applied') or {}
            summary = ', '.join(k+' '+str(len(v)) for k, v in applied.items() if v) or 'nothing'
            refused = len(step.get('refused') or [])
            lines.append('  - '+str(step.get('mode'))+': applied '+summary+(', refused '+str(refused) if refused else '')
                         +(' — done' if step.get('done') is True else '')
                         +(' — error: '+str(step['error'])[:120] if step.get('error') else ''))
    refusals = _refusals(root)
    if refusals:
        lines += ['', '## Refused by the guards (what the structure caught)']
        for task_name, rows in refusals.items():
            lines.append('- `'+task_name+'`: '+str(len(rows))+' refused')
            for reason, count in sorted(_count(r['reason'] for r in rows).items(), key=lambda kv: -kv[1])[:5]:
                lines.append('  - '+str(count)+'× '+reason[:140])
    outages = [r for t in (root/'tasks').glob('*') for r in _read_jsonl(t/'outages.jsonl')] if (root/'tasks').exists() else []
    health = _read_jsonl(root/'health.jsonl')
    notes = _read_jsonl(root/'errors.jsonl')
    services = [r for t in (root/'tasks').glob('*') for r in _read_jsonl(t/'services.jsonl')] if (root/'tasks').exists() else []
    egress = [r for t in (root/'tasks').glob('*') for r in _read_jsonl(t/'scratch'/'egress.jsonl')] if (root/'tasks').exists() else []
    lines += ['', '## Infrastructure',
              '- outages: '+str(len(outages))+(' (last: '+str(outages[-1].get('error'))[:100]+')' if outages else ''),
              '- health events: '+', '.join(str(h.get('event')) for h in health) if health else '- health events: none',
              '- driver errors: '+str(len(notes))+(' (last: '+str(notes[-1].get('where'))+' — '+str(notes[-1].get('error'))[:100]+')' if notes else ''),
              '- services stopped at task end: '+str(sum(len(s.get('stopped') or []) for s in services))]
    if egress:
        allowed = _count(e['host'] for e in egress if e.get('allowed'))
        refused = _count(e['host'] for e in egress if not e.get('allowed'))
        lines.append('- egress: '+str(sum(allowed.values()))+' allowed ('+', '.join(h+' ×'+str(n) for h, n in sorted(allowed.items(), key=lambda kv: -kv[1])[:6])
                     +'); '+str(sum(refused.values()))+' refused ('+', '.join(h+' ×'+str(n) for h, n in sorted(refused.items(), key=lambda kv: -kv[1])[:6])+')')
    sizes = []
    if (root/'controller.jsonl').exists():
        for line in (root/'controller.jsonl').read_text().splitlines():
            try:
                sizes += [int(a.get('observation_chars') or 0) for a in json.loads(line).get('attempts') or [] if a.get('observation_chars')]
            except ValueError:
                continue
    if sizes:
        lines.append('- controller observation: '+str(sizes[-1]//4)+' tokens last, '+str(max(sizes)//4)+' max over '+str(len(sizes))+' steps')
    phase_rows = [ph for c in cycles for ph in (c.get('phases') or [])]
    if phase_rows:
        lines += ['', '## Phases']
        for ph in phase_rows:
            lines.append('- `'+str(ph.get('phase'))+'` '+('closed → '+str(ph.get('next') or 'all phases closed') if ph.get('closed')
                         else 'still open: '+'; '.join(str(x) for x in ph.get('problems') or [])[:300]))
    enabler_rows = [e for c in cycles for e in (c.get('enablers') or [])]
    if enabler_rows:
        lines += ['', '## Enablers']
        for e in enabler_rows:
            lines.append('- `'+str(e.get('enabler'))+'` '+str(e.get('status'))+(' → widget '+str(e['widget']) if e.get('widget') else '')
                         +(' (graduated by '+str(e['by'])+')' if e.get('by') else '')
                         +(' (error: '+str(e['error'])[:200]+')' if e.get('error') else ''))
    score = _score(project)
    if score:
        lines += ['', '## Score', score]
    blocked = _blocked(config, project)
    if blocked:
        lines += ['', '## Blocked', *('- `'+b['id']+'`: '+str(b.get('reason'))[:300] for b in blocked)]
    proposals = _proposals(project)
    if proposals:
        lines += ['', '## Proposals waiting for a person', *('- '+p for p in proposals)]
    path = root/'report.md'
    path.write_text('\n'.join(lines)+'\n')
    return path


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0)+1
    return out


def _refusals(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Every tool call a guard refused, per task, from the session journals: rejected shell commands (paths,
    patterns, oversized arguments) and refused writes to protected records."""
    out: dict[str, list[dict[str, Any]]] = {}
    for journal in sorted(root.glob('tasks/*/events/session.jsonl')):
        rows = []
        for line in journal.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('event_type') != 'tool_outcome':
                continue
            payload = event.get('payload') or {}
            status = payload.get('status')
            detail = str(payload.get('detail') or payload.get('error') or '')
            guard = status == 'rejected' or (status == 'error' and any(
                mark in detail for mark in ('a tool owns', 'refused', 'too long for one call', 'only creates files')))
            if guard:
                reason = detail.split('.')[0].removeprefix('refused: ')[:160] or str(payload.get('code') or status)
                rows.append(dict(reason=reason, at=event.get('created_at')))
        if rows:
            out[journal.parts[-3]] = rows
    return out


def _engine_version() -> str:
    try:
        return subprocess.run(['git', '-C', str(Path(__file__).resolve().parents[3]), 'rev-parse', '--short', 'HEAD'],
                              capture_output=True, text=True, timeout=5).stdout.strip() or '?'
    except (OSError, subprocess.SubprocessError):
        return '?'


def _score(project: Path) -> str:
    """The fixture scorer's last line, when the project came from a fixture; nothing otherwise."""
    key = project.parent/(project.name+'.key.json')
    if not key.exists():
        return ''
    try:
        from fixtures import score as scorer
        rows = scorer.score(project)
    except Exception:  # noqa: BLE001 — a scorer that cannot run is not the run's problem
        return ''
    ok = sum(1 for r in rows if r['ok'])
    wrong = [r for r in rows if not r['ok']]
    text = str(ok)+'/'+str(len(rows))+' checks pass'
    for r in wrong[:6]:
        text += '\n- '+r['kind']+' '+(str(r.get('id') or r.get('file') or r.get('need')))+(
            ': expected '+str(r.get('expected'))+', map has '+str(r.get('found')) if r['kind'] == 'truth' else
            ': '+str(r.get('note') or ('untraced '+str(r.get('untraced'))[:80])) if r['kind'] == 'trace' else '')
    return text


def _blocked(config: dict[str, Any], project: Path) -> list[dict[str, Any]]:
    try:
        from .worker import terra
        return [dict(id=t['id'], reason=t.get('blocked_reason')) for t in terra(config, project, 'route', 'status')['tasks']
                if t['status'] == 'blocked']
    except Exception:  # noqa: BLE001
        return []


def _proposals(project: Path) -> list[str]:
    path = project/'.terra'/'brief.json'
    if not path.exists():
        return []
    try:
        brief = json.loads(path.read_text())
    except ValueError:
        return []
    return [str(p.get('id'))+' '+str(p.get('summary') or '').split(' — evidence:')[0][:200]
            for p in brief.get('proposals') or [] if p.get('status') in (None, 'open', 'pending')]
