"""Direct JSON chat transport and explicit serializable conversation boundaries."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


@dataclass(frozen=True)
class EndpointConfig:
    base_url: str
    timeout_seconds: float
    maximum_response_bytes: int
    headers: Mapping[str, str]
    completion_path: str
    template_path: str
    tokenize_path: str
    tokenizer_add_special: bool
    tokenizer_parse_special: bool
    props_path: str = '/props'   # llama.cpp: modalities, chat-template capabilities, context size

    def __post_init__(self) -> None:
        if not self.base_url.startswith(('http://', 'https://')) or self.timeout_seconds <= 0 or self.maximum_response_bytes <= 0:
            raise ValueError('Invalid endpoint or transport limits')
        if any(not path.startswith('/') for path in (self.completion_path, self.template_path, self.tokenize_path, self.props_path)):
            raise ValueError('Endpoint paths must begin with a slash')


@dataclass(frozen=True)
class WireResponse:
    status: int | None
    body: str
    elapsed_seconds: float
    error: str | None = None
    failure_kind: str | None = None
    evidence: dict[str, Any] | None = None
    definitive: bool = False


class ModelTransportError(RuntimeError):
    """A recorded failed exchange; callers must not infer safe replay."""


class RejectedGeneration(ModelTransportError):
    """An observed rejected response, with no model tool calls executed."""

    def __init__(self, response: WireResponse) -> None:
        super().__init__(response.error or response.failure_kind or 'Generation rejected')
        self.response = response


class DirectJsonTransport:
    """Single-attempt transport. No SDK, retry, streaming, or hidden instruction."""

    def __init__(self, config: EndpointConfig) -> None:
        self.config = config

    def __call__(self, path: str, payload: dict[str, Any], *, timeout_seconds: float | None = None) -> WireResponse:
        started = time.monotonic()
        timeout = self.config.timeout_seconds if timeout_seconds is None else min(timeout_seconds, self.config.timeout_seconds)
        if timeout <= 0:
            raise ValueError('The request deadline has expired')
        request = Request(self.config.base_url.rstrip('/')+path,
                          json.dumps(payload, allow_nan=False).encode('utf-8'),
                          dict(self.config.headers), method='POST')
        status: int | None = None
        raw = b''
        error = None
        try:
            try:
                response = urlopen(request, timeout=timeout)
            except HTTPError as exc:
                response = exc
            with response:
                status = response.status
                raw = response.read(self.config.maximum_response_bytes+1)
                if len(raw) > self.config.maximum_response_bytes:
                    raw = raw[:self.config.maximum_response_bytes]
                    error = 'Response exceeded configured byte limit'
        except (OSError, URLError, TimeoutError) as exc:
            error = f'{type(exc).__name__}: {exc}'
        return WireResponse(status, raw.decode('utf-8', errors='replace'), time.monotonic()-started, error)


class ModelClient:
    """Observe intent before every exchange and raw outcome before parsing."""

    def __init__(self, config: EndpointConfig, *,
                 transport: Callable[..., WireResponse] | None = None,
                 observer: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.config = config
        self.transport = transport or DirectJsonTransport(config)
        self.observer = observer

    def post(self, path: str, payload: dict[str, Any], purpose: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        request_id = str(uuid4())
        # Validate before writing the intent, but never modify the supplied body.
        safe = json.loads(json.dumps(payload, allow_nan=False))
        if self.observer:
            metadata = self.transport.request_metadata(path, safe) if hasattr(self.transport, 'request_metadata') else None
            self.observer('model_request', dict(request_id=request_id, path=path, purpose=purpose, body=safe,
                timeout_seconds=timeout_seconds, **(dict(transport=metadata) if metadata is not None else {})))
        try:
            response = self.transport(path, safe) if timeout_seconds is None else self.transport(path, safe, timeout_seconds=timeout_seconds)
        except Exception as exc:
            if self.observer:
                self.observer('model_response', dict(request_id=request_id, purpose=purpose,
                              status=None, body='', elapsed_seconds=0, error=f'{type(exc).__name__}: {exc}'))
            raise ModelTransportError('Transport raised; exchange outcome is uncertain') from exc
        if self.observer:
            self.observer('model_response', dict(request_id=request_id, purpose=purpose, **asdict(response)))
        if (response.definitive and response.failure_kind == 'repetition_detected'
                and response.evidence and response.evidence.get('upstream_closed') is True):
            raise RejectedGeneration(response)
        if response.error or response.status is None or not 200 <= response.status < 300:
            raise ModelTransportError(response.error or f'HTTP status {response.status}')
        try:
            parsed = json.loads(response.body, parse_constant=_reject_constant)
        except (ValueError, TypeError) as exc:
            raise ModelTransportError('Response is not finite JSON') from exc
        if not isinstance(parsed, dict):
            raise ModelTransportError('Response must be a JSON object')
        return parsed

    def complete(self, payload: dict[str, Any], purpose: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        return self.post(self.config.completion_path, payload, purpose, timeout_seconds=timeout_seconds)

    def capabilities(self, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
        """What the serving model can do, read from the server, never assumed from its name.

        llama.cpp's /props reports `modalities` (vision/audio/video: whether a projector is loaded) and
        `chat_template_caps`. A server without the endpoint yields empty modalities: the session then
        treats the model as text-only, which is the safe reading."""
        request_id = str(uuid4())
        url = self.config.base_url.rstrip('/')+self.config.props_path
        if self.observer:
            self.observer('model_request', dict(request_id=request_id, path=self.config.props_path,
                                                purpose='capabilities', body=None, timeout_seconds=timeout_seconds))
        started = time.monotonic()
        try:
            # A transport that answers property reads itself (tests, other backends) is asked first.
            props_call = getattr(self.transport, 'props', None)
            if callable(props_call):
                props, status, raw = props_call(), 200, ''
            else:
                with urlopen(Request(url, headers=dict(self.config.headers)), timeout=timeout_seconds) as response:
                    status, raw = response.status, response.read(self.config.maximum_response_bytes).decode('utf-8', errors='replace')
                props = json.loads(raw)
            error = None if isinstance(props, dict) else 'props is not an object'
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            status, raw, props, error = None, '', {}, f'{type(exc).__name__}: {exc}'
        if self.observer:
            self.observer('model_response', dict(request_id=request_id, purpose='capabilities', status=status,
                                                 body=raw[:4000], elapsed_seconds=time.monotonic()-started, error=error))
        modalities = props.get('modalities') if isinstance(props, dict) else None
        return dict(vision=bool((modalities or {}).get('vision')), audio=bool((modalities or {}).get('audio')),
                    video=bool((modalities or {}).get('video')),
                    template=dict(props.get('chat_template_caps') or {}) if isinstance(props, dict) else {},
                    model=props.get('model_path') if isinstance(props, dict) else None,
                    context=(props.get('default_generation_settings') or {}).get('n_ctx') if isinstance(props, dict) else None,
                    error=error)

    def count(self, payload: dict[str, Any], purpose: str) -> dict[str, Any]:
        # The template endpoint renders text; an image part is counted as a fixed allowance instead
        # (a projector's token count depends on resolution and is not exposed), so a prompt with images
        # is over-counted rather than under-counted at the rollover.
        images = 0
        countable = deepcopy(payload)
        for message in countable.get('messages') or []:
            if isinstance(message.get('content'), list):
                parts = message['content']
                images += sum(1 for part in parts if isinstance(part, dict) and part.get('type') == 'image_url')
                message['content'] = ' '.join(str(part.get('text') or '') for part in parts
                                              if isinstance(part, dict) and part.get('type') == 'text') or '[image]'
        payload = countable
        rendered = self.post(self.config.template_path, payload, purpose+':template')
        prompt = rendered.get('prompt')
        if not isinstance(prompt, str):
            raise ModelTransportError('Template endpoint did not return a prompt string')
        tokenized = self.post(self.config.tokenize_path,
                              dict(content=prompt, add_special=self.config.tokenizer_add_special,
                                   parse_special=self.config.tokenizer_parse_special), purpose+':tokenize')
        tokens = tokenized.get('tokens')
        if not isinstance(tokens, list) or any(type(token) is not int for token in tokens):
            raise ModelTransportError('Tokenizer did not return integer tokens')
        return dict(prompt=prompt, tokens=len(tokens)+images*IMAGE_TOKEN_ALLOWANCE, **(dict(images=images) if images else {}))


# One image at the resolutions a screenshot takes (≈1280×800) costs a Qwen-family projector about
# 1,300 tokens; the allowance errs high so the rollover fires early, never late.
IMAGE_TOKEN_ALLOWANCE = 1600


REASONING_RETENTION = ('all', 'latest', 'none')


def wire_messages(messages: list[dict[str, Any]], retention: str) -> list[dict[str, Any]]:
    """Project retained messages into the view a model request reads.

    'all' sends every stored reasoning field. 'latest' keeps reasoning only on the
    final assistant message, so the step in progress can continue while completed
    steps are read through their visible content, calls and results. 'none' sends
    no reasoning. Stored messages, archives and audits are never modified.
    """
    if retention not in REASONING_RETENTION:
        raise ValueError('reasoning_retention must be one of '+', '.join(REASONING_RETENTION))
    projected = deepcopy(messages)
    # An image is shown once, in the message that carried it, and thereafter read through what the
    # model said about it: every earlier image part becomes a note, so the window holds one image.
    with_images = [i for i, m in enumerate(projected) if isinstance(m.get('content'), list)
                   and any(isinstance(p, dict) and p.get('type') == 'image_url' for p in m['content'])]
    for index in with_images[:-1]:
        projected[index]['content'] = [p if not (isinstance(p, dict) and p.get('type') == 'image_url')
                                       else dict(type='text', text='[an image was shown here earlier; it is no longer in view — read that file again to see it]')
                                       for p in projected[index]['content']]
    if retention == 'all':
        return projected
    last = next((index for index in range(len(projected)-1, -1, -1)
                 if projected[index].get('role') == 'assistant'), None)
    for index, message in enumerate(projected):
        if message.get('role') == 'assistant' and 'reasoning_content' in message \
                and (retention == 'none' or index != last):
            del message['reasoning_content']
    return projected


def _argument_projection(value: Any, limit: int, marker: str) -> Any:
    """Replace long string fields of completed call arguments with a notice; short fields stay.

    The notice keeps only the first line for recognition and states that the full
    value was applied. A truncated prefix would read as a truncated file.
    """
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        first = value.split('\n', 1)[0][:limit]
        return first+marker.format(total=len(value), lines=value.count('\n')+1)
    if isinstance(value, dict):
        return {key: _argument_projection(item, limit, marker) for key, item in value.items()}
    if isinstance(value, list):
        return [_argument_projection(item, limit, marker) for item in value]
    return value


def project_completed_arguments(messages: list[dict[str, Any]], excerpt_characters: int | None,
                                archives: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Replace long arguments of tool calls whose results are already in the conversation.

    A call still awaiting its result keeps its full arguments. Once the result is
    present, the argument text is history: the effect is in the result and in the
    workspace. Long string fields are replaced by their first line and a notice. When
    the caller has saved the full arguments to a file, `archives` maps the call id to
    that path and the notice names it, so the model can read the file rather than
    carry the text. The projection is deterministic, so a projected call renders
    identically on every later request. None disables projection.
    """
    projected = deepcopy(messages)
    if excerpt_characters is None:
        return projected
    if type(excerpt_characters) is not int or excerpt_characters < 0:
        raise ValueError('argument_excerpt_characters must be a nonnegative integer or None')
    answered = {message.get('tool_call_id') for message in projected if message.get('role') == 'tool'}
    outcome = {message.get('tool_call_id'): _result_status(message.get('content'))
               for message in projected if message.get('role') == 'tool'}
    archives = dict(archives or {})
    for message in projected:
        if message.get('role') != 'assistant':
            continue
        for call in message.get('tool_calls') or []:
            if call.get('id') not in answered:
                continue
            saved = archives.get(call.get('id'))
            read_it = (' The call as sent is saved at '+saved+'; read it in ranges with `read` (offset/limit).' if saved else '')
            status = outcome.get(call.get('id'))
            if status in ('rejected', 'error'):
                # A refused or failed call was noted as "applied … Nothing to redo" for as long as it stayed in the
                # window: the worker was told a write that never landed was in the workspace (audit, 2026-09-22).
                marker = ('\n[transcript note: this call was NOT applied (its result says '+status+'); this transcript '
                          'keeps only the first line of its {total:,} characters ({lines} lines).'+read_it+']')
            else:
                # The fact first, the caveat second: "applied in full; only the first line is shown" was read by two
                # models as "your write was truncated" — one rewrote the file, one wrote the note back as the file.
                marker = ('\n[transcript note: all {total:,} characters ({lines} lines) of this call were applied and are in '
                          'the workspace; this transcript keeps only the first line of it.'+read_it+' Nothing to redo.]')
            function = call['function']
            value = function['arguments']
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    function['arguments'] = _argument_projection(value, excerpt_characters, marker)
                    continue
                function['arguments'] = json.dumps(_argument_projection(parsed, excerpt_characters, marker), ensure_ascii=False)
            else:
                function['arguments'] = _argument_projection(value, excerpt_characters, marker)
    return projected


def cut_note(content: str, kept: int, saved: str | None = None) -> str:
    """One sentence for a tool result shown cut: whether the call ran, where the whole of it is, how to read it."""
    try:
        parsed = json.loads(content)
    except ValueError:
        parsed = None
    parsed = parsed if isinstance(parsed, dict) else {}
    status = parsed.get('status')
    files = [f for f in parsed.get('output_files') or [] if isinstance(f, str)]
    if isinstance(parsed.get('arguments_saved_at'), str):
        files.append(parsed['arguments_saved_at'])
    if saved and saved not in files:
        files.append(saved)
    ran = ('the call was NOT applied (status '+str(status)+')' if status in ('rejected', 'error') else
           'the command was stopped at its output limit and did NOT finish' if status == 'output_limit' else
           'the call ran in full')
    where = ('; the whole of it is in '+', '.join(files)+' — read it in ranges with `read` (offset/limit), do not re-run it'
             if files else '; the full result is in your window archive under .session-history/')
    return f'result cut at {kept:,} of {len(content):,} characters; {ran}{where}.'


def _result_status(content: Any) -> str | None:
    """The `status` a tool result reports (`ok`, `rejected`, `error`, `timeout`, …), or None when it is not JSON."""
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        return None
    return str(parsed.get('status')) if isinstance(parsed, dict) and parsed.get('status') is not None else None


def _reject_constant(value: str) -> None:
    raise ValueError(f'Non-finite JSON constant: {value}')


@dataclass(frozen=True)
class SessionPolicy:
    context_capacity: int
    rollover_threshold: int
    worker_output_tokens: int
    handoff_output_tokens: int
    recent_result_count: int
    recent_result_characters: int
    handoff_prompt: str
    resume_prefix: str
    handoff_generation_overrides: Mapping[str, Any]
    output_headroom_tokens: int = 1
    overflow_excerpt_characters: int = 16000
    reasoning_retention: str = 'all'
    argument_excerpt_characters: int | None = None
    # Stop and reflect before the window rolls: at the threshold the worker is told to record its method (the
    # reflect prompt) and given this many turns for it, overshooting the threshold into the capacity's buffer;
    # then it writes the handoff. 0 turns: no reflection, the handoff at once (the old shape). Negative: as many
    # turns as it needs, until it says done or the prompt reaches capacity less headroom.
    reflect_prompt: str = ''
    reflect_turns: int = 0

    def __post_init__(self) -> None:
        if self.reasoning_retention not in REASONING_RETENTION:
            raise ValueError('reasoning_retention must be one of '+', '.join(REASONING_RETENTION))
        if self.argument_excerpt_characters is not None and (
                type(self.argument_excerpt_characters) is not int or self.argument_excerpt_characters < 0):
            raise ValueError('argument_excerpt_characters must be a nonnegative integer or None')
        if any(type(v) is not int or v <= 0 for v in (self.context_capacity, self.rollover_threshold,
                self.recent_result_characters, self.output_headroom_tokens, self.overflow_excerpt_characters)):
            raise ValueError('Context limits must be positive integers')
        if any(type(v) is not int or (v <= 0 and v != -1)
               for v in (self.worker_output_tokens, self.handoff_output_tokens)):
            raise ValueError('Output allowance must be positive or -1 for unrestricted generation')
        if self.recent_result_count < 0 or self.rollover_threshold >= self.context_capacity:
            raise ValueError('Invalid rollover or tail limits')
        if self.output_headroom_tokens >= self.context_capacity:
            raise ValueError('Output headroom must leave room for a prompt')
        if not self.handoff_prompt or not self.resume_prefix:
            raise ValueError('Explicit handoff and resume text is required')


@dataclass(frozen=True)
class ParsedTurn:
    message: dict[str, Any]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


class PersistentSession:
    """Explicit state only; no automatic retrieval, guidance, retry, or rollover."""

    def __init__(self, base_messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 generation: dict[str, Any], policy: SessionPolicy) -> None:
        if not base_messages or not any(m.get('role') == 'user' for m in base_messages):
            raise ValueError('Base messages require a user assignment')
        if any(key in generation for key in ('messages', 'tools', 'stream', 'max_tokens')):
            raise ValueError('Generation settings cannot replace session structure')
        self.base_messages = deepcopy(base_messages)
        self.tools = deepcopy(tools)
        self.generation = deepcopy(generation)
        self.policy = policy
        self.messages = deepcopy(base_messages)
        self.argument_archives: dict[str, str] = {}
        self.recent_results: list[dict[str, Any]] = []
        self.standing: str = ''
        self.pending_tools: list[str] = []
        self.seen_tool_ids: list[str] = []
        self.window_index = 0

    def payload(self, *, output_tokens: int | None = None) -> dict[str, Any]:
        if self.pending_tools:
            raise ValueError('Resolve all pending tool results before a model request')
        return deepcopy(self.generation) | dict(messages=self._wire_view(self.messages), tools=deepcopy(self.tools),
            max_tokens=self.policy.worker_output_tokens if output_tokens is None else output_tokens, stream=False)

    def handoff_payload(self, *, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload = self.payload(output_tokens=self.policy.handoff_output_tokens)
        if history is not None:
            if history[:len(self.base_messages)] != self.base_messages:
                raise ValueError('Projected handoff history must preserve the original base messages')
            payload['messages'] = self._wire_view(history)
        payload.pop('tools')
        payload.pop('tool_choice', None)
        payload.pop('parallel_tool_calls', None)
        payload.update(deepcopy(dict(self.policy.handoff_generation_overrides)))
        prompt = self.policy.handoff_prompt
        boundary = self.continuation_boundary()
        if boundary['awaiting_worker_response']:
            prompt += ('\nThe last tool batch finished, but you have not yet acted on its results. '
                'Preserve the next required action and any unfinished obligation in your own handoff.\n'
                +json.dumps(boundary, ensure_ascii=False))
        payload['messages'].append(dict(role='user', content=prompt))
        return payload

    def _wire_view(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return project_completed_arguments(wire_messages(messages, self.policy.reasoning_retention),
                                           self.policy.argument_excerpt_characters, self.argument_archives)

    def needs_rollover(self, prompt_tokens: int) -> bool:
        return prompt_tokens >= self.policy.rollover_threshold

    def assert_fits(self, prompt_tokens: int, output_tokens: int) -> None:
        reserve = max(self.policy.output_headroom_tokens, output_tokens)
        if (prompt_tokens < 0 or (output_tokens <= 0 and output_tokens != -1)
                or prompt_tokens+reserve > self.policy.context_capacity):
            raise ValueError('Request exceeds the declared active context capacity')

    def accept(self, response: dict[str, Any]) -> ParsedTurn:
        if self.pending_tools:
            raise ValueError('Assistant cannot advance over unresolved tools')
        turn = parse_turn(response)
        calls = turn.message.get('tool_calls', [])
        ids = [call['id'] for call in calls]
        if len(set(ids)) != len(ids) or any(value in self.seen_tool_ids for value in ids):
            raise ValueError('Duplicate tool call identity')
        self.messages.append(deepcopy(turn.message))
        self.pending_tools = ids
        self.seen_tool_ids.extend(ids)
        return turn

    def append_tool_result(self, tool_call_id: str, name: str, content: str, *,
                           arguments_archive: str | None = None) -> None:
        if not self.pending_tools or tool_call_id != self.pending_tools[0]:
            raise ValueError('Tool result must match the next unresolved call')
        if arguments_archive is not None:
            if not isinstance(arguments_archive, str) or not arguments_archive.strip():
                raise ValueError('arguments_archive must be a nonempty path string')
            self.argument_archives[tool_call_id] = arguments_archive
        message = dict(role='tool', tool_call_id=tool_call_id, content=content)
        self.messages.append(message)
        self.pending_tools.pop(0)
        self.recent_results.append(dict(tool_call_id=tool_call_id, name=name, content=content))
        self.recent_results = self.recent_results[-self.policy.recent_result_count:] if self.policy.recent_result_count else []

    def recent_tail(self) -> list[dict[str, Any]]:
        remaining = self.policy.recent_result_characters
        result: list[dict[str, Any]] = []
        for entry in reversed(self.recent_results):
            item = deepcopy(entry)
            value = item['content']
            item['content'] = value[:remaining]
            item['truncated'] = len(value) > remaining
            if item['truncated']:
                # The cut takes the end of the result, which is where a shell result names its files: say what ran
                # and where the whole of it is before the pointer is lost (audit, 2026-09-22).
                item['note'] = cut_note(value, len(item['content']), self.argument_archives.get(item['tool_call_id']))
            remaining -= len(item['content'])
            result.append(item)
            if remaining <= 0:
                break
        return list(reversed(result))

    def continuation_boundary(self) -> dict[str, Any]:
        """Expose the completed action still awaiting the worker's next response.

        This is evidence, not an inferred plan or controller-authored handoff.
        Large action text is labeled as an excerpt; callers retain raw archives.
        Tool results use the same explicit tail budget as normal continuation.
        """
        if self.pending_tools:
            raise ValueError('Continuation evidence requires a completed tool boundary')
        last = next((item for item in reversed(self.messages) if item.get('role') == 'assistant'), None)
        calls = (last or {}).get('tool_calls') or []
        waiting = bool(calls and self.messages[-1].get('role') == 'tool')
        if not waiting:
            return dict(awaiting_worker_response=False)
        visible = {key:value for key,value in last.items() if key != 'reasoning_content'}
        text = json.dumps(visible, ensure_ascii=False)
        limit = self.policy.recent_result_characters
        identities = [call['id'] for call in calls]
        results = [item for item in self.recent_tail() if item['tool_call_id'] in identities]
        saved = [self.argument_archives[i] for i in identities if i in self.argument_archives]
        last_action = dict(text=text[:limit], total_characters=len(text), truncated=len(text)>limit)
        if saved:
            last_action['saved_at'] = saved   # the calls as sent, whole: read them in ranges rather than re-sending
        return dict(awaiting_worker_response=True,
            last_action=last_action,
            tool_results=results,
            omitted_tool_result_ids=[identity for identity in identities
                                     if identity not in {item['tool_call_id'] for item in results}])

    def rollover(self, handoff: str, *, source_archive: str | None = None) -> dict[str, Any]:
        if self.pending_tools or not isinstance(handoff, str) or not handoff.strip():
            raise ValueError('Rollover requires completed tools and a nonempty handoff')
        old = deepcopy(self.messages)
        resumed = dict(handoff=handoff, recent_tool_results=self.recent_tail(),
                       continuation_boundary=self.continuation_boundary())
        if source_archive:
            resumed['source_archive'] = source_archive
        fresh = deepcopy(self.base_messages)
        if self.standing:
            # The host's current instruction outlives the window it was given in: a worker told "gate green,
            # record the method" must not wake up in a window whose assignment still says "take the readings".
            fresh.append(dict(role='user', content='The host\'s current instruction, given after your earlier completion '
                                                   '(it supersedes the assignment\'s "done" condition):\n'+self.standing))
        self.messages = fresh+[dict(role='user', content=self.policy.resume_prefix+'\n'+json.dumps(resumed, ensure_ascii=False))]
        self.window_index += 1
        return dict(window_index=self.window_index, old_messages=old, new_messages=deepcopy(self.messages), handoff=handoff)

    def append_image(self, note: str, mime: str, data_base64: str) -> None:
        """Show the model an image the worker asked to read: a user message of a text part and an image part.

        It is appended at a tool boundary like guidance; the note says which file it is, so the transcript
        reads the same whether or not the image is still in the wire view."""
        if self.pending_tools or not note.strip() or not data_base64:
            raise ValueError('An image requires a completed tool boundary, a note and data')
        self.messages.append(dict(role='user', content=[dict(type='text', text=note),
                                                        dict(type='image_url', image_url=dict(url='data:'+mime+';base64,'+data_base64))]))

    def append_guidance(self, content: str, *, standing: bool = False) -> None:
        if self.pending_tools or not content.strip():
            raise ValueError('Guidance requires a completed tool boundary and nonempty text')
        self.messages.append(dict(role='user', content=content))
        if standing:
            self.standing = content   # the latest host continuation is the current objective across windows

    def export_state(self) -> dict[str, Any]:
        return deepcopy(dict(base_messages=self.base_messages, tools=self.tools, generation=self.generation,
            policy=asdict(self.policy), messages=self.messages, recent_results=self.recent_results, standing=self.standing,
            pending_tools=self.pending_tools, seen_tool_ids=self.seen_tool_ids, window_index=self.window_index,
            argument_archives=self.argument_archives))

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> PersistentSession:
        result = cls(state['base_messages'], state['tools'], state['generation'], SessionPolicy(**state['policy']))
        for name in ('messages', 'recent_results', 'pending_tools', 'seen_tool_ids', 'window_index'):
            setattr(result, name, deepcopy(state[name]))
        result.argument_archives = deepcopy(state.get('argument_archives', {}))
        result.standing = str(state.get('standing') or '')
        if len(set(result.seen_tool_ids)) != len(result.seen_tool_ids) or any(i not in result.seen_tool_ids for i in result.pending_tools) or result.window_index < 0:
            raise ValueError('Inconsistent serialized tool or window identities')
        return result


def parse_turn(response: dict[str, Any]) -> ParsedTurn:
    """Retain native content, reasoning and tool arguments without repair."""
    choices, usage = response.get('choices'), response.get('usage')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(usage, dict):
        raise ValueError('A single choice and explicit token usage are required')
    choice = choices[0]
    message, finish = choice.get('message'), choice.get('finish_reason')
    if not isinstance(message, dict) or message.get('role') != 'assistant' or not isinstance(finish, str):
        raise ValueError('Invalid assistant response')
    for name in ('content', 'reasoning_content'):
        if message.get(name) is not None and not isinstance(message[name], str):
            raise ValueError('Assistant content and reasoning must be strings or null')
    for name in ('prompt_tokens', 'completion_tokens'):
        if type(usage.get(name)) is not int or usage[name] < 0:
            raise ValueError('Token usage must be explicit nonnegative integers')
    calls = message.get('tool_calls') or []
    if not isinstance(calls, list):
        raise ValueError('tool_calls must be a list')
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get('id'), str) or not call['id'] or call.get('type') != 'function':
            raise ValueError('Native function calls require a nonempty identity')
        function = call.get('function')
        if not isinstance(function, dict) or not isinstance(function.get('name'), str) or not isinstance(function.get('arguments'), (str, dict)):
            raise ValueError('Malformed function call structure')
    clean = {key: deepcopy(message[key]) for key in ('role', 'content', 'reasoning_content', 'tool_calls') if key in message}
    return ParsedTurn(clean, finish, usage['prompt_tokens'], usage['completion_tokens'])
