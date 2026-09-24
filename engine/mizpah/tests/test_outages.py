"""A transport failure is recorded with the role that hit it and the endpoint that did not answer."""
import json
from pathlib import Path

import pytest

from mizpah import controller, ops


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Every wait is recorded, none is slept; and the network is there unless a test says otherwise."""
    slept = []
    monkeypatch.setattr(ops.time, 'sleep', slept.append)
    monkeypatch.setattr(ops, 'network_reachable', lambda host, timeout=5.0: True)
    return slept


class _Health:
    def __init__(self, answers):
        self.answers = list(answers)

    def watch(self, spec, *, at_least, at_most):
        # The model looks up throughout: the whole backoff is waited (through ops.time.sleep, which tests record).
        if at_least:
            ops.time.sleep(at_least)
        return self.answers.pop(0) if self.answers else True

    def wait_for_network(self, host, *, patience_seconds=None):
        return True


def test_record_outage_names_role_and_endpoint(tmp_path: Path) -> None:
    ops.record_outage(tmp_path, 'controller', {'endpoint': {'base_url': 'http://127.0.0.1:58081'}}, ConnectionError('refused'), 1)
    ops.record_outage(tmp_path/'tasks'/'t1', 'worker',
                      {'provider': 'subscription', 'subscription': 'chatgpt', 'generation': {'model': 'gpt-5.6-luna'}}, 'timeout', 2)
    rows = [json.loads(l) for l in (tmp_path/'outages.jsonl').read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]['role'] == 'controller' and rows[0]['endpoint'] == 'http://127.0.0.1:58081' and rows[0]['error'] == 'refused'
    assert rows[0]['kind'] == 'server_down' and 'nothing is listening' in rows[0]['what']
    row = json.loads((tmp_path/'tasks'/'t1'/'outages.jsonl').read_text())
    assert row['role'] == 'worker' and row['endpoint'] == 'chatgpt/gpt-5.6-luna' and row['provider'] == 'subscription' and row['outage'] == 2
    assert row['kind'] == 'no_reply'
    ops.record_outage(tmp_path/'x', 'worker', {}, OSError('response exceeded the configured byte limit'), 1, task='write_sketch_sheet', turn=23,
                      action='the reply is discarded and the worker is asked again with a warning')
    big = json.loads((tmp_path/'x'/'outages.jsonl').read_text())
    assert big['kind'] == 'reply_too_big' and big['task'] == 'write_sketch_sheet' and big['turn'] == 23 and 'larger than the transport allows' in big['what']


def test_controller_outage_is_recorded_at_the_run_root(tmp_path: Path, monkeypatch) -> None:
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError
    calls = []

    def decide(client, config, system, user, **_):
        calls.append(1)
        if len(calls) == 1:
            raise ModelTransportError('gateway gone')
        return {'ok': True}

    monkeypatch.setattr(controller, 'decide', decide)
    config = {'mizpah': {'run_root': str(tmp_path)}, 'controller': {'endpoint': {'base_url': 'http://c:1'}}}
    out = controller.decide_through_outages(None, config, 's', 'u', health=_Health([True]))
    assert out == {'ok': True}
    row = json.loads((tmp_path/'outages.jsonl').read_text())
    assert row['role'] == 'controller' and row['endpoint'] == 'http://c:1' and 'gateway gone' in row['error']


def test_worker_outage_resumes_the_rest_of_the_burst(tmp_path: Path) -> None:
    """A torn call mid-burst does not start the burst over: the effort boundary stays where it was."""
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError
    from mizpah import worker

    class Session:
        def __init__(self):
            self.turns = 0
            self.asked = []
            self.torn = False

        def status(self):
            return dict(completed_worker_turns=self.turns, status='paused', phase='worker', pending_io=None)

        def run(self, *, maximum_worker_turns, stop_when=None):
            self.asked.append(maximum_worker_turns)
            if not self.torn:
                self.torn = True
                self.turns += 40
                raise ModelTransportError('tls torn')
            self.turns += maximum_worker_turns
            return self.status()

        def discard_pending(self):
            return None

    root = tmp_path/'tasks'/'t1'; root.mkdir(parents=True)
    session = Session()
    status = worker.run_through_outages(session, {'worker': {}}, root, maximum_worker_turns=100, health=_Health([True]))
    assert session.asked == [100, 60] and status['completed_worker_turns'] == 100


def test_worker_outage_with_the_network_down_is_waited_for_not_counted(tmp_path: Path, monkeypatch) -> None:
    """No route to the host: the torn call is discarded and asked again once the network is back; the count
    of outages (six in a row fail the task) does not move."""
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError
    from mizpah import worker, ops

    probes = iter([False, True])   # the first tear: no route; the second: the network is up
    monkeypatch.setattr(ops, 'network_reachable', lambda host, timeout=5.0: next(probes, True))
    monkeypatch.setattr(ops.time, 'sleep', lambda s: None)
    monkeypatch.setattr(ops, 'provider_host', lambda spec, config=None: 'api.example')

    class Session:
        def __init__(self):
            self.turns = 0
            self.torn = 0

        def status(self):
            return dict(completed_worker_turns=self.turns, status='paused', phase='worker', pending_io=None)

        def run(self, *, maximum_worker_turns, stop_when=None):
            if self.torn < 2:
                self.torn += 1
                self.turns += 5
                raise ModelTransportError('connection reset')
            self.turns += maximum_worker_turns
            return self.status()

        def discard_pending(self):
            return dict(kind='tool')

    root = tmp_path/'tasks'/'t1'; root.mkdir(parents=True)
    session = Session()
    status = worker.run_through_outages(session, {'worker': {}, 'mizpah': {}}, root, maximum_worker_turns=50, health=_Health([True]))
    rows = [json.loads(l) for l in (root/'outages.jsonl').read_text().splitlines()]
    assert status['completed_worker_turns'] == 50
    assert rows[0]['action'].startswith('the network is down') and rows[0]['outage'] == 0
    assert rows[1]['outage'] == 1   # the second tear, with the network up, is a real outage: counted


class _TearingSession:
    """Tears on the given run() calls (1-based); each run that is not torn completes `advance` turns first."""

    def __init__(self, tears, errors=None, advance=3):
        self.turns = 0
        self.calls = 0
        self.tears = set(tears)
        self.errors = errors or {}
        self.advance = advance

    def status(self):
        return dict(completed_worker_turns=self.turns, status='paused', phase='worker', pending_io=None)

    def run(self, *, maximum_worker_turns, stop_when=None):
        from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError
        self.calls += 1
        if self.calls in self.tears:
            self.turns += self.advance
            raise self.errors.get(self.calls) or ModelTransportError('SSLError: bad record mac')
        self.turns = max(self.turns, maximum_worker_turns)
        return self.status()

    def discard_pending(self):
        return None

    def interject(self, **_):
        self.interjected = True


def test_a_good_turn_between_drops_starts_a_fresh_streak(tmp_path: Path, no_waiting) -> None:
    """compose_piece_mid, 2026-09-22: three drops tens of turns apart climbed 15 → 30 → 60 s and were halfway to
    failing the task. Each drop after progress is the first of its own streak now."""
    from mizpah import worker
    root = tmp_path/'tasks'/'t1'; root.mkdir(parents=True)
    session = _TearingSession(tears=range(1, 9))
    worker.run_through_outages(session, {'worker': {}}, root, maximum_worker_turns=100, health=_Health([]))
    rows = [json.loads(l) for l in (root/'outages.jsonl').read_text().splitlines()]
    assert len(rows) == 8 and {r['outage'] for r in rows} == {1}
    assert all(1.5 <= s <= 2.5 for s in no_waiting)


def test_a_streak_without_progress_backs_off_then_gives_up_on_time(tmp_path: Path, monkeypatch, no_waiting) -> None:
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError
    from mizpah import worker
    root = tmp_path/'tasks'/'t1'; root.mkdir(parents=True)
    clock = [0.0]
    monkeypatch.setattr(ops.time, 'sleep', lambda s: (no_waiting.append(s), clock.__setitem__(0, clock[0]+s)))
    from cg.universal_failure_streak_patience_python.src import failure_streak_patience as fsp
    monkeypatch.setattr(fsp.time, 'monotonic', lambda: clock[0])
    session = _TearingSession(tears=range(1, 100), advance=0)
    config = {'worker': {}, 'mizpah': {'ops': {'patience_seconds': 600}}}
    with pytest.raises(ModelTransportError):
        worker.run_through_outages(session, config, root, maximum_worker_turns=100, health=_Health([]))
    rows = [json.loads(l) for l in (root/'outages.jsonl').read_text().splitlines()]
    assert [r['outage'] for r in rows] == list(range(1, len(rows)+1)) and len(rows) >= 7
    assert rows[-1]['action'].startswith('patience spent') and sum(no_waiting) <= 600
    assert no_waiting[:3] == pytest.approx([2, 5, 15], rel=0.21)


def test_a_malformed_request_is_not_asked_again(tmp_path: Path, no_waiting) -> None:
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError
    from mizpah import worker
    root = tmp_path/'tasks'/'t1'; root.mkdir(parents=True)
    session = _TearingSession(tears={1}, errors={1: ModelTransportError('HTTP status 400')})
    with pytest.raises(ModelTransportError):
        worker.run_through_outages(session, {'worker': {}}, root, maximum_worker_turns=10, health=_Health([]))
    rows = [json.loads(l) for l in (root/'outages.jsonl').read_text().splitlines()]
    assert len(rows) == 1 and rows[0]['kind'] == 'bad_request' and 'cannot fix' in rows[0]['action']
    assert session.calls == 1 and no_waiting == []


def test_a_refused_credential_is_not_asked_again() -> None:
    assert ops.outage_kind('HTTP status 401')[0] == 'unauthorized'
    assert ops.outage_kind('RefreshFailed: invalid_grant')[0] == 'unauthorized'
    assert ops.failure_class('unauthorized') == 'fatal'


def test_a_rate_limit_waits_what_the_server_asked(tmp_path: Path, no_waiting) -> None:
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError, WireResponse
    from mizpah import worker
    root = tmp_path/'tasks'/'t1'; root.mkdir(parents=True)
    limited = ModelTransportError('HTTP status 429', WireResponse(429, '', 0.1, evidence={'retry_after': '42'}))
    session = _TearingSession(tears={1}, errors={1: limited})
    worker.run_through_outages(session, {'worker': {}}, root, maximum_worker_turns=10, health=_Health([]))
    row = json.loads((root/'outages.jsonl').read_text())
    assert row['kind'] == 'rate_limited' and no_waiting == [42.0] and 'the server asked' in row['action']


def test_a_reply_too_big_is_asked_again_at_once(tmp_path: Path, no_waiting) -> None:
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError
    from mizpah import worker
    root = tmp_path/'tasks'/'t1'; root.mkdir(parents=True)
    session = _TearingSession(tears={1}, errors={1: ModelTransportError('OSError: response exceeded the configured byte limit')})
    worker.run_through_outages(session, {'worker': {}}, root, maximum_worker_turns=10, health=_Health([]))
    assert no_waiting == [] and 'at once' in json.loads((root/'outages.jsonl').read_text())['action']


def test_retry_after_reads_seconds_and_dates() -> None:
    from email.utils import formatdate
    import time as _time
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError, WireResponse
    assert ops.retry_after(ModelTransportError('x', WireResponse(429, '', 0, evidence={'retry_after': '7'}))) == 7.0
    dated = ModelTransportError('x', WireResponse(429, '', 0, evidence={'retry_after': formatdate(_time.time()+30, usegmt=True)}))
    assert 25 <= ops.retry_after(dated) <= 31
    assert ops.retry_after(ModelTransportError('x')) is None


def test_each_seat_gets_its_patience_and_config_can_change_it() -> None:
    assert ops.streak({}).policy.patience_seconds == 1800
    assert ops.streak({}, role='deputy').policy.patience_seconds == 180
    assert ops.streak({'mizpah': {'ops': {'patience_seconds': 7200}}}).policy.patience_seconds == 7200


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ops.time, 'time', lambda: now[0])
    monkeypatch.setattr(ops.time, 'sleep', lambda s: now.__setitem__(0, now[0]+s))
    monkeypatch.setattr(ops, 'notify', lambda *a, **k: None)
    return now


def _hosted(monkeypatch, answers):
    """A hosted seat whose signed model list gives `answers` in turn (None: answered), then answers for good."""
    from mizpah import providers

    class Profile:
        models_url = 'https://api.example/v1/models'

    class Session:
        profile = Profile()
        reads = 0

        def list_model_info(self, *, timeout_seconds=None):
            self.reads += 1
            answer = answers.pop(0) if answers else None
            if answer is not None:
                raise answer
            return []

    session = Session()
    monkeypatch.setattr(providers, 'session_for', lambda name, config=None, open_browser=True: session)
    return session, {'provider': 'subscription', 'subscription': 'example'}


def test_a_model_that_comes_back_is_asked_again_within_seconds_not_at_the_end_of_the_backoff(tmp_path, monkeypatch, clock) -> None:
    """Down at the drop, back 20 s later: the trace resumes by 25 s, though the backoff said a minute."""
    session, spec = _hosted(monkeypatch, [LookupError('model list http 502: bad gateway')]*4)
    health = ops.Health({'mizpah': {}}, tmp_path)
    assert health.watch(spec, at_least=60, at_most=1800)
    assert clock[0]-1000.0 == 20 and session.reads == 5
    events = [json.loads(l)['event'] for l in (tmp_path/'health.jsonl').read_text().splitlines()]
    assert events == ['model_down', 'model_back']


def test_a_model_that_looks_up_waits_the_backoff(tmp_path, monkeypatch, clock) -> None:
    """A blip, or completions failing behind a list that answers: the re-send is paced by the backoff."""
    _, spec = _hosted(monkeypatch, [])
    assert ops.Health({'mizpah': {}}, tmp_path).watch(spec, at_least=30, at_most=1800)
    assert clock[0]-1000.0 == 30


def test_a_model_down_for_the_whole_patience_fails_the_step(tmp_path, monkeypatch, clock) -> None:
    _, spec = _hosted(monkeypatch, [LookupError('model list http 500: oops')]*1000)
    assert not ops.Health({'mizpah': {}}, tmp_path).watch(spec, at_least=5, at_most=60)
    assert clock[0]-1000.0 == 60


def test_a_local_server_is_watched_on_health_and_restarted_when_down_long_enough(tmp_path, monkeypatch, clock) -> None:
    answers = [False]*14+[True]
    monkeypatch.setattr(ops, 'model_up', lambda base_url, timeout=5.0: answers.pop(0))
    runs = []
    monkeypatch.setattr(ops.subprocess, 'run', lambda cmd, **k: (runs.append(cmd), type('R', (), dict(stdout='active', returncode=0, stderr=''))())[1])
    health = ops.Health({'mizpah': {}, 'worker': {'model_unit': 'llama.service'}}, tmp_path)
    assert health.watch({'endpoint': {'base_url': 'http://127.0.0.1:1'}}, at_least=120, at_most=1800)
    assert clock[0]-1000.0 == 70 and ['systemctl', '--user', 'restart', 'llama.service'] in runs


def test_the_backoff_is_capped_at_a_minute_by_default() -> None:
    policy = ops.streak({}).policy
    assert policy.cap_seconds == 60 and max(policy.delay(n) for n in range(1, 40)) == 60


def test_the_provider_check_reads_the_signed_list_and_falls_back_to_the_front_door() -> None:
    class Signed:
        def __init__(self, outcome):
            self.outcome = outcome

        def list_model_info(self, *, timeout_seconds=None):
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return []

        def reachable(self, *, timeout_seconds=5.0):
            return True, 'http 401'

    from cg.bp_subscription_provider_session_python.src.subscription_provider_session import NotSignedIn

    assert ops.provider_answers(Signed(None)) == (True, 'model list answered')
    assert ops.provider_answers(Signed(LookupError('model list http 403: nope')))[0]      # live, refusing
    assert not ops.provider_answers(Signed(LookupError('model list http 503: busy')))[0]
    assert not ops.provider_answers(Signed(LookupError('model list is not JSON')))[0]     # a maintenance page
    assert not ops.provider_answers(Signed(TimeoutError('timed out')))[0]
    assert ops.provider_answers(Signed(NotSignedIn('no credential'))) == (True, 'http 401')
