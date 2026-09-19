from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.focused_agent_session import (
    ControllerSettings, EndpointConfig, FocusedSession, ModelClient, ReviewPolicy,
    SessionEventLog, SessionPolicy, SessionSettings, ShellConfig, ShellLimits,
    ShellResult, UnresolvedOperation, ReviewLimitExceeded, WireResponse, read_workspace_file, workspace_files, write_workspace_file,
    GenerationRetryExceeded, llama_model_client, worker_tools,
)


def response(content='Done', calls=None, finish=None):
    message = dict(role='assistant', content=content, reasoning_content='Internal reasoning retained in audit.')
    if calls:
        message['tool_calls'] = calls
    return dict(choices=[dict(message=message, finish_reason=finish or ('tool_calls' if calls else 'stop'))],
                usage=dict(prompt_tokens=50, completion_tokens=20))


def tool(identity, command):
    return dict(id=identity, type='function', function=dict(name='bash', arguments=json.dumps(dict(command=command))))


def review(correction='None', completed=''):
    return response(json.dumps(dict(correction=correction,
        evidence='' if correction == 'None' else 'Turn 1 showed a relevant result.',
        warrant='' if correction == 'None' else 'The reference requires a supported report.')))


class WorkerTransport:
    def __init__(self, total=42, rollover=True, batch=False):
        self.total = total
        self.rollover = rollover
        self.batch = batch
        self.turns = 0
        self.requests = []
        self.handoffs = 0
        self.oversize = False

    def __call__(self, path, payload, **kwargs):
        if path == '/template':
            count = 600 if self.rollover and 'tools' in payload and len(payload['messages']) > 9 else 100
            if self.oversize:
                count = 3000
            value = dict(prompt='x'*count)
        elif path == '/tokenize':
            value = dict(tokens=[1]*len(payload['content']))
        else:
            self.requests.append(deepcopy(payload))
            if 'tools' not in payload:
                self.handoffs += 1
                value = response('worker-owned handoff '+str(self.turns))
            else:
                self.turns += 1
                calls = [tool('call-'+str(self.turns), 'write-'+str(self.turns))] if self.turns <= self.total else None
                if calls and self.batch:
                    calls.append(tool('call-extra-'+str(self.turns), 'extra-'+str(self.turns)))
                value = response('Working '+str(self.turns) if calls else 'Verified final report.', calls)
        return WireResponse(200, json.dumps(value), 0)


class ReviewTransport:
    def __init__(self):
        self.requests = []
        self.inputs = []
        self.malformed = False
        self.too_large = False
        self.replacement_on_final = False
        self.inspect = False
        self.concern = None

    def __call__(self, path, payload, **kwargs):
        if path == '/template':
            value = dict(prompt='x'*(100000 if self.too_large else 100))
        elif path == '/tokenize':
            value = dict(tokens=[1]*len(payload['content']))
        else:
            self.requests.append(deepcopy(payload))
            envelope = json.loads(payload['messages'][1]['content'])
            first_call = len(payload['messages']) == 2
            if first_call:
                self.inputs.append(envelope)
            if self.malformed:
                value = response('{"correction":"None"}')
            elif first_call:
                previous = envelope['project_document']['text']
                old = '' if not previous else 'Current: Observed through turn '+str(envelope['last_review_turn'])
                new = 'Current: Observed through turn '+str(envelope['completed_turns'])
                if not previous:
                    new = ('# Project\nPRIVATE_PROJECT_DOCUMENT\n'
                           '- [x] Explore: initial evidence at turn 1.\n'
                           '- [ ] Implement and verify\n- [ ] Deliver\n'+new)
                calls = [dict(id='edit-'+str(envelope['completed_turns']), type='function',
                    function=dict(name='project_edit', arguments=json.dumps(dict(
                        expected_revision=envelope['project_document']['revision'], old_text=old, new_text=new))))]
                if self.inspect and envelope['completed_turns'] == 21:
                    if self.concern:
                        calls.append(dict(id='concern-21', type='function', function=dict(name='investigate', arguments=json.dumps(dict(concern=self.concern)))))
                    for name, args in [
                        ('workspace_list', dict(prefix='', offset=0)),
                        ('workspace_read', dict(path='result.txt', offset=0)),
                        ('history_read', dict(start_turn=2, end_turn=2, offset=0)),
                        ('run_check', dict(command='check-mutates-copy')),
                        ('project_read', dict(offset=0)),
                    ]:
                        calls.append(dict(id=name+'-21', type='function', function=dict(name=name, arguments=json.dumps(args))))
                value = response('Inspect evidence and update the project document.', calls)
            else:
                change = ('Keep the public format.' if len(self.inputs) == 1 else 'None')
                if envelope['boundary'] == 'completion' and self.replacement_on_final:
                    change = 'Verify the missing result.'
                    self.replacement_on_final = False
                value = review(change)
        return WireResponse(200, json.dumps(value), 0)


class FixtureShell:
    def __init__(self, root):
        self.config = ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/usr', str(root),
            ShellLimits(1000000, 1000000, 1000000, 10000, 2000, 20, 100, 5, 2, 1000))
        self.calls = []
        self.interrupt = False

    def run(self, command, workspace=b''):
        self.calls.append(command)
        if self.interrupt:
            raise RuntimeError('Interrupted after executing the command')
        workspace = write_workspace_file(workspace, 'result.txt', command.encode(), byte_limit=1000000, file_limit=1000)
        return ShellResult('completed', 0, 'Recorded '+command, '', False, False, workspace, ('result.txt',), 0)


def setup(tmp_path, *, total=42, enabled=True, rollover=True, batch=False):
    worker_transport = WorkerTransport(total, rollover, batch)
    review_transport = ReviewTransport()
    endpoint = EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True)
    worker = ModelClient(endpoint, transport=worker_transport)
    controller = ModelClient(endpoint, transport=review_transport)
    shell = FixtureShell(tmp_path)
    settings = SessionSettings('Investigate the workspace and produce a report.', 'You operate the shell.',
        'PRIVATE_REFERENCE_SENTINEL: produce a supported report.' if enabled else None,
        dict(model='worker'), SessionPolicy(2000, 500, 100, 100, 2, 2000,
            'Prepare your own handoff.', 'Resume your own work.', {}),
        ReviewPolicy(20, 10, 1, 10000, 2000, 200000),
        ControllerSettings('Review direction and maintain the project document.', dict(model='controller'), 20000, 1000, 8, 12, 24000) if enabled else None,
        True, 'Controller guidance:')
    return settings, worker, shell, controller, worker_transport, review_transport


class RejectingTransport:
    def __init__(self, original, count=1, handoff_only=False, uncertain=False):
        self.original = original
        self.remaining = count
        self.handoff_only = handoff_only
        self.uncertain = uncertain
        self.requests = []

    def __call__(self, path, payload, **kwargs):
        if path == '/complete':
            self.requests.append(deepcopy(payload))
            if self.remaining and (not self.handoff_only or 'tools' not in payload):
                self.remaining -= 1
                return WireResponse(200, '', .25, 'cancelled',
                    'incomplete_stream' if self.uncertain else 'repetition_detected',
                    dict(upstream_closed=not self.uncertain, raw_event_tail='UNTRUSTED_PARTIAL_CALL'),
                    not self.uncertain)
        return self.original(path, payload, **kwargs)


def test_cancelled_worker_recovers_after_reopen_without_partial_tools(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1, enabled=False, rollover=False)
    settings = replace(settings, maximum_generation_retries=2)
    transport = RejectingTransport(wt)
    worker = ModelClient(worker.config, transport=transport)
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell)
    before = session.session.export_state()
    status = session.step()
    assert status['completed_worker_turns'] == 0 and status['pending_io'] is None
    assert status['generation_rejections'] == {'worker':1}
    assert session.session.export_state() == before and shell.calls == []
    reopened = FocusedSession.open(root, worker=worker, shell=shell)
    assert reopened.run()['status'] == 'complete'
    assert shell.calls == ['write-1']
    assert 'cancelled after sustained repetition' in transport.requests[1]['messages'][-1]['content']
    assert 'UNTRUSTED_PARTIAL_CALL' not in json.dumps(transport.requests)
    events = SessionEventLog(root/'events').read_strict('session')
    rejected = [e.payload for e in events if e.event_type == 'generation_rejected']
    assert len(rejected) == 1 and rejected[0]['tools_executed'] is False
    assert rejected[0]['elapsed_seconds'] == .25


def test_recovery_budget_survives_reopen_and_stops_after_exact_attempt_count(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, enabled=False)
    settings = replace(settings, maximum_generation_retries=2)
    transport = RejectingTransport(wt, count=99)
    worker = ModelClient(worker.config, transport=transport)
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell)
    session.step()
    session = FocusedSession.open(root, worker=worker, shell=shell)
    session.step()
    with pytest.raises(GenerationRetryExceeded):
        session.step()
    assert session.status()['status'] == 'blocked' and session.status()['pending_io'] is None
    session = FocusedSession.open(root, worker=worker, shell=shell)
    with pytest.raises(GenerationRetryExceeded):
        session.step()
    assert len(transport.requests) == 3 and shell.calls == []


def test_uncertain_model_exchange_never_enters_generation_recovery(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, enabled=False)
    transport = RejectingTransport(wt, uncertain=True)
    worker = ModelClient(worker.config, transport=transport)
    session = FocusedSession.create(tmp_path/'session', replace(settings, maximum_generation_retries=2),
                                    worker=worker, shell=shell)
    with pytest.raises(RuntimeError):
        session.step()
    with pytest.raises(UnresolvedOperation):
        session.step()
    assert len(transport.requests) == 1 and session.status()['generation_rejections'] == {}


def test_cancelled_handoff_preserves_worker_window_until_success(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=8, enabled=False)
    transport = RejectingTransport(wt, handoff_only=True)
    worker = ModelClient(worker.config, transport=transport)
    root = tmp_path/'session'
    session = FocusedSession.create(root, replace(settings, maximum_generation_retries=2), worker=worker, shell=shell)
    while session.status()['phase'] != 'handoff':
        session.step()
    before = session.session.export_state()
    session.step()
    assert session.session.export_state() == before and session.status()['handoffs'] == 0
    reopened = FocusedSession.open(root, worker=worker, shell=shell)
    reopened.step()
    assert reopened.status()['handoffs'] == 1 and reopened.status()['window_index'] == 1


def test_cancelled_controller_preserves_document_and_counts_failed_call(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1, rollover=False)
    def successful_review(path, payload, **kwargs):
        if path == '/complete':
            return WireResponse(200, json.dumps(review()), 0)
        return ct(path, payload, **kwargs)
    transport = RejectingTransport(successful_review)
    controller = ModelClient(controller.config, transport=transport)
    root = tmp_path/'session'
    session = FocusedSession.create(root, replace(settings, maximum_generation_retries=2),
        worker=worker, shell=shell, controller=controller, initial_project_document='Existing complete project record.')
    session.step()
    session.step()
    before = session.project_document()
    session.step()
    assert session.status()['active_review'] == dict(model_calls=1, tool_calls=0)
    assert session.project_document() == before and shell.calls == ['write-1']
    reopened = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    reopened.step()
    assert reopened.status()['controller_reviews'] == 1 and reopened.project_document() == before


def test_native_provider_identity_and_policy_are_enforced_on_reopen(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, enabled=False)
    protected = llama_model_client(worker.config, known_issues=dict(repetition=dict(identical_tool_calls=8)))
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=protected, shell=shell)
    assert session.state['worker_identity']['transport']['provider'] == 'llama_client'
    FocusedSession.open(root, worker=protected, shell=shell)
    changed = llama_model_client(worker.config, known_issues=dict(repetition=dict(identical_tool_calls=4)))
    with pytest.raises(ValueError, match='bindings'):
        FocusedSession.open(root, worker=changed, shell=shell)


def test_20_turn_cadence_10_turn_pairs_and_state_survive_resets_and_reopen(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path)
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    assert session.run(maximum_worker_turns=25)['status'] == 'paused'
    saved_guidance = session.status()['held_guidance']
    resumed = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    assert resumed.status()['held_guidance'] == saved_guidance
    result = resumed.run()
    assert result['status'] == 'complete'
    assert result['handoffs'] >= 2
    assert result['handoffs'] == wt.handoffs
    assert [item['completed_turns'] for item in ct.inputs] == [1, 21, 41, 43]
    assert [len(item['recent_turns']) for item in ct.inputs] == [1, 10, 10, 10]
    assert [item['turn'] for item in ct.inputs[1]['recent_turns']] == list(range(12, 22))
    assert 'Current: Observed through turn 1' in ct.inputs[1]['project_document']['text']
    assert 'Current: Observed through turn 21' in ct.inputs[2]['project_document']['text']
    assert 'Explore: initial evidence at turn 1.' in resumed.project_document()
    assert all(item['held_guidance']['correction'] == 'Keep the public format.' for item in ct.inputs[1:])
    assert all('PRIVATE_REFERENCE_SENTINEL' not in json.dumps(payload) for payload in wt.requests)
    assert all('tools' in payload for payload in ct.requests)
    assert all('PRIVATE_PROJECT_DOCUMENT' not in json.dumps(payload) for payload in wt.requests)
    assert all('reasoning_content' not in observation['response'] for item in ct.inputs for observation in item['recent_turns'])
    for payload in wt.requests[1:]:
        assert sum(message.get('content', '').startswith('Controller guidance:') for message in payload['messages']) == 1
    for item in ct.inputs[1]['recent_turns']:
        assert item['response']['tool_calls'][0]['id'] == item['tool_results'][0]['call_id']
        assert 'applied_input' in item
    assert len(shell.calls) == 42
    assert read_workspace_file(resumed.workspace(), 'result.txt', byte_limit=1000000, file_limit=1000) == b'write-42'
    events = SessionEventLog(root/'events').read_strict('session')
    assert sum(event.event_type == 'worker_handoff' for event in events) == wt.handoffs
    assert all('worker-owned handoff' in event.payload['handoff'] for event in events if event.event_type == 'worker_handoff')


def test_no_reference_bypasses_controller_and_guidance(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, enabled=False, total=3)
    session = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell)
    assert session.run()['controller_reviews'] == 0
    assert ct.requests == []
    assert all('Controller guidance:' not in json.dumps(payload) for payload in wt.requests)


def test_parallel_tool_batch_counts_as_one_completed_turn_and_reopens_mid_batch(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1, batch=True)
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    session.step()
    session.step()
    assert session.progress.turns == 0
    assert not ct.requests
    reopened = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    reopened.step()
    assert reopened.progress.turns == 1
    reopened.step()
    assert len(ct.inputs[0]['recent_turns'][0]['tool_results']) == 2
    assert shell.calls == ['write-1', 'extra-1']


def test_interrupted_tool_is_not_replayed_by_same_object_or_reopened_session(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1)
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    session.step()
    shell.interrupt = True
    with pytest.raises(RuntimeError, match='Interrupted'):
        session.step()
    shell.interrupt = False
    with pytest.raises(UnresolvedOperation):
        session.step()
    reopened = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    assert reopened.status()['status'] == 'blocked'
    with pytest.raises(UnresolvedOperation):
        reopened.run()
    assert shell.calls == ['write-1']
    assert not ct.requests


def test_invalid_controller_response_stops_before_guidance_and_preserves_record(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2)
    ct.malformed = True
    session = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    with pytest.raises(ReviewLimitExceeded, match='model-call budget'):
        session.run()
    assert wt.turns == 1
    assert session.progress.review_count == 0
    assert session.progress.guidance['correction'] == ''
    assert session.status()['status'] == 'blocked'
    assert session.status()['pending_io'] is None
    with pytest.raises(ReviewLimitExceeded):
        session.step()


def test_rejected_controller_decision_can_be_corrected_after_reopen_without_worker_dispatch(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1)
    requests=[]
    stages=[response('```json\n{"correction":"None","evidence":"","warrant":""}\n```'),
        review(),  # Valid JSON still cannot skip initial project memory.
        response('',[dict(id='create-project',type='function',function=dict(name='project_edit',
            arguments=json.dumps(dict(expected_revision=0,old_text='',new_text='# Project\n- [x] Investigated\n- [ ] Deliver'))))]),
        review(), review()]

    def transport(path,payload,**kwargs):
        if path!='/complete': return ct(path,payload,**kwargs)
        requests.append(deepcopy(payload))
        return WireResponse(200,json.dumps(stages[len(requests)-1]),0)
    controller.transport=transport
    root=tmp_path/'session'
    item=FocusedSession.create(root,settings,worker=worker,shell=shell,controller=controller)
    item.step(); item.step(); item.step()
    assert wt.turns==1 and item.progress.review_count==0
    item=FocusedSession.open(root,worker=worker,shell=shell,controller=controller)
    assert item.run()['status']=='complete'
    assert wt.turns==2 and item.progress.review_count==2
    assert 'was rejected' in requests[1]['messages'][-1]['content']
    events=SessionEventLog(root/'events').read_strict('session')
    assert sum(e.event_type=='controller_decision_rejected' for e in events)==2


def test_exact_token_fit_blocks_controller_dispatch(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2)
    ct.too_large = True
    session = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    with pytest.raises(ValueError, match='context capacity'):
        session.run()
    assert not ct.requests
    assert wt.turns == 1
    assert session.status()['pending_io'] is None


def test_oversized_handoff_stops_without_discarding_context(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2)
    session = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    wt.oversize = True
    with pytest.raises(ValueError, match='context capacity'):
        session.run()
    assert not wt.requests
    assert session.session.window_index == 0
    assert session.status()['status'] == 'blocked'
    assert 'context capacity' in session.status()['blocked_reason']
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    with pytest.raises(ValueError, match='context capacity'):
        reopened.step()
    assert not wt.requests


def test_oversized_tool_batch_gets_archived_projected_and_compacted(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1, batch=True, enabled=False)
    settings = replace(settings, session_policy=replace(settings.session_policy,
        context_capacity=6000, rollover_threshold=3000, worker_output_tokens=-1,
        handoff_output_tokens=-1, output_headroom_tokens=1000,
        overflow_excerpt_characters=128, recent_result_characters=500))

    def counted_transport(path, payload):
        if path == '/template':
            return WireResponse(200, json.dumps(dict(prompt=json.dumps(payload['messages']))), 0)
        return wt(path, payload)
    worker.transport = counted_transport
    original_run = shell.run
    shell.run = lambda command, workspace: replace(original_run(command, workspace),
                                                   stdout='o'*2000, stderr='e'*2000)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell)
    for _ in range(3):
        item.step()  # One native response and two resolved, individually bounded results.
    original_messages = deepcopy(item.session.messages)
    assert len(json.dumps(original_messages)) > settings.session_policy.context_capacity
    item.step()
    assert item.status()['phase'] == 'handoff'
    item.step()
    assert item.status()['handoffs'] == 1 and item.status()['status'] == 'ready'
    assert wt.handoffs == 1 and wt.requests[-1]['max_tokens'] == -1
    events = SessionEventLog(tmp_path/'session'/'events').read_strict('session')
    projection = next(event.payload for event in events if event.event_type == 'context_projection')
    assert projection['projected_tokens'] <= 5000 < projection['original_tokens']
    archive = read_workspace_file(item.workspace(), projection['archive'], byte_limit=1000000, file_limit=1000)
    assert json.loads(archive)['messages'] == original_messages
    assert 'PRIVATE_REFERENCE' not in archive.decode() and 'PRIVATE_PROJECT_DOCUMENT' not in archive.decode()
    assert projection['archive'] in json.dumps(item.worker_payload())
    projected_messages = wt.requests[-1]['messages']
    native_call_ids = [c['id'] for m in projected_messages for c in m.get('tool_calls', [])]
    assert native_call_ids == [m['tool_call_id'] for m in projected_messages if m['role'] == 'tool']
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell)
    assert reopened.status()['handoffs'] == 1
    assert reopened.run()['status'] == 'complete'


def test_reasoning_budgets_do_not_limit_worker_controller_or_handoff_outputs(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=6)
    settings = replace(settings,
        generation=settings.generation | dict(reasoning_budget_tokens=400),
        session_policy=replace(settings.session_policy, worker_output_tokens=-1, handoff_output_tokens=-1,
            output_headroom_tokens=100, handoff_generation_overrides=dict(reasoning_budget_tokens=200)),
        controller=replace(settings.controller, output_tokens=-1, output_headroom_tokens=500,
            generation=settings.controller.generation | dict(reasoning_budget_tokens=300)))
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete' and wt.handoffs > 0
    assert all(request['max_tokens'] == -1 for request in wt.requests+ct.requests)
    assert all(request['reasoning_budget_tokens'] == (400 if 'tools' in request else 200) for request in wt.requests)
    assert all(request['reasoning_budget_tokens'] == 300 for request in ct.requests)


def test_large_controller_observations_keep_raw_history_and_retrievable_turn_ids(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1, rollover=False)
    settings = replace(settings, controller=replace(settings.controller,
        context_capacity=12000, output_tokens=-1, output_headroom_tokens=1000,
        maximum_tool_output_characters=1000))
    original_run = shell.run
    shell.run = lambda command, workspace: replace(original_run(command, workspace), stdout='EVIDENCE'*20000)

    def counted_transport(path, payload):
        if path == '/template':
            return WireResponse(200, json.dumps(dict(prompt=json.dumps(payload['messages']))), 0)
        return ct(path, payload)
    controller.transport = counted_transport
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    projected = ct.inputs[0]['recent_turns'][0]
    assert projected['turn'] == 1
    assert projected['full_evidence'] == dict(tool='history_read', arguments=dict(start_turn=1, end_turn=1, offset=0))
    assert len(projected['evidence_excerpt']) < 2000
    events = SessionEventLog(tmp_path/'session'/'events').read_strict('session')
    raw = next(event.payload for event in events if event.event_type == 'worker_turn')
    assert 'EVIDENCE'*20000 in json.dumps(raw)
    assert any(event.event_type == 'controller_evidence_projection' for event in events)


def test_completion_correction_resumes_worker_once_and_accepts_next_result(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1)
    ct.replacement_on_final = True
    session = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    result = session.run()
    assert result['status'] == 'complete'
    assert wt.turns == 3
    assert [item['boundary'] for item in ct.inputs] == ['periodic', 'completion', 'completion']


def test_stale_writer_and_changed_bindings_cannot_execute(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path)
    root = tmp_path/'session'
    first = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    stale = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    first.step()
    with pytest.raises(RuntimeError, match='changed'):
        stale.step()
    other = ModelClient(replace(worker.config, base_url='http://other.invalid'), transport=wt)
    with pytest.raises(ValueError, match='bindings'):
        FocusedSession.open(root, worker=other, shell=shell, controller=controller)
    assert wt.turns == 1


def test_complete_journal_checkpoint_recovers_missing_database_commit(tmp_path, monkeypatch):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1)
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    real_commit = session.store.commit
    calls = 0

    def fail_once(expected, transform):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('Database commit interrupted')
        return real_commit(expected, transform)

    monkeypatch.setattr(session.store, 'commit', fail_once)
    with pytest.raises(RuntimeError, match='Database commit interrupted'):
        session.step()
    reopened = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    assert reopened.state['phase'] == 'tools'
    assert reopened.run()['status'] == 'complete'
    assert shell.calls == ['write-1']
    assert wt.turns == 2


def test_controller_inspects_old_evidence_and_discards_check_writes_across_mid_review_restart(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=21)
    ct.inspect = True
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    session.run(maximum_worker_turns=20)
    while session.state['phase'] != 'review_tools':
        session.step()
    assert session.progress.turns == 21
    session.step()  # Commit the project edit before restarting partway through the batch.
    document = session.project_document()
    workspace = session.workspace()
    restarted = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    assert restarted.project_document() == document
    while restarted.state['phase'] == 'review_tools':
        restarted.step()
        assert restarted.workspace() == workspace
        restarted = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    assert shell.calls.count('check-mutates-copy') == 1
    restarted.step()
    payload = ct.requests[-1]
    results = {message['tool_call_id']:json.loads(message['content']) for message in payload['messages'] if message['role'] == 'tool'}
    old = json.loads(results['history_read-21']['text'])
    assert old[0]['turn'] == 2
    assert 'write-2' in old[0]['response']['tool_calls'][0]['function']['arguments']
    assert results['workspace_read-21']['text'] == 'write-21'
    assert results['run_check-21']['workspace_changes_discarded'] is True
    assert 'Explore: initial evidence at turn 1.' in results['project_read-21']['text']
    assert restarted.run()['status'] == 'complete'
    assert 'check-mutates-copy' not in read_workspace_file(restarted.workspace(), 'result.txt', byte_limit=1000000, file_limit=1000).decode()


def test_interrupted_controller_check_blocks_replay_and_preserves_worker_snapshot(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=21)
    ct.inspect = True
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    session.run(maximum_worker_turns=20)
    while not (session.state['phase'] == 'review_tools' and session.state['review']['pending_calls'][0]['function']['name'] == 'run_check'):
        session.step()
    workspace = session.workspace()
    shell.interrupt = True
    with pytest.raises(RuntimeError, match='Interrupted'):
        session.step()
    shell.interrupt = False
    reopened = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    assert reopened.status()['status'] == 'blocked'
    assert reopened.workspace() == workspace
    with pytest.raises(UnresolvedOperation):
        reopened.step()
    assert shell.calls.count('check-mutates-copy') == 1


def test_review_model_budget_stops_worker_before_unreviewed_continuation(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2)
    settings = replace(settings, controller=replace(settings.controller, maximum_model_calls=1))
    session = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    with pytest.raises(ReviewLimitExceeded, match='model-call budget'):
        session.run()
    assert wt.turns == 1
    assert session.status()['status'] == 'blocked'
    assert session.progress.review_count == 0
    assert session.progress.document_revision == 1


def test_controller_tool_batch_budget_prevents_any_tool_execution(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=21)
    ct.inspect = True
    settings = replace(settings, controller=replace(settings.controller, maximum_tool_calls=2))
    session = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    with pytest.raises(ReviewLimitExceeded, match='batch was not executed'):
        session.run()
    assert wt.turns == 21
    assert session.progress.document_revision == 1
    assert 'check-mutates-copy' not in shell.calls


def test_large_project_is_paged_and_bad_inspection_edits_are_recoverable(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=0)
    settings = replace(settings, controller=replace(settings.controller, maximum_tool_output_characters=80))
    initial = '# Project\n- [x] Established result: '+('historical evidence; '*60)+'\nNext: Implement\n- [ ] Deliver'
    captured = []

    def call(identity, name, **arguments):
        return dict(id=identity, type='function', function=dict(name=name, arguments=json.dumps(arguments)))

    stages = [
        response('Inspect the document and test edit conflicts.', [
            call('bad-path', 'workspace_read', path='../host-secret', offset=0),
            call('stale-edit', 'project_edit', expected_revision=99, old_text='Implement', new_text='Verify'),
            call('page-one', 'project_read', offset=0),
        ]),
        response('Update current focus while retaining historical findings.', [
            call('edit', 'project_edit', expected_revision=0, old_text='Next: Implement', new_text='Next: Verify'),
            call('bad-history', 'history_read', start_turn=1, end_turn=2, offset=0),
            call('page-two', 'project_read', offset=80),
        ]), review(),
    ]

    def transport(path, payload, **kwargs):
        if path != '/complete':
            return ct(path, payload, **kwargs)
        captured.append(deepcopy(payload))
        return WireResponse(200, json.dumps(stages[len(captured)-1]), 0)

    controller = ModelClient(controller.config, transport=transport)
    session = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller,
        initial_project_document=initial)
    assert session.run()['status'] == 'complete'
    envelope = json.loads(captured[0]['messages'][1]['content'])
    assert envelope['project_document']['text'] == initial[:80]
    assert envelope['project_document']['next_offset'] == 80
    results = {m['tool_call_id']:json.loads(m['content']) for m in captured[-1]['messages'] if m['role'] == 'tool'}
    assert results['bad-path']['status'] == results['stale-edit']['status'] == results['bad-history']['status'] == 'error'
    assert results['page-two']['text'] == initial[80:160]
    assert session.project_document() == initial.replace('Next: Implement', 'Next: Verify')
    assert session.progress.document_revision == 1
    assert session.progress.review_count == 1


def test_legacy_session_schema_is_rejected_without_silent_record_conversion(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=0)
    root = tmp_path/'session'
    session = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    session.state['schema'] = 1
    session._save()
    with pytest.raises(ValueError, match='Legacy session'):
        FocusedSession.open(root, worker=worker, shell=shell, controller=controller)


def test_unlimited_review_recovers_after_eight_calls_and_restart_then_chooses_completion(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1, rollover=False)
    settings = replace(settings, controller=replace(settings.controller,
        maximum_model_calls=None, maximum_tool_calls=None, input_target_tokens=5000,
        recent_review_exchanges=2, maximum_tool_output_characters=2000))
    requests = []

    def transport(path, payload, **kwargs):
        if path == '/template':
            return WireResponse(200, json.dumps(dict(prompt=json.dumps(payload['messages']))), 0)
        if path == '/tokenize':
            return ct(path, payload, **kwargs)
        envelope = json.loads(payload['messages'][1]['content'])
        if envelope['boundary'] == 'completion':
            value = review()
        else:
            index = len(requests)
            requests.append(deepcopy(payload))
            if index == 18:
                value = response('{"correction":"None"}')
            elif index == 19:
                value = review()
            else:
                name, args = 'project_read', dict(offset=0)
                if index == 0:
                    name, args = 'project_edit', dict(expected_revision=0, old_text='',
                        new_text='# Project\nEstablished: public format verified.\nNext: inspect\nLater: deliver\n')
                elif index == 8:
                    name, args = 'project_edit', dict(expected_revision=1, old_text='missing anchor', new_text='verify')
                elif index == 9:
                    failure = json.loads(next(m['content'] for m in reversed(payload['messages']) if m['role'] == 'tool'))
                    assert failure['status'] == 'error' and failure['project_document']['revision'] == 1
                    page = failure['project_document']
                    start = page['offset']+page['text'].index('inspect')
                    name, args = 'project_edit_range', dict(expected_revision=page['revision'],
                        start_offset=start, end_offset=start+7, new_text='verify')
                elif index == 10:
                    name, args = 'review_history_read', dict(start_message=2, end_message=3, offset=0)
                elif index == 11:
                    result = json.loads(next(m['content'] for m in reversed(payload['messages']) if m['role'] == 'tool'))
                    archived = json.loads(result['text'])
                    assert archived[0]['message']['tool_calls'][0]['id'] == 'extended-0'
                    assert archived[1]['message']['tool_call_id'] == 'extended-0'
                value = response('Resolve the remaining review question.', [dict(id='extended-'+str(index),
                    type='function', function=dict(name=name, arguments=json.dumps(args)))])
        return WireResponse(200, json.dumps(value), 0)

    controller = ModelClient(controller.config, transport=transport)
    root = tmp_path/'session'
    item = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    while not (item.status()['phase'] == 'review' and item.status()['active_review']
               and item.status()['active_review']['model_calls'] == 9):
        item.step()
    assert wt.turns == 1 and item.progress.review_count == 0
    reopened = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    assert reopened.run()['status'] == 'complete'
    assert len(requests) == 20
    assert 'Established: public format verified.' in reopened.project_document()
    assert 'Next: verify\nLater: deliver' in reopened.project_document()
    events = SessionEventLog(root/'events').read_strict('session')
    decision = next(e.payload for e in events if e.event_type == 'controller_review')
    assert decision['model_calls'] == 20 and decision['tool_calls'] == 18
    assert decision['document_revision'] == 2 and decision['decision']['operation'] == 'hold'
    assert sum(e.event_type == 'controller_decision_rejected' for e in events) == 1
    assert any(e.event_type == 'controller_context_projection' for e in events)
    assert all(len(json.dumps(p['messages'])) <= 5000 for p in requests)
    assert all('PRIVATE_REFERENCE_SENTINEL' not in json.dumps(p) for p in wt.requests)


def test_review_growth_projects_after_tool_and_retrieves_full_original_evidence(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=0, rollover=False)
    settings = replace(settings, controller=replace(settings.controller,
        maximum_model_calls=None, maximum_tool_calls=None, context_capacity=9000,
        output_tokens=-1, output_headroom_tokens=1000, input_target_tokens=4500,
        recent_review_exchanges=1, maximum_tool_output_characters=20000))
    requests = []
    original_run = shell.run
    shell.run = lambda command, workspace: replace(original_run(command, workspace),
        stdout='start '+('x'*7000)+' MIDDLE_FACT=retained '+('y'*7000)+' end')

    def transport(path, payload, **kwargs):
        if path == '/template':
            return WireResponse(200, json.dumps(dict(prompt=json.dumps(payload['messages']))), 0)
        if path == '/tokenize':
            return ct(path, payload, **kwargs)
        index = len(requests)
        requests.append(deepcopy(payload))
        if index == 0:
            name, args = 'project_edit', dict(expected_revision=0, old_text='',
                new_text='# Project\nEstablished evidence is retained.\nOpen: inspect the missing detail.')
        elif index == 1:
            name, args = 'run_check', dict(command='inspect-detail')
        elif index == 2:
            assert 'MIDDLE_FACT=retained' not in json.dumps(payload)
            assert 'Excerpt only' in json.dumps(payload)
            assert json.loads(payload['messages'][1]['content'])['project_document']['revision'] == 1
            name, args = 'review_history_read', dict(start_message=5, end_message=5, offset=7000)
        else:
            assert 'MIDDLE_FACT=retained' in json.dumps(payload)
            return WireResponse(200, json.dumps(review()), 0)
        value = response('Inspect the relevant evidence.', [dict(id='growth-'+str(index), type='function',
            function=dict(name=name, arguments=json.dumps(args)))])
        return WireResponse(200, json.dumps(value), 0)

    controller = ModelClient(controller.config, transport=transport)
    root = tmp_path/'session'
    item = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    assert all(len(json.dumps(p['messages'])) <= 4500 for p in requests)
    events = SessionEventLog(root/'events').read_strict('session')
    raw = next(e.payload for e in events if e.event_type == 'controller_tool' and e.payload['name'] == 'run_check')
    assert 'MIDDLE_FACT=retained' in raw['result']['stdout']
    assert raw['result']['workspace_changes_discarded']
    assert any(e.event_type == 'controller_context_projection' and e.payload['model_calls'] >= 2 for e in events)
    for payload in requests:
        identities = [c['id'] for m in payload['messages'] for c in m.get('tool_calls', [])]
        assert identities == [m['tool_call_id'] for m in payload['messages'] if m['role'] == 'tool']


def test_ordinary_rollover_archives_and_retains_the_action_still_awaiting_followup(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, enabled=False, total=7)
    root = tmp_path/'session'
    item = FocusedSession.create(root, settings, worker=worker, shell=shell)
    while item.status()['handoffs'] == 0:
        item.step()
    resumed = json.loads(item.session.messages[-1]['content'].split('\n', 1)[1])
    boundary = resumed['continuation_boundary']
    assert boundary['awaiting_worker_response']
    assert 'write-'+str(wt.turns) in boundary['last_action']['text']
    archive = read_workspace_file(item.workspace(), resumed['source_archive'], byte_limit=1000000, file_limit=1000)
    assert json.loads(archive)['messages'][-1]['role'] == 'tool'
    events = SessionEventLog(root/'events').read_strict('session')
    assert any(e.event_type == 'worker_history_archive' for e in events)
    assert not any(e.event_type == 'context_projection' for e in events)
    handoff_request = next(p for p in wt.requests if 'tools' not in p)
    assert 'not yet acted on its results' in handoff_request['messages'][-1]['content']
    assert resumed['source_archive'] in json.dumps(handoff_request)
    reopened = FocusedSession.open(root, worker=worker, shell=shell)
    assert reopened.run()['status'] == 'complete' and wt.turns == 8


def test_known_incomplete_generation_never_executes_partial_tools_and_can_recover(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=0, rollover=False)
    settings = replace(settings, controller=replace(settings.controller,
        maximum_model_calls=None, maximum_tool_calls=None))
    requests = []

    def transport(path, payload, **kwargs):
        if path != '/complete':
            return ct(path, payload, **kwargs)
        requests.append(deepcopy(payload))
        if len(requests) == 1:
            value = response('Incomplete action', [dict(id='partial', type='function',
                function=dict(name='project_edit', arguments='{"expected_revision":0,"new_text":"partial'))], finish='length')
        elif len(requests) == 2:
            assert 'No tool calls from that incomplete response were executed' in payload['messages'][-1]['content']
            assert json.loads(payload['messages'][1]['content'])['project_document']['revision'] == 0
            value = response('Create verified project memory.', [dict(id='complete-edit', type='function',
                function=dict(name='project_edit', arguments=json.dumps(dict(
                    expected_revision=0, old_text='', new_text='# Project\nVerified state.'))))])
        else:
            value = review()
        return WireResponse(200, json.dumps(value), 0)

    controller = ModelClient(controller.config, transport=transport)
    root = tmp_path/'session'
    item = FocusedSession.create(root, settings, worker=worker, shell=shell, controller=controller)
    item.step()
    item.step()
    assert item.status()['pending_io'] is None and item.progress.document_revision == 0
    reopened = FocusedSession.open(root, worker=worker, shell=shell, controller=controller)
    assert reopened.run()['status'] == 'complete'
    assert reopened.project_document() == '# Project\nVerified state.'
    events = SessionEventLog(root/'events').read_strict('session')
    assert len([e for e in events if e.event_type == 'project_edit']) == 1
    rejected = next(e.payload for e in events if e.event_type == 'controller_generation_rejected')
    assert rejected['tools_executed'] is False
    assert not any(e.event_type == 'controller_tool' and e.payload['call_id'] == 'partial' for e in events)


def _reasoning_count(payload):
    return sum(1 for message in payload['messages'] if message.get('role') == 'assistant' and message.get('reasoning_content'))


def test_latest_reasoning_retention_reaches_worker_handoff_and_controller_review(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=24)
    settings = replace(settings, session_policy=replace(settings.session_policy, reasoning_retention='latest'))
    ct.inspect = True
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete' and wt.handoffs > 0 and len(ct.requests) > 4
    # Every worker, handoff and controller request carries at most one reasoning field,
    # and only on its final assistant message.
    for payload in wt.requests+ct.requests:
        assert _reasoning_count(payload) <= 1
        assistants = [message for message in payload['messages'] if message.get('role') == 'assistant']
        if assistants[:-1]:
            assert all('reasoning_content' not in message for message in assistants[:-1])
    # The raw audit and the stored session still hold every reasoning field.
    archived = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    raw = [json.loads(event['payload']['body']) if isinstance(event['payload']['body'], str) else event['payload']['body']
           for event in archived if event['event_type'] == 'model_response' and event['payload']['purpose'] == 'worker']
    assert raw and all(body['choices'][0]['message'].get('reasoning_content') for body in raw)
    stored = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller).session.messages
    assert sum(1 for m in stored if m.get('role') == 'assistant' and m.get('reasoning_content')) >= 1


def test_default_retention_keeps_all_reasoning_on_the_wire(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=6, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    assert max(_reasoning_count(payload) for payload in wt.requests) >= 2


class FileToolTransport:
    """Scripted worker: an oversized heredoc, then write, edit, a conflicting edit, a read, and completion."""

    def __init__(self):
        self.requests = []
        self.script = [
            ('bash', dict(command="cat << 'EOF' > notes.py\n"+('x = 1\n'*80)+'EOF\n')),
            ('bash', dict(command="cat << 'EOF' > notes.py\n"+('x = 1\n'*80)+'EOF\n')),
            ('write', dict(path='notes.py', content='alpha = 1\nbeta = 1\n')),
            ('edit', dict(path='notes.py', old_text='alpha = 1\nbeta = 1\n'*8, new_text='gamma = 1')),
            ('edit', dict(path='notes.py', old_text='beta = 1', new_text='beta = 2')),
            ('edit', dict(path='notes.py', old_text='= ', new_text='=', expected_occurrences=1)),
            ('edit', dict(path='absent.py', old_text='a', new_text='b')),
            ('write', dict(path='big.txt', content='y'*300)),
            ('read', dict(path='notes.py', offset=2, limit=1)),
            ('read', dict(path='nope.py')),
            ('bash', dict(command='echo between')),
            ('read', dict(path='nope.py')),
            ('read', dict(path='nope.py')),
            ('bash', dict(command='cat notes.py')),
            ('bash', dict(command='cat notes.py')),
        ]
        self.step = 0

    def __call__(self, path, payload, **kwargs):
        if path == '/template':
            value = dict(prompt='x'*100)
        elif path == '/tokenize':
            value = dict(tokens=[1]*len(payload['content']))
        else:
            self.requests.append(deepcopy(payload))
            if self.step < len(self.script):
                name, args = self.script[self.step]; self.step += 1
                value = response('Step '+str(self.step), [dict(id='file-'+str(self.step), type='function',
                    function=dict(name=name, arguments=json.dumps(args)))])
            else:
                value = response('Verified final report.')
        return WireResponse(200, json.dumps(value), 0)


def test_bounded_file_tools_reject_large_actions_and_edit_exactly(tmp_path):
    settings, _, shell, controller, _, ct = setup(tmp_path, total=0, enabled=False, rollover=False)
    settings = replace(settings, worker_tools=('bash', 'read', 'write', 'edit'),
                       maximum_tool_argument_characters=400, maximum_write_characters=200, maximum_edit_characters=100,
                       session_policy=replace(settings.session_policy, argument_excerpt_characters=250))
    transport = FileToolTransport()
    worker = ModelClient(EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True), transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    result = item.run()
    assert result['status'] == 'complete' and result['completed_worker_turns'] == 16
    assert [tool['function']['name'] for tool in transport.requests[0]['tools']] == ['bash', 'read', 'write', 'edit']
    outcomes = [json.loads(message['content']) for message in transport.requests[-1]['messages'] if message.get('role') == 'tool']
    assert [o['status'] for o in outcomes] == ['rejected', 'rejected', 'ok', 'error', 'ok', 'error', 'error', 'error', 'ok', 'error', 'completed', 'error', 'error', 'completed', 'completed']
    assert outcomes[0]['repeated'] is False and outcomes[1]['repeated'] is True and outcomes[1]['identical_repeats'] == 2
    # the interleaved echo does not reset the window: read nope.py counts 1, 2, 3
    assert 'repeated' not in outcomes[9] and outcomes[11]['identical_repeats'] == 2 and outcomes[12]['identical_repeats'] == 3
    assert 'alternating' in outcomes[12]['error']
    assert 'repeated' not in outcomes[13] and outcomes[14]['identical_repeats'] == 2 and 'already reflects it' in outcomes[14]['note']
    assert 'failed 3 times' in outcomes[11]['error'] and 'not_a_file' in outcomes[11]['error'] or 'Not a workspace file' in outcomes[11]['error']
    assert outcomes[11]['error'] != outcomes[12]['error']
    assert 'already rejected' in outcomes[1]['error'] and 'already rejected' not in outcomes[0]['error']
    assert 'old_text is the large part' in outcomes[3]['error']
    assert outcomes[8] == dict(status='ok', tool='read', path='notes.py', offset=2, lines_returned=1, total_lines=2,
                               truncated=False, content='     2\tbeta = 2\n')
    assert outcomes[9]['code'] == 'not_a_file' and outcomes[12]['code'] == 'not_a_file'
    assert outcomes[0]['code'] == 'arguments_too_large' and 'edit' in outcomes[0]['error'] and outcomes[0]['bound'] == 400
    assert outcomes[2] == dict(status='ok', tool='write', path='notes.py', created=True, bytes_written=19)
    assert outcomes[4]['tool'] == 'edit' and outcomes[4]['replacements'] == 1
    assert outcomes[5]['code'] == 'occurrence_mismatch' and outcomes[6]['code'] == 'not_a_file'
    assert 'too long for one call' in outcomes[7]['error'] and '200' not in outcomes[7]['error']
    # The rejected heredoc never reached the shell; only the final cat did.
    assert shell.calls == ['echo between', 'cat notes.py', 'cat notes.py']
    saved = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    files = workspace_files(saved.workspace(), byte_limit=1000000, file_limit=1000)
    assert 'notes.py' in files and 'big.txt' not in files
    archived = sorted(f for f in files if f.startswith('.tool-output/') and f.endswith('.args.json'))
    assert archived == ['.tool-output/file-1.args.json', '.tool-output/file-2.args.json', '.tool-output/file-8.args.json']
    record = json.loads(read_workspace_file(saved.workspace(), '.tool-output/file-8.args.json', byte_limit=1000000, file_limit=1000))
    assert record['tool'] == 'write' and record['status'] == 'error' and record['arguments']['content'] == 'y'*300
    assert outcomes[7]['arguments_saved_at'] == '.tool-output/file-8.args.json' and 'arguments_saved_at' not in outcomes[2]
    wire = transport.requests[-1]['messages']
    projected = [c['function']['arguments'] for m in wire for c in (m.get('tool_calls') or []) if 'saved at .tool-output/' in c['function']['arguments']]
    assert len(projected) >= 1 and all('y'*300 not in a for a in projected)
    assert read_workspace_file(saved.workspace(), 'notes.py', byte_limit=1000000, file_limit=1000) == b'alpha = 1\nbeta = 2\n'
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    rejected = [e['payload'] for e in events if e['event_type'] == 'tool_rejected']
    assert len(rejected) == 2 and rejected[0]['name'] == 'bash' and rejected[0]['characters'] > 400
    assert [r['repeated'] for r in rejected] == [False, True]
    assert sum(1 for e in events if e['event_type'] == 'tool_outcome') == 13
    repeats = [e['payload'] for e in events if e['event_type'] == 'tool_repeated_call']
    assert [(r['name'], r['count']) for r in repeats] == [('bash', 2), ('read', 2), ('read', 3), ('bash', 2)]


class CreateOnlyTransport(FileToolTransport):
    """Scripted worker: create, overwrite (refused), edit, empty the file, write the stub, overwrite again."""

    def __init__(self):
        super().__init__()
        self.script = [
            ('write', dict(path='notes.py', content='alpha = 1\n')),
            ('write', dict(path='notes.py', content='alpha = 2\n')),
            ('edit', dict(path='notes.py', old_text='alpha = 1', new_text='alpha = 2')),
            ('edit', dict(path='notes.py', old_text='alpha = 2\n', new_text='')),
            ('write', dict(path='notes.py', content='def stub():\n    pass\n')),
            ('write', dict(path='notes.py', content='def stub():\n    return 1\n')),
        ]


def test_write_only_creates_files_when_overwrites_are_disabled(tmp_path):
    settings, _, shell, controller, _, ct = setup(tmp_path, total=0, enabled=False, rollover=False)
    settings = replace(settings, worker_tools=('bash', 'read', 'write', 'edit'), write_existing_files=False)
    transport = CreateOnlyTransport()
    worker = ModelClient(EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True), transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    outcomes = [json.loads(message['content']) for message in transport.requests[-1]['messages'] if message.get('role') == 'tool']
    assert [o['status'] for o in outcomes] == ['ok', 'error', 'ok', 'ok', 'ok', 'error']
    assert 'already has content' in outcomes[1]['error'] and 'rebuild it by refinement' in outcomes[1]['error']
    assert outcomes[4]['created'] is False
    assert read_workspace_file(item.workspace(), 'notes.py', byte_limit=1000000, file_limit=1000) == b'def stub():\n    pass\n'


def test_completed_session_continues_on_host_text_and_survives_reopen(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    first = item.run()
    assert first['status'] == 'complete' and first['final_text'] == 'Verified final report.'
    with pytest.raises(ValueError, match='nonempty'):
        item.continue_with('  ')
    wt.total = 4
    status = item.continue_with('Gate red: the reading is missing its unit; keep the probe, add the unit.')
    assert status['status'] == 'ready' and status['phase'] == 'worker' and status['final_text'] == ''
    with pytest.raises(ValueError, match='completed session'):
        item.continue_with('again')
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    second = reopened.run()
    assert second['status'] == 'complete' and second['completed_worker_turns'] == 5
    request = wt.requests[3]['messages']
    assert request[-1] == dict(role='user', content='Gate red: the reading is missing its unit; keep the probe, add the unit.')
    assert request[-2]['role'] == 'assistant' and request[-2]['content'] == 'Verified final report.'
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    continued = [e['payload'] for e in events if e['event_type'] == 'continued']
    assert continued == [dict(window=0, characters=72)]
    turns = [e['payload'] for e in events if e['event_type'] == 'worker_turn']
    assert turns[3]['applied_input'][-1]['content'].startswith('Gate red')


class StuckTransport(FileToolTransport):
    """Scripted worker: the same failing read four times, then a different action, then completion."""

    def __init__(self):
        super().__init__()
        self.script = [('read', dict(path='nope.py'))]*4+[('bash', dict(command='echo moved on'))]

    def __call__(self, path, payload, **kwargs):
        if path not in ('/template', '/tokenize') and 'tools' not in payload:
            self.requests.append(deepcopy(payload))
            return WireResponse(200, json.dumps(response('worker-owned handoff: stop reading nope.py')), 0)
        return super().__call__(path, payload, **kwargs)


def test_repeated_failures_force_a_rollover_that_breaks_the_period(tmp_path):
    settings, _, shell, controller, _, ct = setup(tmp_path, total=0, enabled=False, rollover=False)
    settings = replace(settings, worker_tools=('bash', 'read', 'write', 'edit'), repeated_failure_rollover=3)
    with pytest.raises(ValueError):
        replace(settings, repeated_failure_rollover=1)
    transport = StuckTransport()
    worker = ModelClient(EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True), transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    result = item.run()
    assert result['status'] == 'complete' and result['handoffs'] == 1 and result['window_index'] == 1
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    forced = [e['payload'] for e in events if e['event_type'] == 'rollover_forced']
    assert forced == [dict(reason='repeated_failure', name='read', count=3, window=0)]
    kinds = [e['event_type'] for e in events]
    # the third identical failure is applied, then the next worker step hands off before the fourth
    assert kinds.index('rollover_forced') < kinds.index('worker_handoff') if 'worker_handoff' in kinds else True
    repeats = [e['payload']['count'] for e in events if e['event_type'] == 'tool_repeated_call']
    assert repeats == [2, 3]  # the fourth read runs in the fresh window with a cleared counter
    assert item.state['rollover_requested'] is None


def test_review_envelope_carries_focus_files_bounded(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2, enabled=True, rollover=False)
    settings = replace(settings, review_focus_globs=('probes/*/probe.py',), review_focus_characters=30)
    seed = write_workspace_file(b'', 'probes/a/probe.py', b'def measure(ctx):\n    return {"x": 1}\n', byte_limit=1000000, file_limit=1000)
    seed = write_workspace_file(seed, 'probes/b/probe.py', b'def measure(ctx):\n    return {"y": 2}\n', byte_limit=1000000, file_limit=1000)
    seed = write_workspace_file(seed, 'notes.md', b'not a focus file', byte_limit=1000000, file_limit=1000)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller,
                                 initial_workspace=seed)
    assert item.run()['status'] == 'complete'
    focus = ct.inputs[0]['proposed_input']['focus_files']
    assert list(focus) == ['probes/a/probe.py', 'probes/b/probe.py']
    assert focus['probes/a/probe.py'].startswith('def measure(ctx):') and '[truncated at 30 characters' in focus['probes/a/probe.py']
    assert focus['probes/b/probe.py'].startswith('[omitted: review focus budget exhausted')
    plain, plain_worker, plain_shell, plain_controller, _, plain_ct = setup(tmp_path/'plain', total=2, enabled=True, rollover=False)
    other = FocusedSession.create(tmp_path/'plain'/'session', plain, worker=plain_worker, shell=plain_shell, controller=plain_controller)
    other.run()
    assert plain_ct.inputs[0]['proposed_input']['focus_files'] == {}
    with pytest.raises(ValueError):
        replace(settings, review_focus_characters=0)


class LoopingTransport(FileToolTransport):
    """Scripted worker: the same successful read six times, then completion."""

    def __init__(self):
        super().__init__()
        self.script = [('read', dict(path='notes.py'))]*6

    def __call__(self, path, payload, **kwargs):
        if path not in ('/template', '/tokenize') and 'tools' not in payload:
            self.requests.append(deepcopy(payload))
            return WireResponse(200, json.dumps(response('worker-owned handoff: stop re-reading')), 0)
        return super().__call__(path, payload, **kwargs)


def test_repeated_successes_force_a_rollover_too(tmp_path):
    settings, _, shell, controller, _, ct = setup(tmp_path, total=0, enabled=False, rollover=False)
    settings = replace(settings, worker_tools=('bash', 'read', 'write', 'edit'), repeated_success_rollover=4)
    seed = write_workspace_file(b'', 'notes.py', b'alpha = 1\n', byte_limit=1000000, file_limit=1000)
    transport = LoopingTransport()
    worker = ModelClient(EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True), transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller,
                                 initial_workspace=seed)
    result = item.run()
    assert result['status'] == 'complete' and result['handoffs'] == 1
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    forced = [e['payload'] for e in events if e['event_type'] == 'rollover_forced']
    assert forced == [dict(reason='repeated_success', name='read', count=4, window=0)]


def test_prune_workspaces_keeps_only_the_current_snapshot(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=3, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    # Every committed turn prunes on its own; only the current snapshot survives the run.
    remaining = list((tmp_path/'session'/'workspaces').glob('*.sqlite3'))
    assert [p.stem for p in remaining] == [item.state['workspace']]
    assert item.prune_workspaces() == 0
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    assert reopened.workspace() == item.workspace() and reopened.prune_workspaces() == 0


def test_discard_pending_lets_an_interrupted_session_continue(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.discard_pending() is None
    item.run(maximum_worker_turns=1)
    with item._locked():
        item.state['pending_io'] = dict(kind='model', purpose='worker', prompt_tokens=10)
        item._save()
    with pytest.raises(UnresolvedOperation):
        item.step()
    assert item.discard_pending() == dict(kind='model', purpose='worker', prompt_tokens=10)
    assert item.run()['status'] == 'complete'
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    assert [e['payload'] for e in events if e['event_type'] == 'pending_discarded'] == [dict(kind='model', purpose='worker', prompt_tokens=10)]


class PlainReviewTransport:
    """Scripted plain reviewer: hold, then a malformed reply, then a correction, then hold at completion."""

    def __init__(self):
        self.requests = []
        self.replies = ['{"correction":"None","evidence":"","warrant":""}',
                        'not json at all',
                        '```json\n{"correction":"Measure the mean, not the sum.","evidence":"probe sums","warrant":"reference asks mean"}\n```',
                        '{"correction":"None","evidence":"","warrant":""}']

    def __call__(self, path, payload, **kwargs):
        if path == '/template':
            return WireResponse(200, json.dumps(dict(prompt='x'*100)), 0)
        if path == '/tokenize':
            return WireResponse(200, json.dumps(dict(tokens=[1]*len(payload['content']))), 0)
        self.requests.append(deepcopy(payload))
        reply = self.replies[min(len(self.requests)-1, len(self.replies)-1)]
        return WireResponse(200, json.dumps(response(reply)), 0)


def test_plain_review_is_one_toolless_call_per_boundary(tmp_path):
    settings, worker, shell, _, wt, _ = setup(tmp_path, total=25, enabled=True, rollover=False)
    settings = replace(settings, review_policy=ReviewPolicy(20, 10, 1, 10000, 2000, 200000),
                       controller=replace(settings.controller, plain_review=True, plain_recent_exchanges=3))
    transport = PlainReviewTransport()
    controller = ModelClient(EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True), transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    result = item.run()
    assert result['status'] == 'complete'
    assert all('tools' not in request for request in transport.requests)
    first = json.loads(transport.requests[0]['messages'][1]['content'])
    assert set(first) == {'reference', 'boundary', 'completed_turns', 'held_guidance', 'focus_files', 'recent_turns', 'proposed_completion'}
    assert first['reference'].startswith('PRIVATE_REFERENCE_SENTINEL') and len(first['recent_turns']) <= 3
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    reviews = [e['payload'] for e in events if e['event_type'] == 'controller_review']
    assert [r['boundary'] for r in reviews] == ['periodic', 'periodic', 'completion']
    assert reviews[1]['decision']['operation'] == 'replace' and reviews[1]['model_calls'] == 2  # after one malformed reply
    assert any(e['event_type'] == 'controller_decision_rejected' for e in events)
    assert result['held_guidance']['correction'] == 'Measure the mean, not the sum.'
    guided = [m for m in wt.requests[-1]['messages'] if m.get('role') == 'user' and m['content'].startswith('Controller guidance:')]
    assert guided and 'Measure the mean' in guided[-1]['content']


class ReadBeforeEditTransport(FileToolTransport):
    """Scripted worker: edit unread (refused), read, edit (ok), bash changes the file, edit (stale), read, edit (ok)."""

    def __init__(self):
        super().__init__()
        self.script = [
            ('write', dict(path='notes.py', content='alpha = 1\n')),
            ('bash', dict(command='echo touched')),
            ('edit', dict(path='notes.py', old_text='alpha = 1', new_text='alpha = 2')),
        ]


def test_edit_requires_a_read_of_current_content(tmp_path):
    settings, _, shell, controller, _, ct = setup(tmp_path, total=0, enabled=False, rollover=False)
    settings = replace(settings, worker_tools=('bash', 'read', 'write', 'edit'), edit_requires_read=True)
    transport = ReadBeforeEditTransport()
    transport.script = [
        ('edit', dict(path='absent.py', old_text='a', new_text='b')),
        ('write', dict(path='notes.py', content='alpha = 1\n')),
        ('edit', dict(path='notes.py', old_text='alpha = 1', new_text='alpha = 2')),   # authored: counts as read
        ('bash', dict(command='true')),                                                # fixture shell rewrites result.txt only
        ('edit', dict(path='notes.py', old_text='alpha = 2', new_text='alpha = 3')),   # unchanged: still ok
        ('write', dict(path='other.py', content='x = 1\n')),
        ('edit', dict(path='result.txt', old_text='true', new_text='false')),          # never read: refused
        ('read', dict(path='result.txt')),
        ('edit', dict(path='result.txt', old_text='true', new_text='false')),          # read: ok
    ]
    worker = ModelClient(EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True), transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    outcomes = [json.loads(m['content']) for m in transport.requests[-1]['messages'] if m.get('role') == 'tool']
    assert [o['status'] for o in outcomes] == ['error', 'ok', 'ok', 'completed', 'ok', 'ok', 'error', 'ok', 'ok']
    assert 'requires a read of result.txt first' in outcomes[6]['error']
    assert outcomes[0]['code'] == 'not_a_file'
    assert read_workspace_file(item.workspace(), 'notes.py', byte_limit=1000000, file_limit=1000) == b'alpha = 3\n'


def test_repeated_oversized_writes_to_one_path_force_a_rollover(tmp_path):
    settings, _, shell, controller, _, ct = setup(tmp_path, total=0, enabled=False, rollover=False)
    settings = replace(settings, worker_tools=('bash', 'read', 'write', 'edit'), maximum_tool_argument_characters=300,
                       repeated_failure_rollover=3)
    transport = LoopingTransport()
    transport.script = ([('write', dict(path='big.py', content='x = 0\n' * 80)), ('write', dict(path='big.py', content='y = 1\n'))]
                        + [('write', dict(path='big.py', content='x = %d\n' % i * 80)) for i in range(3)] + [('bash', dict(command='echo ok'))])
    worker = ModelClient(EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True), transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    result = item.run()
    assert result['status'] == 'complete' and result['handoffs'] == 1
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    forced = [e['payload'] for e in events if e['event_type'] == 'rollover_forced']
    assert forced and forced[0]['reason'] == 'oversized_rewrites' and forced[0]['path'] == 'big.py' and forced[0]['count'] == 3


def test_tool_descriptions_state_the_session_contract(tmp_path):
    settings, *_ = setup(tmp_path, total=0, enabled=False, rollover=False)
    plain = {t['function']['name']: t['function']['description'] for t in worker_tools(('bash', 'read', 'write', 'edit'), settings)}
    assert plain['write'].startswith('Create or replace') and 'refused' not in plain['write']
    strict = replace(settings, write_existing_files=False, maximum_write_characters=1500, maximum_edit_characters=1500,
                     edit_requires_read=True)
    bound = {t['function']['name']: t['function']['description'] for t in worker_tools(('bash', 'read', 'write', 'edit'), strict)}
    assert bound['write'].startswith('Create ONE NEW') and 'short skeleton' in bound['write'] and 'replace' not in bound['write'].lower()
    assert 'copied from a read' in bound['edit'] and 'one function body' in bound['edit']


def test_default_settings_register_only_bash_and_no_bound(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2, enabled=False, rollover=False)
    assert settings.worker_tools == ('bash',) and settings.maximum_tool_argument_characters is None
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    assert [tool['function']['name'] for tool in wt.requests[0]['tools']] == ['bash']
    with pytest.raises(ValueError):
        replace(settings, worker_tools=('write',))
    with pytest.raises(ValueError):
        replace(settings, maximum_tool_argument_characters=0)


def test_investigation_budgets_gate_tools_behind_a_concern_and_cap_document_edits(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=24)
    settings = replace(settings, controller=replace(settings.controller,
        investigation_budgets=dict(bootstrap=0, periodic=2, completion=6), maximum_document_edits_per_review=1))
    ct.inspect = True
    # First run: no concern stated; every investigative call must be refused.
    item = FocusedSession.create(tmp_path/'a', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    events = [json.loads(line) for line in (tmp_path/'a'/'events'/'session.jsonl').read_text().splitlines()]
    tools = [e['payload'] for e in events if e['event_type'] == 'controller_tool']
    refused = [t for t in tools if t['name'] in ('workspace_list', 'workspace_read', 'history_read', 'run_check')]
    assert refused and all(t['result'].get('status') == 'error' and 'investigate' in t['result']['error'] for t in refused)
    assert 'check-mutates-copy' not in shell.calls
    assert not [e for e in events if e['event_type'] == 'controller_concern']
    # Second run: a concern unlocks two calls at the periodic review; the third and fourth are refused by budget.
    settings2, worker2, shell2, controller2, wt2, ct2 = setup(tmp_path, total=24)
    settings2 = replace(settings2, controller=replace(settings2.controller,
        investigation_budgets=dict(bootstrap=0, periodic=2, completion=6), maximum_document_edits_per_review=1))
    ct2.inspect = True; ct2.concern = 'The traces show no test run after the last edit.'
    item2 = FocusedSession.create(tmp_path/'b', settings2, worker=worker2, shell=shell2, controller=controller2)
    assert item2.run()['status'] == 'complete'
    events2 = [json.loads(line) for line in (tmp_path/'b'/'events'/'session.jsonl').read_text().splitlines()]
    concerns = [e['payload'] for e in events2 if e['event_type'] == 'controller_concern']
    assert [(c['turn'], c['kind'], c['budget']) for c in concerns] == [(21, 'periodic', 2)]
    t21 = [e['payload'] for e in events2 if e['event_type'] == 'controller_tool' and e['payload']['call_id'].endswith('-21')]
    by_name = {t['name']: t['result'].get('status') for t in t21}
    assert by_name['investigate'] == 'ok' and by_name['workspace_list'] != 'error' and by_name['workspace_read'] != 'error'
    assert by_name['history_read'] == 'error' and by_name['run_check'] == 'error'
    assert 'check-mutates-copy' not in shell2.calls
    # the second project edit in a review is refused by the document-edit cap only if attempted; verify the counter is exposed
    envelope_states = [i['proposed_input']['review_state']['investigation'] for i in ct2.inputs]
    assert envelope_states[0]['kind'] == 'bootstrap' and envelope_states[0]['remaining_tool_calls'] == 0
    assert envelope_states[1]['kind'] == 'periodic' and envelope_states[1]['document_edits_remaining'] == 1


class EmptyOnceTransport:
    """Return an empty completed worker response the first N times a worker request arrives."""

    def __init__(self, original, count=1):
        self.original = original; self.remaining = count; self.empties = 0

    def __call__(self, path, payload, **kwargs):
        if path == '/complete' and 'tools' in payload and self.remaining:
            self.remaining -= 1; self.empties += 1
            return WireResponse(200, json.dumps(dict(choices=[dict(message=dict(role='assistant', content='', reasoning_content=''),
                finish_reason='stop')], usage=dict(prompt_tokens=5, completion_tokens=1))), 0)
        return self.original(path, payload, **kwargs)


def test_empty_worker_response_is_retried_within_the_generation_budget(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2, enabled=False, rollover=False)
    settings = replace(settings, maximum_generation_retries=2)
    transport = EmptyOnceTransport(wt, count=2)
    worker = ModelClient(worker.config, transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    result = item.run()
    assert result['status'] == 'complete' and transport.empties == 2 and result['completed_worker_turns'] == 3
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    rejected = [e['payload'] for e in events if e['event_type'] == 'generation_rejected']
    assert [(r['cause'], r['attempt']) for r in rejected] == [('empty', 1), ('empty', 2)]
    # the retry preface names the empty response, and the successful response cleared the counter
    prefaces = [m['content'] for r in wt.requests for m in r['messages'] if m.get('role') == 'user' and 'previous response was empty' in m.get('content', '')]
    assert len(prefaces) == 1 and 'previous generation was cancelled' not in prefaces[0]
    assert item.status()['generation_rejections'] == {}
    # three empties exceed the budget and block instead of looping
    settings2, worker2, shell2, controller2, wt2, ct2 = setup(tmp_path, total=2, enabled=False, rollover=False)
    settings2 = replace(settings2, maximum_generation_retries=2)
    worker2 = ModelClient(worker2.config, transport=EmptyOnceTransport(wt2, count=3))
    item2 = FocusedSession.create(tmp_path/'blocked', settings2, worker=worker2, shell=shell2, controller=controller2)
    with pytest.raises(GenerationRetryExceeded):
        item2.run()
    assert 'empty responses' in item2.status()['blocked_reason']


def test_paused_session_takes_an_interjection_at_the_turn_boundary(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=3, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    paused = item.run(maximum_worker_turns=1)
    assert paused['status'] == 'paused' and paused['phase'] == 'worker'
    with pytest.raises(ValueError, match='nonempty'):
        item.interject(' ')
    status = item.interject('Effort check: past the estimate; decide whether to keep going or block.')
    assert status['phase'] == 'worker' and status['completed_worker_turns'] == 1
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    assert reopened.run()['status'] == 'complete'
    request = wt.requests[1]['messages']
    assert request[-1] == dict(role='user', content='Effort check: past the estimate; decide whether to keep going or block.')
    assert request[-2]['role'] == 'tool'
    with pytest.raises(ValueError, match='paused worker session'):
        reopened.interject('after completion')
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    assert [e['payload'] for e in events if e['event_type'] == 'interjected'] == [dict(window=0, characters=71)]


def test_retune_changes_wire_view_policy_on_a_saved_session(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=3, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    item.run(maximum_worker_turns=1)
    with pytest.raises(ValueError, match='Not retunable'):
        item.retune(worker_tools=('bash',))
    with pytest.raises(ValueError):
        item.retune(rollover_threshold=10**9)  # must stay below the capacity
    item.retune(reasoning_retention='none', rollover_threshold=settings.session_policy.rollover_threshold-1)
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    assert reopened.settings.session_policy.reasoning_retention == 'none'
    assert reopened.session.policy.reasoning_retention == 'none'
    assert reopened.run()['status'] == 'complete'
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    assert [e['payload'] for e in events if e['event_type'] == 'retuned'] == [
        dict(reasoning_retention='none', rollover_threshold=settings.session_policy.rollover_threshold-1)]
    assert all('reasoning_content' not in m for r in wt.requests[2:] for m in r['messages'] if m.get('role') == 'assistant')


def test_open_drops_a_torn_journal_tail_and_resumes(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=3, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    item.run(maximum_worker_turns=1)
    journal = tmp_path/'session'/'events'/'session.jsonl'
    whole = journal.read_bytes()
    journal.write_bytes(whole+b'{"created_at": "2026-09-18T19:25:33Z", "event_id": "evt_torn", "event_type": "model_resp')
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    assert reopened.run()['status'] == 'complete'
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    assert sum(1 for e in events if e['event_type'] == 'torn_tail_dropped') == 1
    assert not any(e['event_id'] == 'evt_torn' for e in events)


def test_command_tools_render_to_shell_and_run_like_bash(tmp_path):
    from src.focused_agent_session import render_command_tool, worker_tools
    spec = dict(name='cartograph_search', description='Search the widget library.',
                command='cartograph search {query} --language {language} --top-k {top_k} {local_only}',
                parameters=dict(type='object', properties=dict(
                    query=dict(type='string'), language=dict(type='string', default='python'),
                    top_k=dict(type='integer', default=3), local_only=dict(type='boolean', flag='--local-only')),
                    required=['query']))
    assert render_command_tool(spec, dict(query='count csv rows')) == "cartograph search 'count csv rows' --language python --top-k 3"
    assert render_command_tool(spec, dict(query='x', local_only=True, top_k=1)) == "cartograph search x --language python --top-k 1 --local-only"
    with pytest.raises(ValueError, match='missing query'):
        render_command_tool(spec, dict(language='python'))
    with pytest.raises(ValueError, match='unexpected arguments'):
        render_command_tool(spec, dict(query='x', nope=1))
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=1, enabled=False, rollover=False)
    settings = replace(settings, command_tools=(spec,))
    tools = worker_tools(settings.worker_tools, settings)
    assert tools[-1]['function']['name'] == 'cartograph_search' and tools[-1]['function']['parameters']['additionalProperties'] is False
    with pytest.raises(ValueError, match='distinct names'):
        replace(settings, command_tools=(spec, dict(spec, name='bash')))


def test_a_continuation_survives_the_next_handoff(tmp_path):
    """The host's text after a completion is the current objective; a fresh window must see it, not only the assignment."""
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=2, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    assert item.run()['status'] == 'complete'
    wt.total = 6
    item.continue_with('Gate green: record the method as a procedure; nothing else is owed.')
    with item._locked():
        item.state['rollover_requested'] = dict(reason='test', name='bash', count=1, window=0)
        item._save()
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    reopened.run(maximum_worker_turns=3)
    fresh = next(r['messages'] for r in wt.requests if any('current instruction' in (m.get('content') or '') for m in r['messages'] if m.get('role') == 'user'))
    standing = next(m for m in fresh if m.get('role') == 'user' and 'current instruction' in m['content'])
    assert 'Gate green: record the method' in standing['content']
    assert fresh.index(standing) < max(i for i, m in enumerate(fresh) if m.get('role') == 'user')  # before the resume memory


def test_a_generation_block_is_lifted_on_reopen_with_a_fresh_window(tmp_path):
    settings, worker, shell, controller, wt, ct = setup(tmp_path, total=3, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    item.run(maximum_worker_turns=1)
    with item._locked():
        item.state.update(blocked_kind='generation_retries', blocked_reason='worker exhausted its generation recovery budget after repetition responses',
                          generation_rejections={'worker': 3})
        item._save()
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    assert reopened.reset_generation_block() == dict(reason='worker exhausted its generation recovery budget after repetition responses', rejections={'worker': 3})
    assert reopened.reset_generation_block() is None
    assert reopened.status()['blocked_reason'] is None and reopened.state['rollover_requested']['reason'] == 'generation_block_reset'
    assert reopened.run()['status'] == 'complete'
    events = [json.loads(line) for line in (tmp_path/'session'/'events'/'session.jsonl').read_text().splitlines()]
    assert any(e['event_type'] == 'generation_block_reset' for e in events) and any(e['event_type'] == 'rollover_forced' for e in events)


class ImageReadTransport:
    """Scripted worker: write a png, read it, then finish; `vision` decides what the server reports."""

    def __init__(self, vision):
        self.vision = vision
        self.requests = []
        self.script = [('write', dict(path='shot.png', content='not really a png')), ('read', dict(path='shot.png'))]
        self.step = 0

    def props(self):
        return {'modalities': {'vision': self.vision}}

    def __call__(self, path, payload, **kwargs):
        if path == '/template':
            assert all(isinstance(m['content'], str) for m in payload['messages'])
            value = dict(prompt='x'*100)
        elif path == '/tokenize':
            value = dict(tokens=[1]*len(payload['content']))
        else:
            self.requests.append(deepcopy(payload))
            if self.step < len(self.script):
                name, args = self.script[self.step]; self.step += 1
                value = response('Step '+str(self.step), [dict(id='img-'+str(self.step), type='function',
                    function=dict(name=name, arguments=json.dumps(args)))])
            else:
                value = response('Verified final report.')
        return WireResponse(200, json.dumps(value), 0)


def test_read_shows_an_image_only_to_a_model_that_can_see(tmp_path):
    settings, _, shell, controller, _, _ = setup(tmp_path, total=0, enabled=False, rollover=False)
    # An image costs a fixed allowance at the count; the window must have room for one.
    settings = replace(settings, worker_tools=('bash', 'read', 'write'),
                       session_policy=replace(settings.session_policy, context_capacity=20000, rollover_threshold=10000))
    endpoint = EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True)
    for vision in (True, False):
        transport = ImageReadTransport(vision)
        item = FocusedSession.create(tmp_path/('session-'+str(vision)), settings,
                                     worker=ModelClient(endpoint, transport=transport), shell=shell, controller=controller)
        assert item.state['capabilities']['vision'] is vision
        read_description = next(t for t in transport_tools(item) if t['function']['name'] == 'read')['function']['description']
        assert ('shown to you as an image' in read_description) is vision
        result = item.run()
        assert result['status'] == 'complete'
        last = transport.requests[-1]['messages']
        outcome = json.loads([m for m in last if m.get('role') == 'tool'][-1]['content'])
        shown = [m for m in last if m.get('role') == 'user' and isinstance(m.get('content'), list)]
        if vision:
            assert outcome['status'] == 'ok' and outcome['image'] is True and outcome['mime'] == 'image/png'
            assert len(shown) == 1 and shown[0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
            assert any(getattr(e, 'event_type', None) == 'image_shown' for e in item.journal.read_strict('session'))
        else:
            assert outcome['status'] == 'error' and 'cannot see images' in outcome['error'] and not shown


def transport_tools(item):
    return item.session.tools


def test_stop_when_pauses_at_a_turn_boundary_and_the_session_resumes(tmp_path):
    settings, worker, shell, controller, transport, _ = setup(tmp_path, total=6, enabled=False, rollover=False)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    calls = {'n': 0}
    def stop_after_two():
        calls['n'] += 1
        return calls['n'] > 2
    result = item.run(stop_when=stop_after_two)
    assert result['status'] == 'stopped' and 0 < result['completed_worker_turns'] < 6
    assert any(getattr(e, 'event_type', None) == 'stopped' for e in item.journal.read_strict('session'))
    # Nothing was lost: the same session runs on to completion.
    reopened = FocusedSession.open(tmp_path/'session', worker=worker, shell=shell, controller=controller)
    assert reopened.run()['status'] == 'complete'


def test_write_and_edit_refuse_protected_paths(tmp_path):
    settings, _, shell, controller, _, _ = setup(tmp_path, total=0, enabled=False, rollover=False)
    settings = replace(settings, worker_tools=('bash', 'write', 'edit'), protected_paths=('.terra/brief.json', '.terra/map/knowns/*'))
    class T(FileToolTransport):
        def __init__(self):
            super().__init__()
            self.script = [('write', dict(path='.terra/brief.json', content='{}')),
                           ('write', dict(path='/work/.terra/map/knowns/k.json', content='{}')),
                           ('write', dict(path='notes.md', content='ok\n')),
                           ('edit', dict(path='.terra/brief.json', old_text='a', new_text='b'))]
    transport = T()
    worker = ModelClient(EndpointConfig('http://example.invalid', 5, 1000000, {}, '/complete', '/template', '/tokenize', False, True), transport=transport)
    item = FocusedSession.create(tmp_path/'session', settings, worker=worker, shell=shell, controller=controller)
    item.run()
    outcomes = [json.loads(m['content']) for m in transport.requests[-1]['messages'] if m.get('role') == 'tool']
    assert [o['status'] for o in outcomes] == ['error', 'error', 'ok', 'error']
    assert all('a tool owns' in o['error'] for o in outcomes if o['status'] == 'error')
