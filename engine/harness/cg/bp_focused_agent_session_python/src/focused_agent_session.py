"""Compose a durable worker loop and independently retained controller state."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import fcntl
import fnmatch
import hashlib
import json
import os
from types import SimpleNamespace
import shlex
from pathlib import Path
from typing import Any, Iterator

from cg.backend_persistent_model_session_python.src.persistent_model_session import (
    EndpointConfig, ModelClient, PersistentSession, RejectedGeneration, SessionPolicy, WireResponse, parse_turn,
    wire_messages,
)
from cg.logic_llamaclient_python.src.llamaclient import LlamaClient
from cg.logic_llamaclient_python.src.native import KnownIssues, SyncNativeTransport
from cg.data_session_event_log_python.src.session_event_log import SessionEventLog
from cg.infra_revision_store_python.src.revision_store import RevisionStore
from cg.infra_sandboxed_shell_execution_python.src.sandboxed_shell_execution import (
    DirectoryWorkspace, NetworkPolicy, SandboxedShell, ServiceLimits, ShellConfig, ShellLimits, ShellResult, WorkspaceEditError, edit_workspace_file,
    read_workspace_file, read_workspace_lines, workspace_files, write_workspace_file,
)
from cg.universal_controller_progress_python.src.controller_progress import ControllerProgress, ReviewPolicy
from cg.universal_context_payload_projection_python.src.context_payload_projection import (
    fit_native_messages, project_native_messages, project_payload_content,
)


@dataclass(frozen=True)
class ControllerSettings:
    system_prompt: str
    generation: dict[str, Any]
    context_capacity: int
    output_tokens: int
    maximum_model_calls: int | None
    maximum_tool_calls: int | None
    maximum_tool_output_characters: int
    output_headroom_tokens: int = 1
    input_target_tokens: int | None = None
    recent_review_exchanges: int = 2
    investigation_budgets: dict[str, int] | None = None
    maximum_document_edits_per_review: int | None = None
    # One tool-less completion per review: the envelope in, the decision out. No project
    # document, no investigation. For a reviewer whose evidence fits in the envelope
    # (a small reference, focus files, recent exchanges) the tools were never used.
    plain_review: bool = False
    plain_recent_exchanges: int = 6

    def __post_init__(self) -> None:
        if type(self.plain_recent_exchanges) is not int or self.plain_recent_exchanges < 1:
            raise ValueError('plain_recent_exchanges must be a positive integer')
        if self.investigation_budgets is not None:
            if (not isinstance(self.investigation_budgets, dict) or set(self.investigation_budgets) != {'bootstrap', 'periodic', 'completion'}
                    or any(type(value) is not int or value < 0 for value in self.investigation_budgets.values())):
                raise ValueError('investigation_budgets must map bootstrap, periodic and completion to nonnegative integers')
        if self.maximum_document_edits_per_review is not None and (
                type(self.maximum_document_edits_per_review) is not int or self.maximum_document_edits_per_review <= 0):
            raise ValueError('maximum_document_edits_per_review must be a positive integer or None')
        if (not self.system_prompt.strip() or (self.output_tokens != -1
                and not 0 < self.output_tokens < self.context_capacity)
                or not 0 < self.output_headroom_tokens < self.context_capacity):
            raise ValueError('Invalid controller prompt or context limits')
        if set(self.generation) & {'messages', 'tools', 'tool_choice', 'stream', 'max_tokens', 'response_format'}:
            raise ValueError('Controller generation cannot override the review contract')
        if any(value is not None and (type(value) is not int or value <= 0) for value in (
                self.maximum_model_calls, self.maximum_tool_calls, self.input_target_tokens)):
            raise ValueError('Optional controller limits must be positive integers or None')
        if (type(self.maximum_tool_output_characters) is not int or self.maximum_tool_output_characters <= 0
                or type(self.recent_review_exchanges) is not int or self.recent_review_exchanges < 0):
            raise ValueError('Invalid controller excerpt or recent-exchange limit')


@dataclass(frozen=True)
class SessionSettings:
    assignment: str
    worker_system: str
    reference: str | None
    generation: dict[str, Any]
    session_policy: SessionPolicy
    review_policy: ReviewPolicy
    controller: ControllerSettings | None
    review_on_completion: bool
    guidance_prefix: str
    history_archive_prefix: str = '.session-history'
    maximum_generation_retries: int = 0
    worker_tools: tuple[str, ...] = ('bash',)
    # Typed aliases for CLI commands: each is a tool with a schema and a description the model sees on
    # every turn, rendered to a shell command and run exactly like bash. One vocabulary, discoverable.
    command_tools: tuple[dict[str, Any], ...] = ()
    maximum_tool_argument_characters: int | None = None
    maximum_write_characters: int | None = None
    maximum_edit_characters: int | None = None
    maximum_read_lines: int = 200
    # An image the worker reads is shown whole (a projector needs the pixels); this bounds the request body.
    maximum_image_bytes: int = 2*1024*1024
    # Workspace paths (fnmatch globs) write and edit refuse: records a tool owns and the worker only reads.
    protected_paths: tuple[str, ...] = ()
    # When false, write only creates files or fills an emptied one: replacing a file's
    # content in one call is a block replacement, which the method forbids.
    write_existing_files: bool = True
    # An edit must anchor on a read of the file's current content (Claude Code's own rule):
    # anchors recalled from memory or from a stale read are the bulk of failed edits.
    edit_requires_read: bool = False
    # A context that repeats the same failing call is periodic; the counter names it but
    # the model continues it. After this many identical failures in the recent window the
    # worker writes its own handoff and continues in a fresh window, which breaks the period.
    repeated_failure_rollover: int | None = None
    # Identical successful calls repeated this many times in the window are a loop too
    # (a probe re-run over and over with nothing linked between); same remedy.
    repeated_success_rollover: int | None = None
    # Workspace files the reviewer sees on every review, matched by fnmatch on their
    # workspace path: the few files where a reference departure would show, so the
    # decisive evidence is in the envelope rather than behind an investigation budget.
    review_focus_globs: tuple[str, ...] = ()
    review_focus_characters: int = 4000

    def __post_init__(self) -> None:
        for name in ('repeated_failure_rollover', 'repeated_success_rollover'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 2):
                raise ValueError(name+' must be an integer of at least 2 or None')
        object.__setattr__(self, 'review_focus_globs', tuple(self.review_focus_globs))
        if type(self.review_focus_characters) is not int or self.review_focus_characters <= 0:
            raise ValueError('review_focus_characters must be a positive integer')
        if not self.assignment.strip() or not self.worker_system.strip() or not self.guidance_prefix.strip():
            raise ValueError('Explicit worker assignment, system text and guidance prefix are required')
        tools = tuple(self.worker_tools)
        object.__setattr__(self, 'worker_tools', tools)
        if not tools or 'bash' not in tools or len(set(tools)) != len(tools) or set(tools) - set(WORKER_TOOLS):
            raise ValueError('worker_tools must be distinct names from '+', '.join(WORKER_TOOLS)+' and include bash')
        names = [t.get('name') for t in self.command_tools]
        if len(set(names)) != len(names) or set(names) & set(WORKER_TOOLS) or not all(
                isinstance(t, dict) and isinstance(t.get('name'), str) and t['name'] and isinstance(t.get('command'), str)
                and isinstance(t.get('parameters'), dict) for t in self.command_tools):
            raise ValueError('command_tools need distinct names (not bash/read/write/edit), a command template and a parameters schema')
        for name in ('maximum_tool_argument_characters', 'maximum_write_characters', 'maximum_edit_characters'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(name+' must be a positive integer or None')
        if type(self.maximum_read_lines) is not int or self.maximum_read_lines <= 0:
            raise ValueError('maximum_read_lines must be a positive integer')
        if self.reference is not None and (not self.reference.strip() or self.controller is None):
            raise ValueError('A reference requires controller settings')
        if not self.history_archive_prefix.strip():
            raise ValueError('A worker history archive prefix is required')
        if type(self.maximum_generation_retries) is not int or self.maximum_generation_retries < 0:
            raise ValueError('Generation retries must be a nonnegative integer')


class UnresolvedOperation(RuntimeError):
    """An interrupted operation must be inspected; it is never automatically replayed."""


class ReviewLimitExceeded(RuntimeError):
    """The controller exhausted its declared investigation budget."""


class ContextCapacityExceeded(ValueError):
    """The request cannot fit after preserving its fixed inputs and output headroom."""


def expand_model_requests(events: list[Any]) -> list[Any]:
    """The journal's model_request events with every body whole again: a `body_delta` is the shared prefix
    of the request it names plus its own tail. Events are returned in order, payloads copied."""
    texts: dict[str, str] = {}
    out = []
    for event in events:
        if getattr(event, 'event_type', None) != 'model_request':
            out.append(event)
            continue
        payload = dict(event.payload)
        request_id = str(payload.get('request_id') or '')
        delta = payload.pop('body_delta', None)
        if delta is not None:
            base = texts.get(str(delta.get('base')))
            if base is None:
                raise ValueError('model_request '+request_id+' is a delta of '+str(delta.get('base'))+', which the journal does not hold')
            text = base[:int(delta['shared'])]+str(delta.get('tail') or '')
            payload['body'] = json.loads(text)
            texts[request_id] = text
        elif isinstance(payload.get('body'), dict):
            texts[request_id] = json.dumps(payload['body'], sort_keys=True, allow_nan=False)
        out.append(SimpleNamespace(**{**vars(event), 'payload': payload}) if not isinstance(event, dict)
                   else dict(event, payload=payload))
    return out


_EMPTY_DIGEST = hashlib.sha256(b'').hexdigest()


def _state_digest(state: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, allow_nan=False).encode()).hexdigest()


class GenerationRetryExceeded(RuntimeError):
    """The explicitly configured recovery budget was exhausted."""


def llama_model_client(config: EndpointConfig, *, known_issues: dict | None = None,
                       diagnostic_characters: int = 8192, observer: Any = None) -> ModelClient:
    """Compose native guarded streaming with exact template counting and durable audit."""
    client = LlamaClient(base_url=config.base_url, timeout=config.timeout_seconds,
        known_issues=KnownIssues(**(known_issues or {})),
        maximum_response_bytes=config.maximum_response_bytes, diagnostic_characters=diagnostic_characters)
    return ModelClient(config, transport=SyncNativeTransport(client, config.completion_path, config.headers),
                       observer=observer)


def bash_tool() -> dict[str, Any]:
    return dict(type='function', function=dict(name='bash',
        description='Run a shell command in the persistent isolated workspace.',
        parameters=dict(type='object', properties=dict(command=dict(type='string')),
                        required=['command'], additionalProperties=False)))


def write_tool(create_only: bool = False, bounded: bool = False) -> dict[str, Any]:
    """The description states the contract this session enforces, so the model never learns it from a refusal."""
    if create_only:
        description = ('Create ONE NEW UTF-8 text file in the workspace. Refused if the file already has content '
                       '(change it with edit, or empty it with bash first)'
                       + (', and refused if the content is longer than a short skeleton: imports, signatures, '
                          'docstrings, pass bodies, or a short file. Bodies are added afterwards with edit, one '
                          'function per call.' if bounded else '.'))
    else:
        description = ('Create or replace one UTF-8 text file in the workspace with the complete content given. '
                       + ('Short files and skeletons only; longer content is refused unexecuted — build the rest '
                          'with edit, one function per call.' if bounded else
                          'Use for new or short files; use edit to change part of an existing file.'))
    return dict(type='function', function=dict(name='write', description=description,
        parameters=dict(type='object', properties=dict(path=dict(type='string'), content=dict(type='string')),
                        required=['path', 'content'], additionalProperties=False)))


def edit_tool(bounded: bool = False, requires_read: bool = False) -> dict[str, Any]:
    description = ('Replace exact text in one existing UTF-8 workspace file. old_text must occur exactly '
                   'expected_occurrences times (default 1); otherwise nothing changes and the mismatch is reported. '
                   + ('old_text is one or two lines copied from a read of the current file (an edit without such a '
                      'read is refused); ' if requires_read else '')
                   + ('new_text is one function body, branch or test — a few lines; a longer change is refused '
                      'unexecuted, so add helpers first, one per call. ' if bounded else '')
                   + 'Prefer this over rewriting a whole file.')
    return dict(type='function', function=dict(name='edit', description=description,
        parameters=dict(type='object', properties=dict(path=dict(type='string'), old_text=dict(type='string'),
            new_text=dict(type='string'), expected_occurrences=dict(type='integer', minimum=1)),
            required=['path', 'old_text', 'new_text'], additionalProperties=False)))


IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}


def read_tool(vision: bool = False) -> dict[str, Any]:
    return dict(type='function', function=dict(name='read',
        description='Read numbered lines of one UTF-8 workspace file, starting at a 1-based line offset, '
                    'at most limit lines. Use grep -n first to find the region, then read just that range.'
                    + (' A .png/.jpg/.gif/.webp path is shown to you as an image instead (one at a time; '
                       'an earlier image leaves view when a new one is read).' if vision else
                       ' Image files cannot be shown to you (this model has no vision); measure them with a tool.'),
        parameters=dict(type='object', properties=dict(path=dict(type='string'),
            offset=dict(type='integer', minimum=1), limit=dict(type='integer', minimum=1)),
            required=['path'], additionalProperties=False)))


WORKER_TOOLS = dict(bash=bash_tool, read=read_tool, write=write_tool, edit=edit_tool)


def worker_tools(names: tuple[str, ...], settings: 'SessionSettings | None' = None,
                 capabilities: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    tools = []
    for name in names:
        if name == 'read':
            tools.append(read_tool(vision=bool((capabilities or {}).get('vision'))))
        elif name == 'write' and settings is not None:
            tools.append(write_tool(create_only=not settings.write_existing_files,
                                    bounded=settings.maximum_write_characters is not None))
        elif name == 'edit' and settings is not None:
            tools.append(edit_tool(bounded=settings.maximum_edit_characters is not None,
                                   requires_read=settings.edit_requires_read))
        else:
            tools.append(WORKER_TOOLS[name]())
    for spec in (settings.command_tools if settings is not None else ()):
        tools.append(dict(type='function', function=dict(name=spec['name'], description=spec.get('description', ''),
                                                          parameters=dict(spec['parameters'], additionalProperties=False))))
    return tools


def render_command_tool(spec: dict[str, Any], args: dict[str, Any]) -> str:
    """Fill a command template from typed arguments: strings are shell-quoted, booleans become their flag
    (or nothing), absent optional parameters become their default or nothing."""
    schema = spec['parameters']
    properties = schema.get('properties') or {}
    required = schema.get('required') or []
    unknown = set(args)-set(properties)
    if unknown:
        raise ValueError(spec['name']+': unexpected arguments '+', '.join(sorted(unknown)))
    missing = [k for k in required if k not in args]
    if missing:
        raise ValueError(spec['name']+': missing '+', '.join(missing))
    values: dict[str, str] = {}
    for key, prop in properties.items():
        value = args.get(key, prop.get('default'))
        if prop.get('type') == 'boolean':
            values[key] = str(prop.get('flag', '--'+key.replace('_', '-'))) if value else ''
        elif value is None or value == '' or value == []:
            values[key] = ''
        elif prop.get('type') == 'array':
            items = value if isinstance(value, list) else [value]
            flag = str(prop.get('flag', ''))
            values[key] = ' '.join((flag+' ' if flag else '')+shlex.quote(str(v)) for v in items)   # repeated flag
        elif 'flag' in prop:
            values[key] = str(prop['flag'])+' '+shlex.quote(str(value))   # optional flag with a value
        else:
            values[key] = shlex.quote(str(value))
    rendered = spec['command'].format(**values)
    return ' '.join(rendered.split())


def controller_tools() -> list[dict[str, Any]]:
    """The controller edits only its project document and inspects frozen evidence."""
    text = dict(type='string')
    integer = dict(type='integer', minimum=0)
    specs = [
        ('project_read', 'Read your project document by character offset, including its edit revision.',
            dict(offset=integer)),
        ('project_edit', 'Edit your project document using its expected revision and one exact unique text anchor. '
            'An empty old_text initializes an empty document. Preserve completed work, decisions and future phases.',
            dict(expected_revision=integer, old_text=text, new_text=text)),
        ('project_edit_range', 'Replace the half-open character range [start_offset, end_offset) in your project '
            'document at its expected revision. Use offsets from project_read; equal offsets insert text. '
            'Keep completed work, decisions and future phases.',
            dict(expected_revision=integer, start_offset=integer, end_offset=integer, new_text=text)),
        ('workspace_list', 'List worker file paths matching a prefix. The result is paginated text; offset is a character offset.',
            dict(prefix=text, offset=integer)),
        ('workspace_read', 'Read a worker file without modifying it. Use a relative path and a character offset.',
            dict(path=text, offset=integer)),
        ('history_read', 'Read completed worker turns, including older turns outside the recent window. '
            'Turn range is inclusive; offset pages the serialized evidence by characters.',
            dict(start_turn=dict(type='integer', minimum=1), end_turn=dict(type='integer', minimum=1), offset=integer)),
        ('review_history_read', 'Retrieve original messages from this controller review, including evidence '
            'omitted from its current context. Message indices are zero-based and inclusive. '
            'Use the review_history range in the input; offset pages serialized text by characters.',
            dict(start_message=integer, end_message=integer, offset=integer)),
        ('run_check', 'Run a check in a disposable isolated copy of the worker workspace. All file changes are discarded. '
            'Every call starts from the original review snapshot; combine dependent commands in one call.',
            dict(command=text)),
        ('investigate', 'State one concrete concern that the supplied traces cannot settle and that could change your '
            'decision. This unlocks workspace_list, workspace_read, history_read and run_check for this review, within '
            'its investigation budget. Reviews without a concern are decided from the traces and the project document.',
            dict(concern=text)),
    ]
    return [dict(type='function', function=dict(name=name, description=description,
        parameters=dict(type='object', properties=properties, required=list(properties), additionalProperties=False)))
        for name, description, properties in specs]


def _empty_worker_response(response: dict[str, Any]) -> bool:
    """True for a completed response with neither visible text nor a tool call."""
    try:
        choice = response['choices'][0]; message = choice['message']
    except (KeyError, IndexError, TypeError):
        return False
    return (choice.get('finish_reason') in ('stop', 'length', None)
            and not (message.get('tool_calls') or (message.get('content') or '').strip()))


def _capabilities(client: ModelClient) -> dict[str, Any]:
    """Read from the server when the client can; a client without the call (tests, other backends) is text-only."""
    probe = getattr(client, 'capabilities', None)
    if not callable(probe):
        return dict(vision=False, audio=False, video=False, template={}, model=None, context=None, error='no probe')
    return probe()


def _identity(client: ModelClient | None) -> dict[str, Any] | None:
    if client is None:
        return None
    # Persist endpoint identity without authentication headers.
    result = {key:value for key,value in asdict(client.config).items() if key != 'headers'}
    if hasattr(client.transport, 'identity'):
        result['transport'] = deepcopy(client.transport.identity)
    return result


def _shell_identity(shell: SandboxedShell) -> dict[str, Any]:
    # Compared against the saved JSON, so tuples must already be lists.
    return json.loads(json.dumps(asdict(shell.config)))


def _settings(value: dict[str, Any]) -> SessionSettings:
    return SessionSettings(**(value | dict(session_policy=SessionPolicy(**value['session_policy']),
        review_policy=ReviewPolicy(**value['review_policy']),
        controller=ControllerSettings(**value['controller']) if value['controller'] else None)))


class FocusedSession:
    """Single-writer native worker execution, periodic review and explicit recovery.

    The reference and controller record live only in host state. The worker gets
    its assignment, native conversation, its own handoff, and held guidance.
    Controllers choose when to finish investigations. They never author the worker handoff.
    """

    def __init__(self, root: str | Path, worker: ModelClient, shell: SandboxedShell,
                 controller: ModelClient | None, shared_workspaces: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        # Snapshots are content-addressed, so one that several sessions start from (the library seed a
        # loop hands every task, 4.5 MB of procedures and cache) can live once beside them: the seed is
        # written there and read from there; a session's own snapshots stay under its root, where its
        # pruning is the only pruning.
        self.shared_workspaces = Path(shared_workspaces).resolve() if shared_workspaces else None
        self.worker = worker
        self.shell = shell
        self.controller_client = controller
        self.store = RevisionStore(self.root/'state.sqlite3')
        self.journal = SessionEventLog(self.root/'events')
        self.revision = 0
        self.state: dict[str, Any] = {}
        # The last model request journaled, for the next one's delta (in memory only: the first request
        # after a restore is journaled whole).
        self._last_request: dict[str, tuple[str, str]] = {}   # by purpose: worker and controller prompts are separate streams

    @classmethod
    def create(cls, root: str | Path, settings: SessionSettings, *, worker: ModelClient,
               shell: SandboxedShell, controller: ModelClient | None = None,
               initial_workspace: bytes = b'', initial_project_document: str = '',
               shared_workspaces: str | Path | None = None) -> FocusedSession:
        result = cls(root, worker, shell, controller, shared_workspaces)
        with result._locked():
            if result.store.read()['revision'] or result.journal.read_strict('session'):
                raise ValueError('Session already exists; open it without overwriting')
            if settings.reference is not None and controller is None:
                raise ValueError('An enabled controller requires its own explicit client binding')
            result.settings = settings
            capabilities = _capabilities(worker)
            result.session = PersistentSession([
                dict(role='system', content=settings.worker_system),
                dict(role='user', content=settings.assignment),
            ], worker_tools(settings.worker_tools, settings, capabilities), settings.generation, settings.session_policy)
            result.progress = ControllerProgress(settings.review_policy, initial_project_document)
            result.state = dict(schema=2, settings=asdict(settings), worker_identity=_identity(worker),
                capabilities=capabilities,
                controller_identity=_identity(controller) if settings.reference is not None else None,
                shell_config=_shell_identity(shell), phase='worker', pending_io=None,
                workspace=result._put_workspace(initial_workspace, seed=True), input_cursor=0,
                active_turn=None, proposed_final=None, final_text='', handoffs=0, review=None, blocked_reason=None)
            result._save()
        return result

    @classmethod
    def open(cls, root: str | Path, *, worker: ModelClient, shell: SandboxedShell,
             controller: ModelClient | None = None, shared_workspaces: str | Path | None = None) -> FocusedSession:
        result = cls(root, worker, shell, controller, shared_workspaces)
        with result._locked():
            if result.journal.drop_torn_tail('session'):
                result.journal.append(session_id='session', event_type='torn_tail_dropped', payload={})
            result._restore()
            if (result.state['worker_identity'] != _identity(worker)
                    or result.state['shell_config'] != _shell_identity(shell)
                    or result.state['controller_identity'] != (
                        _identity(controller) if result.settings.reference is not None else None)):
                raise ValueError('Reopen requires the saved model and shell bindings')
            # The serving model may have changed behind the same endpoint (a projector loaded, or not):
            # capabilities are re-read on every open and the read tool's contract follows them.
            capabilities = _capabilities(worker)
            if capabilities != result.state.get('capabilities'):
                result.state['capabilities'] = capabilities
                result.session.tools = worker_tools(result.settings.worker_tools, result.settings, capabilities)
                result._event('capabilities', capabilities)
                result._save()
        return result

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root/'writer.lock').open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('Another writer owns this session') from error
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _event(self, kind: str, payload: dict[str, Any]) -> None:
        self.journal.append(session_id='session', event_type=kind, payload=payload)

    # How many committed revisions the session store keeps. Restore reads the newest; the rest were
    # identical snapshots of every save (176 MB per long task) that nothing read back.
    revisions_kept = 2

    @property
    def _wal(self) -> Path:
        return self.root/'events'/'checkpoint.wal.json'

    def _save(self, *, commit: bool = True) -> None:
        """Commit, then a one-line journal record; or with ``commit=False`` only note that the state moved,
        for a boundary inside a turn. The store is written where the never-replay rule needs a copy: before
        each model call and before each shell command (an outcome that never lands is then a pending
        operation, uncertain, never redone on its own), at the end of a turn, and on every operator action.
        The boundaries between — a response whose tool calls are about to run, a tool outcome with more of
        the batch to go, a handoff's bookkeeping — ride on the next commit. A turn with one tool call is
        three writes of the state instead of four; a handoff, one instead of four.

        Commit, then a one-line journal record. The store's commit is atomic on its own (sqlite's
        rollback journal), so the state is written once per save: journalling the whole state every turn
        cost 278 KB a turn (182 MB on one 172-turn task), the store kept every revision besides (176 MB
        more), and a write-ahead copy of each commit doubled the bytes again (1.3 MB written per 240 KB
        checkpoint with the VACUUM the prune ran) — for a recovery that only ever needs the newest copy.
        A save interrupted mid-commit resumes from the previous revision; a write-ahead file left by an
        earlier harness is still honoured on restore."""
        if not commit:
            self._dirty = True
            return
        self._dirty = False
        self.state['session'] = self.session.export_state()
        self.state['controller_progress'] = self.progress.export_state()
        snapshot = deepcopy(self.state)
        digest = _state_digest(snapshot)
        committed = self.store.commit(self.revision, lambda _: dict(checkpoint=digest, state=snapshot))
        self.revision = committed['revision']
        self._committed_workspace = snapshot.get('workspace')
        self.journal.append(session_id='session', event_type='checkpoint',
            payload=dict(revision=self.revision, state_sha256=digest))
        self.store.prune(self.revisions_kept)

    def _flush(self) -> None:
        """Commit what deferred saves left in memory, at a boundary the session may be left on."""
        if getattr(self, '_dirty', False):
            self._save()

    def _restore(self) -> None:
        self._dirty = False
        saved = self.store.read()
        checkpoints = [event for event in self.journal.read_strict('session') if event.event_type == 'checkpoint']
        wal = None
        if self._wal.exists():
            try:
                wal = json.loads(self._wal.read_text())
            except ValueError:
                wal = None   # a torn write-ahead file is no checkpoint; the store stands
        if not checkpoints and not saved['revision'] and wal is None:
            raise ValueError('No saved session')
        if [event.payload['revision'] for event in checkpoints] != list(range(1, len(checkpoints)+1)):
            raise ValueError('Nonconsecutive checkpoint journal')
        legacy = [event for event in checkpoints if 'state' in event.payload]
        if legacy and saved['revision'] < len(legacy):
            # A session saved by the earlier harness carried every state in the journal: replay what the
            # store lacks, exactly as before.
            for event in legacy[saved['revision']:]:
                saved = self.store.commit(saved['revision'], lambda _, event=event:
                    dict(checkpoint=event.event_id, state=event.payload['state']))
        if wal is not None and wal.get('revision') == saved['revision']+1:
            # A complete write-ahead checkpoint finishes an interrupted commit.
            saved = self.store.commit(saved['revision'], lambda _: dict(checkpoint=wal['checkpoint'], state=wal['state']))
            self._wal.unlink(missing_ok=True)
        if saved['revision'] > len(checkpoints)+1:
            raise ValueError('Revision store extends beyond its journal')
        if saved['revision'] == len(checkpoints)+1:
            # Committed, then interrupted before the journal line: the line is derivable, so write it.
            self.journal.append(session_id='session', event_type='checkpoint',
                payload=dict(revision=saved['revision'], state_sha256=_state_digest(saved['data']['state'])))
        elif saved['revision'] < len(checkpoints):
            raise ValueError('Journal extends beyond its revision store')
        last = checkpoints[-1].payload if checkpoints else {}
        if last.get('state_sha256') and saved['revision'] == len(checkpoints) \
                and last['state_sha256'] != _state_digest(saved['data']['state']):
            raise ValueError('Revision store differs from its journal')
        self.revision = saved['revision']
        self.state = deepcopy(saved['data']['state'])
        self._committed_workspace = self.state.get('workspace')
        if self.state['schema'] != 2:
            raise ValueError('Legacy session requires its original harness; start a new document-based session')
        self.settings = _settings(self.state['settings'])
        self.session = PersistentSession.from_state(self.state['session'])
        self.progress = ControllerProgress.from_state(self.state['controller_progress'])
        self.state.setdefault('blocked_reason', None)
        self.workspace()

    def _workspace_store(self, digest: str, *, write: bool = False) -> RevisionStore:
        """Where a snapshot lives: under the session, else in the shared directory; a seed is written shared."""
        local = self.root/'workspaces'/(digest+'.sqlite3')
        shared = self.shared_workspaces/(digest+'.sqlite3') if self.shared_workspaces else None
        if shared is not None and (write or (not local.exists() and shared.exists())):
            return RevisionStore(shared)
        return RevisionStore(local)

    def _put_workspace(self, value: Any, *, seed: bool = False) -> str:
        limits = self.shell.config.limits
        if self._directory() is not None:
            # Bind mode: the tree is the directory; what is saved is the state part — the tar of the state
            # directories (the project's .mizpah, the worker's .playbook) as of now. It seeds the directory
            # now and again on open, so a session's first command sees the brief and a resumed session sees
            # what it left. (Saving the empty marker here left every bind-mode session with an empty state
            # tree: `terra route status` inside the sandbox answered "no route", 2026-09-20.)
            value = value.state if isinstance(value, DirectoryWorkspace) else (value or b'')
            if value:
                self.shell._directory = self._directory().with_state(value)
        else:
            workspace_files(value, byte_limit=limits.workspace_bytes, file_limit=limits.max_files)
        digest = hashlib.sha256(value).hexdigest()
        store = self._workspace_store(digest, write=seed)
        if not store.read()['revision']:
            encoded = base64.b64encode(value).decode('ascii')
            store.commit(0, lambda _: dict(content=encoded))
        return digest

    def workspace(self) -> Any:
        """Return the verified opaque worker workspace, never host controller files.
        In bind mode this is the DirectoryWorkspace: the same operations, over the project directory, seeded
        with the saved state part when the directory holds none yet (a session just opened)."""
        directory = self._directory()
        if directory is not None:
            if not directory.state and self.state.get('workspace') and self.state['workspace'] != _EMPTY_DIGEST:
                saved = self._workspace_store(self.state['workspace']).read()
                if saved['revision']:
                    self.shell._directory = directory.with_state(base64.b64decode(saved['data']['content'], validate=True))
                    directory = self.shell._directory
            return directory
        digest = self.state['workspace']
        if len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest):
            raise ValueError('Invalid workspace identity')
        saved = self._workspace_store(digest).read()
        value = base64.b64decode(saved['data']['content'], validate=True)
        if hashlib.sha256(value).hexdigest() != digest:
            raise ValueError('Saved workspace failed its integrity check')
        return value

    def _directory(self) -> Any:
        """The bound directory in bind mode; None for a snapshot-mode shell (or a fixture without the notion)."""
        return getattr(self.shell, 'directory', None)

    def workspace_snapshot(self) -> bytes:
        """The workspace as tar bytes whichever mode: what a harvest or a re-measurement is given."""
        if self._directory() is not None:
            return self.shell.snapshot()
        return self.workspace()

    def _client(self, original: ModelClient) -> ModelClient:
        """The same client with this session's journal added to its observer.

        A subclass (a hosted provider with its own counting and capabilities) is kept: the copy is made
        through its class with the same attributes, not rebuilt as a plain ModelClient."""
        def observer(kind: str, payload: dict[str, Any]) -> None:
            self._event(kind, self._delta_request(payload) if kind == 'model_request' else payload)
            if original.observer:
                original.observer(kind, payload)
        copy = object.__new__(type(original))
        copy.__dict__.update(original.__dict__)
        copy.observer = observer
        return copy

    def _delta_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """A request body journaled as its difference from the previous request: the prompt is the same
        prefix turn after turn (that is what the cache hit rate measures), so the journal keeps the shared
        length and the new tail instead of the whole prompt again — 117 KB a turn on a long task, 90% of it
        repeated. `expand_model_requests` reads it back verbatim."""
        body = payload.get('body')
        if not isinstance(body, dict):
            return payload
        text = json.dumps(body, sort_keys=True, allow_nan=False)
        request_id = str(payload.get('request_id') or '')
        purpose = str(payload.get('purpose') or '')
        previous = self._last_request.get(purpose)
        self._last_request[purpose] = (request_id, text)
        if previous is None:
            return payload
        shared = len(os.path.commonprefix([previous[1], text]))
        if shared < len(text)//4:
            return payload
        out = dict(payload)
        del out['body']
        out['body_delta'] = dict(base=previous[0], shared=shared, tail=text[shared:])
        return out

    def _guidance_message(self) -> list[dict[str, Any]]:
        text = self.progress.applied_guidance() if self.settings.reference is not None else ''
        return [dict(role='user', content=self.settings.guidance_prefix+'\n'+text)] if text else []

    def worker_payload(self) -> dict[str, Any]:
        """Exact next worker request; private reference and progress are not inserted."""
        value = self.session.payload()
        value['messages'].extend(self._guidance_message())
        return value

    def _incoming(self) -> list[dict[str, Any]]:
        # Completed prior assistant messages are not new input. Tool results and
        # user/system text since the preceding call are applied incoming context.
        return deepcopy(self.session.messages[self.state['input_cursor']:])+self._guidance_message()

    def _complete(self, client: ModelClient, payload: dict[str, Any], purpose: str,
                  capacity: int, prompt_tokens: int | None = None,
                  output_headroom: int = 1) -> dict[str, Any] | None:
        observed = self._client(client)
        if self.state.get('generation_rejections', {}).get(purpose, 0):
            payload = deepcopy(payload)
            cause = self.state.get('generation_rejection_cause', {}).get(purpose, 'repetition')
            payload['messages'].append(dict(role='user', content=(
                'Your previous response was empty: no text and no tool call. Nothing was executed or accepted. '
                'Continue from the unchanged conversation and committed tool outcomes with a tool call or, '
                'if the work is complete, your final report.' if cause == 'empty' else
                'Your previous generation was cancelled after sustained repetition. '
                'No tool calls from that response were executed and no decision or handoff was accepted. '
                'Continue from the unchanged conversation and committed tool outcomes. '
                'Produce a fresh response; avoid repeating the same text or identical tool calls.')))
            prompt_tokens = None
        count = observed.count(payload, purpose)['tokens'] if prompt_tokens is None else prompt_tokens
        if count+max(payload['max_tokens'], output_headroom) > capacity:
            raise ContextCapacityExceeded(purpose+' request exceeds its declared context capacity')
        self.state['pending_io'] = dict(kind='model', purpose=purpose, prompt_tokens=count)
        self._save()
        try:
            response = observed.complete(payload, purpose)
        except RejectedGeneration as error:
            return self._reject_generation(purpose, 'repetition',
                dict(elapsed_seconds=error.response.elapsed_seconds, evidence=error.response.evidence))
        if purpose == 'worker' and _empty_worker_response(response):
            # An empty completion is a generation failure, not a decision: retry within
            # the same recovery budget as a cancelled stream, then block if exhausted.
            usage = response.get('usage') or {}
            return self._reject_generation(purpose, 'empty', dict(evidence=dict(
                finish_reason=(response.get('choices') or [{}])[0].get('finish_reason'),
                completion_tokens=usage.get('completion_tokens'))))
        self.state.setdefault('generation_rejections', {}).pop(purpose, None)
        self.state.setdefault('generation_rejection_cause', {}).pop(purpose, None)
        return response

    def _reject_generation(self, purpose: str, cause: str, record: dict[str, Any]) -> None:
        counts = self.state.setdefault('generation_rejections', {})
        counts[purpose] = counts.get(purpose, 0)+1
        self.state.setdefault('generation_rejection_cause', {})[purpose] = cause
        if purpose == 'controller':
            self.state['review']['model_calls'] += 1
        self.state['pending_io'] = None
        self._event('generation_rejected', dict(purpose=purpose, cause=cause, attempt=counts[purpose],
            maximum_retries=self.settings.maximum_generation_retries, tools_executed=False, **record))
        exhausted = counts[purpose] > self.settings.maximum_generation_retries
        if purpose == 'worker' and not exhausted and counts[purpose] >= 2 and not self.state.get('rollover_requested') \
                and len(self.session.messages) > len(self.session.base_messages)+1:
            # Retrying the same context reproduces the same degenerate generation; the last retry runs
            # in a fresh window instead.
            self.state['rollover_requested'] = dict(reason='generation_'+cause, name=purpose, count=counts[purpose])
        if exhausted:
            self.state.update(blocked_kind='generation_retries',
                blocked_reason=purpose+' exhausted its generation recovery budget after '+cause+' responses')
        self._save()
        if exhausted:
            raise GenerationRetryExceeded(self.state['blocked_reason'])
        return None

    def _refuse_protected(self, path: str) -> None:
        clean = path.removeprefix('/work/')
        while clean.startswith('./'):
            clean = clean[2:]
        for pattern in self.settings.protected_paths:
            if fnmatch.fnmatch(clean, pattern) or fnmatch.fnmatch(path, pattern):
                raise ValueError(path+' is a record a tool owns, not a file to write: change it through that tool\'s '
                                 'commands, or leave it')

    def _finish_turn(self) -> None:
        self.progress.observe(self.state['active_turn'])
        self._event('worker_turn', deepcopy(self.state['active_turn']))
        self.state['active_turn'] = None
        image = self.state.pop('pending_image', None)
        if image:
            self.session.append_image('read '+image['path']+' (image):', image['mime'], image['data'])
            self._event('image_shown', dict(path=image['path'], mime=image['mime'], bytes=len(image['data'])*3//4))
        final = self.state['proposed_final'] is not None
        review = self.settings.reference is not None and (
            self.progress.due() or (final and self.settings.review_on_completion))
        self.state['phase'] = 'review' if review else ('complete' if final else 'worker')
        if final and not review:
            self.state['final_text'] = self.state['proposed_final']
        # Snapshots the saved state no longer references are history nobody reads; a full /work tarball
        # per turn (5 MB with a few widgets in cg/) filled a RAM-backed scratch disk in an afternoon.
        # The committed state may still point at the one before (its save is deferred to the next
        # commit), and a restore needs it: keep both.
        keep = {self.state['workspace'], getattr(self, '_committed_workspace', None)}
        for path in (self.root/'workspaces').glob('*.sqlite3'):
            if path.stem not in keep:
                path.unlink(missing_ok=True)

    def _worker(self) -> None:
        payload = self.worker_payload()
        count = self._client(self.worker).count(payload, 'worker')['tokens']
        requested = self.state.get('rollover_requested')
        if requested and len(self.session.messages) > len(self.session.base_messages)+1:
            self._event('rollover_forced', dict(requested, window=self.session.window_index))
            self.state['rollover_requested'] = None
            self.state['recent_calls'] = []
            self.state['phase'] = 'handoff'
            self._save(commit=False)
            return
        if self.session.needs_rollover(count):
            # A just-reset window must leave room for actual work.
            if self.session.window_index and len(self.session.messages) <= len(self.session.base_messages)+1:
                raise ContextCapacityExceeded('The resumed context is already above the rollover threshold')
            self.state['phase'] = 'handoff'
            self._save(commit=False)
            return
        incoming = self._incoming()
        response = self._complete(self.worker, payload, 'worker', self.settings.session_policy.context_capacity, count,
                                  self.settings.session_policy.output_headroom_tokens)
        if response is None:
            return
        parsed = parse_turn(response)
        if parsed.finish_reason not in ('stop', 'tool_calls') or not (
                parsed.message.get('tool_calls') or (parsed.message.get('content') or '').strip()):
            raise ValueError('Worker did not complete a usable response')
        self.session.accept(response)
        visible = {key:value for key,value in parsed.message.items() if key != 'reasoning_content'}
        self.state['active_turn'] = dict(turn=self.progress.turns+1, window=self.session.window_index,
            applied_input=incoming, response=visible, tool_results=[])
        self.state['input_cursor'] = len(self.session.messages)
        self.state['proposed_final'] = None if self.session.pending_tools else parsed.message['content']
        self.state['pending_io'] = None
        if self.session.pending_tools:
            self.state['phase'] = 'tools'
        else:
            self._finish_turn()
        self._save(commit=self.state['phase'] != 'tools')   # a response with tool calls is committed with its first tool

    def _tool(self) -> None:
        turn = self.state['active_turn']
        completed = len(turn['tool_results'])
        call = turn['response']['tool_calls'][completed]
        function = call['function']
        name = function['name']
        arguments = function['arguments']
        rendered = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
        bound = self.settings.maximum_tool_argument_characters
        limits = self.shell.config.limits
        options = dict(byte_limit=limits.workspace_bytes, file_limit=limits.max_files)
        signature = hashlib.sha256((name+'\x00'+rendered).encode('utf-8')).hexdigest()
        # Repetition is judged over a short window, not only the immediately previous
        # call, so an alternating cycle (delete, oversized write, delete, ...) is
        # counted as the repeat it is.
        window = list(self.state.get('recent_calls') or [])[-6:]
        repeats = window.count(signature)
        repeated = repeats > 0
        if name not in self.settings.worker_tools and name not in {t['name'] for t in self.settings.command_tools}:
            output = dict(status='error', error='Unknown tool: '+name)
        elif bound is not None and len(rendered) > bound:
            # A rejected call never reaches the shell or the workspace. The bound is
            # the size of an action this harness is willing to carry in context.
            # An unchanged repeat gets a firmer message rather than the same one.
            advice = ('This exact call was already rejected and nothing has changed, so it was rejected again. '
                      'Do not retry it. Split the change: delete the block with bash if needed, then rebuild it '
                      'one small piece per edit.' if repeated else
                      'This call is too large to execute. Nothing was executed. Split the change rather than '
                      'shrinking it: write a skeleton first, then fill in one function, branch, test or section '
                      'per edit, and keep bash commands brief.')
            if name == 'edit' and not repeated:
                # The usual oversized edit is one function that does everything; name the split.
                advice = ('This edit is too large to execute; nothing was executed. A single function is doing '
                          'several jobs. Split it by responsibility: one small function per quantity or step '
                          '(each reads its input and returns one value), added one per edit; then a short '
                          'function that only calls them. Shrinking the same function will be refused again.')
            output = dict(status='rejected', code='arguments_too_large', characters=len(rendered), bound=bound,
                          repeated=repeated, error=advice)
            self._event('tool_rejected', dict(call_id=call['id'], name=name, characters=len(rendered), bound=bound,
                                              repeated=repeated))
            # Oversized attempts at one file are the same loop whether or not the bytes match:
            # a whole-file rewrite refused, deleted, tried again. Count them per path.
            try:
                target = (json.loads(rendered) if isinstance(arguments, str) else arguments).get('path')
            except (ValueError, AttributeError, TypeError):
                target = None
            threshold = self.settings.repeated_failure_rollover
            if target and threshold is not None:
                counts = self.state.setdefault('oversized_by_path', {})
                counts[target] = counts.get(target, 0)+1
                if counts[target] >= threshold:
                    self.state['rollover_requested'] = dict(reason='oversized_rewrites', name=name, count=counts[target],
                                                            path=target)
                    counts[target] = 0
        elif name in ('read', 'write', 'edit'):
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
                if not isinstance(args, dict):
                    raise ValueError(name+' requires an object of arguments')
                if name == 'read':
                    if not {'path'} <= set(args) <= {'path', 'offset', 'limit'} or not isinstance(args['path'], str):
                        raise ValueError('read requires a path string and optional offset and limit integers')
                    mime = IMAGE_TYPES.get(Path(args['path']).suffix.lower())
                    if mime is not None:
                        if not (self.state.get('capabilities') or {}).get('vision'):
                            raise ValueError('this model cannot see images (no vision modality is loaded on the server); '
                                             'measure the image with a tool instead of reading it')
                        data = read_workspace_file(self.workspace(), args['path'], **options)
                        if len(data) > self.settings.maximum_image_bytes:
                            raise ValueError('image is larger than '+str(self.settings.maximum_image_bytes)+' bytes; '
                                             'downscale or crop it first')
                        # The image itself is shown at the turn boundary, as a user message the wire view keeps
                        # only the latest of; the tool result records what was shown.
                        self.state['pending_image'] = dict(path=args['path'], mime=mime,
                                                           data=base64.b64encode(data).decode('ascii'))
                        report = dict(path=args['path'], image=True, mime=mime, bytes=len(data),
                                      note='shown to you after this tool batch')
                        limit = None
                    else:
                        limit = min(int(args.get('limit', self.settings.maximum_read_lines)), self.settings.maximum_read_lines)
                        report = read_workspace_lines(self.workspace(), args['path'], offset=int(args.get('offset', 1)),
                            limit=limit, **options)
                    # Remember what the worker saw: an edit must anchor on a read of the current content.
                    self.state.setdefault('read_hashes', {})[args['path']] = hashlib.sha256(
                        read_workspace_file(self.workspace(), args['path'], **options)).hexdigest()
                    snapshot = None
                elif name == 'write':
                    if set(args) != {'path', 'content'} or not all(isinstance(args[k], str) for k in args):
                        raise ValueError('write requires exactly path and content strings')
                    self._refuse_protected(args['path'])
                    limit = self.settings.maximum_write_characters
                    if limit is not None and len(args['content']) > limit:
                        raise ValueError('write content is too long for one call; write the skeleton first and fill it in with edit')
                    existing = set(workspace_files(self.workspace(), **options))
                    data = args['content'].encode('utf-8')
                    if (not self.settings.write_existing_files and args['path'] in existing
                            and read_workspace_file(self.workspace(), args['path'], **options).strip()):
                        raise ValueError('write only creates files; '+args['path']+' already has content. Change it with '
                                         'edit one piece at a time, or delete the block with bash first and rebuild it by refinement')
                    snapshot = write_workspace_file(self.workspace(), args['path'], data, **options)
                    report = dict(path=args['path'], created=args['path'] not in existing, bytes_written=len(data))
                else:
                    keys = {'path', 'old_text', 'new_text'}
                    if not keys <= set(args) <= keys | {'expected_occurrences'} or not all(isinstance(args[k], str) for k in keys):
                        raise ValueError('edit requires path, old_text and new_text strings and an optional expected_occurrences integer')
                    self._refuse_protected(args['path'])
                    if self.settings.edit_requires_read:
                        try:
                            current = hashlib.sha256(read_workspace_file(self.workspace(), args['path'], **options)).hexdigest()
                        except FileNotFoundError:
                            current = None
                        seen = self.state.get('read_hashes', {}).get(args['path'])
                        if current is not None and seen is None:
                            raise ValueError('edit requires a read of '+args['path']+' first: read the region you are '
                                             'changing and copy old_text from those numbered lines')
                        if current is not None and seen != current:
                            raise ValueError(args['path']+' has changed since you last read it: read the region again '
                                             'and copy old_text from the current lines')
                    limit = self.settings.maximum_edit_characters
                    size = len(args['old_text'])+len(args['new_text'])
                    if limit is not None and size > limit:
                        old_share = len(args['old_text'])/max(1, size)
                        side = ('old_text is the large part: anchor on one or two lines instead of a whole block'
                                if old_share >= 0.4 else
                                'new_text is the large part: add one function, branch, test or section per edit')
                        raise ValueError('edit text is too long for one call; '+side+
                                         '. If replacing a block, delete it with bash first and rebuild it in pieces')
                    snapshot, report = edit_workspace_file(self.workspace(), args['path'], args['old_text'], args['new_text'],
                        expected_occurrences=args.get('expected_occurrences', 1), **options)
            except WorkspaceEditError as error:
                hints = dict(
                    old_text_empty='To add text, anchor on an existing line: old_text is that line copied verbatim '
                                   '(for example the section heading), new_text is that line followed by the new text. '
                                   'To append at the end, use bash: cat >> FILE << \'EOF\'.',
                    text_not_found='Read the region first and copy old_text verbatim from the numbered lines; do not '
                                   'recall it from memory.',
                    occurrence_mismatch='Choose a longer or more specific old_text so it matches exactly once, or set '
                                        'expected_occurrences to the count you intend.')
                output = dict(status='error', code=error.code, error=str(error)+(' '+hints[error.code] if error.code in hints else ''))
            except (ValueError, TypeError) as error:
                output = dict(status='error', error=str(error))
            else:
                if snapshot is not None:
                    self.state['workspace'] = self._put_workspace(snapshot)
                    # Progress on the file: oversized attempts before it were not a stuck loop.
                    self.state.get('oversized_by_path', {}).pop(args['path'], None)
                    # The worker authored this content; count it as read.
                    self.state.setdefault('read_hashes', {})[args['path']] = hashlib.sha256(
                        read_workspace_file(self.workspace(), args['path'], **options)).hexdigest()
                output = dict(status='ok', tool=name, **report)
            self._event('tool_outcome', dict(call_id=call['id'], **{key:value for key,value in output.items() if key != 'tool'}, tool=name))
        else:
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
                spec = next((t for t in self.settings.command_tools if t['name'] == name), None)
                if spec is not None:
                    if not isinstance(args, dict):
                        raise ValueError(name+' requires an object of arguments')
                    args = dict(command=render_command_tool(spec, args))
                    self._event('command_tool', dict(call_id=call['id'], name=name, command=args['command']))
                if isinstance(args, dict):
                    # A timeout the model adds from habit is not an error: the harness bounds every command
                    # itself. Any other extra key is named, so the retry drops it instead of rewording the command
                    # (logo_mark8 retried fifteen variants against an error that never said "timeout").
                    args = {k: v for k, v in args.items() if k not in ('timeout', 'timeout_ms', 'timeout_seconds')}
                if not isinstance(args, dict) or 'command' not in args:
                    raise ValueError('bash takes one argument, "command" (a string); got '
                                     +(', '.join(sorted(map(str, args))) if isinstance(args, dict) else type(args).__name__))
                if set(args) != {'command'}:
                    raise ValueError('bash takes only "command"; drop '+', '.join(sorted(k for k in args if k != 'command')))
                if not isinstance(args['command'], str) or not args['command'].strip():
                    raise ValueError('bash requires a nonempty command string')
            except (ValueError, TypeError) as error:
                output = dict(status='error', error=str(error))
            else:
                self.state['pending_io'] = dict(kind='tool', call_id=call['id'], command=args['command'])
                self._save()
                result = self.shell.run(args['command'], self.workspace())
                self._event('tool_outcome', dict(call_id=call['id'], **{
                    key:value for key,value in asdict(result).items() if key != 'workspace'}))
                if result.status == 'interrupted':
                    raise UnresolvedOperation('Shell outcome is uncertain; inspect the saved event before recovery')
                self.state['workspace'] = self._put_workspace(result.workspace)
                output = {key:value for key,value in asdict(result).items() if key != 'workspace'}
        # Any call repeated byte-for-byte gets a distinct, counted response, whatever
        # its outcome. Identical calls with identical responses make the context
        # periodic, and a model continues a periodic context; the counter breaks the
        # period and names the loop. A different call resets it.
        count = repeats+1
        self.state['recent_calls'] = (window+[signature])[-6:]
        if count > 1:
            if output.get('status') in ('rejected', 'error'):
                note = (f'This identical call has now failed {count} times in your last few actions with the same result. '
                        'Repeating it, alone or alternating with another call, will not change the outcome. Original error: '+str(output.get('error', output.get('code', '')))+
                        ' Read the current file, then take a different, smaller action.')
                output = dict(output, repeated=True, identical_repeats=count, error=note)
            else:
                output = dict(output, repeated=True, identical_repeats=count,
                    note=f'This identical call has now been applied {count} times in your last few actions; the workspace already reflects it. '
                         'Do not repeat it. Continue with the next step: read the file if unsure of its current content.')
            self._event('tool_repeated_call', dict(call_id=call['id'], name=name, count=count,
                                                   status=output.get('status'), code=output.get('code')))
            failed = output.get('status') in ('rejected', 'error')
            threshold = self.settings.repeated_failure_rollover if failed else self.settings.repeated_success_rollover
            if threshold is not None and count >= threshold:
                self.state['rollover_requested'] = dict(reason='repeated_failure' if failed else 'repeated_success',
                                                        name=name, count=count)
        # Arguments longer than the wire excerpt go to a workspace file, like large
        # command output: the transcript keeps a path, the worker can read it in ranges.
        archive = None
        excerpt = self.settings.session_policy.argument_excerpt_characters
        if excerpt is not None and len(rendered) > excerpt:
            archive = '.tool-output/'+call['id']+'.args.json'
            record = json.dumps(dict(call_id=call['id'], tool=name, status=output.get('status'),
                                     arguments=json.loads(rendered) if isinstance(arguments, str) else arguments),
                                ensure_ascii=False, indent=1).encode('utf-8')
            self.state['workspace'] = self._put_workspace(write_workspace_file(self.workspace(), archive, record, **options))
            output = dict(output, arguments_saved_at=archive)
        self.session.append_tool_result(call['id'], function['name'], json.dumps(output, ensure_ascii=False),
                                        arguments_archive=archive)
        turn['tool_results'].append(dict(call_id=call['id'], name=function['name'], result=output))
        self.state['pending_io'] = None
        if not self.session.pending_tools:
            self._finish_turn()
        self._save(commit=not self.session.pending_tools)   # the turn ends with its last tool; the ones before ride on the next tool's commit

    def _handoff(self) -> None:
        policy = self.settings.session_policy
        # Every reset retains a worker-visible original, even when the handoff
        # request itself fits. The archive contains only worker-owned history.
        original = json.dumps(dict(window_index=self.session.window_index,
            messages=self.session.messages), ensure_ascii=False).encode('utf-8')
        digest = hashlib.sha256(original).hexdigest()
        source_archive = (self.settings.history_archive_prefix.rstrip('/')+
                          f'/window-{self.session.window_index:05d}-{digest[:16]}.json')
        limits = self.shell.config.limits
        options = dict(byte_limit=limits.workspace_bytes, file_limit=limits.max_files)
        workspace = self.workspace()
        if source_archive in workspace_files(workspace, **options):
            if read_workspace_file(workspace, source_archive, **options) != original:
                raise ValueError('A different file occupies the worker history archive path')
        else:
            workspace = write_workspace_file(workspace, source_archive, original, **options)
        self.state['workspace'] = self._put_workspace(workspace)
        self._event('worker_history_archive', dict(window_index=self.session.window_index,
            archive=source_archive, sha256=digest))
        self._save(commit=False)
        archive_message = dict(role='user', content=
            'Your complete original history for this window is saved in your workspace at '+source_archive+'. '
            'Use it when a needed detail is absent from your own handoff or retained results. '
            'Read existing accumulated notes before updating them; retain established findings. '
            'Preserve any unfinished obligation following the last completed tool result.')
        payload = self.session.handoff_payload()
        payload['messages'][-1:-1] = self._guidance_message()+[archive_message]
        client = self._client(self.worker)
        count = client.count(payload, 'handoff')['tokens']
        limit = policy.context_capacity-max(payload['max_tokens'], policy.output_headroom_tokens)
        if count > limit and len(self.session.messages) > len(self.session.base_messages):
            original_count = count
            excerpt = policy.overflow_excerpt_characters
            while True:
                projected = project_native_messages(self.session.messages,
                    protected_prefix=len(self.session.base_messages),
                    max_field_characters=excerpt, archive_reference=source_archive)
                payload = self.session.handoff_payload(history=projected['messages'])
                payload['messages'][-1:-1] = self._guidance_message()+[archive_message, dict(role='user', content=
                    'This is an explicit excerpt view for compaction. The complete original worker history '
                    'is preserved in your workspace at '+source_archive+'. Preserve this path in your handoff '
                    'when further retrieval may be needed. Do not infer missing evidence was never observed.')]
                count = client.count(payload, 'handoff_projection')['tokens']
                if count <= limit or excerpt == 0:
                    break
                excerpt //= 2
            self._event('context_projection', dict(archive=source_archive, original_tokens=original_count,
                projected_tokens=count, excerpt_characters=excerpt, replaced_fields=projected['replaced_fields']))
        response = self._complete(self.worker, payload, 'handoff', policy.context_capacity, count,
                                  policy.output_headroom_tokens)
        if response is None:
            return
        turn = parse_turn(response)
        if turn.finish_reason != 'stop' or turn.message.get('tool_calls') or not (turn.message.get('content') or '').strip():
            raise ValueError('The worker did not produce a complete tool-free handoff')
        transition = self.session.rollover(turn.message['content'], source_archive=source_archive)
        self._event('worker_handoff', transition)
        self.state.update(phase='worker', pending_io=None, input_cursor=0, handoffs=self.state['handoffs']+1)
        self.state.get('generation_rejections', {}).pop('worker', None)   # a fresh window gets a fresh retry budget
        self._save(commit=False)

    def _focus_files(self) -> dict[str, str]:
        """Bounded content of the workspace files the settings mark as decisive for review."""
        globs = self.settings.review_focus_globs
        if not globs:
            return {}
        limits = self.shell.config.limits
        options = dict(byte_limit=limits.workspace_bytes, file_limit=limits.max_files)
        snapshot = self.workspace()
        result: dict[str, str] = {}
        budget = self.settings.review_focus_characters
        for name in workspace_files(snapshot, **options):
            if any(fnmatch.fnmatch(name, pattern) for pattern in globs):
                if budget <= 0:
                    result[name] = '[omitted: review focus budget exhausted; read it with workspace_read]'
                    continue
                text = read_workspace_file(snapshot, name, **options).decode('utf-8', errors='replace')
                if len(text) > budget:
                    text = text[:budget]+'\n[truncated at '+str(budget)+' characters; read the rest with workspace_read]'
                budget -= len(text)
                result[name] = text
        return result

    def _review_payload(self, session: PersistentSession, review: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Fit each request from archived originals and the latest project state."""
        settings = self.settings.controller
        payload = session.payload()
        initial = json.loads(session.base_messages[1]['content'])
        last_message = len(session.messages)-1
        retrieval = dict(tool='review_history_read', arguments=dict(
            start_message=0, end_message=last_message, offset=0))
        proposed = initial['proposed_input'] | dict(review_state=dict(
            model_calls=review['model_calls'], tool_calls=review['tool_calls'], review_history=retrieval,
            investigation=dict(kind=review.get('kind'), concern=review.get('concern'),
                remaining_tool_calls=review.get('investigation_remaining'),
                document_edits_remaining=(None if self.settings.controller.maximum_document_edits_per_review is None
                    else self.settings.controller.maximum_document_edits_per_review-review.get('document_edits', 0)))))
        observations = initial['recent_turns']
        hard_limit = settings.context_capacity-max(settings.output_tokens, settings.output_headroom_tokens)
        target = min(settings.input_target_tokens or hard_limit, hard_limit)
        excerpt = settings.maximum_tool_output_characters
        observations_projected = False
        client = self._client(self.controller_client)
        archive = f'review_history_read(start_message=0,end_message={last_message},offset=0)'

        def count(messages: list[dict[str, Any]]) -> int:
            return client.count(payload | dict(messages=messages), 'controller_input')['tokens']

        while True:
            try:
                envelope = self.progress.envelope(self.settings.reference, proposed, boundary=review['boundary'],
                    document_characters=settings.maximum_tool_output_characters, observations=observations)
            except ValueError as error:
                if 'evidence exceeds its limit' not in str(error):
                    raise
                fitted = None
            else:
                # Fit the same wire view the session would send: completed review
                # steps keep their visible calls and results, not their reasoning.
                messages = wire_messages(session.messages, session.policy.reasoning_retention)
                messages[1]['content'] = envelope
                try:
                    fitted = fit_native_messages(messages, count_tokens=count, token_budget=target,
                        protected_prefix=len(session.base_messages), recent_exchanges=settings.recent_review_exchanges,
                        max_field_characters=settings.maximum_tool_output_characters, archive_reference=archive)
                except ValueError as error:
                    if 'exceed the token budget' not in str(error):
                        raise
                    fitted = None
            if fitted is not None:
                break
            if observations_projected and excerpt == 0:
                if target < hard_limit:
                    # The input target is a soft working allowance. Fixed inputs
                    # may use more, provided the actual context still fits.
                    target = hard_limit
                    continue
                raise ContextCapacityExceeded('Controller fixed inputs exceed the declared context capacity')
            if observations_projected:
                excerpt //= 2
            observations_projected = True
            observations = [dict(turn=item['turn'], window=item.get('window'),
                evidence_excerpt=project_payload_content(json.dumps(item, ensure_ascii=False),
                    projection=dict(decision='summarize' if excerpt else 'drop',
                        reason='review context capacity; retrieve the full turn with history_read',
                        name='worker turn '+str(item['turn'])), max_summary_chars=max(1, excerpt)).content,
                full_evidence=dict(tool='history_read', arguments=dict(
                    start_turn=item['turn'], end_turn=item['turn'], offset=0)))
                for item in self.progress.recent_turns]
            proposed = proposed | dict(incoming=dict(retrievable_in_completed_turn=self.progress.turns))
        if fitted['projected'] or observations_projected:
            self._event('controller_context_projection', dict(turn=self.progress.turns,
                model_calls=review['model_calls'], prompt_tokens=fitted['prompt_tokens'],
                original_tokens=fitted['original_tokens'], target_tokens=target,
                observation_excerpt_characters=excerpt if observations_projected else None,
                omitted_message_ranges=fitted['omitted_message_ranges'], replaced_fields=fitted['replaced_fields'],
                review_history=retrieval, project_revision=self.progress.document_revision))
        payload['messages'] = fitted['messages']
        return payload, fitted['prompt_tokens']

    def _plain_review(self, boundary: str, final: bool) -> None:
        """One completion, no tools: reference, held guidance, focus files, recent exchanges → decision."""
        settings = self.settings.controller
        recent = []
        for item in self.progress.recent_turns[-settings.plain_recent_exchanges:]:
            calls = []
            for call, result in zip(item['response'].get('tool_calls') or [], item.get('tool_results') or []):
                arguments = call['function']['arguments']
                text = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
                outcome = result['result']
                shown = (outcome.get('stdout') or outcome.get('error') or outcome.get('content') or '')
                calls.append(dict(tool=call['function']['name'], arguments=text[:600], status=outcome.get('status'),
                                  exit_code=outcome.get('exit_code'), output=str(shown)[:600]))
            recent.append(dict(turn=item['turn'], said=(item['response'].get('content') or '')[:600], calls=calls))
        envelope = dict(reference=self.settings.reference, boundary=boundary, completed_turns=self.progress.turns,
                        held_guidance=self.progress.guidance, focus_files=self._focus_files(),
                        recent_turns=recent, proposed_completion=self.state['proposed_final'] if final else None)
        payload = dict(settings.generation, messages=[dict(role='system', content=settings.system_prompt),
                                                       dict(role='user', content=json.dumps(envelope, ensure_ascii=False))],
                       max_tokens=settings.output_tokens)
        review = self.state['review'] or dict(model_calls=0, tool_calls=0, boundary=boundary, kind='plain', plain=True,
                                              rejections=[])
        self.state['review'] = review
        self._save()
        response = self._complete(self.controller_client, payload, 'controller', settings.context_capacity,
                                  output_headroom=settings.output_headroom_tokens)
        if response is None:
            return
        review['model_calls'] += 1
        turn = parse_turn(response)
        content = (turn.message.get('content') or '').strip()
        start, end = content.find('{'), content.rfind('}')
        raw = content[start:end+1] if start >= 0 and end > start else content
        if not self.progress.document.strip():
            # A plain reviewer keeps no document; the acceptance rule still wants one to exist.
            self.progress.edit_document(self.progress.document_revision, '', 'Plain review: no project document.')
        try:
            decision = self.progress.accept(raw)
        except ValueError as error:
            review['rejections'].append(str(error)[:300])
            self._event('controller_decision_rejected', dict(turn=self.progress.turns, model_call=review['model_calls'],
                                                              error=str(error), content=content[:2000]))
            if review['model_calls'] >= 2:
                # Two malformed decisions: hold rather than stall the worker.
                decision = self.progress.accept(json.dumps(dict(correction='None', evidence='', warrant='')))
                self._event('controller_review', dict(boundary=boundary, turn=self.progress.turns, decision=decision,
                    document_revision=self.progress.document_revision, model_calls=review['model_calls'], tool_calls=0,
                    termination='malformed_decisions_held'))
            else:
                self.state.update(phase='review', pending_io=None)
                self._save()
                return
        else:
            self._event('controller_review', dict(boundary=boundary, turn=self.progress.turns, decision=decision,
                document_revision=self.progress.document_revision, model_calls=review['model_calls'], tool_calls=0,
                termination='voluntary_decision'))
        self.state.update(pending_io=None, review=None)
        if final and decision['operation'] != 'replace':
            self.state.update(phase='complete', final_text=self.state['proposed_final'])
        else:
            self.state.update(phase='worker', proposed_final=None)
        self._save()

    def _review(self) -> None:
        settings = self.settings.controller
        if settings is None or self.controller_client is None or self.settings.reference is None:
            raise ValueError('Missing controller binding for a saved review boundary')
        final = self.state['proposed_final'] is not None
        boundary = 'completion' if final else 'periodic'
        if settings.plain_review:
            self._plain_review(boundary, final)
            return
        prompt_tokens = None
        if self.state['review'] is None:
            proposed = dict(assignment=self.settings.assignment, incoming=self._incoming(),
                            focus_files=self._focus_files(),
                proposed_completion=self.state['proposed_final'], review_budget=dict(
                    maximum_model_calls=settings.maximum_model_calls,
                    maximum_tool_calls=settings.maximum_tool_calls))
            # Archive the native review while fitting a fresh view before every
            # request. A valid controller decision is its normal stopping point.
            policy = replace(self.settings.session_policy, context_capacity=settings.context_capacity,
                rollover_threshold=settings.context_capacity-1, worker_output_tokens=settings.output_tokens,
                handoff_output_tokens=settings.output_tokens, recent_result_count=0,
                output_headroom_tokens=settings.output_headroom_tokens)
            observations = None
            excerpt = settings.maximum_tool_output_characters
            while True:
                try:
                    envelope = self.progress.envelope(self.settings.reference, proposed, boundary=boundary,
                        document_characters=settings.maximum_tool_output_characters, observations=observations)
                except ValueError as error:
                    if 'evidence exceeds its limit' not in str(error):
                        raise
                    prompt_tokens = None
                else:
                    session = PersistentSession([dict(role='system', content=settings.system_prompt),
                        dict(role='user', content=envelope)], controller_tools(), settings.generation, policy)
                    prompt_tokens = self._client(self.controller_client).count(session.payload(), 'controller_input')['tokens']
                    if prompt_tokens+max(settings.output_tokens, settings.output_headroom_tokens) <= settings.context_capacity:
                        break
                if observations is not None and excerpt == 0:
                    raise ContextCapacityExceeded('Controller fixed inputs exceed the declared context capacity')
                if observations is not None:
                    excerpt //= 2
                observations = [dict(turn=item['turn'], window=item.get('window'),
                    evidence_excerpt=project_payload_content(json.dumps(item, ensure_ascii=False),
                        projection=dict(decision='summarize' if excerpt else 'drop',
                                        reason='context capacity; retrieve the full turn with history_read',
                                        name='worker turn '+str(item['turn'])), max_summary_chars=max(1, excerpt)).content,
                    full_evidence=dict(tool='history_read', arguments=dict(
                        start_turn=item['turn'], end_turn=item['turn'], offset=0)))
                    for item in self.progress.recent_turns]
                # Incoming results are already present in those completed turns;
                # avoid carrying a second unbounded copy into the review envelope.
                proposed = proposed | dict(incoming=dict(retrievable_in_completed_turn=self.progress.turns))
            if observations is not None:
                self._event('controller_evidence_projection', dict(turn=self.progress.turns,
                    excerpt_characters=excerpt, prompt_tokens=prompt_tokens,
                    turn_ids=[item['turn'] for item in observations]))
            budgets = settings.investigation_budgets
            kind = ('completion' if boundary == 'completion' else
                    'bootstrap' if self.progress.turns <= self.settings.review_policy.bootstrap_after_turns else 'periodic')
            self.state['review'] = dict(session=session.export_state(), model_calls=0, tool_calls=0,
                pending_calls=[], workspace=self.state['workspace'], boundary=boundary, kind=kind,
                concern=None, document_edits=0,
                investigation_remaining=None if budgets is None else budgets[kind])
        review = self.state['review']
        if settings.maximum_model_calls is not None and review['model_calls'] >= settings.maximum_model_calls:
            raise ReviewLimitExceeded('Controller model-call budget exhausted before a final decision')
        session = PersistentSession.from_state(review['session'])
        payload, prompt_tokens = self._review_payload(session, review)
        response = self._complete(self.controller_client, payload, 'controller', settings.context_capacity,
                                  prompt_tokens=prompt_tokens, output_headroom=settings.output_headroom_tokens)
        if response is None:
            return
        choices = response.get('choices')
        if (isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
                and choices[0].get('finish_reason') == 'length'):
            # A completed response with an explicit length finish is a known
            # rejected result. Execute none of its potentially partial calls.
            review['model_calls'] += 1
            self._event('controller_generation_rejected', dict(turn=self.progress.turns,
                model_call=review['model_calls'], finish_reason='length', tools_executed=False))
            session.append_guidance('Your last response reached the physical generation/context boundary. '
                'No tool calls from that incomplete response were executed and no decision was accepted. '
                'Continue from the unchanged document and recorded tool outcomes. Use smaller incremental '
                'actions if needed, and choose when to finish. The following is the rejected response '
                'as evidence, not an executed action:\n'+json.dumps(response, ensure_ascii=False))
            review['session'] = session.export_state()
            self.state.update(phase='review', pending_io=None)
            self._save()
            return
        turn = parse_turn(response)
        if turn.finish_reason not in ('stop', 'tool_calls'):
            raise ValueError('Controller response did not finish cleanly')
        session.accept(response)
        review['model_calls'] += 1
        review['session'] = session.export_state()
        if session.pending_tools:
            calls = turn.message['tool_calls']
            if settings.maximum_tool_calls is not None and len(calls)+review['tool_calls'] > settings.maximum_tool_calls:
                raise ReviewLimitExceeded('Controller tool-call budget exceeded; batch was not executed')
            review['pending_calls'] = deepcopy(calls)
            self.state.update(phase='review_tools', pending_io=None)
            self._save()
            return
        try:
            decision = self.progress.accept(turn.message['content'])
        except ValueError as error:
            # A complete response with a rejected decision has a known outcome.
            # Return validation feedback inside this bounded review, preserving
            # strict acceptance and every already-committed document/tool effect.
            self._event('controller_decision_rejected', dict(turn=self.progress.turns,
                model_call=review['model_calls'], error=str(error), content=turn.message['content']))
            budget_note = ('Model calls remaining: '+str(settings.maximum_model_calls-review['model_calls'])+'.'
                           if settings.maximum_model_calls is not None else
                           'Continue investigating or correcting the document as needed; choose when to finish.')
            session.append_guidance('Your final decision was rejected: '+str(error)+'. '
                'The worker remains paused. Correct the problem using the available tools if needed. '
                'If the project document is empty, create it with project_edit before finishing. '
                'Return a raw JSON object with exactly correction, evidence and warrant; no Markdown fences. '
                +budget_note)
            review['session'] = session.export_state()
            self.state.update(phase='review', pending_io=None)
            self._save()
            return
        self._event('controller_review', dict(boundary=boundary, turn=self.progress.turns, decision=decision,
            document_revision=self.progress.document_revision, model_calls=review['model_calls'], tool_calls=review['tool_calls'],
            termination='voluntary_decision'))
        self.state.update(pending_io=None, review=None)
        if final and decision['operation'] != 'replace':
            self.state.update(phase='complete', final_text=self.state['proposed_final'])
        else:
            self.state.update(phase='worker', proposed_final=None)
        self._save()

    def discard_pending(self) -> dict[str, Any] | None:
        """Drop an operation whose outcome was never committed, so a killed session can continue.

        A pending model call applied nothing, so the next step simply asks again. A pending
        shell command may or may not have run, but the saved workspace is the one from
        before it, so discarding it loses that command's effects rather than doubling them.
        Either way the decision is explicit and journaled; nothing is replayed.
        """
        with self._locked():
            if self.store.read()['revision'] != self.revision:
                raise RuntimeError('Session changed; reopen before discarding')
            pending = self.state.get('pending_io')
            if pending is None:
                return None
            self._event('pending_discarded', dict(pending))
            self.state['pending_io'] = None
            if pending.get('kind') == 'tool':
                # The turn's response is kept; the call gets an explicit failed result.
                turn = self.state.get('active_turn')
                if turn is not None:
                    call = turn['response']['tool_calls'][len(turn['tool_results'])]
                    output = dict(status='error', error='This command was interrupted before its outcome was recorded; '
                                                          'its effects were discarded. Run it again if still needed.')
                    self.session.append_tool_result(call['id'], call['function']['name'], json.dumps(output, ensure_ascii=False))
                    turn['tool_results'].append(dict(call_id=call['id'], name=call['function']['name'], result=output))
                    if not self.session.pending_tools:
                        self._finish_turn()
            self._save()
            return pending

    def reset_generation_block(self) -> dict[str, Any] | None:
        """Lift a generation-retries block on reopen: the block is a verdict on one window's context, not on
        the task. The retry budget resets and a handoff is requested so the retry runs in a fresh window."""
        with self._locked():
            if self.store.read()['revision'] != self.revision:
                raise RuntimeError('Session changed; reopen before resetting')
            if self.state.get('blocked_kind') != 'generation_retries':
                return None
            record = dict(reason=self.state.get('blocked_reason'), rejections=dict(self.state.get('generation_rejections') or {}))
            self.state.update(blocked_kind=None, blocked_reason=None, generation_rejections={})
            if len(self.session.messages) > len(self.session.base_messages)+1 and not self.state.get('rollover_requested'):
                self.state['rollover_requested'] = dict(reason='generation_block_reset', name='worker', count=0)
            self._event('generation_block_reset', record)
            self._save()
            return record

    def prune_workspaces(self) -> int:
        """Delete snapshot stores the saved state no longer references; returns how many.

        Every applied command stores a full content-addressed snapshot. Only the current
        one is needed to continue; the rest are history a session never reads back.
        """
        with self._locked():
            if self.store.read()['revision'] != self.revision:
                raise RuntimeError('Session changed; reopen before pruning')
            self._flush()
            keep = {self.state['workspace']}
            removed = 0
            for path in (self.root/'workspaces').glob('*.sqlite3'):
                if path.stem not in keep:
                    path.unlink()
                    removed += 1
            return removed

    def continue_with(self, message: str) -> dict[str, Any]:
        """Resume a completed session on new user text; the accepted final answer is withdrawn.

        The host judged the outcome outside the model (a gate, a check) and states what
        is still missing. Nothing is replayed: the text lands as the next user message
        after the worker's final answer, and the cursor makes it the new incoming input.
        """
        if not isinstance(message, str) or not message.strip():
            raise ValueError('Continuation requires nonempty text')
        with self._locked():
            if self.store.read()['revision'] != self.revision:
                raise RuntimeError('Session changed; reopen before continuing')
            if self.state['phase'] != 'complete':
                raise ValueError('Only a completed session can be continued')
            self.session.append_guidance(message, standing=True)   # the current objective; survives a handoff
            self.state.update(phase='worker', proposed_final=None, final_text='')
            self._event('continued', dict(window=self.session.window_index, characters=len(message)))
            self._save()
            return self.status()

    def interject(self, message: str) -> dict[str, Any]:
        """Hand the worker host text at a paused turn boundary, without withdrawing anything.

        A session paused by `run(maximum_worker_turns=...)` sits in the worker phase with no
        tool call open; the host may speak there (an effort check, a note) and the text lands
        as the next user message. Refused mid-call, mid-review or after completion, where
        `continue_with` is the right door.
        """
        if not isinstance(message, str) or not message.strip():
            raise ValueError('Interjection requires nonempty text')
        with self._locked():
            if self.store.read()['revision'] != self.revision:
                raise RuntimeError('Session changed; reopen before interjecting')
            if self.state['phase'] != 'worker' or self.state['pending_io'] is not None:
                raise ValueError('Only a paused worker session can take an interjection')
            self.session.append_guidance(message)
            self._event('interjected', dict(window=self.session.window_index, characters=len(message)))
            self._save()
            return self.status()

    RETUNABLE = ('reasoning_retention', 'rollover_threshold', 'output_headroom_tokens', 'context_capacity',
                 'recent_result_count', 'recent_result_characters')

    def retune(self, **changes: Any) -> dict[str, Any]:
        """Change wire-view policy on a saved session without replaying anything.

        Only fields that shape how the next request is rendered or bounded may change
        (`RETUNABLE`); the assignment, tools and model bindings stay what they were. The
        change is checkpointed like any other state, so a reopen sees it.
        """
        unknown = sorted(set(changes)-set(self.RETUNABLE))
        if unknown:
            raise ValueError('Not retunable: '+', '.join(unknown))
        with self._locked():
            if self.store.read()['revision'] != self.revision:
                raise RuntimeError('Session changed; reopen before retuning')
            policy = replace(self.settings.session_policy, **changes)  # __post_init__ validates the limits
            self.settings = replace(self.settings, session_policy=policy)
            self.session.policy = policy
            self.state['settings'] = asdict(self.settings)
            self._event('retuned', dict(changes))
            self._save()
            return self.status()

    def project_document(self) -> str:
        """Return the controller-owned full-project document."""
        return self.progress.document

    def _page(self, value: str, offset: int) -> dict[str, Any]:
        if type(offset) is not int or not 0 <= offset <= len(value):
            raise ValueError('Invalid evidence character offset')
        end = min(len(value), offset+self.settings.controller.maximum_tool_output_characters)
        return dict(text=value[offset:end], offset=offset, end_offset=end, total_characters=len(value),
                    next_offset=end if end < len(value) else None)

    def _review_tool(self) -> None:
        review = self.state['review']
        if review is None or review['workspace'] != self.state['workspace']:
            raise ValueError('The review workspace changed unexpectedly')
        session = PersistentSession.from_state(review['session'])
        call = review['pending_calls'][0]
        name = call['function']['name']
        schemas = {item['function']['name']:item['function']['parameters'] for item in controller_tools()}
        args = {}
        try:
            raw = call['function']['arguments']
            args = json.loads(raw) if isinstance(raw, str) else raw
            schema = schemas.get(name)
            if schema is None or not isinstance(args, dict) or set(args) != set(schema['required']):
                raise ValueError('Unknown controller tool or invalid arguments')
            for key, specification in schema['properties'].items():
                value = args[key]
                if specification['type'] == 'string' and not isinstance(value, str):
                    raise ValueError(key+' must be a string')
                if specification['type'] == 'integer' and (
                        type(value) is not int or value < specification.get('minimum', 0)):
                    raise ValueError(key+' must be a valid integer')
            limits = self.shell.config.limits
            investigative = name in ('workspace_list', 'workspace_read', 'history_read', 'run_check')
            if investigative and self.settings.controller.investigation_budgets is not None:
                if review.get('concern') is None:
                    raise ValueError('Investigative tools are locked until you state a concrete concern with investigate. '
                                     'If the traces and project document already show an aligned trajectory, decide now.')
                remaining = review.get('investigation_remaining')
                if remaining is not None and remaining <= 0:
                    raise ValueError('The investigation budget for this review is exhausted. Decide with the evidence '
                                     'you have; an unresolved concern is not a reference mismatch.')
            if name in ('project_edit', 'project_edit_range'):
                cap = self.settings.controller.maximum_document_edits_per_review
                if cap is not None and review.get('document_edits', 0) >= cap:
                    raise ValueError(f'At most {cap} document edit(s) per review: consolidate your changes. '
                                     'The document is memory for later reviews, not a log of this one.')
            if name == 'investigate':
                if not args['concern'].strip():
                    raise ValueError('A concern must be a nonempty statement')
                if review.get('concern') is not None:
                    raise ValueError('One concern per review; resolve it or decide')
                review['concern'] = args['concern'].strip()
                self._event('controller_concern', dict(turn=self.progress.turns, kind=review.get('kind'),
                                                       concern=review['concern'], budget=review.get('investigation_remaining')))
                output = dict(status='ok', concern=review['concern'], remaining_tool_calls=review.get('investigation_remaining'),
                              note=('No investigative calls are available at this boundary; decide from the traces.'
                                    if review.get('investigation_remaining') == 0 else
                                    'Use the fewest calls that settle this concern, then decide.'))
            elif name == 'project_read':
                output = self.progress.read_document(args['offset'], self.settings.controller.maximum_tool_output_characters)
            elif name == 'project_edit':
                output = self.progress.edit_document(**args)
                self._event('project_edit', dict(turn=self.progress.turns, **args, **output))
                output['project_document'] = self.progress.read_document(output['start_offset'],
                    self.settings.controller.maximum_tool_output_characters)
            elif name == 'project_edit_range':
                output = self.progress.edit_range(**args)
                self._event('project_edit', dict(turn=self.progress.turns, method='range', **(args | output)))
                output['project_document'] = self.progress.read_document(output['start_offset'],
                    self.settings.controller.maximum_tool_output_characters)
            elif name == 'workspace_list':
                names = workspace_files(self.workspace(), byte_limit=limits.workspace_bytes, file_limit=limits.max_files)
                output = self._page(json.dumps([path for path in names if path.startswith(args['prefix'])]), args['offset'])
            elif name == 'workspace_read':
                data = read_workspace_file(self.workspace(), args['path'], byte_limit=limits.workspace_bytes, file_limit=limits.max_files)
                output = self._page(data.decode('utf-8', errors='replace'), args['offset'])
            elif name == 'history_read':
                if not args['start_turn'] <= args['end_turn'] <= self.progress.turns:
                    raise ValueError('History range must contain completed worker turns')
                observations = [event.payload for event in self.journal.read_strict('session')
                    if event.event_type == 'worker_turn' and args['start_turn'] <= event.payload['turn'] <= args['end_turn']]
                if len(observations) != args['end_turn']-args['start_turn']+1:
                    raise ValueError('Requested historical evidence is incomplete')
                output = self._page(json.dumps(observations, ensure_ascii=False), args['offset'])
            elif name == 'review_history_read':
                if not args['start_message'] <= args['end_message'] < len(session.messages):
                    raise ValueError('Review history range must name existing message indices')
                originals = [dict(message_index=index, message=session.messages[index])
                    for index in range(args['start_message'], args['end_message']+1)]
                output = self._page(json.dumps(originals, ensure_ascii=False), args['offset'])
                output.update(start_message=args['start_message'], end_message=args['end_message'],
                              total_messages=len(session.messages))
            else:
                if not args['command'].strip():
                    raise ValueError('A nonempty check command is required')
                self.state['pending_io'] = dict(kind='controller_check', call_id=call['id'], command=args['command'])
                self._save()
                # A check must not change the worker's state: in bind mode it runs detached on a tmpfs copy.
                result = (self.shell.run(args['command'], self.workspace(), detached=True) if self._directory() is not None
                          else self.shell.run(args['command'], self.workspace()))
                record = {key:value for key,value in asdict(result).items() if key != 'workspace'}
                record.update(workspace_changes_discarded=True, source_workspace=review['workspace'])
                self._event('controller_check', dict(call_id=call['id'], **record))
                if result.status == 'interrupted':
                    raise UnresolvedOperation('Controller check outcome is uncertain; inspect its saved event')
                # Deliberately never put the returned workspace into the worker state.
                output = record
        except (ValueError, TypeError, KeyError, FileNotFoundError) as error:
            # A malformed inspection/edit is recoverable feedback for this mini-loop.
            # Never convert an uncertain external operation into a committed failure.
            if self.state['pending_io'] is not None:
                raise
            output = dict(status='error', error=str(error))
            if name in ('project_edit', 'project_edit_range'):
                offset = args.get('start_offset', 0) if isinstance(args, dict) else 0
                offset = min(max(0, offset), len(self.progress.document)) if type(offset) is int else 0
                output['project_document'] = self.progress.read_document(offset,
                    self.settings.controller.maximum_tool_output_characters)
        rendered = json.dumps(output, ensure_ascii=False)
        cap = self.settings.controller.maximum_tool_output_characters
        if name == 'run_check' and len(rendered) > cap:
            rendered = json.dumps(dict(truncated=True, total_characters=len(rendered), text=rendered[:cap],
                workspace_changes_discarded=True, full_result=dict(tool='review_history_read', arguments=dict(
                    start_message=len(session.messages), end_message=len(session.messages), offset=0))), ensure_ascii=False)
        session.append_tool_result(call['id'], name, rendered)
        self._event('controller_tool', dict(call_id=call['id'], name=name, arguments=call['function']['arguments'], result=output))
        if output.get('status') != 'error':
            if name in ('workspace_list', 'workspace_read', 'history_read', 'run_check') and review.get('investigation_remaining') is not None:
                review['investigation_remaining'] -= 1
            if name in ('project_edit', 'project_edit_range'):
                review['document_edits'] = review.get('document_edits', 0)+1
        review['session'] = session.export_state()
        review['pending_calls'].pop(0)
        review['tool_calls'] += 1
        self.state.update(pending_io=None, phase='review_tools' if session.pending_tools else 'review')
        self._save()

    def step(self) -> dict[str, Any]:
        """Perform one boundary and persist it; never replay an uncertain operation."""
        status = self._step()
        with self._locked():
            self._flush()
        return status

    def _step(self) -> dict[str, Any]:
        """One boundary, committed only where recovery needs it (see `_save`); `run` drives this and
        flushes where it leaves the session."""
        with self._locked():
            if self.store.read()['revision'] != self.revision:
                raise RuntimeError('Session changed; reopen before continuing')
            if self.state['pending_io'] is not None:
                raise UnresolvedOperation('A previous operation lacks a committed outcome: '+json.dumps(self.state['pending_io']))
            if self.state.get('blocked_reason'):
                if self.state.get('blocked_kind') == 'generation_retries':
                    raise GenerationRetryExceeded(self.state['blocked_reason'])
                raise ContextCapacityExceeded(self.state['blocked_reason'])
            phase = self.state['phase']
            try:
                if phase == 'worker':
                    self._worker()
                elif phase == 'tools':
                    self._tool()
                elif phase == 'handoff':
                    self._handoff()
                elif phase == 'review':
                    self._review()
                elif phase == 'review_tools':
                    self._review_tool()
                elif phase != 'complete':
                    raise ValueError('Unknown saved session phase')
            except ContextCapacityExceeded as error:
                self._restore()
                self.state['blocked_reason'] = str(error)
                self._event('context_blocked', dict(phase=self.state['phase'], reason=str(error)))
                self._save()
                raise
            except BaseException:
                # Discard partial in-memory mutations as well as on process restart.
                self._restore()
                raise
            if self.state['phase'] == 'complete' or self.state.get('blocked_reason'):
                self._flush()
            return self.status()

    def run(self, *, maximum_worker_turns: int | None = None, stop_when: Any = None) -> dict[str, Any]:
        """Run until completion, or pause at a resolved boundary after a requested burst.

        `stop_when` is asked between steps (a kill switch the operator flips, a file, a signal): when it answers
        true the session pauses at the boundary it is on and reports status 'stopped'; nothing mid-flight is lost
        and the session reopens where it paused."""
        if maximum_worker_turns is not None and (type(maximum_worker_turns) is not int or maximum_worker_turns <= 0):
            raise ValueError('A requested burst must contain a positive number of worker turns')
        initial = self.progress.turns
        while self.state['phase'] != 'complete':
            if (maximum_worker_turns is not None and self.progress.turns-initial >= maximum_worker_turns
                    and self.state['phase'] == 'worker'):
                with self._locked():
                    self._flush()
                return self.status() | dict(status='paused')
            if stop_when is not None and self.state['phase'] == 'worker' and self.state.get('pending_io') is None and stop_when():
                with self._locked():
                    self._event('stopped', dict(turns=self.progress.turns))
                    self._flush()
                return self.status() | dict(status='stopped')
            self._step()
        with self._locked():
            self._flush()
        return self.status()

    def status(self) -> dict[str, Any]:
        review = self.state['review']
        exhausted = (review is not None and self.state['phase'] == 'review'
                     and self.settings.controller.maximum_model_calls is not None
                     and review['model_calls'] >= self.settings.controller.maximum_model_calls)
        status = 'blocked' if self.state['pending_io'] is not None or exhausted or self.state.get('blocked_reason') else (
            'complete' if self.state['phase'] == 'complete' else 'ready')
        return dict(status=status, phase=self.state['phase'],
            completed_worker_turns=self.progress.turns, controller_reviews=self.progress.review_count,
            window_index=self.session.window_index, handoffs=self.state['handoffs'],
            controller_enabled=self.settings.reference is not None,
            project_revision=self.progress.document_revision, project_characters=len(self.progress.document),
            active_review=dict(model_calls=review['model_calls'], tool_calls=review['tool_calls']) if review else None,
            held_guidance=deepcopy(self.progress.guidance), pending_io=deepcopy(self.state['pending_io']),
            final_text=self.state['final_text'], blocked_reason=self.state.get('blocked_reason'),
            generation_rejections=deepcopy(self.state.get('generation_rejections', {})))
