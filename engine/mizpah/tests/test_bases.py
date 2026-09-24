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
    (folder/'venv'/'lib'/'python3.12'/'site-packages').mkdir(parents=True)
    bases.apply(config, 'orchestra')
    assert config['mizpah']['sandbox']['environment']['PYTHONPATH'] == str(folder/'venv'/'lib'/'python3.12'/'site-packages')
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
    config = worker.load_config(ROOT/'config.openai.json')
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


def test_the_brief_names_the_environment_and_the_host_resolves_it(data_home: Path, tmp_path: Path) -> None:
    """The brief is the authority for the gym environment; `base` in the project config only counts when the
    brief names nothing. A draft refuses a name that is not saved; so does authorize."""
    from mizpah import draft
    bases.create('piano', note='a piano studio')
    project = tmp_path/'gym'
    (project/'.mizpah').mkdir(parents=True)
    (project/'.mizpah'/'brief.json').write_text(json.dumps(dict(title='t', status='draft', environment='piano')))
    config = {'mizpah': {'sandbox': {'read_only_binds': [], 'environment': {}}}}
    init_module.apply_project_config(config, project)
    assert config['mizpah']['base']['name'] == 'piano'
    assert init_module.project_environment(project) == 'piano'
    # The brief wins over an older project-config base.
    init_module.set_base(project, 'piano')
    bases.create('other', note='other')
    (project/'.mizpah'/'brief.json').write_text(json.dumps(dict(title='t', status='draft', environment='other')))
    config = {'mizpah': {'sandbox': {'read_only_binds': [], 'environment': {}}}}
    init_module.apply_project_config(config, project)
    assert config['mizpah']['base']['name'] == 'other'
    # A brief that names nothing falls back to the project config; one naming an unknown environment is refused.
    (project/'.mizpah'/'brief.json').write_text(json.dumps(dict(title='t', status='draft', environment='')))
    config = {'mizpah': {'sandbox': {'read_only_binds': [], 'environment': {}}}}
    init_module.apply_project_config(config, project)
    assert config['mizpah']['base']['name'] == 'piano'
    assert draft.environment_exists('piano') and not draft.environment_exists('orchestra')
    assert [e['name'] for e in draft.environments()] == ['bare', 'other', 'piano']   # bare: the default, made on first listing
    (project/'.mizpah'/'brief.json').write_text(json.dumps(dict(title='t', status='draft', environment='orchestra')))
    with pytest.raises(SystemExit) as stop:
        draft.authorize(project)
    assert 'orchestra' in str(stop.value) and 'no saved environment' in str(stop.value)


def test_a_run_freezes_its_base_and_edits_after_start_do_not_reach_it(data_home: Path) -> None:
    folder = Path(bases.create('studio', note='a synth')['path'])
    (folder/'tool.sh').write_text('echo one\n')
    frozen = bases.snapshot('studio')
    # An editor saving in place after the run started: the frozen copy keeps what the run began with.
    with open(folder/'tool.sh', 'r+') as handle:
        handle.write('echo two\n')
    assert (frozen/'tool.sh').read_text() == 'echo one\n'
    config = {'mizpah': {'sandbox': {'read_only_binds': [str(folder)]}}}
    bases.apply(config, 'studio', frozen=frozen)
    # Mounted where the base sits, so paths baked into it still resolve; the live base is not bound at all.
    assert config['mizpah']['sandbox']['read_only_binds'] == [str(frozen)+':'+str(folder)]
    assert config['mizpah']['sandbox']['environment']['MIZPAH_BASE'] == str(folder)
    assert config['mizpah']['base']['snapshot'] == str(frozen)
    # A snapshot outlives nothing but its run: one whose process has gone is removed on the next snapshot.
    dead = frozen.with_name('studio@20260101T000000-999999999')
    frozen.rename(dead)
    assert bases.prune_snapshots() == [dead.name] and not dead.exists()
