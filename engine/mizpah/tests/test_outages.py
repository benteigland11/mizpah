"""A transport failure is recorded with the role that hit it and the endpoint that did not answer."""
import json
from pathlib import Path

from mizpah import controller, ops


class _Health:
    def __init__(self, answers):
        self.answers = list(answers)

    def wait_for_model(self, base_url, *, wait_seconds):
        return self.answers.pop(0)


def test_record_outage_names_role_and_endpoint(tmp_path: Path) -> None:
    ops.record_outage(tmp_path, 'controller', {'endpoint': {'base_url': 'http://127.0.0.1:58081'}}, ConnectionError('refused'), 1)
    ops.record_outage(tmp_path/'tasks'/'t1', 'worker',
                      {'provider': 'subscription', 'subscription': 'chatgpt', 'generation': {'model': 'gpt-5.6-luna'}}, 'timeout', 2)
    rows = [json.loads(l) for l in (tmp_path/'outages.jsonl').read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]['role'] == 'controller' and rows[0]['endpoint'] == 'http://127.0.0.1:58081' and rows[0]['error'] == 'refused'
    row = json.loads((tmp_path/'tasks'/'t1'/'outages.jsonl').read_text())
    assert row['role'] == 'worker' and row['endpoint'] == 'chatgpt/gpt-5.6-luna' and row['provider'] == 'subscription' and row['outage'] == 2


def test_controller_outage_is_recorded_at_the_run_root(tmp_path: Path, monkeypatch) -> None:
    from cg.backend_persistent_model_session_python.src.persistent_model_session import ModelTransportError
    calls = []

    def decide(client, config, system, user):
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
