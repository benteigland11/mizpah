"""A transport failure is recorded with the role that hit it and the endpoint that did not answer."""
import json
from pathlib import Path

from mizpah import controller, ops


class _Health:
    def __init__(self, answers):
        self.answers = list(answers)

    def wait_for_model(self, base_url, *, wait_seconds, attempt=1):
        return self.answers.pop(0)


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
