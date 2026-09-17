"""Quota-bounded Linux shell execution with an opaque persistent workspace."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import json
from pathlib import Path, PurePosixPath
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

    def __post_init__(self) -> None:
        if any(not Path(value).is_absolute() for value in
               (self.bwrap, self.systemd_run, self.systemctl, self.runtime_root, self.scratch_root)):
            raise ValueError('Host paths must be absolute')
        if any('/' in value or not value for value in (self.shell_name, self.python_name)):
            raise ValueError('Runtime binary names must be basenames')


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


def _name(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Workspace paths must be relative without parent traversal')
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
    canonical = _name(name)
    for member, data in _members(snapshot, byte_limit, file_limit):
        if member.name == canonical and member.isfile():
            return data
    raise FileNotFoundError(canonical)


def write_workspace_file(snapshot: bytes, name: str, data: bytes, *, byte_limit: int, file_limit: int) -> bytes:
    """Replace a regular member, preserving all other safe workspace members."""
    canonical = _name(name)
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
    count = original.count(old_text)
    if count == 0:
        raise WorkspaceEditError('text_not_found', f'old_text not found in {name}')
    if count != expected_occurrences:
        raise WorkspaceEditError('occurrence_mismatch',
                                 f'Expected {expected_occurrences} occurrence(s) of old_text in {name} but found {count}')
    updated = original.replace(old_text, new_text).encode('utf-8')
    output = write_workspace_file(snapshot, name, updated, byte_limit=byte_limit, file_limit=file_limit)
    return output, dict(path=_name(name), replacements=count, previous_bytes=len(data), bytes_written=len(updated))


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

    def command_argv(self, input_dir: str, unit: str) -> list[str]:
        """Build the complete resource-control and isolation command for auditing."""
        config, limits = self.config, self.config.limits
        if not Path(input_dir).is_absolute() or not unit.replace('-', '').isalnum():
            raise ValueError('Invalid input directory or unit name')
        helper = str(Path(__file__).resolve().parent)
        return [
            config.systemd_run, '--user', '--quiet', '--wait', '--pipe', '--collect', '--unit='+unit,
            '--property=MemoryMax='+str(limits.memory_bytes), '--property=MemorySwapMax=0',
            '--property=TasksMax='+str(limits.processes), '--property=CPUQuota='+str(limits.cpu_percent)+'%',
            '--property=RuntimeMaxSec='+str(limits.command_seconds+limits.shutdown_seconds),
            '--property=TimeoutStopSec='+str(limits.shutdown_seconds), '--property=KillMode=control-group',
            config.bwrap, '--unshare-all', '--unshare-user', '--disable-userns', '--die-with-parent', '--new-session',
            '--as-pid-1', '--cap-drop', 'ALL', '--clearenv',
            '--ro-bind', config.runtime_root, '/usr',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--ro-bind', helper, '/runner', '--ro-bind', input_dir, '/input',
            '--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev', '--remount-ro', '/dev',
            '--size', str(limits.workspace_bytes), '--tmpfs', '/work',
            '--size', str(limits.temporary_bytes), '--tmpfs', '/tmp', '--remount-ro', '/',
            '--setenv', 'PATH', '/usr/bin', '--setenv', 'HOME', '/work', '--setenv', 'TMPDIR', '/tmp',
            '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'OPENBLAS_NUM_THREADS', '1',
            '--setenv', 'OMP_NUM_THREADS', '1', '--chdir', '/work',
            '/usr/bin/'+config.python_name, '-B', '-c',
            'import sys; sys.path.insert(0,"/runner"); from sandbox_worker import execute; execute("/input/request.json")',
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
        unit = 'isolated-shell-'+uuid4().hex
        with tempfile.TemporaryDirectory(dir=config.scratch_root, prefix='shell-input-') as directory:
            input_dir = Path(directory)
            (input_dir/'workspace.tar').write_bytes(workspace)
            payload = dict(command=command, timeout=timeout, limits=limits.__dict__, shell='/usr/bin/'+config.shell_name,
                           capture_id=uuid4().hex, workspace_path='/input/workspace.tar')
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
            return ShellResult(response['status'], response['exit_code'], response['stdout'], response['stderr'],
                               response['timed_out'], response['output_truncated'], snapshot,
                               tuple(response['output_files']), time.monotonic()-start, response.get('detail', ''))
        except (ValueError, KeyError, TypeError, tarfile.TarError) as error:
            return ShellResult('interrupted', None, '', stderr.decode(errors='replace')[:limits.visible_output_bytes],
                               False, False, workspace, (), time.monotonic()-start, str(error))

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
