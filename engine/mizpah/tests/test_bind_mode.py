"""Bind mode through the worker's plumbing: the project directory is /work, a cache larger than the snapshot
cap persists outside evidence, `.terra` still travels as a tar and lands through write-back, and a
re-measurement runs detached with the cache read-only. Needs the real sandbox (bwrap + systemd-run)."""
from __future__ import annotations

import json
import os
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
    assert shell.config.workspace_dir == str(project.resolve()) and shell.config.state_dirs == worker.state_dirs(project)
    initial = worker.pack_workspace(project, only=worker.state_dirs(project))
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


def _terra(project: Path, *args: str) -> str:
    return subprocess.run([str(ROOT.parent.parent/'.venv'/'bin'/'terra'), *args], cwd=project, capture_output=True, text=True).stdout


def _first_json(text: str):
    """The first JSON document in a CLI's stdout (some verbs print a note before or after it)."""
    starts = [i for i in (text.find('{'), text.find('[')) if i >= 0]
    return json.JSONDecoder().raw_decode(text[min(starts):])[0]


def _known_of_file(project: Path, kid: str, path: str) -> None:
    """A number known counting the lines of `path`, depending on the file."""
    _terra(project, 'unknown', 'create', kid, '--claim', 'lines in '+path, '--evidence', 'count', '--type', 'number', '--quantity', kid)
    _terra(project, 'probe', 'create', kid+'_probe', '--purpose', 'count lines of '+path, '--kind', 'run', '--measure', kid)
    (project/'.terra'/'map'/'probes'/(kid+'_probe')/'measure.py').write_text(
        'def measure(ctx):\n    return {"'+kid+'": sum(1 for _ in open("'+path+'"))}\n')
    run_id = _first_json(_terra(project, 'probe', 'run', kid+'_probe', '--to', '{"kind":"file"}', '--json'))['id']
    _terra(project, 'unknown', 'link-run', kid, run_id)
    _terra(project, 'unknown', 'graduate', kid)
    _terra(project, 'known', 'depend', kid, '--on', 'file:'+path)


def test_the_host_refreshes_a_stale_known_whose_reading_reproduces_and_reports_one_that_moved(tmp_path: Path) -> None:
    config = worker.load_config(ROOT/'config.luna.json')
    config['mizpah']['sandbox'] = dict(config['mizpah']['sandbox'], workspace='bind', cache_dirs=[], services=None, network=None,
                                       share_network=False)
    config['shell']['limits'] = dict(config['shell']['limits'], workspace_bytes=4*1024**2, max_files=2000)
    project = tmp_path/'proj'
    project.mkdir()
    _terra(project, 'init')
    (project/'same.txt').write_text('a\nb\nc\n')
    (project/'grew.txt').write_text('a\nb\nc\n')
    _known_of_file(project, 'same_lines', 'same.txt')
    _known_of_file(project, 'grew_lines', 'grew.txt')
    # The task rewrote both files: one keeps its line count, one does not.
    (project/'same.txt').write_text('x\ny\nz\n')
    (project/'grew.txt').write_text('a\nb\nc\nd\n')
    rows = {r['id']: r for r in _first_json(_terra(project, 'known', 'list', '--json'))}
    assert rows['same_lines']['stale'] and rows['grew_lines']['stale']
    root = tmp_path/'sess'
    root.mkdir()
    out = worker.refresh_stale(config, project, root)
    assert out['refreshed'] == ['same_lines'], out
    assert len(out['changed']) == 1 and out['changed'][0].startswith('grew_lines = 3') and 'reads 4' in out['changed'][0]
    rows = {r['id']: r for r in _first_json(_terra(project, 'known', 'list', '--json'))}
    assert not rows['same_lines']['stale'] and rows['same_lines']['record']['stats']['n'] == 2
    assert rows['grew_lines']['stale']
    assert (root/'refresh.jsonl').exists()


def test_a_bind_session_seeds_its_state_part_and_finds_it_again_after_open(tmp_path: Path) -> None:
    """The state tar (the project's .mizpah, the worker's .playbook) must reach the directory at create and
    survive an open: every bind-mode session started with an empty state tree before (2026-09-20)."""
    from cg.bp_focused_agent_session_python.src.focused_agent_session import FocusedSession, workspace_files
    config = worker.load_config(ROOT/'config.luna.json')
    config['mizpah']['sandbox'] = dict(config['mizpah']['sandbox'], workspace='bind', cache_dirs=[], services=None, network=None,
                                       share_network=False)
    config['shell']['limits'] = dict(config['shell']['limits'], workspace_bytes=4*1024**2, max_files=2000)
    project = tmp_path/'proj'
    (project/'.mizpah'/'map').mkdir(parents=True)
    (project/'.mizpah'/'brief.json').write_text('{"title": "reference"}')
    (project/'a.py').write_text('x = 1\n')
    root = tmp_path/'sess'
    root.mkdir()
    model, _, shell = worker.bindings(config, root, 'm1', checkins=False, project=project)
    settings = worker.build_settings(config, 'assignment', 'reference', [])
    initial = worker.pack_workspace(project, only=worker.state_dirs(project))
    session = FocusedSession.create(root/'s', settings, worker=model, shell=shell, controller=None, initial_workspace=initial)
    assert workspace_files(session.workspace().state, byte_limit=10**8, file_limit=10**5) == ('.mizpah/brief.json',)
    result = shell.run('cat /work/.mizpah/brief.json', session.workspace())
    assert result.status == 'ok' and 'reference' in result.stdout, (result.status, result.stderr)
    shell.close()
    model2, _, shell2 = worker.bindings(config, root, 'm1', checkins=False, project=project)
    reopened = FocusedSession.open(root/'s', worker=model2, shell=shell2, controller=None)
    assert workspace_files(reopened.workspace().state, byte_limit=10**8, file_limit=10**5) == ('.mizpah/brief.json',)
    shell2.close()


def test_pack_workspace_drops_symlinks_that_leave_the_tree(tmp_path: Path) -> None:
    """A venv under the project (not on the cache list) carries bin/python -> an absolute interpreter; the packed
    tar must not carry it, or the sandbox refuses every member (score-video's re-measure, 2026-09-20)."""
    import io
    import tarfile
    project = tmp_path/'proj'
    (project/'.mizpah').mkdir(parents=True)
    (project/'.mizpah'/'brief.json').write_text('{}')
    (project/'venv-x'/'bin').mkdir(parents=True)
    (project/'venv-x'/'bin'/'python').symlink_to('/usr/bin/python3')
    (project/'venv-x'/'lib64').symlink_to('lib')
    (project/'src').mkdir()
    (project/'src'/'a.py').write_text('x = 1\n')
    os.link(project/'src'/'a.py', project/'src'/'a_link.py')   # a hard link, as uv makes site-packages
    (project/'env2').mkdir()
    (project/'env2'/'pyvenv.cfg').write_text('home = /usr/bin\n')
    (project/'env2'/'big.txt').write_text('x'*100)
    data = worker.pack_workspace(project)
    names = {m.name: m for m in tarfile.open(fileobj=io.BytesIO(data), mode='r:')}
    assert 'src/a.py' in names and 'venv-x/lib64' in names and names['venv-x/lib64'].issym()
    assert 'venv-x/bin/python' not in names
    assert names['src/a_link.py'].isfile() and names['src/a_link.py'].size == 6   # regular, not a link entry
    assert not any(n.startswith('env2') for n in names)   # a venv by any name is environment, not evidence
