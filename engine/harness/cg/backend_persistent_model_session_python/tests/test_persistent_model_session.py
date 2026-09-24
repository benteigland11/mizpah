from dataclasses import asdict, replace
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from src.persistent_model_session import (
    IMAGE_TOKEN_ALLOWANCE, DirectJsonTransport, EndpointConfig, ModelClient, ModelTransportError,
    PersistentSession, RejectedGeneration, SessionPolicy, WireResponse, parse_turn, project_completed_arguments,
    wire_messages,
)


def config():
    return EndpointConfig('http://example.invalid', 2, 4096, {'Content-Type': 'application/json'},
                          '/complete', '/template', '/tokenize', True, True)


@pytest.mark.parametrize('definitive,closed,rejected', [(True, True, True), (False, True, False), (True, False, False)])
def test_repetition_outcome_is_audited_before_typed_rejection(definitive, closed, rejected):
    events = []
    failed = WireResponse(200, '', .3, 'cancelled', 'repetition_detected', dict(upstream_closed=closed), definitive)
    client = ModelClient(config(), transport=lambda path, payload: failed,
                         observer=lambda kind, value: events.append((kind, value)))
    with pytest.raises(ModelTransportError) as captured:
        client.complete(dict(messages=[]), 'worker')
    assert isinstance(captured.value, RejectedGeneration) is rejected
    assert [event[0] for event in events] == ['model_request', 'model_response']
    assert events[-1][1]['elapsed_seconds'] == .3
    assert events[-1][1]['evidence'] == dict(upstream_closed=closed)


def session():
    return PersistentSession([{'role': 'system', 'content': 'contract'}, {'role': 'user', 'content': 'assignment'}],
        [{'type': 'function', 'function': {'name': 'measure'}}], {'temperature': .5, 'seed': 7},
        SessionPolicy(1000, 500, 200, 100, 2, 8, 'Write a handoff', 'Resume from this handoff', {}))


def response(content='answer', calls=None):
    message = dict(role='assistant', content=content, reasoning_content='retained reasoning')
    if calls is not None:
        message['tool_calls'] = calls
    return dict(choices=[dict(message=message, finish_reason='tool_calls' if calls else 'stop')],
                usage=dict(prompt_tokens=5, completion_tokens=6))


def call(value='one'):
    return dict(id=value, type='function', function=dict(name='measure', arguments='{"value":1}'))


def test_a_handoff_that_keeps_tools_shares_the_worker_prefix():
    item = session()
    dropped = item.handoff_payload()
    assert 'tools' not in dropped and 'tool_choice' not in dropped
    item.policy = replace(item.policy, handoff_keeps_tools=True)
    kept, worker = item.handoff_payload(), item.payload()
    assert kept['tools'] == worker['tools'] and kept['tool_choice'] == 'none'
    assert kept['messages'][:-1] == worker['messages'] and kept['messages'][-1]['content'] == 'Write a handoff'


def test_unrestricted_outputs_use_separate_input_headroom_and_preserve_archive():
    item = session()
    item.policy = replace(item.policy, worker_output_tokens=-1, handoff_output_tokens=-1,
                          output_headroom_tokens=150)
    assert item.payload()['max_tokens'] == -1
    assert item.handoff_payload()['max_tokens'] == -1
    item.assert_fits(850, -1)
    with pytest.raises(ValueError, match='capacity'):
        item.assert_fits(851, -1)
    transition = item.rollover('Carry the established result.', source_archive='records/window.json')
    assert json.loads(transition['new_messages'][-1]['content'].split('\n', 1)[1])['source_archive'] == 'records/window.json'
    restored = PersistentSession.from_state(item.export_state())
    assert restored.payload()['max_tokens'] == -1
    assert restored.policy.output_headroom_tokens == 150


def test_handoff_projection_cannot_replace_original_assignment_or_mutate_history():
    item = session()
    item.accept(response('very long original output'))
    projected = item.base_messages + [dict(role='assistant', content='Explicit excerpt')]
    payload = item.handoff_payload(history=projected)
    assert payload['messages'][2]['content'] == 'Explicit excerpt'
    assert item.messages[2]['content'] == 'very long original output'
    with pytest.raises(ValueError, match='base messages'):
        item.handoff_payload(history=[dict(role='user', content='changed assignment')])


def test_transport_observer_exact_order_and_count():
    events = []
    def transport(path, body):
        assert events[-1][0] == 'model_request' and events[-1][1]['body'] == body
        data = {'/template': {'prompt': 'rendered'}, '/tokenize': {'tokens': [1, 2, 3]}, '/complete': response()}
        return WireResponse(200, json.dumps(data[path]), .1)
    client = ModelClient(config(), transport=transport, observer=lambda kind, value: events.append((kind, value)))
    assert client.count(session().payload(), 'count') == dict(prompt='rendered', tokens=3)
    assert parse_turn(client.complete(session().payload(), 'worker')).completion_tokens == 6
    assert [entry[0] for entry in events] == ['model_request', 'model_response']*3
    assert len({entry[1]['request_id'] for entry in events}) == 3


@pytest.mark.parametrize('wire', [WireResponse(500, '{}', 1), WireResponse(None, '', 1, 'offline'),
    WireResponse(200, 'bad JSON', 1), WireResponse(200, '[]', 1), WireResponse(200, '{"x":NaN}', 1)])
def test_exchange_failure_never_retries(wire):
    calls = []
    client = ModelClient(config(), transport=lambda p, b: calls.append(p) or wire)
    with pytest.raises(ModelTransportError):
        client.complete({}, 'worker')
    assert calls == ['/complete']


def test_transport_exception_recorded_and_observer_fail_closed():
    events=[]
    def broken(path, body):
        raise RuntimeError('uncertain')
    client=ModelClient(config(), transport=broken, observer=lambda *args: events.append(args))
    with pytest.raises(ModelTransportError):
        client.complete({}, 'worker')
    assert events[-1][1]['error'] == 'RuntimeError: uncertain'
    calls=[]
    def bad_observer(*args):
        raise OSError('journal unavailable')
    client=ModelClient(config(), transport=lambda *args: calls.append(args), observer=bad_observer)
    with pytest.raises(OSError):
        client.complete({}, 'worker')
    assert not calls


@pytest.mark.parametrize('data', [{'prompt': 7}, {'prompt': 'text', 'tokens': ['a']}])
def test_bad_count_response(data):
    client=ModelClient(config(), transport=lambda *args: WireResponse(200, json.dumps(data), 0))
    with pytest.raises(ModelTransportError):
        client.count({}, 'count')


def test_real_transport_request_contract(monkeypatch):
    class Reply(io.BytesIO):
        status=200
    def fake(request, timeout):
        assert request.method == 'POST' and timeout == 2
        assert json.loads(request.data) == {'x': 1}
        return Reply(b'{}')
    monkeypatch.setattr('src.persistent_model_session.urlopen', fake)
    assert DirectJsonTransport(config())('/complete', {'x':1}).body == '{}'


@pytest.mark.parametrize('mode', ['http', 'network', 'limit'])
def test_direct_transport_failures(monkeypatch, mode):
    class Reply(io.BytesIO):
        status=200
    def fake(*args, **kwargs):
        if mode == 'http':
            raise HTTPError('http://example.invalid', 429, 'busy', {}, io.BytesIO(b'busy'))
        if mode == 'network':
            raise URLError('offline')
        return Reply(b'x'*5000)
    monkeypatch.setattr('src.persistent_model_session.urlopen', fake)
    wire=DirectJsonTransport(config())('/complete', {})
    if mode == 'http':
        assert wire.status == 429 and wire.body == 'busy'
    else:
        assert wire.error


def test_pending_tools_and_rollover_are_explicit():
    s=session()
    s.accept(response('', [call('one'), call('two')]))
    for action in (s.payload, s.handoff_payload, lambda: s.rollover('handoff'), lambda: s.accept(response()), lambda: s.append_guidance('guide')):
        with pytest.raises(ValueError): action()
    with pytest.raises(ValueError): s.append_tool_result('two', 'measure', 'wrong order')
    s.append_tool_result('one', 'measure', 'abcdefgh')
    restored=PersistentSession.from_state(s.export_state())
    restored.append_tool_result('two', 'measure', '123456')
    tail = restored.recent_tail()
    assert tail[1] == dict(tool_call_id='two', name='measure', content='123456', truncated=False)
    assert tail[0]['content'] == 'ab' and tail[0]['truncated'] is True
    # A cut result says what ran and where the rest is — this one names no file, so it points at the window archive.
    assert tail[0]['note'].startswith('result cut at 2 of 8 characters; the call ran in full') and '.session-history/' in tail[0]['note']
    boundary=restored.rollover('keep the findings')
    assert boundary['old_messages'][2]['reasoning_content'] == 'retained reasoning'
    assert restored.window_index == 1 and len(restored.messages) == 3
    assert restored.messages[:2] == s.base_messages
    assert 'keep the findings' in restored.messages[-1]['content']
    assert restored.seen_tool_ids == ['one', 'two']
    restored.append_guidance('Check the stated requirement.')
    assert restored.messages[-1]['role'] == 'user'
    with pytest.raises(ValueError): restored.accept(response('', [call('one')]))


def test_payload_copy_handoff_tail_and_limits():
    s=session()
    payload=s.payload(); payload['messages'][0]['content']='changed'
    assert s.messages[0]['content'] == 'contract'
    assert not s.needs_rollover(499) and s.needs_rollover(500)
    s.assert_fits(800, 200)
    with pytest.raises(ValueError): s.assert_fits(801, 200)
    handoff=s.handoff_payload()
    assert 'tools' not in handoff and handoff['max_tokens'] == 100
    assert handoff['messages'][-1]['content'] == 'Write a handoff'
    assert len(s.messages) == 2
    with pytest.raises(ValueError): s.rollover('')
    with pytest.raises(ValueError): s.append_guidance('')
    s.policy=SessionPolicy(**(asdict(s.policy)|{'recent_result_count':0}))
    s.accept(response('', [call()]))
    s.append_tool_result('one', 'measure', 'result')
    assert s.recent_tail() == []


def test_completed_action_and_unanswered_results_survive_worker_handoff():
    s = session()
    s.policy = SessionPolicy(**(asdict(s.policy)|{'recent_result_characters':1000}))
    s.accept(response('Read the next item.', [call('one')]))
    s.append_tool_result('one', 'measure', 'Observed fact; its follow-up is not yet performed.')
    raw = s.export_state()
    handoff = s.handoff_payload()
    assert 'not yet acted on its results' in handoff['messages'][-1]['content']
    assert 'Observed fact' in handoff['messages'][-1]['content']
    assert s.export_state() == raw
    s.rollover('My next action is to record the observed fact.', source_archive='records/previous.json')
    resumed = json.loads(s.messages[-1]['content'].split('\n', 1)[1])
    assert resumed['continuation_boundary']['awaiting_worker_response']
    assert resumed['continuation_boundary']['tool_results'][0]['tool_call_id'] == 'one'
    assert resumed['source_archive'] == 'records/previous.json'
    restored = PersistentSession.from_state(s.export_state())
    assert restored.payload() == s.payload()


def test_continuation_boundary_distinguishes_answered_tools_and_labeled_excerpts():
    s = session()
    s.accept(response('An action longer than the tail budget', [call('one')]))
    s.append_tool_result('one', 'measure', 'observed')
    boundary = s.continuation_boundary()
    assert boundary['last_action']['truncated']
    assert len(boundary['last_action']['text']) == s.policy.recent_result_characters
    s.accept(response('I have acted on that result.'))
    assert s.continuation_boundary() == dict(awaiting_worker_response=False)


@pytest.mark.parametrize('overrides', [{'context_capacity':0}, {'rollover_threshold':1000}, {'recent_result_count':-1}, {'handoff_prompt':''}, {'reasoning_retention':'recent'}, {'argument_excerpt_characters':-1}])
def test_bad_policy(overrides):
    with pytest.raises(ValueError): SessionPolicy(**(asdict(session().policy)|overrides))


def test_invalid_session_and_restore():
    with pytest.raises(ValueError): PersistentSession([], [], {}, session().policy)
    with pytest.raises(ValueError): PersistentSession(session().base_messages, [], {'messages':[]}, session().policy)
    state=session().export_state(); state['pending_tools']=['unknown']
    with pytest.raises(ValueError): PersistentSession.from_state(state)
    with pytest.raises(ValueError): session().accept(response('', [call(), call()]))


@pytest.mark.parametrize('overrides', [{'base_url':'invalid'}, {'timeout_seconds':0}, {'completion_path':'invalid'}])
def test_bad_endpoint(overrides):
    with pytest.raises(ValueError): EndpointConfig(**(asdict(config())|overrides))


@pytest.mark.parametrize('bad', [dict(choices=[]), dict(choices=[{'message':{}, 'finish_reason':None}], usage={}),
    response()|{'usage':{'prompt_tokens':True, 'completion_tokens':1}}])
def test_bad_turn_topology(bad):
    with pytest.raises(ValueError): parse_turn(bad)


@pytest.mark.parametrize('overrides', [{'content':[]}, {'reasoning_content':{}}, {'tool_calls':'bad'},
    {'tool_calls':[{}]}, {'tool_calls':[call()|{'function':{}}]}])
def test_bad_message(overrides):
    bad=response(); bad['choices'][0]['message'].update(overrides)
    with pytest.raises(ValueError): parse_turn(bad)


def test_per_request_deadline_is_bounded_and_observed(monkeypatch):
    class Reply(io.BytesIO):
        status=200
    observed=[]
    def fake(request,timeout):
        observed.append(timeout)
        return Reply(b'{}')
    monkeypatch.setattr('src.persistent_model_session.urlopen',fake)
    client=ModelClient(config())
    client.complete({},'worker',timeout_seconds=.5)
    client.complete({},'worker',timeout_seconds=10)
    assert observed==[.5,2]
    with pytest.raises(ValueError): DirectJsonTransport(config())('/complete',{},timeout_seconds=0)


def retained(retention):
    s = PersistentSession(session().base_messages, session().tools, session().generation,
                          replace(session().policy, reasoning_retention=retention))
    s.accept(response('', [call('a')])); s.append_tool_result('a', 'measure', 'r1')
    s.accept(response('', [call('b')])); s.append_tool_result('b', 'measure', 'r2')
    s.accept(response('', [call('c')])); s.append_tool_result('c', 'measure', 'r3')
    return s


def reasoning_positions(messages):
    return [i for i, m in enumerate(messages) if m.get('reasoning_content')]


@pytest.mark.parametrize('retention,expected', [('all', [2, 4, 6]), ('latest', [6]), ('none', [])])
def test_reasoning_retention_shapes_wire_view_only(retention, expected):
    s = retained(retention)
    assert reasoning_positions(s.messages) == [2, 4, 6]
    for _ in range(2):
        assert reasoning_positions(s.payload()['messages']) == expected
    assert reasoning_positions(s.messages) == [2, 4, 6]
    state = s.export_state()
    assert reasoning_positions(state['messages']) == [2, 4, 6]
    assert state['policy']['reasoning_retention'] == retention
    assert reasoning_positions(PersistentSession.from_state(state).payload()['messages']) == expected
    for message in s.payload()['messages']:
        assert set(message) - {'reasoning_content'} == set(s.messages[s.payload()['messages'].index(message)]) - {'reasoning_content'}


def test_latest_reasoning_follows_the_step_in_progress():
    s = retained('latest')
    wire = s.payload()['messages']
    assert reasoning_positions(wire) == [6] and wire[6]['tool_calls'][0]['id'] == 'c'
    s.accept(response('', [call('d')]))
    assert reasoning_positions(wire_messages(s.messages, 'latest')) == [8]
    s.append_tool_result('d', 'measure', 'r4')
    s.append_guidance('steer')
    assert reasoning_positions(s.payload()['messages']) == [8]
    s.accept(response('final'))
    assert reasoning_positions(s.payload()['messages']) == [11]
    assert s.payload()['messages'][11]['content'] == 'final'


def test_handoff_payload_and_explicit_history_use_same_retention():
    s = retained('latest')
    assert reasoning_positions(s.handoff_payload()['messages']) == [6]
    history = s.messages[:5]
    assert reasoning_positions(s.handoff_payload(history=history)['messages']) == [4]
    assert reasoning_positions(history) == [2, 4]


def test_old_state_without_retention_field_defaults_to_all():
    state = retained('latest').export_state(); del state['policy']['reasoning_retention']
    restored = PersistentSession.from_state(state)
    assert restored.policy.reasoning_retention == 'all'
    assert reasoning_positions(restored.payload()['messages']) == [2, 4, 6]


def test_wire_messages_is_pure_and_validates():
    original = [dict(role='assistant', reasoning_content='x', content=None, tool_calls=[call()]),
                dict(role='tool', tool_call_id='one', content='r'),
                dict(role='assistant', reasoning_content='y', content='done')]
    frozen = json.dumps(original)
    assert reasoning_positions(wire_messages(original, 'latest')) == [2]
    assert reasoning_positions(wire_messages(original, 'none')) == []
    assert wire_messages(original, 'all') == original and wire_messages(original, 'all') is not original
    assert json.dumps(original) == frozen
    assert wire_messages([dict(role='user', content='only')], 'latest') == [dict(role='user', content='only')]
    with pytest.raises(ValueError): wire_messages(original, 'recent')


def big_call(identity, size):
    return dict(id=identity, type='function', function=dict(name='write', arguments=json.dumps(dict(path='item.py', content='x'*size))))


def test_completed_arguments_are_excerpted_only_after_their_result_arrives():
    s = PersistentSession(session().base_messages, session().tools, session().generation,
                          replace(session().policy, argument_excerpt_characters=20))
    s.accept(response('', [big_call('a', 500)]))
    # pending: full arguments on the wire (payload() itself refuses pending tools, so inspect the view)
    view = s._wire_view(s.messages)
    assert json.loads(view[2]['tool_calls'][0]['function']['arguments'])['content'] == 'x'*500
    s.append_tool_result('a', 'write', json.dumps(dict(status='ok')), arguments_archive='.tool-output/a.args.json')
    first = s.payload()['messages']
    content = json.loads(first[2]['tool_calls'][0]['function']['arguments'])['content']
    # An applied call stays verbatim: the worker's working memory (v8c), and nothing in its own words to copy.
    assert content == 'x'*500 and 'transcript note' not in content
    assert PersistentSession.from_state(s.export_state()).argument_archives == {'a': '.tool-output/a.args.json'}
    with pytest.raises(ValueError):
        s2 = retained('latest'); s2.accept(response('', [call('z')])); s2.append_tool_result('z', 'measure', 'r', arguments_archive='')
    assert json.loads(first[2]['tool_calls'][0]['function']['arguments'])['path'] == 'item.py'
    # stored history keeps the original; projection is deterministic across requests
    assert json.loads(s.messages[2]['tool_calls'][0]['function']['arguments'])['content'] == 'x'*500
    s.accept(response('', [big_call('b', 30)])); s.append_tool_result('b', 'write', '{"status":"rejected"}')
    second = s.payload()['messages']
    assert second[2] == first[2]
    rejected = second[4]['tool_calls'][0]['function']['arguments']
    # A rejected call is projected and never described as applied (it was, until 2026-09-22: "applied … Nothing to redo").
    assert 'NOT applied' in rejected and 'says rejected' in rejected and 'were applied' not in rejected and 'saved at' not in rejected
    assert s.export_state()['policy']['argument_excerpt_characters'] == 20


def test_argument_projection_disabled_by_default_and_handles_shapes():
    s = retained('latest')
    assert s.policy.argument_excerpt_characters is None
    assert [m.get('tool_calls') for m in s.payload()['messages']] == [m.get('tool_calls') for m in wire_messages(s.messages, 'latest')]
    messages = [dict(role='assistant', content=None, tool_calls=[
                    dict(id='one', type='function', function=dict(name='t', arguments=dict(nested=dict(text='y'*50, n=3), items=['z'*50, 'short']))),
                    dict(id='two', type='function', function=dict(name='t', arguments='not json '+'w'*50))]),
                dict(role='tool', tool_call_id='one', content='{"status": "rejected"}'), dict(role='tool', tool_call_id='two', content='{"status": "rejected"}')]
    out = project_completed_arguments(messages, 10)
    args = out[0]['tool_calls'][0]['function']['arguments']
    assert args['nested']['n'] == 3 and args['nested']['text'].startswith('y'*10) and 'of its 50 characters' in args['nested']['text']
    assert args['items'][1] == 'short' and 'of its 50 characters' in args['items'][0]
    assert out[0]['tool_calls'][1]['function']['arguments'].startswith('not json ') and 'of its 59 characters' in out[0]['tool_calls'][1]['function']['arguments']
    multi = project_completed_arguments([dict(role='assistant', content=None, tool_calls=[dict(id='m', type='function',
        function=dict(name='t', arguments=json.dumps(dict(content='first line\nsecond line\nthird'))))]),
        dict(role='tool', tool_call_id='m', content='{"status": "error"}')], 12)
    shown = json.loads(multi[0]['tool_calls'][0]['function']['arguments'])['content']
    assert shown.startswith('first line\n[transcript note') and '(3 lines)' in shown and 'second line' not in shown
    applied = [dict(role='assistant', content=None, tool_calls=[dict(id='ok', type='function', function=dict(name='t', arguments=dict(text='y'*50)))]),
               dict(role='tool', tool_call_id='ok', content='{"status": "ok"}')]
    assert project_completed_arguments(applied, 10) == applied
    assert project_completed_arguments(messages, None) == messages
    with pytest.raises(ValueError): project_completed_arguments(messages, -1)


def test_capabilities_read_from_transport_props_and_default_text_only():
    class Transport:
        def __call__(self, path, body):
            return WireResponse(200, '{}', .1)
        def props(self):
            return {'modalities': {'vision': True, 'audio': False}, 'chat_template_caps': {'supports_parallel_tool_calls': True},
                    'model_path': '/m.gguf', 'default_generation_settings': {'n_ctx': 61440}}
    caps = ModelClient(config(), transport=Transport()).capabilities()
    assert caps['vision'] is True and caps['audio'] is False and caps['context'] == 61440 and caps['error'] is None
    assert caps['template']['supports_parallel_tool_calls'] is True
    # No /props behind the endpoint: text-only, never an exception.
    blind = ModelClient(config(), transport=lambda path, body: WireResponse(200, '{}', .1)).capabilities(timeout_seconds=0.2)
    assert blind['vision'] is False and blind['error']


def test_images_count_as_an_allowance_and_only_the_latest_stays_in_view():
    def transport(path, body):
        if path == '/template':
            # Content parts must never reach the template endpoint: it renders strings.
            assert all(isinstance(m['content'], str) for m in body['messages'])
            return WireResponse(200, json.dumps({'prompt': 'rendered'}), .1)
        return WireResponse(200, json.dumps({'tokens': [1, 2]}), .1)
    item = session()
    item.append_image('read a.png (image):', 'image/png', 'AAAA')
    item.append_guidance('what do you see')
    item.append_image('read b.png (image):', 'image/png', 'BBBB')
    counted = ModelClient(config(), transport=transport).count(item.payload(), 'count')
    assert counted['images'] == 1 and counted['tokens'] == 2+IMAGE_TOKEN_ALLOWANCE   # the wire view holds one image
    view = item.payload()['messages']
    with_image = [m for m in view if isinstance(m.get('content'), list)]
    assert len(with_image) == 2
    assert with_image[0]['content'][1]['type'] == 'text' and 'no longer in view' in with_image[0]['content'][1]['text']
    assert with_image[1]['content'][1]['type'] == 'image_url' and with_image[1]['content'][1]['image_url']['url'].endswith('BBBB')
    # Stored messages keep both images: the wire view is a projection.
    assert sum(1 for m in item.messages if isinstance(m.get('content'), list)
               and m['content'][1]['type'] == 'image_url') == 2


def test_a_cut_shell_result_keeps_its_file_pointers_in_the_note():
    """The cut takes the end of a shell result, where `output_files` sit; the note restates them (audit, 2026-09-22)."""
    from src.persistent_model_session import cut_note
    full = json.dumps(dict(status='ok', exit_code=0, stdout='x'*5000, output_truncated=True,
                           output_files=['.tool-output/c1.stdout', '.tool-output/c1.stderr']))
    note = cut_note(full, 1200)
    assert note.startswith(f'result cut at 1,200 of {len(full):,} characters; the call ran in full')
    assert '.tool-output/c1.stdout, .tool-output/c1.stderr' in note and 'do not re-run' in note
    stopped = cut_note(json.dumps(dict(status='output_limit', output_files=['.tool-output/c2.stdout'])), 10)
    assert 'did NOT finish' in stopped
    refused = cut_note(json.dumps(dict(status='rejected')), 5, '.tool-output/c3.args.json')
    assert 'NOT applied' in refused and '.tool-output/c3.args.json' in refused
