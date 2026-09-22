"""Quota-bounded Linux shell execution with an opaque persistent workspace."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import os
import shutil
import json
from pathlib import Path, PurePosixPath
import re
import selectors
import subprocess
import sys
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
    # A service's log lives on host scratch; past this size its head is dropped (the tail is what anyone reads).
    log_bytes: int = 8*1024*1024

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
class NetworkPolicy:
    """A private network namespace per shell, with one door out: an allowlisting HTTP proxy.

    Commands and services share the namespace (they reach each other on loopback) and nothing else: the
    host's services, the LAN and the internet are unroutable. Outbound HTTP(S) goes through a proxy the host
    runs on a unix socket, bridged to 127.0.0.1:<proxy_port> inside; the proxy admits `allowed_domains` (and
    their subdomains), refuses local and private addresses always, and logs every decision to egress.jsonl
    in the scratch root. Programs that ignore the proxy variables simply have no network — fail-safe."""

    allowed_domains: tuple[str, ...] = ()
    proxy_port: int = 3128
    unshare: str = '/usr/bin/unshare'
    nsenter: str = '/usr/bin/nsenter'
    socat: str = '/usr/bin/socat'

    def __post_init__(self) -> None:
        object.__setattr__(self, 'allowed_domains', tuple(str(d).strip().lower() for d in self.allowed_domains if str(d).strip()))
        if not 1024 <= self.proxy_port <= 65535:
            raise ValueError('proxy_port must be an unprivileged port')
        for tool in (self.unshare, self.nsenter, self.socat):
            if not Path(tool).is_absolute():
                raise ValueError('network tools must be absolute host paths')


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
    # share a network namespace with commands, so share_network or network is required.
    services: ServiceLimits | None = None
    # Set: commands and services run in a private network namespace with the allowlisting proxy as the only
    # way out (see NetworkPolicy). This is the mode for an unattended worker; share_network alone is the
    # legacy mode that exposes the host's loopback services.
    network: NetworkPolicy | None = None
    # Host paths a command may not name: a toolchain that must be bound to run (an editable install's
    # source) but is not the worker's to read. A command whose text contains one is refused unexecuted
    # with the reason; the tools still run because the bind stays. Prefixes, matched as substrings.
    refused_paths: tuple[str, ...] = ()
    # (regex, reason) pairs a command's text is matched against; a match is refused unexecuted with the reason.
    # For the few verbs that are policy today and must be structure: a gate override, a publish, a write to a
    # record only a tool may write. Regexes are compiled once at construction.
    refused_patterns: tuple[tuple[str, str], ...] = ()
    # Bind mode: /work is this host directory, bound read-write, instead of a tmpfs filled from a snapshot.
    # Nothing is packed per command and no size cap applies; the directory is the state. For projects whose
    # builds (a Flutter build/, node_modules, a Mathlib .lake) could never fit a snapshot. `cache_dirs` are
    # relative directories inside it that are the worker's rebuildable state, never evidence: left out of
    # `snapshot()` (what a harvest or a re-measurement sees) and, in a detached tmpfs run, bound read-only.
    workspace_dir: str | None = None
    cache_dirs: tuple[str, ...] = ()
    # Bind mode: relative directories that stay snapshot-managed — a tmpfs over the bind, filled from the
    # tar given to run() and packed back into the result — so whatever guards the host applies to them on
    # write-back (a map's entitlements) hold exactly as in snapshot mode. Everything else is the directory.
    state_dirs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not Path(value).is_absolute() for value in
               (self.bwrap, self.systemd_run, self.systemctl, self.runtime_root, self.scratch_root)):
            raise ValueError('Host paths must be absolute')
        if self.services is not None and not (self.share_network or self.network is not None):
            raise ValueError('Services need a shared or private network: a command reaches a service on localhost')
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
    workspace: Any   # tar bytes, or the DirectoryWorkspace in bind mode
    output_files: tuple[str, ...]
    elapsed_seconds: float
    detail: str = ''


WORKSPACE_MOUNT = '/work'
SERVICES_MOUNT = '/svc'
REQUESTS_DIR = '.svc/requests'   # inside the workspace: what `svc start/stop/wait` leaves for the host



def _unit_result(systemctl: str, unit: str) -> str:
    """systemd's verdict on a transient unit that exited non-zero: 'oom-kill', 'exit-code', 'signal', …"""
    try:
        out = subprocess.run([systemctl, '--user', 'show', unit, '-p', 'Result', '--value'],
                             capture_output=True, text=True, timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''
    return out

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



SCRATCH_STATE = ('.tool-output', '.session-history')   # state that is scratch, not evidence (see DirectoryWorkspace.snapshot)

def _members(snapshot: bytes, byte_limit: int, file_limit: int) -> list[tuple[tarfile.TarInfo, bytes]]:
    if not snapshot:
        return []
    # Tar headers are bounded separately from file content.
    if len(snapshot) > 4*byte_limit + file_limit * 2048 + 10240:
        # Four times the evidence cap: scratch state is exempt from the content count below, so the tar may
        # carry more than the cap, but not without bound.
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
            # Scratch state (saved outputs, window archives) rides in the state tar but is not the evidence the
            # cap is for: a window that rendered 242 MB of video frames under .tool-output/ could not even write
            # its handoff archive (follow-the-score, 2026-09-22). The tar's own size is still bounded above.
            if not (name.split('/')[0] in SCRATCH_STATE):
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


class DirectoryWorkspace:
    """A workspace that is a host directory bound at /work (bind mode): the same operations the snapshot
    helpers offer over tar bytes, over the directory. It stands in for the snapshot everywhere the session
    threads one; `write` and `edit` return the same object, and `snapshot()` packs the evidence part of the
    tree (caches and ignored directories left out) for a harvest or a re-measurement."""

    def __init__(self, root: str | Path, *, cache_dirs: tuple[str, ...] = (), snapshot_ignore: tuple[str, ...] = (),
                 state_dirs: tuple[str, ...] = (), state: bytes = b'', limits: Any = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_dirs = tuple(_name(c) for c in cache_dirs)
        self.snapshot_ignore = tuple(snapshot_ignore)
        self.state_dirs = tuple(_name(d) for d in state_dirs)
        self.state = state          # the snapshot-managed part (tar bytes), as of the last command
        self.limits = limits

    def _in_state(self, name: str) -> bool:
        parts = PurePosixPath(name).parts
        return any(parts[:len(PurePosixPath(d).parts)] == PurePosixPath(d).parts for d in self.state_dirs)

    def _excluded(self, relative: PurePosixPath) -> bool:
        parts = relative.parts
        return any(part in self.snapshot_ignore for part in parts) or any(
            parts[:len(PurePosixPath(c).parts)] == PurePosixPath(c).parts for c in self.cache_dirs) \
            or self._in_state(str(relative))

    def with_state(self, state: bytes) -> DirectoryWorkspace:
        return DirectoryWorkspace(self.root, cache_dirs=self.cache_dirs, snapshot_ignore=self.snapshot_ignore,
                                  state_dirs=self.state_dirs, state=state, limits=self.limits)

    def _state_limits(self) -> tuple[int, int]:
        return ((self.limits.workspace_bytes, self.limits.max_files) if self.limits is not None else (10**9, 10**6))

    def _path(self, name: str, *, user: bool = False) -> Path:
        canonical = _name(name, user=user)
        path = self.root/canonical
        # Never follow a link out of the tree: resolve and check containment.
        resolved = path.resolve() if path.exists() or path.is_symlink() else (path.parent.resolve()/path.name)
        if self.root not in resolved.parents and resolved != self.root:
            raise ValueError('Workspace paths must stay inside the workspace')
        return path

    def files(self, *, file_limit: int) -> tuple[str, ...]:
        byte_limit, state_files = self._state_limits()
        out = list(workspace_files(self.state, byte_limit=byte_limit, file_limit=state_files)) if self.state else []
        for path in sorted(self.root.rglob('*')):
            relative = PurePosixPath(str(path.relative_to(self.root)))
            if self._excluded(relative) or not path.is_file() or path.is_symlink():
                continue
            out.append(str(relative))
            if len(out) >= file_limit:
                break
        return tuple(out)

    def read(self, name: str, *, byte_limit: int) -> bytes:
        if self._in_state(_name(name, user=True)):
            limit, files = self._state_limits()
            return read_workspace_file(self.state, name, byte_limit=limit, file_limit=files)
        path = self._path(name, user=True)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(_name(name, user=True))
        if path.stat().st_size > byte_limit:
            raise ValueError('File exceeds the workspace byte limit')
        return path.read_bytes()

    def write(self, name: str, data: bytes, *, byte_limit: int) -> DirectoryWorkspace:
        if len(data) > byte_limit:
            raise ValueError('Workspace file content exceeds its limit')
        if self._in_state(_name(name, user=True)):
            limit, files = self._state_limits()
            return self.with_state(write_workspace_file(self.state, name, data, byte_limit=limit, file_limit=files))
        path = self._path(name, user=True)
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ValueError('Destination is not a regular file')
        for parent in path.parents:
            if parent == self.root:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError('A workspace parent is not a directory')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self

    # State that is scratch, not evidence: saved tool output and the window archives. A harvest, a checklist and a
    # detached check read none of it, and a worker that renders 1,175 video frames under .tool-output/ (follow-
    # the-score, 2026-09-22) put 242 MB there — past the evidence cap, so every harvest failed and the task with it.
    SCRATCH_STATE = SCRATCH_STATE

    def snapshot(self, *, byte_limit: int, file_limit: int) -> bytes:
        """The evidence part of the tree as a tar — the directory (caches left out) plus the state part that is
        evidence (the playbook walks and the services' records; not saved outputs or window archives) —
        what a harvest or a fresh sandbox is given."""
        sink = io.BytesIO()
        with tarfile.open(fileobj=sink, mode='w:', dereference=False) as archive:
            if self.state:
                with tarfile.open(fileobj=io.BytesIO(self.state), mode='r:') as source:
                    for member in source:
                        parts = [p for p in PurePosixPath(member.name).parts if p not in ('.', '/')]
                        if parts and parts[0] in self.SCRATCH_STATE:
                            continue
                        archive.addfile(member, source.extractfile(member) if member.isfile() else None)
            for path in sorted(self.root.rglob('*')):
                relative = PurePosixPath(str(path.relative_to(self.root)))
                if self._excluded(relative):
                    continue
                if path.is_symlink():
                    link = os.readlink(path)
                    if os.path.isabs(link) or '..' in link.split('/'):
                        continue
                info = archive.gettarinfo(str(path), arcname=str(relative))
                if path.is_file() and not path.is_symlink():
                    info.type = tarfile.REGTYPE
                    info.linkname = ''
                    with path.open('rb') as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)
        data = sink.getvalue()
        _members(data, byte_limit, file_limit)
        return data


def _only_state(snapshot: bytes, state_dirs: tuple[str, ...], limits: Any) -> bytes:
    """The members of a snapshot that lie under the state directories (bind mode sends only those)."""
    if not snapshot or not state_dirs:
        return b''
    prefixes = tuple(_name(d)+'/' for d in state_dirs)+tuple(_name(d) for d in state_dirs)
    sink = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(snapshot), mode='r:') as source, tarfile.open(fileobj=sink, mode='w:') as out:
        for member in source:
            if member.name in prefixes or member.name.startswith(tuple(p for p in prefixes if p.endswith('/'))):
                out.addfile(member, source.extractfile(member) if member.isfile() else None)
    return sink.getvalue()


def workspace_files(snapshot: bytes | DirectoryWorkspace, *, byte_limit: int, file_limit: int) -> tuple[str, ...]:
    """List persisted regular files without extracting the archive on the host."""
    if isinstance(snapshot, DirectoryWorkspace):
        return snapshot.files(file_limit=file_limit)
    return tuple(member.name for member, _ in _members(snapshot, byte_limit, file_limit) if member.isfile())


def read_workspace_file(snapshot: bytes | DirectoryWorkspace, name: str, *, byte_limit: int, file_limit: int) -> bytes:
    """Read a regular member; never follow a workspace symlink on the host."""
    if isinstance(snapshot, DirectoryWorkspace):
        return snapshot.read(name, byte_limit=byte_limit)
    canonical = _name(name, user=True)
    for member, data in _members(snapshot, byte_limit, file_limit):
        if member.name == canonical and member.isfile():
            return data
    raise FileNotFoundError(canonical)


def write_workspace_file(snapshot: bytes | DirectoryWorkspace, name: str, data: bytes, *, byte_limit: int, file_limit: int) -> bytes | DirectoryWorkspace:
    """Replace a regular member, preserving all other safe workspace members."""
    if isinstance(snapshot, DirectoryWorkspace):
        return snapshot.write(name, data, byte_limit=byte_limit)
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


def edit_workspace_file(snapshot: bytes | DirectoryWorkspace, name: str, old_text: str, new_text: str, *,
                        expected_occurrences: int = 1, byte_limit: int, file_limit: int) -> tuple[bytes | DirectoryWorkspace, dict[str, Any]]:
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
        # The private network namespace: a holder process that owns it, the host proxy, and the bridge inside.
        self._netns: dict[str, Any] | None = None

    # ------------------------------------------------------------------ private network

    @property
    def egress_log(self) -> Path:
        return Path(self.config.scratch_root)/'egress.jsonl'

    def _ensure_network(self) -> dict[str, Any]:
        """Start (once) the namespace holder, the egress proxy and the loopback bridge; return their handles."""
        policy = self.config.network
        if policy is None:
            raise ValueError('This shell has no private network')
        if self._netns is not None and all(p.poll() is None for p in self._netns['processes']):
            return self._netns
        self.close_network()
        import os
        import ctypes

        def die_with_parent() -> None:
            # PR_SET_PDEATHSIG: the helpers never outlive the harness process that started them.
            ctypes.CDLL(None).prctl(1, 9)

        scratch = Path(self.config.scratch_root)
        scratch.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen([policy.unshare, '--user', '--map-current-user', '--keep-caps', '--net',
                                   '/usr/bin/sh', '-c', 'ip link set lo up && exec sleep infinity'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  preexec_fn=die_with_parent)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            probe = subprocess.run(self._nsenter(holder.pid)+['/usr/bin/sh', '-c', 'ip -o link show lo'],
                                   capture_output=True, text=True, timeout=5)
            if probe.returncode == 0 and 'UP' in probe.stdout:
                break
            if holder.poll() is not None:
                raise RuntimeError('network namespace holder exited: '+(holder.stderr.read().decode(errors='replace') if holder.stderr else ''))
            time.sleep(0.1)
        else:
            raise RuntimeError('network namespace did not come up')
        # The socket lives in a short directory of its own, not the scratch root: AF_UNIX paths are bound at
        # 108 bytes, and a session's scratch (`<project>/.mizpah/sessions/<stamp>/tasks/<task>/scratch`) ran
        # past that. The proxy then died on bind with its stderr dropped, the wait below ran its whole ten
        # seconds on every command, and the sandbox had no egress at all (2026-09-21: 85 calls at 10 s each,
        # "registry unreachable").
        sock_dir = Path(tempfile.mkdtemp(prefix='egress-', dir=os.environ.get('XDG_RUNTIME_DIR') or None))
        sock = sock_dir/'egress.sock'
        proxy = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve().parent/'egress_proxy.py'),
                                  str(sock), str(self.egress_log), *policy.allowed_domains],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 preexec_fn=die_with_parent)
        deadline = time.monotonic()+10
        while not sock.exists() and time.monotonic() < deadline:
            if proxy.poll() is not None:
                holder.terminate()
                raise RuntimeError('egress proxy exited before listening: '+(proxy.stderr.read().decode(errors='replace') if proxy.stderr else ''))
            time.sleep(0.05)
        if not sock.exists():
            holder.terminate()
            proxy.terminate()
            raise RuntimeError('egress proxy did not listen on '+str(sock))
        bridge = subprocess.Popen(self._nsenter(holder.pid)+[policy.socat,
                                  'TCP-LISTEN:'+str(policy.proxy_port)+',fork,bind=127.0.0.1,reuseaddr',
                                  'UNIX-CONNECT:'+str(sock)],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  preexec_fn=die_with_parent)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            probe = subprocess.run(self._nsenter(holder.pid)+['/usr/bin/sh', '-c',
                                   'exec 3<>/dev/tcp/127.0.0.1/'+str(policy.proxy_port)], capture_output=True, timeout=5)
            if probe.returncode == 0:
                break
            time.sleep(0.1)
        else:
            self.close_network()
            raise RuntimeError('loopback bridge did not come up on port '+str(policy.proxy_port))
        self._netns = dict(holder=holder, proxy=proxy, bridge=bridge, processes=[holder, proxy, bridge], socket=sock)
        return self._netns

    def _nsenter(self, pid: int) -> list[str]:
        policy = self.config.network
        assert policy is not None
        return [policy.nsenter, '--user=/proc/'+str(pid)+'/ns/user', '--net=/proc/'+str(pid)+'/ns/net',
                '--preserve-credentials', '--']

    def close_network(self) -> None:
        """Stop the bridge, the proxy and the namespace holder (services in it die with the namespace)."""
        if self._netns is None:
            return
        for process in reversed(self._netns['processes']):
            if process.poll() is None:
                process.terminate()
        for process in self._netns['processes']:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        sock = self._netns.get('socket')
        if sock is not None:
            shutil.rmtree(sock.parent, ignore_errors=True)
        self._netns = None

    def close(self) -> None:
        """Everything this shell started: services, then the private network."""
        self.stop_all()
        self.close_network()

    @property
    def services_root(self) -> Path:
        return Path(self.config.scratch_root)/'services'

    def _isolation_argv(self) -> list[str]:
        """The bwrap namespace, mounts and environment a command and a service share."""
        config, limits = self.config, self.config.limits
        helper = str(Path(__file__).resolve().parent)
        private = config.network is not None
        if private:
            netns = self._ensure_network()
            prefix = self._nsenter(netns['holder'].pid)
            proxy_url = 'http://127.0.0.1:'+str(config.network.proxy_port)
            proxy_env = [part for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')
                         for part in ('--setenv', name, proxy_url)] + ['--setenv', 'NO_PROXY', '127.0.0.1,localhost',
                                                                         '--setenv', 'no_proxy', '127.0.0.1,localhost']
        else:
            prefix, proxy_env = [], []
        networked = config.share_network or private
        return [
            *prefix,
            config.bwrap,
            *(('--unshare-user', '--unshare-ipc', '--unshare-pid', '--unshare-uts', '--unshare-cgroup')
              if networked else ('--unshare-all', '--unshare-user')),
            '--disable-userns', '--die-with-parent', '--new-session',
            '--as-pid-1', '--cap-drop', 'ALL', '--clearenv',
            '--ro-bind', config.runtime_root, '/usr',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--ro-bind', helper, '/runner',
            *[part for bind in config.read_only_binds for part in ('--ro-bind', bind, bind)],
            *[part for entry in (config.network_files if networked else ())
              if Path(entry).exists() for part in ('--ro-bind', str(Path(entry).resolve()), entry)],
            '--proc', '/proc', '--remount-ro', '/proc', '--dev', '/dev', '--remount-ro', '/dev',
            '--size', str(limits.temporary_bytes), '--tmpfs', '/tmp',
            *proxy_env,
            '--setenv', 'PATH', ':'.join(filter(None, (config.environment.get('PATH'), '/usr/bin',
                                                        '/runner' if config.services else None))),
            '--setenv', 'HOME', '/work', '--setenv', 'TMPDIR', '/tmp',
            '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'OPENBLAS_NUM_THREADS', '1',
            '--setenv', 'OMP_NUM_THREADS', '1',
            *(('--setenv', 'SVC_ROOT', SERVICES_MOUNT) if config.services else ()),
            *[part for name, value in sorted(config.environment.items()) if name != 'PATH'
              for part in ('--setenv', name, value)],
        ]

    def _work_argv(self, *, detached: bool = False) -> list[str]:
        """/work: the bound directory in bind mode, else a capped tmpfs the snapshot is unpacked into. A
        detached run in bind mode gets the tmpfs (filled from `snapshot()`) with the caches bound read-only
        on top, so a build can use what was built without being able to change it."""
        config, limits = self.config, self.config.limits
        if config.workspace_dir and not detached:
            root = Path(config.workspace_dir).resolve()
            argv = ['--bind', str(root), WORKSPACE_MOUNT]
            # A read-only bind that lies inside the workspace is bound again, read-only, at its place under
            # /work after the writable bind, so it shadows it: a directory the command may read but not change
            # in a workspace it otherwise owns (an issued project among the Deputy's drafts).
            for bind in config.read_only_binds:
                source = Path(bind).resolve()
                if source != root and source.is_relative_to(root) and source.is_dir():
                    argv += ['--ro-bind', str(source), WORKSPACE_MOUNT+'/'+source.relative_to(root).as_posix()]
            for state in config.state_dirs:
                argv += ['--size', str(limits.workspace_bytes), '--tmpfs', WORKSPACE_MOUNT+'/'+_name(state)]
            return argv
        argv = ['--size', str(limits.workspace_bytes), '--tmpfs', WORKSPACE_MOUNT]
        if config.workspace_dir and detached:
            root = Path(config.workspace_dir).resolve()
            for cache in config.cache_dirs:
                source = root/_name(cache)
                if source.is_dir():
                    argv += ['--ro-bind', str(source), WORKSPACE_MOUNT+'/'+_name(cache)]
        return argv

    def command_argv(self, input_dir: str, unit: str, *, detached: bool | None = None) -> list[str]:
        """Build the complete resource-control and isolation command for auditing."""
        config, limits = self.config, self.config.limits
        if detached is None:
            detached = bool(getattr(self, '_detached', False))
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
            *self._work_argv(detached=detached), '--remount-ro', '/',
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
            # Bind mode: the service sees the project directory itself, read-only (its state is in /svc/<name>);
            # snapshot mode: the mirror of the workspace as of the last command.
            *(('--ro-bind', str(Path(config.workspace_dir).resolve()), '/work') if config.workspace_dir
              else ('--bind', str(home/'work'), '/work')), '--remount-ro', '/',
            '--setenv', 'SVC_NAME', name, '--setenv', 'PYTHONUNBUFFERED', '1', '--chdir', '/work',
            '/usr/bin/'+config.shell_name, '--noprofile', '--norc', '-c',
            'exec >>"/svc/$SVC_NAME/log" 2>&1; exec "$0" --noprofile --norc -c "$(cat "/svc/$SVC_NAME/command")"',
            '/usr/bin/'+config.shell_name,
        ]

    @property
    def directory(self) -> DirectoryWorkspace | None:
        """The workspace as a directory in bind mode; None in snapshot mode."""
        config = self.config
        if not config.workspace_dir:
            return None
        if getattr(self, '_directory', None) is None:
            self._directory = DirectoryWorkspace(config.workspace_dir, cache_dirs=config.cache_dirs, snapshot_ignore=config.snapshot_ignore,
                                                 state_dirs=config.state_dirs, limits=config.limits)
        return self._directory

    def snapshot(self) -> bytes:
        """Bind mode: the evidence part of the directory as a tar (caches left out)."""
        directory = self.directory
        if directory is None:
            raise ValueError('snapshot() is for bind mode; a snapshot-mode shell returns the workspace from run()')
        limits = self.config.limits
        return directory.snapshot(byte_limit=limits.workspace_bytes, file_limit=limits.max_files)

    def run(self, command: str, workspace: bytes | DirectoryWorkspace = b'', *, timeout_seconds: float | None = None,
            detached: bool = False) -> ShellResult:
        """Run one Bash command and return its persisted workspace and bounded output.

        Bind mode (`workspace_dir` set): the directory is the workspace, `workspace` is ignored unless
        `detached`, and the result's workspace is the directory. `detached` runs the command on a tmpfs copy
        of the evidence tree (caches read-only): a check whose changes must not persist."""
        config, limits = self.config, self.config.limits
        if not isinstance(command, str) or not command.strip():
            raise ValueError('A nonempty shell command is required')
        timeout = limits.command_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0 or timeout > limits.command_seconds:
            raise ValueError('Timeout exceeds the configured command limit')
        bound = config.workspace_dir is not None and not detached
        if isinstance(workspace, DirectoryWorkspace):
            if detached:
                workspace = workspace.snapshot(byte_limit=limits.workspace_bytes, file_limit=limits.max_files)
            else:
                self._directory = workspace
                workspace = workspace.state
        elif config.workspace_dir and detached and not workspace:
            workspace = self.snapshot()
        if bound:
            # Only members under the state directories travel: the rest of the tree is the bind.
            workspace = _only_state(workspace, config.state_dirs, limits)
        _members(workspace, limits.workspace_bytes, limits.max_files)
        start = time.monotonic()
        named = [p for p in config.refused_paths if p in command]
        if named:
            return ShellResult('rejected', None, '', '', False, False, self._state(workspace, bound), (), time.monotonic()-start,
                               'refused: the command names '+', '.join(named)+', which is the toolchain, not your workspace. '
                               'Read tools through their --help and the errors they print; a refusal you cannot resolve '
                               'is a reason to block the task, not source to read.')
        for pattern, reason in config.refused_patterns:
            if re.search(pattern, command):
                return ShellResult('rejected', None, '', '', False, False, workspace, (), time.monotonic()-start,
                                   'refused: '+reason)
        unit = 'isolated-shell-'+uuid4().hex
        if self._services:
            if not bound:
                self._mirror_workspace(workspace)
            self._cap_logs()
        Path(config.scratch_root).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=config.scratch_root, prefix='shell-input-') as directory:
            input_dir = Path(directory)
            (input_dir/'workspace.tar').write_bytes(workspace)
            payload = dict(command=command, timeout=timeout, limits=limits.__dict__, shell='/usr/bin/'+config.shell_name,
                           capture_id=uuid4().hex, workspace_path='/input/workspace.tar',
                           snapshot_ignore=list(config.snapshot_ignore), bind=bound,
                           state_dirs=[_name(d) for d in config.state_dirs] if bound else [])
            (input_dir/'request.json').write_text(json.dumps(payload))
            self._detached = detached
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
                return ShellResult('interrupted', None, '', '', False, False, self._state(workspace, bound), (), time.monotonic()-start, str(error))
        try:
            if process.returncode != 0:
                # `--collect` removes the unit the moment it exits, so its Result is usually already gone: the
                # status systemd-run itself returns is the one that survives (128+signal for a kill).
                if process.returncode in (137, -9, 9) or _unit_result(config.systemctl, unit) == 'oom-kill':
                    # The kernel killed the unit at its memory limit: a known outcome, told to the model as the
                    # failed command it was. It had come back as "uncertain" and blocked the task — an ffmpeg
                    # encode at exactly 3 GB, twice, on follow-the-score (2026-09-22).
                    return ShellResult('error', 137, '', '', False, False, self._state(workspace, bound), (),
                                       time.monotonic()-start,
                                       'killed: the command exceeded the sandbox memory limit ('
                                       +str(limits.memory_bytes//(1024*1024))+' MB); nothing of it was applied. '
                                       'Do the work in smaller pieces or with less in memory at once')
                detail = stderr.decode(errors='replace')[:limits.visible_output_bytes].strip()
                if not detail:
                    # The runner said nothing: the unit died on a limit of its own (memory, tasks, runtime), not
                    # on anything this harness can name. A failed command the model can act on, never an
                    # uncertain outcome — that class blocks the task and ends the run.
                    return ShellResult('error', process.returncode, '', '', False, False,
                                       self._state(workspace, bound), (), time.monotonic()-start,
                                       'the command was killed by the sandbox (exit '+str(process.returncode)+
                                       '): it exceeded a limit — memory ('+str(limits.memory_bytes//(1024*1024))+
                                       ' MB), processes ('+str(limits.processes)+') or time. Nothing of it was '
                                       'applied. Do the work in smaller pieces')
                raise ValueError('Sandbox service failed: '+detail)
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
            if bound:
                # The directory is the tree; the packed part is the state directories as of now.
                self._directory = self.directory.with_state(snapshot)
                snapshot = self._directory
            result = ShellResult(response['status'], response['exit_code'], response['stdout'], response['stderr'],
                                 response['timed_out'], response['output_truncated'], snapshot,
                                 tuple(response['output_files']), time.monotonic()-start, response.get('detail', ''))
        except (ValueError, KeyError, TypeError, tarfile.TarError) as error:
            return ShellResult('interrupted', None, '', stderr.decode(errors='replace')[:limits.visible_output_bytes],
                               False, False, self._state(workspace, bound), (), time.monotonic()-start, str(error))
        return self._service_requests(result) if config.services else result

    def _state(self, workspace: Any, bound: bool) -> Any:
        return self.directory.with_state(workspace) if bound else workspace

    # ------------------------------------------------------------------ services

    def _service_requests(self, result: ShellResult) -> ShellResult:
        """Carry out what `svc` asked for during the command and report it in the command's own output.

        The request files are removed from the snapshot: they were messages to the host, not files."""
        limits = self.config.limits
        requests: list[tuple[str, bytes]] = []
        if isinstance(result.workspace, DirectoryWorkspace):
            directory = result.workspace
            folder = directory.root/REQUESTS_DIR
            for path in sorted(folder.glob('*')) if folder.is_dir() else []:
                if path.is_file():
                    requests.append((REQUESTS_DIR+'/'+path.name, path.read_bytes()))
                    path.unlink()
            kept = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(directory.state), mode='r:') as source, tarfile.open(fileobj=kept, mode='w:') as sink_tar:
                for member in source:
                    if member.isfile() and member.name.startswith(REQUESTS_DIR+'/'):
                        requests.append((member.name, source.extractfile(member).read()))
                    else:
                        sink_tar.addfile(member, source.extractfile(member) if member.isfile() else None)
            if not requests:
                return result
            directory = directory.with_state(kept.getvalue())
            self._directory = directory
            lines = []
            for name, data in requests:
                try:
                    lines.append(self._service_request(json.loads(data)))
                except (ValueError, TypeError, KeyError) as error:
                    lines.append('svc: bad request '+name+': '+str(error))
            return ShellResult(result.status, result.exit_code, (result.stdout+'\n' if result.stdout else '')+'\n'.join(lines)+'\n',
                               result.stderr, result.timed_out, result.output_truncated, directory, result.output_files,
                               result.elapsed_seconds, result.detail)
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
        if workspace is not None and not isinstance(workspace, DirectoryWorkspace) and not self.config.workspace_dir:
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

    def _cap_logs(self) -> None:
        """Keep every running service's log under log_bytes by dropping its head (called before each command)."""
        limit = self.config.services.log_bytes if self.config.services else None
        if limit is None:
            return
        for name, entry in self._services.items():
            if entry.get('stopped_at') is not None:
                continue
            log = self.services_root/name/'log'
            try:
                size = log.stat().st_size
            except OSError:
                continue
            if size > limit:
                with log.open('rb') as handle:
                    handle.seek(size-limit//2)
                    tail = handle.read()
                log.write_bytes(b'[log head dropped: it passed '+str(limit).encode()+b' bytes]\n'+tail)

    def _mirror_workspace(self, workspace: bytes, *, only: str | None = None) -> None:
        """Give each running service the workspace as of now at its /work (wipe and re-extract: small, exact)."""
        limits = self.config.limits
        members = [(info, data) for info, data in _members(workspace, limits.workspace_bytes, limits.max_files)
                   if not (info.name.startswith('.svc/') or info.name.startswith('.tool-output/'))]
        incoming = {info.name for info, _ in members}
        for name, entry in self._services.items():
            if entry.get('stopped_at') is not None or (only is not None and name != only):
                continue
            target = self.services_root/name/'work'
            target.mkdir(parents=True, exist_ok=True)
            # The service writes into its /work while it runs (a browser's profile, a build's cache); those
            # files are its own and stay. The mirror removes only what it put there last time and is gone
            # from the workspace now, then lays down the current workspace over the top.
            for stale in sorted(entry.get('mirrored', set())-incoming, key=lambda n: -len(n)):
                path = target/stale
                try:
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                except OSError:
                    continue   # the service is using it; it is the service's now
            for info, data in members:
                path = target/info.name
                try:
                    if info.isdir():
                        path.mkdir(parents=True, exist_ok=True)
                    elif info.isfile():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                        path.chmod(info.mode & 0o777 | 0o600)
                except OSError:
                    continue
            entry['mirrored'] = incoming

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
