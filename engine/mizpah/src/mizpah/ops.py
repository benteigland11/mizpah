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
import re
from pathlib import Path
import shutil
import subprocess
import socket
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from . import layout


def settings(config: dict[str, Any]) -> dict[str, Any]:
    # A local server's unit rides on the seat that uses it (`models.worker.model_unit` in the project), so one
    # engine config serves every model; an explicit ops.model_unit still wins.
    seat_unit = (config.get('worker') or {}).get('model_unit')
    return dict(model_unit=seat_unit, restart_after_seconds=60, restart_cooldown_seconds=600, disk_high_percent=92,
                notify_command=None, network_patience_seconds=3600, patience_seconds=1800,
                deputy_patience_seconds=180, retry_cap_seconds=60) | dict((config.get('mizpah') or {}).get('ops') or {})


# ---------------------------------------------------------------- health

def model_label(spec: dict[str, Any]) -> str:
    """How a report names the model: a local server by its address, a subscription by profile and model."""
    if spec.get('provider') == 'subscription':
        return str(spec.get('subscription'))+'/'+str((spec.get('generation') or {}).get('model') or '?')
    return str((spec.get('endpoint') or {}).get('base_url') or '?')


def outage_kind(error: BaseException | str) -> tuple[str, str]:
    """(kind, what happened in plain words) for a transport failure. The exception text is for the log; the
    anomaly the person reads says what went wrong ('the reply was too big', not 'OSError')."""
    text = str(error)
    low = text.lower()
    if 'exceeded the configured byte limit' in low:
        return 'reply_too_big', 'its reply was larger than the transport allows and was discarded'
    if 'timed out' in low or 'timeout' in low:
        return 'no_reply', 'it gave no reply before the request timed out'
    if 'name resolution' in low or 'nodename' in low or 'getaddrinfo' in low:
        return 'dns', 'its address could not be resolved (DNS)'
    if 'ssl' in low or 'tls' in low or 'record mac' in low:
        return 'tls', 'the encrypted connection failed mid-reply (TLS)'
    if 'incompleteread' in low or 'remote end closed' in low or 'connection reset' in low or 'broken pipe' in low:
        return 'connection_dropped', 'the connection dropped mid-reply'
    if 'connection refused' in low or 'errno 111' in low or low.strip() in ('refused', 'connectionrefusederror'):
        return 'server_down', 'nothing is listening at its address'
    if 'http status 5' in low or ' 502' in low or ' 503' in low or ' 504' in low:
        return 'server_error', 'the server answered with an error'
    if ('http status 401' in low or 'http status 403' in low or 'refreshfailed' in low or 'notsignedin' in low
            or 'quarantinedcredential' in low):
        return 'unauthorized', 'the provider refused our credential (sign in again)'
    if 'http status 4' in low and 'http status 429' not in low:
        return 'bad_request', 'the server refused the request as malformed (a config problem on our side, not an outage)'
    if 'http status 429' in low or 'rate limit' in low:
        return 'rate_limited', 'the server refused for rate limiting'
    if 'uncertain' in low:
        return 'uncertain', 'the exchange failed with the outcome unknown'
    return 'transport', 'the transport failed'


def failure_class(kind: str) -> str:
    """How a patience streak treats an outage kind: a reply too big says nothing about the server (ask again at
    once); a malformed request or a refused credential cannot be fixed by asking again; the rest pass."""
    if kind == 'reply_too_big':
        return 'immediate'
    if kind in ('bad_request', 'unauthorized'):
        return 'fatal'
    if kind == 'rate_limited':
        return 'rate_limited'
    return 'transient'


def retry_after(error: BaseException) -> float | None:
    """The server's Retry-After, in seconds, when the failed response carried one (seconds or an HTTP date)."""
    seen = error
    while seen is not None:
        response = getattr(seen, 'response', None)
        value = ((getattr(response, 'evidence', None) or {}).get('retry_after') if response is not None else None)
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                from email.utils import parsedate_to_datetime
                try:
                    return max(0.0, parsedate_to_datetime(str(value)).timestamp()-time.time())
                except (TypeError, ValueError):
                    return None
        seen = seen.__cause__
    return None


def streak(config: dict[str, Any], *, role: str = 'worker') -> Any:
    """A patience streak for one seat: the failures since its last good exchange, spent against a time budget
    (`ops.patience_seconds`, half an hour; the Deputy, whom a person is waiting on, `ops.deputy_patience_seconds`).
    It replaced a count of six per burst that never reset on success, so three drops an hour apart on
    compose_piece_mid (2026-09-22) had climbed to a minute's wait and were halfway to failing the task."""
    from cg.universal_failure_streak_patience_python.src.failure_streak_patience import FailureStreak, PatiencePolicy
    knobs = settings(config)
    patience = knobs['deputy_patience_seconds'] if role == 'deputy' else knobs['patience_seconds']
    return FailureStreak(PatiencePolicy(patience_seconds=float(patience), cap_seconds=float(knobs['retry_cap_seconds'])))


def ride_out(failures: Any, error: BaseException, *, role: str, spec: dict[str, Any], config: dict[str, Any],
             root: Path, health: Any, task: str | None = None, turn: int | None = None) -> bool:
    """One failed exchange: record it, wait as its kind deserves, and say whether to ask again (True) or give up.

    No route to the host is the network's outage, waited for and not counted. Otherwise the streak decides:
    a transient drop waits 2 s, 5 s, 15 s, 30 s, then a minute (`ops.retry_cap_seconds`) until the patience is
    spent; a rate limit waits what the server asked; a malformed request or a refused credential stops at once.
    The model is watched through every wait (`Health.watch`), so one that was down is asked again within seconds
    of answering."""
    kind, _ = outage_kind(error)
    host = provider_host(spec, config)
    if kind != 'reply_too_big' and not network_reachable(host):
        record_outage(root, role, spec, error, failures.attempts, task=task, turn=turn,
                      action='the network is down; waiting for it, not counted')
        return bool(health.wait_for_network(host))
    decision = failures.failed(failure_class(kind), retry_after_seconds=retry_after(error))
    if not decision.retry:
        action = ('asking again cannot fix this: the step fails' if decision.reason == 'not retryable'
                  else 'patience spent after '+str(int(decision.elapsed_seconds))+' s and '+str(decision.attempt)+' tries: the step fails')
    elif decision.delay_seconds == 0:
        action = 'asked again at once'
    else:
        action = ('asked again after '+str(round(decision.delay_seconds, 1))+' s ('+decision.reason
                  +'), or as soon as the model answers again if it is down')
    record_outage(root, role, spec, error, decision.attempt, task=task, turn=turn,
                  waited_seconds=decision.delay_seconds or None, action=action)
    if not decision.retry:
        return False
    if kind == 'reply_too_big':
        return True
    left = max(decision.delay_seconds, failures.policy.patience_seconds-failures.elapsed_seconds)
    if not health.watch(spec, at_least=decision.delay_seconds, at_most=left):
        record_outage(root, role, spec, error, decision.attempt, task=task, turn=turn,
                      action='the model did not answer again within the patience: the step fails')
        return False
    return True


def record_outage(root: Path, role: str, spec: dict[str, Any], error: BaseException | str, outage: int, *,
                  task: str | None = None, turn: int | None = None, waited_seconds: float | None = None,
                  action: str | None = None) -> None:
    """One line per transport failure: who hit it (role, endpoint), what happened (kind, in words), where the work
    stood (task, turn), how long the loop waited, and what it did about it. The anomaly the app shows is this
    line; it said 'OSError: response exceeded the configured byte limit' before, which reads as a network
    fault when it was the model typing a 16 MB drawing (2026-09-20)."""
    root.mkdir(parents=True, exist_ok=True)
    kind, what = outage_kind(error)
    (root/'outages.jsonl').open('a').write(json.dumps(dict(
        at=time.time(), role=role, endpoint=model_label(spec), provider=spec.get('provider') or 'llama',
        kind=kind, what=what, task=task, turn=turn, waited_seconds=round(waited_seconds, 1) if waited_seconds else None,
        action=action, error=str(error)[:200], outage=outage))+'\n')


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


def provider_host(spec: dict[str, Any], config: dict[str, Any] | None = None) -> str | None:
    """The host a subscription seat's calls cross the network to (its profile's API host); None for a local
    endpoint, whose being down is the server's matter (`wait_for_model` restarts its unit), not the network's."""
    if (spec.get('endpoint') or {}).get('base_url') or spec.get('provider') != 'subscription' or not spec.get('subscription'):
        return None
    try:
        from . import providers
        base = providers.registry(config, only=spec['subscription']).get(spec['subscription']).api_base_url
    except Exception:  # noqa: BLE001 — no profile, no host to probe
        return None
    return urllib.parse.urlsplit(base).hostname


def network_reachable(host: str | None, timeout: float = 5.0) -> bool:
    """A TCP connect to the host's HTTPS port: the network is there, whatever the service says."""
    if not host:
        return True
    try:
        socket.create_connection((host, 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def provider_answers(session: Any, *, timeout_seconds: float = 10.0) -> tuple[bool, str]:
    """(up, why) from the model list read as the seat reads it, signed in: the answer comes from behind the
    provider's auth layer, as a completion's does, where an unsigned read is refused at the front door with a
    401 that says only that the host is there. A 4xx on the signed read is a live service refusing (the
    credential is the next call's matter, not an outage); a 5xx, a socket error or a page that is not the list
    is down. With no usable credential the unsigned read is all there is."""
    try:
        session.list_model_info(timeout_seconds=timeout_seconds)
        return True, 'model list answered'
    except LookupError as error:
        if not str(error).startswith('model list'):   # NotSignedIn is a LookupError too: no credential, not an answer
            return session.reachable(timeout_seconds=timeout_seconds)
        code = re.search(r'http (\d{3})', str(error))
        if code and int(code.group(1)) < 500:
            return True, str(error)[:120]
        return False, str(error)[:120]
    except OSError as error:   # URLError and TimeoutError are OSErrors
        return False, type(error).__name__+': '+str(error)[:120]
    except Exception:  # noqa: BLE001 — not signed in, quarantined, refresh refused: the front door is all we can ask
        return session.reachable(timeout_seconds=timeout_seconds)


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

    def watch(self, spec: dict[str, Any], *, at_least: float, at_most: float, poll_seconds: float = 5.0) -> bool:
        """The wait before a torn call is asked again, watching the model the whole time.

        While the check says the model is up (a blip, or a failure the check cannot see) the wait is `at_least`,
        the streak's backoff, so re-sent prompts stay paced. Once the check has seen it down, the wait ends the
        moment it answers again: a trace resumes within `poll_seconds` of the model coming back, not at the end
        of a five-minute sleep (the old ladder slept 15 s to 240 s blind, then looked). False when it is still
        down at `at_most`. The check spends no tokens: /health for a local server (whose unit is restarted once
        it has been down long enough), the signed model list for a hosted one, a TCP connect otherwise."""
        check = self._check(spec)
        started = time.time()
        down_since: float | None = None
        while True:
            elapsed = time.time()-started
            up = check()
            if up and (down_since is not None or elapsed >= at_least):
                if down_since is not None:
                    self._record(event='model_back', endpoint=model_label(spec), after=round(time.time()-down_since))
                return True
            if not up:
                if down_since is None:
                    down_since = time.time()
                    self._record(event='model_down', endpoint=model_label(spec))
                self._restart_if_due(spec, down_since)
            if elapsed >= at_most:
                self._record(event='model_still_down', endpoint=model_label(spec), waited=round(elapsed))
                notify(self.config, self.root, 'model down', model_label(spec)+' did not answer for '+str(int(elapsed))+' s')
                return False
            time.sleep(max(0.0, min(poll_seconds, (at_least-elapsed) if up else poll_seconds, at_most-elapsed)))

    def _check(self, spec: dict[str, Any]) -> Any:
        base_url = (spec.get('endpoint') or {}).get('base_url')
        if base_url:
            return lambda: model_up(base_url)
        host = provider_host(spec, self.config)
        session = None
        if spec.get('provider') == 'subscription' and spec.get('subscription'):
            try:
                from . import providers
                session = providers.session_for(spec['subscription'], self.config, open_browser=False)
                session = session if session.profile.models_url else None
            except Exception:  # noqa: BLE001 — no profile, nothing but the host to watch
                session = None
        return lambda: network_reachable(host) and (session is None or provider_answers(session)[0])

    def _restart_if_due(self, spec: dict[str, Any], down_since: float) -> None:
        unit = self.ops['model_unit']
        if not ((spec.get('endpoint') or {}).get('base_url') and unit
                and time.time()-down_since >= self.ops['restart_after_seconds']
                and time.time()-self.last_restart >= self.ops['restart_cooldown_seconds']):
            return
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

    def wait_for_network(self, host: str | None, *, patience_seconds: float | None = None) -> bool:
        """True at once when the host answers a connect; otherwise wait for the network to come back, up to
        `network_patience_seconds` (an hour by default), and say so once. A dropped network is not an outage of
        the model: on 2026-09-22 a few minutes without a route cost the loop five counted outages and the task."""
        if network_reachable(host):
            return True
        patience = self.ops['network_patience_seconds'] if patience_seconds is None else patience_seconds
        down_since = time.time()
        self._record(event='network_down', host=host)
        notify(self.config, self.root, 'network down', 'no route to '+str(host)+'; waiting up to '+str(int(patience))+' s')
        while time.time()-down_since <= patience:
            time.sleep(5)
            if network_reachable(host):
                self._record(event='network_back', host=host, after=round(time.time()-down_since))
                notify(self.config, self.root, 'network back', str(host)+' answers after '+str(int(time.time()-down_since))+' s')
                return True
        self._record(event='network_still_down', host=host, waited=patience)
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


def journal_usage(journal: Path) -> dict[str, Any]:
    """Tokens one session journal consumed: calls, prompt, completion, cached and the cache share."""
    out = dict(calls=0, prompt=0, completion=0, cached=0)
    try:
        with journal.open() as handle:
            for line in handle:
                if '"model_response"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                payload = event.get('payload') or {}
                if event.get('event_type') != 'model_response' or payload.get('status') != 200:
                    continue
                body = payload.get('body')
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except ValueError:
                        continue
                u = (body or {}).get('usage') or {}
                if not u:
                    continue
                out['calls'] += 1
                out['prompt'] += int(u.get('prompt_tokens') or 0)
                out['completion'] += int(u.get('completion_tokens') or 0)
                out['cached'] += int((u.get('prompt_tokens_details') or {}).get('cached_tokens') or 0)
    except OSError:
        pass
    out['cache_share'] = round(out['cached']/out['prompt'], 3) if out['prompt'] else None
    return out


def usage_summary(root: Path) -> list[dict[str, Any]]:
    """Per (role, model): calls, prompt/completion/cached tokens and the cache share — the worker from its
    session journals, the controller from its steps. What a person reads on the notice of completion."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def add(role: str, model: Any, prompt: Any, completion: Any, cached: Any) -> None:
        key = (role, str(model or '?'))
        r = rows.setdefault(key, dict(role=role, model=str(model or '?'), calls=0, prompt=0, completion=0, cached=0))
        r['calls'] += 1
        r['prompt'] += int(prompt or 0)
        r['completion'] += int(completion or 0)
        r['cached'] += int(cached or 0)
    for step in _read_jsonl(Path(root)/'controller.jsonl'):
        for u in step.get('usage') or []:
            add('controller', u.get('model'), u.get('prompt'), u.get('completion'), u.get('cached'))
    for journal in sorted(Path(root).glob('tasks/*/events/session.jsonl')):
        try:
            with journal.open() as handle:
                for line in handle:
                    if '"model_response"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get('event_type') != 'model_response':
                        continue
                    payload = event.get('payload') or {}
                    if payload.get('status') != 200:
                        continue
                    body = payload.get('body')
                    if isinstance(body, str):
                        try:
                            body = json.loads(body)
                        except ValueError:
                            continue
                    u = (body or {}).get('usage') or {}
                    if not u:
                        continue
                    role = 'worker' if payload.get('purpose') in ('worker', 'handoff') else str(payload.get('purpose') or 'worker')
                    add(role, (body or {}).get('model'), u.get('prompt_tokens'), u.get('completion_tokens'),
                        (u.get('prompt_tokens_details') or {}).get('cached_tokens'))
        except OSError:
            continue
    out = []
    for r in rows.values():
        r['cache_share'] = round(r['cached']/r['prompt'], 3) if r['prompt'] else None
        out.append(r)
    return sorted(out, key=lambda r: (r['role'] != 'controller', r['role'], r['model']))


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

def jsonl_lines(path: Path) -> list[str]:
    """The lines of a JSONL file, split on newline only. `str.splitlines` also splits on U+0085, U+2028, U+001C…
    which json.dumps(ensure_ascii=False) leaves raw inside a string: a worker that read an mp3 as text put one in
    a tool result, the record split in two, and every reader of that journal failed (render, 2026-09-22)."""
    if not path.exists():
        return []
    return [line for line in path.read_text(errors='replace').split('\n') if line.strip()]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in jsonl_lines(path):
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
    usage = usage_summary(root)
    if usage:
        lines.append('Models: '+'; '.join(r['role']+' '+r['model']+' — '+str(r['calls'])+' calls, '+f"{r['prompt']:,} in / {r['completion']:,} out"
                                        +(', cache '+str(round(r['cache_share']*100))+'%' if r['cache_share'] is not None else '') for r in usage))
        lines.append('')
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
        for line in jsonl_lines(root/'controller.jsonl'):
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


_REFUSALS_CACHE: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}


def _refusals(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Every tool call a guard refused, per task, from the session journals: rejected shell commands (paths,
    patterns, oversized arguments) and refused writes to protected records. Cached per journal by (mtime,
    size): the report is rebuilt after every task and eval, and re-parsing every finished task's journal each
    time was gigabytes of JSON per report on a long run."""
    out: dict[str, list[dict[str, Any]]] = {}
    for journal in sorted(root.glob('tasks/*/events/session.jsonl')):
        stat = journal.stat()
        stamp = (int(stat.st_mtime), stat.st_size)
        cached = _REFUSALS_CACHE.get(str(journal))
        if cached and cached[0] == stamp:
            if cached[1]:
                out[journal.parts[-3]] = cached[1]
            continue
        rows = []
        for line in jsonl_lines(journal):
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
        _REFUSALS_CACHE[str(journal)] = (stamp, rows)
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
    path = project/layout.dirname(project)/'brief.json'
    if not path.exists():
        return []
    try:
        brief = json.loads(path.read_text())
    except ValueError:
        return []
    return [str(p.get('id'))+' '+str(p.get('summary') or '').split(' — evidence:')[0][:200]
            for p in brief.get('proposals') or [] if p.get('status') in (None, 'open', 'pending')]
