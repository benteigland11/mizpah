import io
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.sandboxed_shell_execution import (
    ShellConfig, ShellLimits, SandboxedShell, WorkspaceEditError, edit_workspace_file, read_workspace_file, read_workspace_lines,
    workspace_files, write_workspace_file,
)
from src.sandbox_worker import execute


def limits():
    return ShellLimits(1024**3, 16*1024**2, 8*1024**2, 65536, 8192, 32, 100, 10, 5, 1000)


def test_workspace_roundtrip_and_replacement():
    args = dict(byte_limit=1024, file_limit=10)
    state = write_workspace_file(b'', 'notes/a.txt', b'first', **args)
    state = write_workspace_file(state, 'measurements/b.txt', b'data', **args)
    state = write_workspace_file(state, 'notes/a.txt', b'revised', **args)
    assert read_workspace_file(state, 'notes/a.txt', **args) == b'revised'
    assert set(workspace_files(state, **args)) == {'notes/a.txt', 'measurements/b.txt'}
    with pytest.raises(FileNotFoundError):
        read_workspace_file(state, 'missing.txt', **args)


@pytest.mark.parametrize('name', ['/outside', '../outside', 'safe/../../outside'])
def test_workspace_rejects_parent_and_absolute_paths(name):
    with pytest.raises(ValueError):
        write_workspace_file(b'', name, b'x', byte_limit=1024, file_limit=10)


def test_archive_rejects_symlink_escape_and_duplicate_records():
    for kind in ('symlink', 'duplicate', 'device'):
        sink = io.BytesIO()
        with tarfile.open(fileobj=sink, mode='w:') as archive:
            info = tarfile.TarInfo('item')
            if kind == 'symlink':
                info.type, info.linkname = tarfile.SYMTYPE, '/outside'
            elif kind == 'device':
                info.type = tarfile.CHRTYPE
            archive.addfile(info)
            if kind == 'duplicate':
                archive.addfile(info)
        with pytest.raises(ValueError):
            workspace_files(sink.getvalue(), byte_limit=1024, file_limit=10)


def test_content_and_entry_limits():
    with pytest.raises(ValueError):
        write_workspace_file(b'', 'item', b'x'*1001, byte_limit=1000, file_limit=10)
    state = write_workspace_file(b'', 'a', b'x', byte_limit=1024, file_limit=1)
    with pytest.raises(ValueError):
        write_workspace_file(state, 'b', b'x', byte_limit=1024, file_limit=1)


def test_launch_contract_contains_no_writable_host_mount(tmp_path):
    shell = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits()))
    argv = shell.command_argv(str(tmp_path), 'example-unit')
    assert '--bind' not in argv and '--dev-bind' not in argv
    assert '--unshare-all' in argv and '--disable-userns' in argv and '--clearenv' in argv
    assert '--property=MemorySwapMax=0' in argv
    assert '--property=TasksMax=32' in argv
    assert argv.count('--tmpfs') == 2
    assert argv.count('--remount-ro') == 3
    with pytest.raises(ValueError):
        shell.run('', b'')
    with pytest.raises(ValueError):
        shell.run('true', b'', timeout_seconds=100)


def test_private_entrypoint_refuses_host_execution():
    with pytest.raises(RuntimeError, match='isolated PID 1'):
        execute('unused.json')


def test_invalid_limits_and_host_paths():
    with pytest.raises(ValueError):
        ShellLimits(0, 1, 1, 1, 1, 1, 1, 1, 1, 1)
    with pytest.raises(ValueError):
        ShellConfig('bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', '/scratch', limits())


@pytest.mark.parametrize('mode', ['normal', 'timeout', 'output'])
def test_worker_serializes_command_results_and_restores_files(tmp_path, monkeypatch, capsys, mode):
    import base64
    import json
    import os
    import shutil
    import signal
    import subprocess
    from src import sandbox_worker

    workspace = tmp_path/'work'
    workspace.mkdir()
    initial = write_workspace_file(b'', 'saved.txt', b'previous state', byte_limit=4096, file_limit=10)
    archive_path = tmp_path/'input.tar'
    archive_path.write_bytes(initial)
    commands = {'normal': 'cat saved.txt; printf output; printf error >&2', 'timeout': 'sleep 5', 'output': 'yes output'}
    payload = dict(command=commands[mode], timeout=0.2 if mode == 'timeout' else 2,
                   limits=limits().__dict__, shell=shutil.which('bash'), capture_id='sample', workspace_path=str(archive_path))
    if mode == 'output':
        payload['limits']['output_bytes'] = 1024
        payload['limits']['visible_output_bytes'] = 128
    request_path = tmp_path/'request.json'
    request_path.write_text(json.dumps(payload))
    children = []
    real_popen = subprocess.Popen
    def launch(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    def cleanup():
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
    # These unit cases exercise the transport with known benign commands. Real namespace
    # protection is qualified separately; never issue kill(-1) in the test process.
    monkeypatch.setattr(sandbox_worker.os, 'getpid', lambda: 1)
    monkeypatch.setattr(sandbox_worker, '_kill_children', cleanup)
    monkeypatch.setattr(sandbox_worker.subprocess, 'Popen', launch)
    monkeypatch.chdir(workspace)
    sandbox_worker.execute(str(request_path))
    result = __import__('json').loads(capsys.readouterr().out)
    assert result['status'] == {'normal':'ok','timeout':'timeout','output':'output_limit'}[mode]
    snapshot = base64.b64decode(result['workspace'])
    assert read_workspace_file(snapshot, 'saved.txt', byte_limit=100000, file_limit=100) == b'previous state'
    if mode == 'normal':
        assert result['stdout'] == 'previous stateoutput' and result['stderr'] == 'error'
    elif mode == 'timeout':
        assert result['timed_out']
    else:
        assert result['output_truncated'] and len(result['stdout']) == 128


@pytest.mark.parametrize('mode', ['normal', 'identity', 'invalid', 'exit', 'timeout'])
def test_host_transport_preserves_workspace_on_incomplete_results(tmp_path, monkeypatch, mode):
    import json
    from src import sandboxed_shell_execution as module

    shell = SandboxedShell(ShellConfig('/bin/bwrap','/bin/systemd-run','/bin/systemctl','/runtime',str(tmp_path),limits()))
    state = write_workspace_file(b'', 'item.txt', b'retained', byte_limit=4096, file_limit=10)
    script = '''import base64,json,pathlib,sys,time
p=pathlib.Path(sys.argv[1]); mode=sys.argv[2]; req=json.loads((p/'request.json').read_text())
if mode=='exit': sys.stderr.write('service failed'); sys.exit(1)
if mode=='invalid': print('invalid JSON'); sys.exit(0)
if mode=='timeout': time.sleep(10)
print(json.dumps(dict(capture_id=req['capture_id'] if mode!='identity' else 'wrong',status='ok',exit_code=0,stdout='result',stderr='',timed_out=False,output_truncated=False,output_files=[],workspace=base64.b64encode((p/'workspace.tar').read_bytes()).decode())))'''
    monkeypatch.setattr(shell, 'command_argv', lambda directory, unit: [sys.executable,'-c',script,directory,mode])
    if mode == 'timeout':
        collect = shell._collect
        monkeypatch.setattr(shell,'_collect',lambda process,cap,timeout: collect(process,cap,0.1))
        monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs: None)
    result = shell.run('echo example', state)
    assert result.workspace == state
    assert result.status == ('ok' if mode == 'normal' else 'interrupted')
    if mode == 'normal':
        assert result.stdout == 'result'
    else:
        assert result.detail


@pytest.mark.parametrize('mode', ['absolute_link', 'parent_link', 'device', 'bad_archive', 'wrong_identity'])
def test_rejected_workspace_is_a_recoverable_rollback_after_identified_execution(tmp_path, monkeypatch, mode):
    shell = SandboxedShell(ShellConfig('/bin/bwrap','/bin/systemd-run','/bin/systemctl','/runtime',str(tmp_path),limits()))
    state = write_workspace_file(b'', 'saved.txt', b'previous', byte_limit=4096, file_limit=10)
    script = '''import base64,io,json,pathlib,sys,tarfile
p=pathlib.Path(sys.argv[1]); mode=sys.argv[2]; req=json.loads((p/'request.json').read_text())
sink=io.BytesIO()
with tarfile.open(fileobj=sink,mode='w:') as archive:
 info=tarfile.TarInfo('link')
 info.type=tarfile.CHRTYPE if mode=='device' else tarfile.SYMTYPE
 info.linkname='../outside' if mode=='parent_link' else '/outside'
 archive.addfile(info)
snapshot=b'invalid tar' if mode=='bad_archive' else sink.getvalue()
print(json.dumps(dict(capture_id='wrong' if mode=='wrong_identity' else req['capture_id'],status='ok',exit_code=0,stdout='completed output',stderr='diagnostic',timed_out=False,output_truncated=False,output_files=['new.log'],workspace=base64.b64encode(snapshot).decode())))'''
    monkeypatch.setattr(shell, 'command_argv', lambda directory, unit: [sys.executable,'-c',script,directory,mode])
    result = shell.run('create an unsupported workspace entry', state)
    assert result.workspace == state
    assert result.output_files == ()
    assert read_workspace_file(result.workspace, 'saved.txt', byte_limit=4096, file_limit=10) == b'previous'
    if mode == 'wrong_identity':
        assert result.status == 'interrupted' and result.exit_code is None
    else:
        assert result.status == 'workspace_rejected' and result.exit_code == 0
        assert result.stdout == 'completed output' and result.stderr == 'diagnostic'
        assert 'changes from this command were discarded' in result.detail


def test_edit_replaces_exact_text_and_preserves_other_members():
    args = dict(byte_limit=4096, file_limit=10)
    state = write_workspace_file(b'', 'notes/a.txt', 'alpha beta\ngamma\n'.encode(), **args)
    state = write_workspace_file(state, 'other.bin', b'\x00\xff', **args)
    before = read_workspace_file(state, 'other.bin', **args)
    updated, report = edit_workspace_file(state, 'notes/a.txt', 'beta', 'delta', **args)
    assert read_workspace_file(updated, 'notes/a.txt', **args) == 'alpha delta\ngamma\n'.encode()
    assert read_workspace_file(updated, 'other.bin', **args) == before
    assert read_workspace_file(state, 'notes/a.txt', **args) == 'alpha beta\ngamma\n'.encode()
    assert report == dict(path='notes/a.txt', replacements=1, previous_bytes=17, bytes_written=18)
    assert set(workspace_files(updated, **args)) == set(workspace_files(state, **args))


def test_edit_multiple_occurrences_requires_explicit_count():
    args = dict(byte_limit=4096, file_limit=10)
    state = write_workspace_file(b'', 'a.txt', b'x = 1\ny = 1\n', **args)
    with pytest.raises(WorkspaceEditError) as captured:
        edit_workspace_file(state, 'a.txt', '1', '2', **args)
    assert captured.value.code == 'occurrence_mismatch'
    updated, report = edit_workspace_file(state, 'a.txt', '1', '2', expected_occurrences=2, **args)
    assert read_workspace_file(updated, 'a.txt', **args) == b'x = 2\ny = 2\n' and report['replacements'] == 2


@pytest.mark.parametrize('name,old,new,count,code', [
    ('a.txt', 'absent', 'x', 1, 'text_not_found'),
    ('missing.txt', 'x', 'y', 1, 'not_a_file'),
    ('bin', 'x', 'y', 1, 'not_text'),
    ('a.txt', '', 'y', 1, 'old_text_empty'),
    ('a.txt', 'x', 'y', 0, 'occurrences_invalid'),
])
def test_edit_refusals_leave_snapshot_unchanged(name, old, new, count, code):
    args = dict(byte_limit=4096, file_limit=10)
    state = write_workspace_file(b'', 'a.txt', b'x = 1\n', **args)
    state = write_workspace_file(state, 'bin', b'\xff\xfe', **args)
    with pytest.raises(WorkspaceEditError) as captured:
        edit_workspace_file(state, name, old, new, expected_occurrences=count, **args)
    assert captured.value.code == code
    assert read_workspace_file(state, 'a.txt', **args) == b'x = 1\n'


def test_edit_respects_workspace_quota():
    args = dict(byte_limit=20, file_limit=10)
    state = write_workspace_file(b'', 'a.txt', b'short', **args)
    with pytest.raises(ValueError):
        edit_workspace_file(state, 'a.txt', 'short', 'x'*40, **args)


def test_read_lines_numbers_and_pages():
    args = dict(byte_limit=4096, file_limit=10)
    state = write_workspace_file(b'', 'a.py', b'one\ntwo\nthree\nfour\n', **args)
    first = read_workspace_lines(state, 'a.py', limit=2, **args)
    assert first == dict(path='a.py', offset=1, lines_returned=2, total_lines=4, truncated=True,
                         content='     1\tone\n     2\ttwo\n')
    rest = read_workspace_lines(state, 'a.py', offset=3, limit=10, **args)
    assert rest['content'] == '     3\tthree\n     4\tfour\n' and not rest['truncated']
    beyond = read_workspace_lines(state, 'a.py', offset=9, limit=2, **args)
    assert beyond['lines_returned'] == 0 and not beyond['truncated'] and beyond['content'] == ''


@pytest.mark.parametrize('name,offset,limit,code', [
    ('missing.py', 1, 5, 'not_a_file'), ('bin', 1, 5, 'not_text'), ('a.py', 0, 5, 'range_invalid'), ('a.py', 1, 0, 'range_invalid')])
def test_read_lines_refusals(name, offset, limit, code):
    args = dict(byte_limit=4096, file_limit=10)
    state = write_workspace_file(b'', 'a.py', b'x\n', **args)
    state = write_workspace_file(state, 'bin', b'\xff\xfe', **args)
    with pytest.raises(WorkspaceEditError) as captured:
        read_workspace_lines(state, name, offset=offset, limit=limit, **args)
    assert captured.value.code == code
