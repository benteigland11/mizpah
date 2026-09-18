"""Private entrypoint which refuses to run outside its isolated PID namespace."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tarfile
import time


def execute(request_path: str) -> None:
    """Execute inside the namespace only; return a bounded workspace snapshot."""
    if os.getpid() != 1:
        raise RuntimeError('The sandbox worker must be isolated PID 1')
    request = json.loads(Path(request_path).read_text())
    limits = request['limits']
    work = Path.cwd()
    initial = Path(request['workspace_path']).read_bytes()
    if initial:
        with tarfile.open(fileobj=io.BytesIO(initial), mode='r:') as archive:
            archive.extractall(work, filter='data')
    captured = [bytearray(), bytearray()]
    timed_out = truncated = False
    process = subprocess.Popen([request['shell'], '--noprofile', '--norc', '-c', request['command']],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, close_fds=True)
    deadline = time.monotonic()+request['timeout']
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, 0)
        selector.register(process.stderr, selectors.EVENT_READ, 1)
        while selector.get_map():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            for key, _ in selector.select(timeout=min(0.1, max(0, deadline-time.monotonic()))):
                chunk = key.fileobj.read1(65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    remaining = limits['output_bytes']-sum(map(len, captured))
                    captured[key.data].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated = True
                        break
            if truncated:
                break
            if process.poll() is not None:
                # Background descendants may hold the pipes open. Reap them now.
                _kill_children()
        _kill_children()
    process.wait()
    while True:
        try:
            child, _ = os.waitpid(-1, os.WNOHANG)
            if not child:
                break
        except ChildProcessError:
            break
    log_root = work/'.tool-output'
    log_root.mkdir(exist_ok=True)
    paths = []
    for name, content in zip(('stdout', 'stderr'), captured):
        target = log_root/(request['capture_id']+'.'+name)
        target.write_bytes(content)
        paths.append(str(target.relative_to(work)))
    sink = io.BytesIO()
    dropped = []
    ignore = set(request.get('snapshot_ignore') or ())
    with tarfile.open(fileobj=sink, mode='w:', dereference=False) as archive:
        for target in sorted(work.rglob('*')):
            if ignore and any(part in ignore for part in target.relative_to(work).parts):
                continue
            if target.is_symlink():
                # A link that leaves the workspace (a venv's bin/python -> /usr/bin/python3)
                # cannot persist; drop it rather than reject every change the command made.
                link = os.readlink(target)
                if os.path.isabs(link) or '..' in link.split('/'):
                    dropped.append(str(target.relative_to(work)))
                    continue
            # Store hardlinked regular files independently, so the host never follows archive links.
            info = archive.gettarinfo(str(target), arcname=str(target.relative_to(work)))
            if target.is_file() and not target.is_symlink():
                info.type = tarfile.REGTYPE
                info.linkname = ''
                info.size = target.stat().st_size
                with target.open('rb') as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)
    response = dict(capture_id=request['capture_id'], status='timeout' if timed_out else 'output_limit' if truncated else 'ok',
                    exit_code=process.returncode, timed_out=timed_out, output_truncated=truncated or any(len(value)>limits['visible_output_bytes'] for value in captured),
                    stdout=captured[0][:limits['visible_output_bytes']].decode(errors='replace'),
                    stderr=captured[1][:limits['visible_output_bytes']].decode(errors='replace'),
                    output_files=paths, workspace=base64.b64encode(sink.getvalue()).decode(),
                    detail=('dropped %d symlink(s) that leave the workspace: %s' % (len(dropped), ', '.join(dropped[:5])))
                    if dropped else '')
    sys.stdout.write(json.dumps(response, ensure_ascii=True)+'\n')
    sys.stdout.flush()


def _kill_children() -> None:
    # Linux excludes both the caller and namespace PID 1 from kill(-1).
    try:
        os.kill(-1, signal.SIGKILL)
    except ProcessLookupError:
        pass
