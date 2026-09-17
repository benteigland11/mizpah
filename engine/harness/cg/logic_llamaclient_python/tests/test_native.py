import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]/'src'))
from llamaclient import LlamaClient
from native import IncompleteGeneration, KnownIssues, SSEDecoder, SyncNativeTransport


def sse(event):
    return ('data: '+json.dumps(event, ensure_ascii=False)+'\n\n').encode()


def delta(value, finish=None):
    return dict(id='response-id', model='example', choices=[dict(index=0, delta=value, finish_reason=finish)])


@contextmanager
def server(chunks, *, repeat=False, delay=0):
    requests = []
    disconnected = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, payload, self.headers.get('Authorization')))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if self.path == '/complete' else 'application/json')
            self.end_headers()
            try:
                if self.path != '/complete':
                    self.wfile.write(json.dumps(dict(prompt='template', tokens=[1, 2])).encode())
                    return
                while True:
                    for chunk in chunks:
                        if delay:
                            time.sleep(delay)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    if not repeat:
                        break
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()
    service = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:'+str(service.server_port), requests, disconnected
    finally:
        service.shutdown()
        service.server_close()
        thread.join()


def test_native_fragmented_parallel_tools_and_request_fields_survive():
    first = delta(dict(role='assistant', reasoning_content='Think é', tool_calls=[
        dict(index=1, id='call-', type='function', function=dict(name='mea', arguments='{"b":')),
        dict(index=0, id='first', type='function', function=dict(name='read', arguments='{"a":1}'))]))
    second = delta(dict(reasoning_content='終', tool_calls=[
        dict(index=1, id='second', function=dict(name='sure', arguments='2}'))]), 'tool_calls')
    usage = dict(choices=[], usage=dict(prompt_tokens=17, completion_tokens=9, total_tokens=26), timings=dict(predicted_n=9))
    wire = sse(first)+sse(second)+sse(usage)+b'data: [DONE]\n\n'
    request = dict(messages=[dict(role='user', content='example')], reasoning_budget=-1,
                   chat_template_kwargs=dict(enable_thinking=True), seed=13, stream=False)
    with server([wire[i:i+1] for i in range(len(wire))]) as (url, requests, _):
        result = SyncNativeTransport(LlamaClient(base_url=url), '/complete', {'Authorization':'test-token'})('/complete', request)
    assert result.error is None
    body = json.loads(result.body)
    calls = body['choices'][0]['message']['tool_calls']
    assert [c['id'] for c in calls] == ['first', 'call-second']
    assert calls[1]['function'] == dict(name='measure', arguments='{"b":2}')
    assert body['choices'][0]['message']['reasoning_content'] == 'Think é終'
    assert body['usage'] == usage['usage'] and body['timings'] == usage['timings']
    assert requests[0][1] == request | dict(stream=True, stream_options=dict(include_usage=True))
    assert requests[0][2] == 'test-token'
    assert request['stream'] is False


@pytest.mark.parametrize('wire', [
    sse(delta(dict(content='partial'))),
    sse(delta(dict(content='partial')))+b'data: [DONE]\n\n',
    sse(delta(dict(content='partial'), 'stop')),
    b'data: {bad}\n\n', b'data: {"choices":NaN}\n\n', b'data: {"error":"bad"}\n\n',
    b'data: {"choices":"bad"}\n\n', b'data: '+b'\xff'+b'\n\n',
])
def test_incomplete_or_malformed_stream_never_reports_success(wire):
    with server([wire]) as (url, requests, _):
        result = SyncNativeTransport(LlamaClient(base_url=url), '/complete', {})('/complete', {})
    assert result.failure_kind == 'incomplete_stream'
    assert not result.definitive and result.body == ''
    assert len(requests) == 1


@pytest.mark.parametrize('channel', ['content', 'reasoning_content', 'tool'])
def test_repetition_closes_real_upstream_and_preserves_bounded_failure(channel):
    text = 'The same repeated sentence with no additional information. '*12
    value = {channel:text} if channel != 'tool' else dict(tool_calls=[dict(index=0, function=dict(arguments=text))])
    with server([sse(delta(value))], repeat=True, delay=.001) as (url, requests, closed):
        client = LlamaClient(base_url=url, known_issues=KnownIssues(repetition={}), diagnostic_characters=400)
        result = SyncNativeTransport(client, '/complete', {})('/complete', {})
        assert closed.wait(2), 'server did not observe client cancellation'
    assert len(requests) == 1 and result.definitive and result.body == ''
    assert result.failure_kind == 'repetition_detected' and result.evidence['upstream_closed']
    assert len(result.evidence['raw_event_tail']) <= 400
    assert client._active_response is None and client._native_busy is False


def test_cross_call_policy_is_opt_in_and_operates_across_unique_ids():
    chunks = [sse(delta(dict(tool_calls=[dict(index=i, id='call-'+str(i), type='function',
        function=dict(name='read', arguments='{"path":"item"}'))]))) for i in range(6)]
    chunks += [sse(delta({}, 'tool_calls')), b'data: [DONE]\n\n']
    for policy, expected in [(None, None), ({'identical_tool_calls':4}, 'repetition_detected')]:
        with server(chunks) as (url, _, _):
            client = LlamaClient(base_url=url, known_issues={'repetition':policy})
            result = SyncNativeTransport(client, '/complete', {})('/complete', {})
        assert result.failure_kind == expected
        if expected:
            assert result.evidence['detection']['repeated_calls'] == 4
        else:
            assert len(json.loads(result.body)['choices'][0]['message']['tool_calls']) == 6


def test_native_transport_works_inside_event_loop_and_keeps_exact_json_payload():
    async def caller(url):
        client = LlamaClient(base_url=url)
        return SyncNativeTransport(client, '/complete', {})('/template', dict(messages=[], tools=[], extra=3))
    with server([]) as (url, requests, _):
        result = asyncio.run(caller(url))
    assert json.loads(result.body)['prompt'] == 'template'
    assert requests[0][1] == dict(messages=[], tools=[], extra=3)


def test_timeout_and_response_bound_are_uncertain_without_retry():
    for options, delay in [(dict(timeout=.01), .04), (dict(maximum_response_bytes=50), 0)]:
        with server([sse(delta(dict(content='x'*1000)))], delay=delay) as (url, requests, _):
            result = SyncNativeTransport(LlamaClient(base_url=url, **options), '/complete', {})('/complete', {})
        assert result.failure_kind == 'incomplete_stream' and not result.definitive
        assert len(requests) == 1 and result.body == ''


def test_sse_multiline_crlf_comments_and_utf8_are_boundary_independent():
    wire = ': comment\r\ndata: {"text":\r\ndata: "é終"}\r\n\r\n'.encode()
    parser = SSEDecoder(1000)
    events = []
    for byte in wire:
        events.extend(parser.feed(bytes([byte])))
    events.extend(parser.feed(b'', final=True))
    assert [json.loads(item) for item in events] == [dict(text='é終')]


def test_known_issue_input_mutation_does_not_change_policy():
    original = dict(identical_tool_calls=4)
    issues = KnownIssues(repetition=original)
    original['identical_tool_calls'] = 2
    assert issues.repetition['identical_tool_calls'] == 4


@pytest.mark.parametrize('terminal', [False, True])
def test_high_level_stream_never_publishes_truncated_tools_or_done(terminal):
    chunks = [sse(delta(dict(tool_calls=[dict(index=0, id='partial', type='function',
        function=dict(name='read', arguments='{"path":'))])))]
    if terminal:
        chunks += [sse(delta({}, 'length')), b'data: [DONE]\n\n']
    async def collect(url):
        result = []
        client = LlamaClient(base_url=url)
        # High-level completion endpoint is relative to the configured API root.
        native_stream = client.stream_raw
        async def stream(payload):
            async for event in native_stream(payload, path='/complete'):
                yield event
        client.stream_raw = stream
        with pytest.raises(IncompleteGeneration):
            async for event in client.stream_chat([dict(role='user', content='example')]):
                result.append(event)
        return result
    with server(chunks) as (url, _, _):
        events = asyncio.run(collect(url))
    assert all(event['type'] != 'tool_calls' and not event.get('done') for event in events)
    assert any(event['type'] == 'tool_call_delta' and event['provisional'] for event in events)
