"""Native single-attempt JSON/SSE exchange with explicit incomplete outcomes."""
from __future__ import annotations

import asyncio
import codecs
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import time
from typing import Any, AsyncIterator, Mapping

import aiohttp

if __package__:
    from .repetition_guard import RepetitionGuard
else:
    from repetition_guard import RepetitionGuard


async def _chunks(content: Any) -> AsyncIterator[bytes]:
    iterator = content.iter_any() if hasattr(content, 'iter_any') else content
    async for chunk in iterator:
        yield chunk


def _invalid_number(value: str) -> None:
    raise ValueError('Non-finite JSON number: '+value)


@dataclass(frozen=True)
class KnownIssues:
    """Explicit caller-selected model policy; no model-name guessing."""

    repetition: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.repetition is not None:
            guard = RepetitionGuard(**self.repetition)
            object.__setattr__(self, 'repetition', guard.policy())


class IncompleteGeneration(RuntimeError):
    """Diagnostic partial output is never an executable model response."""

    def __init__(self, kind: str, message: str, *, evidence: dict | None = None,
                 status: int | None = None, elapsed_seconds: float = 0,
                 definitive: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.evidence = evidence or {}
        self.status = status
        self.elapsed_seconds = elapsed_seconds
        self.definitive = definitive


class SSEDecoder:
    """Decode SSE independently of byte boundaries, including UTF-8 splits."""

    def __init__(self, maximum_event_bytes: int) -> None:
        self.maximum_event_bytes = maximum_event_bytes
        self.decoder = codecs.getincrementaldecoder('utf-8')('strict')
        self.buffer = ''
        self.data: list[str] = []

    def feed(self, chunk: bytes, *, final: bool = False) -> list[str]:
        self.buffer += self.decoder.decode(chunk, final=final)
        result = []
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            line = line.removesuffix('\r')
            if not line:
                if self.data:
                    result.append('\n'.join(self.data))
                    self.data = []
            elif line.startswith('data:'):
                self.data.append(line[5:].removeprefix(' '))
            if len((self.buffer + '\n'.join(self.data)).encode()) > self.maximum_event_bytes:
                raise ValueError('SSE event exceeded configured byte limit')
        if len((self.buffer + '\n'.join(self.data)).encode()) > self.maximum_event_bytes:
            raise ValueError('SSE event exceeded configured byte limit')
        if final and (self.buffer or self.data):
            raise ValueError('SSE ended inside an event')
        return result


class NativeAssembler:
    """Retain native choices, reasoning, call identities, usage and finish reasons."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.choices: dict[int, dict] = {}

    def add(self, event: dict) -> None:
        self.metadata.update({k:deepcopy(v) for k,v in event.items() if k != 'choices'})
        for value in event.get('choices', []):
            index = value.get('index', 0)
            if type(index) is not int or index < 0:
                raise ValueError('Invalid choice index')
            choice = self.choices.setdefault(index, dict(index=index,
                message=dict(role='assistant', content=None), finish_reason=None))
            delta = value.get('delta', {})
            if choice['finish_reason'] is not None and delta:
                raise ValueError('Delta followed a finished choice')
            message = choice['message']
            for key, part in delta.items():
                if key in ('content', 'reasoning_content', 'reasoning', 'refusal'):
                    if part is not None:
                        if not isinstance(part, str):
                            raise ValueError('Text delta must be a string')
                        message[key] = (message.get(key) or '') + part
                elif key == 'tool_calls':
                    calls = message.setdefault('tool_calls', {})
                    for call in part:
                        position = call.get('index', 0)
                        if type(position) is not int or position < 0:
                            raise ValueError('Invalid tool index')
                        target = calls.setdefault(position, dict(id='', type='function',
                            function=dict(name='', arguments='')))
                        if call.get('id'):
                            target['id'] += call['id']
                        if call.get('type'):
                            target['type'] = call['type']
                        for field, text in call.get('function', {}).items():
                            if field not in ('name', 'arguments') or not isinstance(text, str):
                                raise ValueError('Unsupported tool function delta')
                            target['function'][field] += text
                else:
                    message[key] = deepcopy(part)
            if value.get('finish_reason') is not None:
                choice['finish_reason'] = value['finish_reason']
            for key, item in value.items():
                if key not in ('index', 'delta', 'finish_reason'):
                    choice[key] = deepcopy(item)

    def finish(self) -> dict:
        if not self.choices or any(c['finish_reason'] is None for c in self.choices.values()):
            raise ValueError('Stream has no complete finish reason for every choice')
        result = deepcopy(self.metadata)
        result['object'] = 'chat.completion'
        result['choices'] = [deepcopy(self.choices[i]) for i in sorted(self.choices)]
        for choice in result['choices']:
            calls = choice['message'].get('tool_calls')
            if calls is not None:
                calls = [calls[i] for i in sorted(calls)]
                if any(not call['id'] or not call['function']['name'] for call in calls):
                    raise ValueError('Stream ended with an incomplete tool identity')
                choice['message']['tool_calls'] = calls
            if choice['finish_reason'] == 'tool_calls' and not calls:
                raise ValueError('Tool finish reason without tool calls')
        return result


class NativeProtocol:
    """Mixin for a client providing base_url, timeout and active response state."""

    async def request_json(self, path: str, payload: dict, *, headers: Mapping[str, str] | None = None,
                           timeout_seconds: float | None = None) -> dict:
        """Post the exact payload to a caller-selected native endpoint once."""
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError('The request deadline has expired')
        started = time.monotonic()
        status = None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.base_url.rstrip('/') + path, json=deepcopy(payload),
                        headers=headers, timeout=aiohttp.ClientTimeout(
                            total=self.timeout if timeout_seconds is None else min(self.timeout, timeout_seconds))) as response:
                    status = response.status
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in _chunks(response.content):
                        raw.extend(chunk)
                        if len(raw) > self.maximum_response_bytes:
                            raise ValueError('JSON response exceeded configured byte limit')
                    value = json.loads(raw, parse_constant=_invalid_number)
                    if not isinstance(value, dict):
                        raise ValueError('JSON response must be an object')
                    return value
        except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError) as error:
            raise IncompleteGeneration('transport_error', str(error) or type(error).__name__, status=status,
                elapsed_seconds=time.monotonic()-started) from error

    async def stream_raw(self, payload: dict, *, path: str = '/chat/completions',
                         headers: Mapping[str, str] | None = None,
                         timeout_seconds: float | None = None) -> AsyncIterator[dict]:
        """Yield unmodified native events; exhaustion requires a complete terminal stream.

        Events are provisional. Consumers must wait for successful exhaustion
        before treating accumulated tool calls as executable.
        """
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError('The request deadline has expired')
        if getattr(self, '_native_busy', False):
            raise ValueError('Use a separate client for each concurrent response')
        self._native_busy = True
        started = time.monotonic()
        request = deepcopy(payload)
        request['stream'] = True
        request['stream_options'] = dict(request.get('stream_options') or {}, include_usage=True)
        policy = self.known_issues.repetition
        guard = RepetitionGuard(**policy) if policy is not None else None
        parser = SSEDecoder(self.maximum_response_bytes)
        assembler = NativeAssembler()
        count = 0
        tail = ''
        status = None
        terminal = False
        response = None
        try:
            async with aiohttp.ClientSession() as session:
                self._active_session = session
                async with session.post(self.base_url.rstrip('/')+path, json=request, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=self.timeout if timeout_seconds is None
                            else min(self.timeout, timeout_seconds))) as response:
                    self._active_response = response
                    status = response.status
                    response.raise_for_status()
                    async for chunk in _chunks(response.content):
                        count += len(chunk)
                        if count > self.maximum_response_bytes:
                            raise ValueError('Stream exceeded configured byte limit')
                        for data in parser.feed(chunk):
                            if data == '[DONE]':
                                assembler.finish()
                                terminal = True
                                break
                            event = json.loads(data, parse_constant=_invalid_number)
                            if not isinstance(event, dict) or 'error' in event:
                                raise ValueError('Upstream returned an invalid event or error: '+str(event))
                            tail = (tail + data + '\n')[-self.diagnostic_characters:]
                            if guard is not None:
                                detected = guard.observe(event)
                                if detected:
                                    response.close()
                                    raise IncompleteGeneration('repetition_detected',
                                        'Generation cancelled after sustained repetitive output',
                                        status=status, elapsed_seconds=time.monotonic()-started,
                                        definitive=True, evidence=dict(detection=asdict(detected),
                                            raw_event_tail=tail, upstream_closed=True, response_bytes=count,
                                            effective_known_issues=asdict(self.known_issues),
                                            wire_overrides=dict(stream=True, stream_options=request['stream_options'])))
                            assembler.add(event)
                            yield event
                        if terminal:
                            break
                    if not terminal:
                        parser.feed(b'', final=True)
                        raise ValueError('Stream ended without its terminal marker')
        except IncompleteGeneration:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError, TypeError, KeyError, AttributeError) as error:
            raise IncompleteGeneration('incomplete_stream', str(error) or type(error).__name__, status=status,
                elapsed_seconds=time.monotonic()-started,
                evidence=dict(raw_event_tail=tail, response_bytes=count)) from error
        finally:
            if response is not None:
                response.close()
            self._active_response = None
            self._active_session = None
            self._native_busy = False

    async def chat_raw(self, payload: dict, *, path: str = '/chat/completions',
                       headers: Mapping[str, str] | None = None,
                       timeout_seconds: float | None = None) -> dict:
        """Collect a native response through the same guarded streaming path."""
        assembler = NativeAssembler()
        async for event in self.stream_raw(payload, path=path, headers=headers, timeout_seconds=timeout_seconds):
            assembler.add(event)
        return assembler.finish()


@dataclass(frozen=True)
class NativeExchange:
    status: int | None
    body: str
    elapsed_seconds: float
    error: str | None = None
    failure_kind: str | None = None
    evidence: dict | None = None
    definitive: bool = False


class SyncNativeTransport:
    """One synchronous native exchange, including calls inside an existing event loop."""

    def __init__(self, client: NativeProtocol, completion_path: str, headers: Mapping[str, str]) -> None:
        self.client = client
        self.completion_path = completion_path
        self.headers = dict(headers)

    @property
    def identity(self) -> dict:
        """Persist reproducible provider policy without authentication material."""
        return dict(provider='llama_client', known_issues=asdict(self.client.known_issues),
                    diagnostic_characters=self.client.diagnostic_characters)

    def request_metadata(self, path: str, payload: dict) -> dict:
        """Describe exact wire changes before dispatch, including failed exchanges."""
        result = self.identity
        result['wire_overrides'] = dict(stream=True,
            stream_options=dict(payload.get('stream_options') or {}, include_usage=True)) if path == self.completion_path else {}
        return result

    def __call__(self, path: str, payload: dict, *, timeout_seconds: float | None = None) -> NativeExchange:
        started = time.monotonic()
        async def exchange() -> dict:
            if path == self.completion_path:
                return await self.client.chat_raw(payload, path=path, headers=self.headers,
                                                  timeout_seconds=timeout_seconds)
            return await self.client.request_json(path, payload, headers=self.headers,
                                                   timeout_seconds=timeout_seconds)
        def run() -> dict:
            return asyncio.run(exchange())
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                result = run()
            else:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    result = executor.submit(run).result()
            evidence = dict(provider='llama_client', effective_known_issues=asdict(self.client.known_issues))
            if path == self.completion_path:
                evidence['wire_overrides'] = dict(stream=True,
                    stream_options=dict(payload.get('stream_options') or {}, include_usage=True))
            return NativeExchange(200, json.dumps(result, ensure_ascii=False), time.monotonic()-started,
                                  evidence=evidence)
        except IncompleteGeneration as error:
            return NativeExchange(error.status, '', time.monotonic()-started, str(error), error.kind,
                                  error.evidence, error.definitive)
