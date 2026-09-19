"""Quota-bounded Linux shell execution with an opaque persistent workspace."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import json
from pathlib import Path, PurePosixPath
import re
import selectors
import subprocess
import tarfile
import tempfile
import time
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ShellLimits:
    """Hard per-command resource limits and workspace serialization limits."""

    memory_bytes: int
    workspace_bytes: int
    temporary_bytes: int
    output_bytes: int
    visible_output_bytes: int
    processes: int
    cpu_percent: int
    command_seconds: float
    shutdown_seconds: float
    max_files: int

    def __post_init__(self) -> None:
        if any(value <= 0 for value in self.__dict__.values()):
            raise ValueError('All limits must be positive')
        if self.visible_output_bytes > self.output_bytes:
            raise ValueError('Visible output cannot exceed captured output')


@dataclass(frozen=True)
class ServiceLimits:
    """Bounds on processes a command starts that outlive it (a dev server, a browser, a watcher).

    A service runs in its own sandbox with the same isolation as a command, the host network namespace
    (commands reach it on localhost), a mirror of the workspace as of the last command at /work, and a
    directory of its own at /svc/<name> for its log. It dies with the shell that started it, or at
    lifetime_seconds. wait_seconds bounds one `svc wait`: the host blocks, the model spends one turn."""

    memory_bytes: int
    processes: int
    lifetime_seconds: float
    maximum_services: int = 4
    wait_seconds: float = 1800

    def __post_init__(self) -> None:
        if any(value <= 0 for value in self.__dict__.values()):
            raise ValueError('All service limits must be positive')


# Kernel-level hardening applied to every command and service scope, enforced by systemd's user manager
# (seccomp, so no root needed). Debugging/ptrace, kernel modules, raw I/O, reboot, swap and io_uring are
# what escape attempts reach for; a shell, a package install and a headless browser never need them.
# @mount stays allowed because bwrap builds the namespace inside the scope; inside it, mounts are already
# impossible (all capabilities dropped, no nested user namespaces). Raw and packet sockets are refused;
# memory W^X is not enforced because JITs (V8) need it.
HARDENING_PROPERTIES = (
    '--property=NoNewPrivileges=yes',
    '--property=SystemCallFilter=~@debug @module @raw-io @reboot @swap @obsolete @cpu-emulation '
    'io_uring_setup io_uring_enter io_uring_register',
    '--property=SystemCallErrorNumber=EPERM',
    '--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK',
    '--property=RestrictRealtime=yes',
    '--property=RestrictSUIDSGID=yes',
    '--property=LockPersonality=yes',
)


@dataclass(frozen=True)
class ShellConfig:
    """Host paths are explicit; virtual paths belong to the sandbox protocol."""

    bwrap: str
    systemd_run: str
    systemctl: str
    runtime_root: str
    scratch_root: str
    limits: ShellLimits
    shell_name: str = 'bash'
    python_name: str = 'python3'
    # Host trees mounted read-only at their own path, for a toolchain that lives
    # outside the runtime root. Environment entries are added after the fixed
    # sandbox variables; a PATH entry is prepended to the sandbox PATH.
    read_only_binds: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    # Keep the host network namespace (package installs, registries). The name
    # resolution and trust files the runtime root lacks are bound read-only from
    # `network_files`; entries that do not exist on the host are skipped.
    share_network: bool = False
    # Directory names never persisted in the snapshot: rebuildable, large, and
    # environment-bound (a venv's symlinks point outside the workspace anyway).
    snapshot_ignore: tuple[str, ...] = ('.venv', '__pycache__', '.pytest_cache', 'node_modules', '.mypy_cache')
    network_files: tuple[str, ...] = ('/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/ssl', '/etc/pki',
                                      '/etc/ca-certificates', '/etc/crypto-policies')
    # None: nothing outlives a command. Set: the `svc` command is on PATH in every command and services
    # share the host network namespace with commands, so share_network is required.
    services: ServiceLimits | None = None
    # Host paths a command may not name: a toolchain that must be bound to run (an editable install's
    # source) but is not the worker's to read. A command whose text contains one is refused unexecuted
    # with the reason; the tools still run because the bind stays. Prefixes, matched as substrings.
    refused_paths: tuple[str, ...] = ()
    # (regex, reason) pairs a command's text is matched against; a match is refused unexecuted with the reason.
    # For the few verbs that are policy today and must be structure: a gate override, a publish, a write to a
    # record only a tool may write. Regexes are compiled once at construction.
    refused_patterns: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if any(not Path(value).is_absolute() for value in
               (self.bwrap, self.systemd_run, self.systemctl, self.runtime_root, self.scratch_root)):
            raise ValueError('Host paths must be absolute')
        if self.services is not None and not self.share_network:
            raise ValueError('Services need share_network: a command reaches a service on localhost')
        if any('/' in value or not value for value in (self.shell_name, self.python_name)):
            raise ValueError('Runtime binary names must be basenames')
        object.__setattr__(self, 'read_only_binds', tuple(self.read_only_binds))
        reserved = {'/', '/usr', '/bin', '/lib', '/lib64', '/work', '/tmp', '/proc', '/dev', '/input', '/runner', '/svc'}
        for bind in self.read_only_binds:
            if not Path(bind).is_absolute() or Path(bind).as_posix() in reserved:
                raise ValueError('Read-only binds must be absolute host paths outside the sandbox layout')
        for name, value in self.environment.items():
            if not name or '=' in name or not isinstance(value, str):
                raise ValueError('Environment entries must be NAME -> string value')
        object.__setattr__(self, 'network_files', tuple(self.network_files))
        object.__setattr__(self, 'refused_paths', tuple(self.refused_paths))
        if any(not Path(entry).is_absolute() for entry in self.refused_paths):
            raise ValueError('refused_paths must be absolute host paths')
        patterns = tuple((str(p), str(r)) for p, r in self.refused_patterns)
        for pattern, reason in patterns:
            if not pattern or not reason:
                raise ValueError('refused_patterns entries are (regex, reason) pairs')
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError('refused_patterns: bad regex '+repr(pattern)+': '+str(error)) from error
        object.__setattr__(self, 'refused_patterns', patterns)
        object.__setattr__(self, 'snapshot_ignore', tuple(self.snapshot_ignore))
        if any('/' in name or not name for name in self.snapshot_ignore):
            raise ValueError('snapshot_ignore entries are directory basenames')
        if any(not Path(entry).is_absolute() for entry in self.network_files):
            raise ValueError('network_files must be absolute host paths')


@dataclass(frozen=True)
class ShellResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool
    workspace: bytes
    output_files: tuple[str, ...]
    elapsed_seconds: float
    detail: str = ''


WORKSPACE_MOUNT = '/work'
SERVICES_MOUNT = '/svc'
REQUESTS_DIR = '.svc/requests'   # inside the workspace: what `svc start/stop/wait` leaves for the host


def _name(value: str, *, user: bool = False) -> str:
    """Canonical member name; `user` marks a path the model supplied, which gets the stray-tree refusal."""
    path = PurePosixPath(value)
    if path.is_absolute():
        # The workspace is mounted at /work in the sandbox; a path spelled from there is the same file.
        if path == PurePosixPath(WORKSPACE_MOUNT) or PurePosixPath(WORKSPACE_MOUNT) in path.parents:
            path = path.relative_to(WORKSPACE_MOUNT)
        else:
            raise ValueError('Workspace paths are relative to the workspace (or start with '+WORKSPACE_MOUNT+'/); '
                             +str(value)+' is outside it')
    if '..' in path.parts or not path.parts:
        raise ValueError('Workspace paths must be relative without parent traversal')
    if user and path.parts[0] == '.work':
        # A model that was refused `/work/x` next tries `.work/x`; accepting it creates a stray copy of the tree.
        raise ValueError('`.work/` is not the workspace: write `'+str(PurePosixPath(*path.parts[1:]))+'` (relative) or `'
                         +WORKSPACE_MOUNT+'/'+str(PurePosixPath(*path.parts[1:]))+'`')
    return str(path)


def _members(snapshot: bytes, byte_limit: int, file_limit: int) -> list[tuple[tarfile.TarInfo, bytes]]:
    if not snapshot:
        return []
    # Tar headers are bounded separately from file content.
    if len(snapshot) > byte_limit + file_limit * 2048 + 10240:
        raise ValueError('Workspace archive exceeds its limit')
    result: list[tuple[tarfile.TarInfo, bytes]] = []
    names: set[str] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(snapshot), mode='r:') as archive:
        for member in archive:
            name = _name(member.name)
            if name in names or len(names) >= file_limit:
                raise ValueError('Duplicate path or too many workspace entries')
            names.add(name)
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError('Only regular files, directories and relative symlinks persist')
            if member.issym():
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or '..' in target.parts:
                    raise ValueError('Workspace symlinks must remain relative without parent traversal')
            total += member.size
            if total > byte_limit:
                raise ValueError('Workspace file content exceeds its limit')
            if member.isfile():
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError('Missing archive member content')
                data = handle.read()
                if len(data) != member.size:
                    raise ValueError('Incomplete archive member')
            else:
                data = b''
            member.name = name
            result.append((member, data))
    return result


def workspace_files(snapshot: bytes, *, byte_limit: int, file_limit: int) -> tuple[str, ...]:
    """List persisted regular files without extracting the archive on the host."""
    return tuple(member.name for member, _ in _members(snapshot, byte_limit, file_limit) if member.isfile())


def read_workspace_file(snapshot: bytes, name: str, *, byte_limit: int, file_limit: int) -> bytes:
    """Read a regular member; never follow a workspace symlink on the host."""
    canonical = _name(name, user=True)
    for member, data in _members(snapshot, byte_limit, file_limit):
        if member.name == canonical and member.isfile():
            return data
    raise FileNotFoundError(canonical)


def write_workspace_file(snapshot: bytes, name: str, data: bytes, *, byte_limit: int, file_limit: int) -> bytes:
    """Replace a regular member, preserving all other safe workspace members."""
    canonical = _name(name, user=True)
    records = _members(snapshot, byte_limit, file_limit)
    parents = {str(path) for path in PurePosixPath(canonical).parents if str(path) != '.'}
    for member, _ in records:
        if member.name in parents and not member.isdir():
            raise ValueError('A workspace parent is not a directory')
        if member.name == canonical and not member.isfile():
            raise ValueError('Destination is not a regular file')
    sink = io.BytesIO()
    with tarfile.open(fileobj=sink, mode='w:') as archive:
        for member, value in records:
            if member.name != canonical:
                archive.addfile(member, io.BytesIO(value) if member.isfile() else None)
        entry = tarfile.TarInfo(canonical)
        entry.size = len(data)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(data))
    output = sink.getvalue()
    _members(output, byte_limit, file_limit)
    return output


class WorkspaceEditError(ValueError):
    """A snapshot edit that was refused; the snapshot is unchanged."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def edit_workspace_file(snapshot: bytes, name: str, old_text: str, new_text: str, *,
                        expected_occurrences: int = 1, byte_limit: int, file_limit: int) -> tuple[bytes, dict[str, Any]]:
    """Replace exact text in a UTF-8 member; refuse ambiguity instead of guessing.

    Returns the new snapshot and a report. The match must occur exactly
    expected_occurrences times; zero or a different count is an explicit
    error and the snapshot is left unchanged. No fuzzy matching is applied.
    """
    if not isinstance(old_text, str) or not old_text:
        raise WorkspaceEditError('old_text_empty', 'old_text must be a nonempty string')
    if not isinstance(new_text, str):
        raise WorkspaceEditError('new_text_invalid', 'new_text must be a string')
    if type(expected_occurrences) is not int or expected_occurrences < 1:
        raise WorkspaceEditError('occurrences_invalid', 'expected_occurrences must be a positive integer')
    try:
        data = read_workspace_file(snapshot, name, byte_limit=byte_limit, file_limit=file_limit)
    except FileNotFoundError:
        raise WorkspaceEditError('not_a_file', f'Not a workspace file: {name}') from None
    try:
        original = data.decode('utf-8')
    except UnicodeDecodeError:
        raise WorkspaceEditError('not_text', f'Not UTF-8 text: {name}') from None
    matched = 'exact'
    count = original.count(old_text)
    if count == 0:
        # Two encodings of the same anchor, tried in order: the model's own JSON escapes
        # left in the text (\\n, \\", \\t), then leading/trailing whitespace per line. The
        # anchor still has to be the text the model read; only its rendering is forgiven,
        # and the replacement is applied to the exact span that was found.
        for label, candidate in (('unescaped', _unescape_literal(old_text)),
                                 ('whitespace', old_text), ('unescaped_whitespace', _unescape_literal(old_text))):
            if label == 'unescaped' and candidate != old_text and original.count(candidate):
                old_text, count, matched = candidate, original.count(candidate), label
                if label != 'unescaped_whitespace':
                    new_text = _unescape_literal(new_text) if new_text != _unescape_literal(new_text) else new_text
                break
            if label in ('whitespace', 'unescaped_whitespace'):
                spans = _whitespace_spans(original, candidate)
                if spans:
                    if label == 'unescaped_whitespace':
                        new_text = _unescape_literal(new_text)
                    count, matched = len(spans), label
                    found = original[spans[0][0]:spans[0][1]]
                    # The anchor lost its indentation on the way in; give the replacement the
                    # indentation the matched span actually has.
                    have = len(found) - len(found.lstrip(' \t'))
                    given = len(candidate.strip('\n')) - len(candidate.strip('\n').lstrip(' \t'))
                    if have > given:
                        pad = found[:have-given] if given == 0 else found[:have][given:]
                        new_text = '\n'.join((pad+line if line.strip() else line) for line in new_text.split('\n'))
                    old_text = found
                    break
    if count == 0:
        raise WorkspaceEditError('text_not_found', f'old_text not found in {name}')
    if count != expected_occurrences:
        raise WorkspaceEditError('occurrence_mismatch',
                                 f'Expected {expected_occurrences} occurrence(s) of old_text in {name} but found {count}')
    updated = original.replace(old_text, new_text).encode('utf-8')
    output = write_workspace_file(snapshot, name, updated, byte_limit=byte_limit, file_limit=file_limit)
    return output, dict(path=_name(name), replacements=count, previous_bytes=len(data), bytes_written=len(updated),
                        matched=matched)


def _unescape_literal(text: str) -> str:
    """Undo one layer of JSON-style escaping a model left inside the string itself."""
    if not any(seq in text for seq in ('\\n', '\\"', '\\t')):
        return text
    return text.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')


def _whitespace_spans(original: str, anchor: str) -> list[tuple[int, int]]:
    """Spans of `original` whose lines equal the anchor's lines once each line is stripped."""
    wanted = [line.strip() for line in anchor.strip('\n').splitlines()]
    if not wanted or not any(wanted):
        return []
    lines = original.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1]+len(line))
    spans = []
    for index in range(len(lines)-len(wanted)+1):
        window = lines[index:index+len(wanted)]
        if [line.strip() for line in window] == wanted:
            end = starts[index+len(wanted)]
            if window[-1].endswith('\n') and not anchor.endswith('\n'):
                end -= 1
            spans.append((starts[index], end))
    return spans


def read_workspace_lines(snapshot: bytes, name: str, *, offset: int = 1, limit: int,
                         byte_limit: int, file_limit: int) -> dict[str, Any]:
    """Return numbered UTF-8 lines of a member from a 1-based offset, at most limit lines.

    The result reports the total line count and whether more lines follow, so a
    caller can continue with another offset instead of reading the whole file.
    """
    if type(offset) is not int or offset < 1 or type(limit) is not int or limit < 1:
        raise WorkspaceEditError('range_invalid', 'offset and limit must be positive integers')
    try:
        data = read_workspace_file(snapshot, name, byte_limit=byte_limit, file_limit=file_limit)
    except FileNotFoundError:
        raise WorkspaceEditError('not_a_file', f'Not a workspace file: {name}') from None
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        raise WorkspaceEditError('not_text', f'Not UTF-8 text: {name}') from None
    selected = lines[offset-1:offset-1+limit]
    text = ''.join(f'{offset+index:6}\t{line}\n' for index, line in enumerate(selected))
    return dict(path=_name(name), offset=offset, lines_returned=len(selected), total_lines=len(lines),
                truncated=offset-1+len(selected) < len(lines), content=text)


class SandboxedShell:
    """Execute explicitly, using tmpfs quotas instead of a writable host bind."""

    def __init__(self, config: ShellConfig) -> None:
        self.config = config
        # name -> dict(unit, command, started_at); the host side of what `svc` shows the worker
        self._services: dict[str, dict[str, Any]] = {}

    @property
    def services_root(self) -> Path:
        return Path(self.config.scratch_root)/'services'

    def _isolation_argv(self) -> list[str]:
        """The bwrap namespace, mounts and environment a command and a service share."""
        config, limits = self.config, self.config.limits
        helper = str(Path(__file__).resolve().parent)
        return [
            config.bwrap,
            *(('--unshare-user', '--unshare-ipc', '--unshare-pid', '--unshare-uts', '--unshare-cgroup')
              if config.share_network else ('--unshare-all', '--unshare-user')),
            '--disable-userns', '--die-with-parent', '--new-session',
            '--as-pid-1', '--cap-drop', 'ALL', '--clearenv',
            '--ro-bind', config.runtime_root, '/usr',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--ro-bind', helper, '/runner',
            *[part for bind in config.read_only_binds for part in ('--ro-bind', bind, bind)],
            *[part for entry in (config.network_files if config.share_network else ())
              if Path(entry).exists() for part in ('--ro-bind', str(Path(entry).resolve()), entry)],
            '--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev', '--remount-ro', '/dev',
            '--size', str(limits.temporary_bytes), '--tmpfs', '/tmp',
            '--setenv', 'PATH', ':'.join(filter(None, (config.environment.get('PATH'), '/usr/bin',
                                                        '/runner' if config.services else None))),
            '--setenv', 'HOME', '/work', '--setenv', 'TMPDIR', '/tmp',
            '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'OPENBLAS_NUM_THREADS', '1',
            '--setenv', 'OMP_NUM_THREADS', '1',
            *(('--setenv', 'SVC_ROOT', SERVICES_MOUNT) if config.services else ()),
            *[part for name, value in sorted(config.environment.items()) if name != 'PATH'
              for part in ('--setenv', name, value)],
        ]

    def command_argv(self, input_dir: str, unit: str) -> list[str]:
        """Build the complete resource-control and isolation command for auditing."""
        config, limits = self.config, self.config.limits
        if not Path(input_dir).is_absolute() or not unit.replace('-', '').isalnum():
            raise ValueError('Invalid input directory or unit name')
        services = self.services_root
        if config.services:
            services.mkdir(parents=True, exist_ok=True)
        return [
            config.systemd_run, '--user', '--quiet', '--wait', '--pipe', '--collect', '--unit='+unit,
            '--property=MemoryMax='+str(limits.memory_bytes), '--property=MemorySwapMax=0',
            '--property=TasksMax='+str(limits.processes), '--property=CPUQuota='+str(limits.cpu_percent)+'%',
            '--property=RuntimeMaxSec='+str(limits.command_seconds+limits.shutdown_seconds),
            '--property=TimeoutStopSec='+str(limits.shutdown_seconds), '--property=KillMode=control-group',
            *HARDENING_PROPERTIES,
            *self._isolation_argv(),
            '--ro-bind', input_dir, '/input',
            # A command reads every service's log and status; only the host writes there.
            *(('--ro-bind', str(services), SERVICES_MOUNT) if config.services else ()),
            '--size', str(limits.workspace_bytes), '--tmpfs', '/work', '--remount-ro', '/',
            '--chdir', '/work',
            '/usr/bin/'+config.python_name, '-B', '-c',
            'import sys; sys.path.insert(0,"/runner"); from sandbox_worker import execute; execute("/input/request.json")',
        ]

    def service_argv(self, name: str, unit: str) -> list[str]:
        """A service: the command's isolation, no wall-clock cap beyond its lifetime, its own log directory."""
        config, limits = self.config, self.config.services
        if limits is None:
            raise ValueError('This shell runs no services')
        home = self.services_root/name
        return [
            config.systemd_run, '--user', '--quiet', '--collect', '--unit='+unit,
            '--property=MemoryMax='+str(limits.memory_bytes), '--property=MemorySwapMax=0',
            '--property=TasksMax='+str(limits.processes), '--property=CPUQuota='+str(config.limits.cpu_percent)+'%',
            '--property=RuntimeMaxSec='+str(limits.lifetime_seconds),
            '--property=TimeoutStopSec='+str(config.limits.shutdown_seconds), '--property=KillMode=control-group',
            *HARDENING_PROPERTIES,
            *self._isolation_argv(),
            '--ro-bind', str(self.services_root), SERVICES_MOUNT,
            '--bind', str(home), SERVICES_MOUNT+'/'+name,
            # The workspace as of the last command, refreshed before every command; the service's own
            # writes to it are not kept (its state belongs in /svc/<name>).
            '--bind', str(home/'work'), '/work', '--remount-ro', '/',
            '--setenv', 'SVC_NAME', name, '--setenv', 'PYTHONUNBUFFERED', '1', '--chdir', '/work',
            '/usr/bin/'+config.shell_name, '--noprofile', '--norc', '-c',
            'exec >>"/svc/$SVC_NAME/log" 2>&1; exec "$0" --noprofile --norc -c "$(cat "/svc/$SVC_NAME/command")"',
            '/usr/bin/'+config.shell_name,
        ]

    def run(self, command: str, workspace: bytes = b'', *, timeout_seconds: float | None = None) -> ShellResult:
        """Run one Bash command and return its persisted workspace and bounded output."""
        config, limits = self.config, self.config.limits
        if not isinstance(command, str) or not command.strip():
            raise ValueError('A nonempty shell command is required')
        timeout = limits.command_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0 or timeout > limits.command_seconds:
            raise ValueError('Timeout exceeds the configured command limit')
        _members(workspace, limits.workspace_bytes, limits.max_files)
        start = time.monotonic()
        named = [p for p in config.refused_paths if p in command]
        if named:
            return ShellResult('rejected', None, '', '', False, False, workspace, (), time.monotonic()-start,
                               'refused: the command names '+', '.join(named)+', which is the toolchain, not your workspace. '
                               'Read tools through their --help and the errors they print; a refusal you cannot resolve '
                               'is a reason to block the task, not source to read.')
        for pattern, reason in config.refused_patterns:
            if re.search(pattern, command):
                return ShellResult('rejected', None, '', '', False, False, workspace, (), time.monotonic()-start,
                                   'refused: '+reason)
        unit = 'isolated-shell-'+uuid4().hex
        if self._services:
            self._mirror_workspace(workspace)
        with tempfile.TemporaryDirectory(dir=config.scratch_root, prefix='shell-input-') as directory:
            input_dir = Path(directory)
            (input_dir/'workspace.tar').write_bytes(workspace)
            payload = dict(command=command, timeout=timeout, limits=limits.__dict__, shell='/usr/bin/'+config.shell_name,
                           capture_id=uuid4().hex, workspace_path='/input/workspace.tar',
                           snapshot_ignore=list(config.snapshot_ignore))
            (input_dir/'request.json').write_text(json.dumps(payload))
            process = subprocess.Popen(self.command_argv(str(input_dir), unit), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
            cap = (limits.workspace_bytes+limits.max_files*2048+10240)*2 + limits.output_bytes*2 + 65536
            try:
                stdout, stderr = self._collect(process, cap, limits.command_seconds+2*limits.shutdown_seconds)
            except (TimeoutError, ValueError) as error:
                subprocess.run([config.systemctl, '--user', 'stop', unit], capture_output=True,
                               timeout=limits.shutdown_seconds, check=False)
                process.kill()
                process.wait()
                return ShellResult('interrupted', None, '', '', False, False, workspace, (), time.monotonic()-start, str(error))
        try:
            if process.returncode != 0:
                raise ValueError('Sandbox service failed: '+stderr.decode(errors='replace')[:limits.visible_output_bytes])
            response = json.loads(stdout)
            if response['capture_id'] != payload['capture_id']:
                raise ValueError('Sandbox output identity mismatch')
            snapshot = base64.b64decode(response['workspace'], validate=True)
            try:
                _members(snapshot, limits.workspace_bytes, limits.max_files)
            except (ValueError, tarfile.TarError) as error:
                # The isolated process ended and returned an identified result.
                # Its outgoing archive is unusable, but the prior state is intact.
                return ShellResult('workspace_rejected', response['exit_code'], response['stdout'], response['stderr'],
                                   response['timed_out'], response['output_truncated'], workspace, (),
                                   time.monotonic()-start,
                                   f'{error}. All workspace changes from this command were discarded; '
                                   'the previous workspace is retained.')
            result = ShellResult(response['status'], response['exit_code'], response['stdout'], response['stderr'],
                                 response['timed_out'], response['output_truncated'], snapshot,
                                 tuple(response['output_files']), time.monotonic()-start, response.get('detail', ''))
        except (ValueError, KeyError, TypeError, tarfile.TarError) as error:
            return ShellResult('interrupted', None, '', stderr.decode(errors='replace')[:limits.visible_output_bytes],
                               False, False, workspace, (), time.monotonic()-start, str(error))
        return self._service_requests(result) if config.services else result

    # ------------------------------------------------------------------ services

    def _service_requests(self, result: ShellResult) -> ShellResult:
        """Carry out what `svc` asked for during the command and report it in the command's own output.

        The request files are removed from the snapshot: they were messages to the host, not files."""
        limits = self.config.limits
        requests: list[tuple[str, bytes]] = []
        with tarfile.open(fileobj=io.BytesIO(result.workspace), mode='r:') as archive:
            for member in archive:
                if member.isfile() and member.name.startswith(REQUESTS_DIR+'/'):
                    requests.append((member.name, archive.extractfile(member).read()))
        if not requests:
            return result
        lines = []
        for name, data in sorted(requests):
            try:
                request = json.loads(data)
                lines.append(self._service_request(request))
            except (ValueError, TypeError, KeyError) as error:
                lines.append('svc: bad request '+name+': '+str(error))
        sink = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(result.workspace), mode='r:') as source, \
                tarfile.open(fileobj=sink, mode='w:') as target:
            for member in source:
                if member.name == REQUESTS_DIR or member.name.startswith(REQUESTS_DIR+'/'):
                    continue
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
        stdout = (result.stdout+('\n' if result.stdout and not result.stdout.endswith('\n') else '')
                  +'\n'.join(lines)+'\n')[:limits.visible_output_bytes]
        return ShellResult(result.status, result.exit_code, stdout, result.stderr, result.timed_out,
                           result.output_truncated, sink.getvalue(), result.output_files, result.elapsed_seconds,
                           result.detail)

    def _service_request(self, request: dict[str, Any]) -> str:
        op, name = request['op'], str(request.get('name') or '')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name):
            raise ValueError('a service name is a short lowercase identifier')
        if op == 'start':
            return self.start_service(name, str(request['command']), workspace=None)
        if op == 'stop':
            return self.stop_service(name)
        if op == 'wait':
            return self.wait_service(name, pattern=request.get('pattern'), seconds=float(request.get('seconds') or 60))
        raise ValueError('unknown op '+op)

    def start_service(self, name: str, command: str, *, workspace: bytes | None) -> str:
        """Start `command` as a service named `name`; its log is at /svc/<name>/log for every later command."""
        limits = self.config.services
        if limits is None:
            raise ValueError('This shell runs no services')
        if not command.strip():
            return 'svc: start '+name+': a command is required'
        if name in self._services and self.service_state(name) == 'active':
            return 'svc: '+name+' is already running (stop it first)'
        if len([n for n in self._services if self.service_state(n) == 'active']) >= limits.maximum_services:
            return 'svc: at most '+str(limits.maximum_services)+' services may run; stop one first'
        home = self.services_root/name
        (home/'work').mkdir(parents=True, exist_ok=True)
        (home/'command').write_text(command)
        (home/'log').write_bytes(b'')
        if workspace is not None:
            self._mirror_workspace(workspace, only=name)
        unit = 'isolated-service-'+uuid4().hex
        process = subprocess.run(self.service_argv(name, unit), capture_output=True, text=True,
                                 timeout=self.config.limits.shutdown_seconds+10, check=False)
        if process.returncode != 0:
            return 'svc: start '+name+' failed: '+(process.stderr or process.stdout).strip()[:400]
        self._services[name] = dict(unit=unit, command=command, started_at=time.time())
        self._write_status(name)
        return ('svc: started '+name+' as `'+command+'`; log at '+SERVICES_MOUNT+'/'+name+'/log, '
                'status with `svc status`; it stops with `svc stop '+name+'` or when this session ends')

    def stop_service(self, name: str) -> str:
        entry = self._services.get(name)
        if entry is None:
            return 'svc: no service named '+name
        subprocess.run([self.config.systemctl, '--user', 'stop', entry['unit']], capture_output=True,
                       timeout=self.config.limits.shutdown_seconds+10, check=False)
        entry['stopped_at'] = time.time()
        self._write_status(name)
        return 'svc: stopped '+name

    def stop_all(self) -> list[str]:
        """Every service this shell started; the owner calls it when the session ends."""
        return [self.stop_service(name) for name in list(self._services)
                if self._services[name].get('stopped_at') is None]

    def service_state(self, name: str) -> str:
        entry = self._services.get(name)
        if entry is None:
            return 'unknown'
        probe = subprocess.run([self.config.systemctl, '--user', 'is-active', entry['unit']],
                               capture_output=True, text=True, timeout=10, check=False)
        return probe.stdout.strip() or 'inactive'

    def wait_service(self, name: str, *, pattern: str | None, seconds: float) -> str:
        """Block the host (not the model) until the log matches `pattern` or the service exits."""
        limits = self.config.services
        if name not in self._services:
            return 'svc: no service named '+name
        seconds = min(max(seconds, 1.0), limits.wait_seconds)
        log = self.services_root/name/'log'
        deadline = time.monotonic()+seconds
        while True:
            text = log.read_text(errors='replace') if log.exists() else ''
            if pattern and pattern in text:
                return 'svc: '+name+' matched '+repr(pattern)+' after '+str(round(seconds-(deadline-time.monotonic()), 1))+' s'
            state = self.service_state(name)
            if state != 'active':
                return 'svc: '+name+' is '+state+(' (no match for '+repr(pattern)+')' if pattern else '')+'; log tail:\n'+text[-800:]
            if time.monotonic() >= deadline:
                return 'svc: '+name+' still running after '+str(round(seconds))+' s'+(' without '+repr(pattern) if pattern else '')+'; log tail:\n'+text[-800:]
            time.sleep(min(1.0, max(0.05, deadline-time.monotonic())))

    def _write_status(self, name: str) -> None:
        entry = self._services[name]
        (self.services_root/name/'status.json').write_text(json.dumps(dict(
            name=name, command=entry['command'], unit=entry['unit'], started_at=entry['started_at'],
            stopped_at=entry.get('stopped_at'), state=self.service_state(name)), indent=1)+'\n')

    def _mirror_workspace(self, workspace: bytes, *, only: str | None = None) -> None:
        """Give each running service the workspace as of now at its /work (wipe and re-extract: small, exact)."""
        limits = self.config.limits
        for name, entry in self._services.items():
            if entry.get('stopped_at') is not None or (only is not None and name != only):
                continue
            target = self.services_root/name/'work'
            for child in sorted(target.rglob('*'), key=lambda p: len(p.parts), reverse=True):
                if child.is_symlink() or child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            target.mkdir(parents=True, exist_ok=True)
            for info, data in _members(workspace, limits.workspace_bytes, limits.max_files):
                if info.name.startswith('.svc/') or info.name.startswith('.tool-output/'):
                    continue
                path = target/info.name
                if info.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif info.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    path.chmod(info.mode & 0o777 | 0o600)

    @staticmethod
    def _collect(process: subprocess.Popen[bytes], cap: int, timeout: float) -> tuple[bytes, bytes]:
        buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
        deadline = time.monotonic()+timeout
        with selectors.DefaultSelector() as selector:
            for pipe in buffers:
                selector.register(pipe, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError('Sandbox service exceeded wall-clock allowance')
                for key, _ in selector.select(timeout=min(0.2, max(0, deadline-time.monotonic()))):
                    chunk = key.fileobj.read1(65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        buffers[key.fileobj].extend(chunk)
                        if sum(map(len, buffers.values())) > cap:
                            raise ValueError('Sandbox serialization exceeded output cap')
        process.wait(timeout=max(0.1, deadline-time.monotonic()))
        return bytes(buffers[process.stdout]), bytes(buffers[process.stderr])
