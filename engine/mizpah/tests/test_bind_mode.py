"""Bind mode through the worker's plumbing: the project directory is /work, a cache larger than the snapshot
cap persists outside evidence, `.terra` still travels as a tar and lands through write-back, and a
re-measurement runs detached with the cache read-only. Needs the real sandbox (bwrap + systemd-run)."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT.parent.parent))

from mizpah import worker  # noqa: E402

pytestmark = pytest.mark.skipif(not (Path('/usr/bin/bwrap').exists() and Path('/usr/bin/systemd-run').exists()),
                                reason='needs bubblewrap and systemd-run')


def test_bind_mode_binds_the_project_keeps_caches_and_writes_state_back(tmp_path: Path) -> None:
    config = worker.load_config(ROOT/'config.luna.json')
    config['mizpah']['sandbox'] = dict(config['mizpah']['sandbox'], workspace='bind', cache_dirs=['build'], services=None, network=None,
                                       share_network=False)
    config['shell']['limits'] = dict(config['shell']['limits'], workspace_bytes=4*1024**2, max_files=2000)
    project = tmp_path/'proj'
    (project/'.terra').mkdir(parents=True)
    (project/'.terra'/'brief.json').write_text('{"title": "reference"}')
    (project/'src').mkdir()
    (project/'src'/'a.py').write_text('x = 1\n')
    root = tmp_path/'sess'
    root.mkdir()
    _, _, shell = worker.bindings(config, root, 'm1', checkins=False, project=project)
    assert shell.config.workspace_dir == str(project.resolve()) and shell.config.state_dirs == worker.STATE_DIRS
    initial = worker.pack_workspace(project, only=worker.STATE_DIRS)
    from cg.infra_sandboxed_shell_execution_python.src.sandboxed_shell_execution import workspace_files
    assert workspace_files(initial, byte_limit=10**8, file_limit=10**5) == ('.terra/brief.json',)
    try:
        r = shell.run('cat .terra/brief.json; mkdir -p build; dd if=/dev/zero of=build/big.bin bs=1M count=8 status=none; '
                      'echo "y = 2" > src/b.py; f=.terra/brie; f="$f"f.json; echo \'{"title": "tampered"}\' > "$f"; '
                      'mkdir -p .terra/notes; echo note > .terra/notes/n.txt', initial)
        assert r.status == 'ok', (r.status, r.detail, r.stderr)
        ws = r.workspace
        # The tree changed in place; the cache is there and bigger than the snapshot cap; `.terra` did not land.
        assert (project/'src'/'b.py').read_text() == 'y = 2\n' and (project/'build'/'big.bin').stat().st_size == 8*1024**2
        assert json.loads((project/'.terra'/'brief.json').read_text()) == {'title': 'reference'}
        # Write-back reads the state part and applies the map's entitlements: the brief is never written.
        written = worker.writeback(worker.state_of(ws), project, dict(id='t', map_id='k'), 'm1')
        assert json.loads((project/'.terra'/'brief.json').read_text()) == {'title': 'reference'}
        assert not any(w.endswith('brief.json') for w in written)
        # Evidence for harvest: the tree plus the state, without the cache.
        names = set(workspace_files(shell.snapshot(), byte_limit=10**8, file_limit=10**5))
        assert 'src/b.py' in names and 'src/a.py' in names
        assert not any(n.startswith('build') for n in names)
        # A detached run (what re-measurement does) sees the cache read-only and changes nothing.
        r = shell.run('ls build; echo z > src/c.py; touch build/x', ws, detached=True)
        assert 'big.bin' in r.stdout and not (project/'src'/'c.py').exists() and not (project/'build'/'x').exists()
    finally:
        shell.close()


def test_open_walks_names_the_next_unticked_step() -> None:
    import io, tarfile
    text = '# build-page — for: the landing page\n- [x] Read\n- [ ] Gutters: check the gutters.\n- [ ] Report\n'
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:') as tar:
        info = tarfile.TarInfo(worker.PLAYBOOK_PREFIX+'/open/build-page--x.md'); data = text.encode(); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    assert worker.open_walks(buf.getvalue()) == [worker.PLAYBOOK_PREFIX+'/open/build-page--x.md: 1/3 ticked; next: Gutters: check the gutters.']
