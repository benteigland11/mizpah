"""A base is an environment several gyms share: bound read-only at its own path, env set, nothing written
into it. Needs the real sandbox for the last test."""
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

from mizpah import bases, init as init_module, worker  # noqa: E402


@pytest.fixture
def data_home(tmp_path: Path, monkeypatch):
    # Only the bases root moves: the provider credentials stay where they are. It lives under the home
    # directory, not /tmp: the sandbox mounts its own /tmp over a bind made there.
    import shutil, tempfile
    home = Path(tempfile.mkdtemp(prefix='mizpah-bases-', dir=Path.home()/'.cache'))
    root = home/'bases'
    root.mkdir()
    monkeypatch.setattr(bases, 'bases_root', lambda: root)
    yield home
    shutil.rmtree(home, ignore_errors=True)


def test_a_base_is_created_listed_and_applied_to_a_sandbox_config(data_home: Path) -> None:
    made = bases.create('orchestra', note='a synth and a soundfont')
    folder = Path(made['path'])
    (folder/'venv'/'bin').mkdir(parents=True)
    (folder/'soundfonts').mkdir()
    (folder/'base.json').write_text(json.dumps(dict(name='orchestra', note='a synth and a soundfont',
                                                     env={'SOUNDFONT': '$BASE/soundfonts/gm.sf2'})))
    assert [b['name'] for b in bases.list_bases()] == ['orchestra']
    with pytest.raises(FileExistsError):
        bases.create('orchestra')
    with pytest.raises(ValueError):
        bases.create('Bad Name')
    with pytest.raises(FileNotFoundError):
        bases.load('nope')

    config = {'mizpah': {'sandbox': {'read_only_binds': ['/sys'], 'environment': {'PATH': '/x/bin:/usr/bin'}}}}
    bases.apply(config, 'orchestra')
    sandbox = config['mizpah']['sandbox']
    assert sandbox['read_only_binds'] == ['/sys', str(folder)]
    assert sandbox['environment']['PATH'] == str(folder/'venv'/'bin')+':/x/bin:/usr/bin'
    assert sandbox['environment']['SOUNDFONT'] == str(folder/'soundfonts'/'gm.sf2')
    assert sandbox['environment']['MIZPAH_BASE'] == str(folder)
    text = bases.enabler_text(config)
    assert 'base "orchestra"' in text and 'a synth and a soundfont' in text and 'read-only' in text
    assert bases.enabler_text({'mizpah': {}}) == ''


def test_a_gym_declares_its_base_and_the_project_config_carries_it(data_home: Path, tmp_path: Path) -> None:
    bases.create('tools', note='tools')
    project = tmp_path/'gym'
    (project/'.mizpah').mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        init_module.set_base(project, 'missing')
    pc = init_module.set_base(project, 'tools')
    assert pc['base'] == 'tools' and json.loads((project/'.mizpah'/'config.json').read_text())['base'] == 'tools'
    config = {'mizpah': {'sandbox': {'read_only_binds': [], 'environment': {}}}}
    init_module.apply_project_config(config, project)
    assert config['mizpah']['base']['name'] == 'tools'
    assert config['mizpah']['sandbox']['read_only_binds'] == [str(bases.path_of('tools'))]
    init_module.set_base(project, None)
    assert 'base' not in json.loads((project/'.mizpah'/'config.json').read_text())


@pytest.mark.skipif(not (Path('/usr/bin/bwrap').exists() and Path('/usr/bin/systemd-run').exists()),
                    reason='needs bubblewrap and systemd-run')
def test_the_sandbox_sees_the_base_read_only_with_its_venv_first(data_home: Path, tmp_path: Path) -> None:
    made = bases.create('py', note='a venv')
    folder = Path(made['path'])
    subprocess.run([sys.executable, '-m', 'venv', str(folder/'venv')], check=True)
    (folder/'soundfonts').mkdir()
    (folder/'soundfonts'/'gm.sf2').write_bytes(b'not really')
    (folder/'base.json').write_text(json.dumps(dict(name='py', note='a venv', env={'SOUNDFONT': '$BASE/soundfonts/gm.sf2'})))
    config = worker.load_config(ROOT/'config.luna.json')
    config['mizpah']['sandbox'] = dict(config['mizpah']['sandbox'], workspace='bind', cache_dirs=[], services=None,
                                       network=None, share_network=False)
    project = tmp_path/'gym'
    (project/'.mizpah').mkdir(parents=True)
    (project/'.mizpah'/'brief.json').write_text('{"title": "t"}')
    init_module.set_base(project, 'py')
    init_module.apply_project_config(config, project)
    root = tmp_path/'sess'/'tasks'/'t1'
    root.mkdir(parents=True)
    _, _, shell = worker.bindings(config, root, 'm1', checkins=False, project=project)
    try:
        r = shell.run('echo "$MIZPAH_BASE"; command -v python; ls "$SOUNDFONT"; touch "$MIZPAH_BASE/x" 2>&1; echo rc=$?; '
                      'echo mine > here.txt', worker.pack_workspace(project, only=worker.state_dirs(project)))
        assert r.status == 'ok', (r.status, r.detail, r.stderr)
        assert r.stdout.splitlines()[0] == str(folder)
        assert r.stdout.splitlines()[1] == str(folder/'venv'/'bin'/'python')
        assert str(folder/'soundfonts'/'gm.sf2') in r.stdout
        assert 'Read-only file system' in r.stdout and 'rc=1' in r.stdout
        assert (project/'here.txt').read_text() == 'mine\n' and not (folder/'x').exists()
    finally:
        shell.close()
