import io
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.sandboxed_shell_execution import (
    ServiceLimits, ShellConfig, ShellLimits, SandboxedShell, WorkspaceEditError, edit_workspace_file, read_workspace_file, read_workspace_lines,
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


def test_launch_contract_adds_read_only_binds_and_environment(tmp_path):
    config = ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                         read_only_binds=('/opt/toolchain', '/home/example_org/project'),
                         environment={'PATH': '/opt/toolchain/bin', 'EXAMPLE_HOME': '/home/example_org'})
    argv = SandboxedShell(config).command_argv(str(tmp_path), 'example-unit')
    assert '--bind' not in argv
    for bind in config.read_only_binds:
        index = argv.index(bind)
        assert argv[index-1] == '--ro-bind' and argv[index+1] == bind
    assert argv[argv.index('PATH')+1] == '/opt/toolchain/bin:/usr/bin'
    assert argv[argv.index('EXAMPLE_HOME')+1] == '/home/example_org'
    assert argv.index('EXAMPLE_HOME') < argv.index('--chdir')
    for bad in ('relative/path', '/usr', '/work', '/tmp'):
        with pytest.raises(ValueError):
            ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                        read_only_binds=(bad,))
    with pytest.raises(ValueError):
        ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                    environment={'A=B': 'x'})


def test_launch_contract_keeps_the_network_only_when_asked(tmp_path):
    base = ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits())
    assert '--unshare-all' in SandboxedShell(base).command_argv(str(tmp_path), 'example-unit')
    resolv = tmp_path/'resolv.conf'
    resolv.write_text('nameserver 127.0.0.53\n')
    shared = ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                         share_network=True, network_files=(str(resolv), str(tmp_path/'absent')))
    argv = SandboxedShell(shared).command_argv(str(tmp_path), 'example-unit')
    assert '--unshare-all' not in argv and '--unshare-net' not in argv
    for flag in ('--unshare-user', '--unshare-ipc', '--unshare-pid', '--unshare-uts', '--unshare-cgroup'):
        assert flag in argv
    index = argv.index(str(resolv))
    assert argv[index-1] == '--ro-bind' and argv[index+1] == str(resolv)
    assert str(tmp_path/'absent') not in argv and '--bind' not in argv
    with pytest.raises(ValueError):
        ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                    network_files=('relative',))


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
    assert report == dict(path='notes/a.txt', replacements=1, previous_bytes=17, bytes_written=18, matched='exact')
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


def test_worker_drops_symlinks_that_leave_the_workspace(tmp_path, monkeypatch, capsys):
    """A venv's bin/python -> /usr/bin/python3 must not reject the whole snapshot."""
    import base64
    import json as _json
    import os
    import shutil
    import signal
    import subprocess
    from src import sandbox_worker
    work = tmp_path/'work'
    work.mkdir()
    (tmp_path/'empty.tar').write_bytes(b'')
    request = tmp_path/'request.json'
    request.write_text(_json.dumps(dict(
        command="mkdir -p v/bin .venv/bin pkg/__pycache__ && ln -s /usr/bin/python3 v/bin/python && ln -s ../x v/up && ln -s bin v/here && echo hi > v/file && echo x > .venv/bin/python && echo y > pkg/__pycache__/m.pyc && echo z > pkg/m.py",
        timeout=5, limits=limits().__dict__, shell=shutil.which('bash'), capture_id='cid', workspace_path=str(tmp_path/'empty.tar'),
        snapshot_ignore=['.venv', '__pycache__'])))
    children = []
    real_popen = subprocess.Popen
    def launch(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(sandbox_worker.os, 'getpid', lambda: 1)
    monkeypatch.setattr(sandbox_worker, '_kill_children', lambda: [os.killpg(c.pid, signal.SIGKILL) for c in children if c.poll() is None])
    monkeypatch.setattr(sandbox_worker.subprocess, 'Popen', launch)
    monkeypatch.chdir(work)
    sandbox_worker.execute(str(request))
    response = _json.loads(capsys.readouterr().out)
    names = [m.name for m in tarfile.open(fileobj=io.BytesIO(base64.b64decode(response['workspace'])), mode='r:')]
    assert 'v/file' in names and 'v/here' in names
    assert 'v/bin/python' not in names and 'v/up' not in names
    assert 'dropped 2 symlink(s)' in response['detail']
    assert 'pkg/m.py' in names and not any('.venv' in n or '__pycache__' in n for n in names)


def test_edit_forgives_the_anchor_encoding_but_not_its_content():
    snapshot = write_workspace_file(b'', 'm.py', b'def f(x):\n    """Doc."""\n    return x\n', byte_limit=4096, file_limit=10)
    # the model's own JSON escapes left in the string: \\n and \\" as literal characters
    literal = 'def f(x):\\n    \\"\\"\\"Doc.\\"\\"\\"\\n    return x'
    out, report = edit_workspace_file(snapshot, 'm.py', literal, 'def f(x):\\n    return x * 2', byte_limit=4096, file_limit=10)
    assert report['matched'] == 'unescaped'
    assert read_workspace_file(out, 'm.py', byte_limit=4096, file_limit=10) == b'def f(x):\n    return x * 2\n'
    # leading/trailing whitespace per line forgiven; the exact span is what gets replaced
    out, report = edit_workspace_file(snapshot, 'm.py', '"""Doc."""\nreturn x', 'return x + 1', byte_limit=4096, file_limit=10)
    assert report['matched'] == 'whitespace'
    assert read_workspace_file(out, 'm.py', byte_limit=4096, file_limit=10) == b'def f(x):\n    return x + 1\n'
    with pytest.raises(WorkspaceEditError) as error:
        edit_workspace_file(snapshot, 'm.py', 'return y', 'z', byte_limit=4096, file_limit=10)
    assert error.value.code == 'text_not_found'
    exact, report = edit_workspace_file(snapshot, 'm.py', '    return x\n', '    return 0\n', byte_limit=4096, file_limit=10)
    assert report['matched'] == 'exact'


def test_workspace_paths_accept_the_mount_and_refuse_the_dot_work_lookalike():
    from src.sandboxed_shell_execution import _name
    import pytest
    assert _name('/work/.terra/map/probes/p/measure.py', user=True) == '.terra/map/probes/p/measure.py'
    assert _name('weather/cli.py', user=True) == 'weather/cli.py'
    with pytest.raises(ValueError, match='outside it'):
        _name('/etc/passwd', user=True)
    with pytest.raises(ValueError, match='not the workspace'):
        _name('.work/.terra/map/probes/p/measure.py', user=True)
    assert _name('.work/old.txt') == '.work/old.txt'   # an existing snapshot member still reads back


def services():
    return ServiceLimits(512*1024**2, 64, 600, 2, 120)


def test_services_need_the_shared_network_and_change_the_command_contract(tmp_path):
    with pytest.raises(ValueError):
        ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(), services=services())
    plain = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                                       share_network=True))
    argv = plain.command_argv(str(tmp_path), 'unit-a')
    assert '/svc' not in argv and '/runner' not in argv[argv.index('PATH')+1]
    shell = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                                       share_network=True, services=services()))
    argv = shell.command_argv(str(tmp_path), 'unit-b')
    # Commands see every service's records read-only, find `svc` on PATH, and still get no writable host mount.
    triples = [tuple(argv[i:i+3]) for i in range(len(argv)-2)]
    assert ('--ro-bind', str(tmp_path/'services'), '/svc') in triples and ('--setenv', 'SVC_ROOT', '/svc') in triples
    assert argv[argv.index('PATH')+1].endswith('/runner') and '--bind' not in argv
    with pytest.raises(ValueError):
        plain.service_argv('web', 'unit-c')
    service = shell.service_argv('web', 'unit-c')
    # A service: no --wait/--pipe, its own lifetime and memory, the workspace mirror and its own directory writable.
    assert '--wait' not in service and '--pipe' not in service
    assert '--property=RuntimeMaxSec=600' in service and '--property=MemoryMax='+str(512*1024**2) in service
    triples = [tuple(service[i:i+3]) for i in range(len(service)-2)]
    assert ('--bind', str(tmp_path/'services'/'web'/'work'), '/work') in triples
    assert ('--bind', str(tmp_path/'services'/'web'), '/svc/web') in triples
    assert 'PYTHONUNBUFFERED' in service


def test_service_requests_are_carried_out_after_the_command_and_removed_from_the_snapshot(tmp_path, monkeypatch):
    shell = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                                       share_network=True, services=services()))
    seen = []
    monkeypatch.setattr(shell, 'start_service', lambda name, command, workspace: seen.append(('start', name, command)) or 'svc: started '+name)
    monkeypatch.setattr(shell, 'stop_service', lambda name: seen.append(('stop', name)) or 'svc: stopped '+name)
    ws = write_workspace_file(b'', 'index.html', b'hi', byte_limit=10**6, file_limit=100)
    ws = write_workspace_file(ws, '.svc/requests/1.json', b'{"op": "start", "name": "web", "command": "python3 -m http.server"}',
                              byte_limit=10**6, file_limit=100)
    ws = write_workspace_file(ws, '.svc/requests/2.json', b'{"op": "stop", "name": "web"}', byte_limit=10**6, file_limit=100)
    ws = write_workspace_file(ws, '.svc/requests/3.json', b'{"op": "start", "name": "../x", "command": "true"}', byte_limit=10**6, file_limit=100)
    from src.sandboxed_shell_execution import ShellResult
    result = shell._service_requests(ShellResult('ok', 0, 'command output', '', False, False, ws, (), 0.1))
    assert seen == [('start', 'web', 'python3 -m http.server'), ('stop', 'web')]
    assert result.stdout.splitlines() == ['command output', 'svc: started web', 'svc: stopped web',
                                          'svc: bad request .svc/requests/3.json: a service name is a short lowercase identifier']
    assert workspace_files(result.workspace, byte_limit=10**6, file_limit=100) == ('index.html',)


@pytest.mark.skipif(not (Path('/usr/bin/bwrap').exists() and Path('/usr/bin/systemd-run').exists()),
                    reason='needs bwrap and a user systemd')
def test_a_service_outlives_commands_and_sees_the_workspace_as_of_each_command(tmp_path):
    shell = SandboxedShell(ShellConfig('/usr/bin/bwrap', '/usr/bin/systemd-run', '/usr/bin/systemctl', '/usr', str(tmp_path),
                                       ShellLimits(1024**3, 128*1024**2, 64*1024**2, 1024**2, 262144, 64, 200, 60, 5, 10000),
                                       share_network=True, services=services()))
    port = 8700+(hash(str(tmp_path)) % 200)
    ws = b''
    def run(command):
        nonlocal ws
        result = shell.run(command, ws)
        ws = result.workspace
        return result.stdout
    try:
        out = run('echo hello > index.html; svc start web -- python3 -m http.server %d --bind 127.0.0.1' % port)
        assert 'svc: started web' in out
        assert 'matched' in run("svc wait web --for 'Serving HTTP' --max 30")
        assert run('curl -s http://127.0.0.1:%d/index.html' % port).strip() == 'hello'
        run('echo changed > index.html')
        assert run('curl -s http://127.0.0.1:%d/index.html' % port).strip() == 'changed'
        assert 'GET /index.html' in run('svc logs web -n 5')
        assert 'already running' in run('svc start web -- true')
        assert 'svc: stopped web' in run('svc stop web')
        assert '.svc' not in ' '.join(workspace_files(ws, byte_limit=10**8, file_limit=10000))
    finally:
        shell.stop_all()


def test_a_command_naming_a_refused_path_is_rejected_unexecuted(tmp_path):
    shell = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                                       refused_paths=('/opt/toolchain/src',)))
    result = shell.run('sed -n 1,40p /opt/toolchain/src/cli.py', b'')
    assert result.status == 'rejected' and result.exit_code is None and 'not your workspace' in result.detail
    with pytest.raises(ValueError):
        ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(), refused_paths=('relative',))


def test_every_scope_carries_the_kernel_hardening(tmp_path):
    from src.sandboxed_shell_execution import HARDENING_PROPERTIES
    shell = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                                       share_network=True, services=services()))
    for argv in (shell.command_argv(str(tmp_path), 'unit-h'), shell.service_argv('web', 'unit-s')):
        assert all(prop in argv for prop in HARDENING_PROPERTIES)
        assert argv.index('--property=NoNewPrivileges=yes') < argv.index('/bin/bwrap')
    assert any('io_uring_setup' in p and '@debug' in p and '@mount' not in p for p in HARDENING_PROPERTIES)


def test_a_command_matching_a_refused_pattern_is_rejected_with_its_reason(tmp_path):
    shell = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                                       refused_patterns=((r'--skip-gate\b', 'the gate is not yours to skip'),
                                                         (r'>\s*\.terra/brief\.json', 'the brief moves by proposal'))))
    assert shell.run('terra route complete t --skip-gate "x"', b'').detail == 'refused: the gate is not yours to skip'
    assert shell.run("echo '{}' > .terra/brief.json", b'').status == 'rejected'
    with pytest.raises(ValueError):
        ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(), refused_patterns=(('(', 'bad'),))


def test_a_service_log_past_its_cap_keeps_only_its_tail(tmp_path):
    shell = SandboxedShell(ShellConfig('/bin/bwrap', '/bin/systemd-run', '/bin/systemctl', '/runtime', str(tmp_path), limits(),
                                       share_network=True, services=ServiceLimits(512*1024**2, 64, 600, 2, 120, log_bytes=1000)))
    home = shell.services_root/'web'
    home.mkdir(parents=True)
    (home/'log').write_bytes(b'x'*2000+b'END')
    shell._services['web'] = dict(unit='u', command='c', started_at=0)
    shell._cap_logs()
    data = (home/'log').read_bytes()
    assert data.startswith(b'[log head dropped') and data.endswith(b'END') and len(data) < 1100
