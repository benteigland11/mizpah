"""The Deputy's drafts: gyms in draft status beside the issued ones, which it may not write, and the desk read
off the journal rather than off the model's words."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT.parent.parent))

from mizpah import deputy, draft  # noqa: E402


@pytest.fixture
def gyms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv('MIZPAH_GYMS', str(tmp_path/'gyms'))
    (tmp_path/'gyms').mkdir()
    return tmp_path/'gyms'


def test_a_new_draft_is_a_terra_project_in_draft_status(gyms: Path) -> None:
    out = draft.new('ornith-landing', 'Ornith landing page', 'Build a page and prove it.', 'none')
    assert out['status'] == 'ok' and out['brief_status'] == 'draft' and out['title'] == 'Ornith landing page'
    brief = json.loads((gyms/'ornith-landing'/'.mizpah'/'brief.json').read_text())
    assert brief['status'] == 'draft' and brief['mission'] == 'Build a page and prove it.'
    assert [d['slug'] for d in json.loads(json.dumps([draft.summary(p) for p in draft.listing()]))] == ['ornith-landing']


def test_slug_rule_and_duplicates_are_refused(gyms: Path) -> None:
    with pytest.raises(SystemExit) as refused:
        draft.new('Ornith Landing', 't', 'm', 'none')
    assert 'slug' in str(refused.value)
    draft.new('ornith-landing', 't', 'm', 'none')
    with pytest.raises(SystemExit) as again:
        draft.new('ornith-landing', 't', 'm', 'none')
    assert 'exists' in str(again.value)


def test_show_and_discard(gyms: Path) -> None:
    draft.new('ornith-landing', 't', 'm', 'none')
    assert draft.show('ornith-landing')['showing'] == dict(draft='ornith-landing')
    with pytest.raises(SystemExit) as missing:
        draft.show('nope')
    assert 'ornith-landing' in str(missing.value)   # the refusal lists what there is
    assert draft.discard('ornith-landing') == dict(status='ok', discarded='ornith-landing')
    assert not (gyms/'ornith-landing').exists()


def test_a_draft_is_a_gym_with_a_route_and_no_furnishing_yet(gyms: Path) -> None:
    draft.new('ornith-landing', 't', 'm', 'none')
    state = gyms/'ornith-landing'/'.mizpah'
    assert (gyms/'ornith-landing'/'.git').is_dir() and (state/'route.json').exists()
    assert not (state/'config.json').exists()   # furnishing (config, sessions, registry) is the host's, on signature


def test_issued_gyms_are_read_roots_and_drafts_are_not(gyms: Path) -> None:
    draft.new('ornith-landing', 't', 'm', 'none')
    (gyms/'issued-20260920T000000Z'/'.mizpah').mkdir(parents=True)
    (gyms/'issued-20260920T000000Z'/'.mizpah'/'brief.json').write_text(json.dumps(dict(status='active', title='x')))
    (gyms/'.home').mkdir()
    roots = deputy.read_roots(dict(mizpah=dict()))
    assert str(gyms/'issued-20260920T000000Z') in roots
    assert str(gyms/'ornith-landing') not in roots and str(gyms/'.home') not in roots


def test_authorize_in_place_furnishes_and_keeps_the_directory(gyms: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('mizpah.init.register_project', lambda *a, **k: None)
    draft.new('ornith-landing', 't', 'm', 'none')
    out = draft.authorize(gyms/'ornith-landing')
    assert out['status'] == 'ok' and out['gym'] and out['project'] == str(gyms/'ornith-landing')
    assert (gyms/'ornith-landing'/'.mizpah'/'config.json').exists() and (gyms/'ornith-landing'/'.mizpah'/'sessions').is_dir()


def test_authorize_with_the_engine_config_locks_the_crew_in(gyms: Path, homes: Path) -> None:
    draft.new('ornith-landing', 't', 'm', 'none')
    harness = homes/'harness.json'
    harness.write_text(json.dumps(dict(worker=dict(provider='subscription', subscription='openai_chatgpt', generation=dict(model='gpt-5.6-luna', reasoning_effort='high')),
                                       controller=dict(provider='llama_client', generation=dict(model='gemma-4-26b')))))
    engine_config = homes/'engine.json'
    engine_config.write_text(json.dumps(dict(harness_config='harness.json')))
    out = draft.authorize(gyms/'ornith-landing', engine_config=engine_config)
    assert out['crew'] == dict(worker=dict(provider='openai_chatgpt', model='gpt-5.6-luna', effort='high'),
                               controller=dict(provider='llama_client', model='gemma-4-26b', effort=None))
    pc = json.loads((gyms/'ornith-landing'/'.mizpah'/'config.json').read_text())
    assert pc['models']['worker']['generation']['model'] == 'gpt-5.6-luna'
    # A choice the task made itself is kept when the defaults move.
    harness.write_text(json.dumps(dict(worker=dict(provider='subscription', subscription='xai_grok', generation=dict(model='grok-4.6')))))
    out = draft.authorize(gyms/'ornith-landing', engine_config=engine_config)
    assert out['crew']['worker']['model'] == 'gpt-5.6-luna'


def test_an_issued_brief_is_not_discardable(gyms: Path) -> None:
    draft.new('ornith-landing', 't', 'm', 'none')
    path = gyms/'ornith-landing'/'.mizpah'/'brief.json'
    path.write_text(json.dumps(json.loads(path.read_text()) | dict(status='active')))
    with pytest.raises(SystemExit) as refused:
        draft.discard('ornith-landing')
    assert 'issued' in str(refused.value)
    assert (gyms/'ornith-landing').exists()


def _session(events: list[tuple[str, dict]]) -> SimpleNamespace:
    journal = SimpleNamespace(read=lambda _sid: tuple(SimpleNamespace(event_type=t, payload=p) for t, p in events))
    return SimpleNamespace(journal=journal)


def test_the_desk_follows_successful_show_and_discard_calls_only() -> None:
    ok = dict(status='ok', stdout='{"status": "ok", "showing": {"draft": "a"}}')
    bad = dict(status='ok', stdout='{"status": "error", "error": "no draft named b"}')
    events = [
        ('command_tool', dict(call_id='1', name='draft_show', command='python -m mizpah.draft show a')),
        ('tool_outcome', dict(call_id='1', **ok)),
        ('command_tool', dict(call_id='2', name='draft_show', command='python -m mizpah.draft show b')),
        ('tool_outcome', dict(call_id='2', **bad)),
    ]
    assert deputy.showing_after(_session(events), 0) == dict(draft='a')
    assert deputy.showing_after(_session(events), 4) is False     # nothing new this turn
    events += [
        ('command_tool', dict(call_id='3', name='draft_discard', command='python -m mizpah.draft discard a')),
        ('tool_outcome', dict(call_id='3', status='ok', stdout='{"status": "ok", "discarded": "a"}')),
    ]
    assert deputy.showing_after(_session(events), 0) is None      # shown, then discarded: clear the desk
    assert deputy.showing_after(_session(events), 4) is None


def test_turn_log_round_trips(tmp_path: Path) -> None:
    deputy._turn(tmp_path, 'user', 'hello')
    deputy._turn(tmp_path, 'deputy', 'hi', showing=dict(draft='a'), tool_calls=2)
    log = deputy.turns(tmp_path)
    assert [t['role'] for t in log] == ['user', 'deputy'] and log[1]['showing'] == dict(draft='a')


def test_the_sandbox_owns_the_gyms_with_issued_ones_read_only(gyms: Path, tmp_path: Path) -> None:
    from mizpah.worker import load_config
    config = load_config(ROOT/'config.luna.json')
    draft.new('ornith-landing', 't', 'm', 'none')
    (gyms/'issued-20260920T000000Z'/'.mizpah').mkdir(parents=True)
    (gyms/'issued-20260920T000000Z'/'.mizpah'/'brief.json').write_text(json.dumps(dict(status='active', title='x')))
    shell = deputy.shell_for(config, tmp_path/'root')
    assert shell.config.workspace_dir == str(gyms)
    assert str(gyms/'issued-20260920T000000Z') in shell.config.read_only_binds
    assert str(gyms/'ornith-landing') not in shell.config.read_only_binds
    assert shell.config.environment['MIZPAH_GYMS'] == '/work'
    assert shell.config.share_network is False and shell.config.services is None
    reasons = [r for _, r in shell.config.refused_patterns]
    assert any('signature' in r for r in reasons) and any('terra brief set' in r for r in reasons)


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A projects registry of its own, so authorize never touches the machine's (the gyms root is `gyms`)."""
    from mizpah import init as init_module
    monkeypatch.setattr(init_module, 'projects_registry', lambda: tmp_path/'projects.jsonl')
    return tmp_path


def test_authorize_in_place_makes_the_draft_a_project_where_it_is(gyms: Path, homes: Path) -> None:
    draft.new('ornith-landing', 'Ornith landing page', 'm', 'none')
    (gyms/'ornith-landing'/'content').mkdir()
    (gyms/'ornith-landing'/'content'/'pitch.md').write_text('# Ornith\n')
    out = draft.authorize(gyms/'ornith-landing')
    project = Path(out['project'])
    assert out['gym'] and project == gyms/'ornith-landing'
    assert (project/'.mizpah'/'brief.json').exists() and (project/'.mizpah'/'route.json').exists()
    assert (project/'.mizpah'/'config.json').exists() and (project/'.mizpah'/'sessions').is_dir()
    assert (project/'content'/'pitch.md').read_text() == '# Ornith\n' and (project/'README.md').exists()
    assert json.loads(project.joinpath('.mizpah', 'brief.json').read_text())['status'] == 'draft'   # issuing is the signature
    assert json.loads((homes/'projects.jsonl').read_text().splitlines()[-1])['project'] == str(project)
    # Again is harmless: a project the app issues is authorized every time.
    assert draft.authorize(gyms/'ornith-landing')['status'] == 'ok'


def test_authorize_into_a_repository_keeps_its_files(gyms: Path, homes: Path) -> None:
    import subprocess
    repo = homes/'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    (repo/'README.md').write_text('theirs\n')
    draft.new('ornith-landing', 'Ornith', 'm', 'none')
    (gyms/'ornith-landing'/'README.md').write_text('deputy\n')
    out = draft.authorize(gyms/'ornith-landing', repo)
    assert out['project'] == str(repo) and out['kept'] == ['README.md'] and not out['gym']
    assert (repo/'README.md').read_text() == 'theirs\n' and (repo/'.mizpah'/'brief.json').exists()
    assert '.mizpah/sessions/' in (repo/'.gitignore').read_text()
    assert not (gyms/'ornith-landing').exists() and not (repo/'.git'/'gym').exists()
    with pytest.raises(SystemExit) as refused:
        draft.authorize(gyms/'nope', repo)
    assert 'no brief' in str(refused.value)


def test_authorize_refuses_a_repository_that_is_not_a_git_top_or_already_a_project(gyms: Path, homes: Path) -> None:
    draft.new('ornith-landing', 'Ornith', 'm', 'none')
    plain = homes/'plain'
    plain.mkdir()
    with pytest.raises(SystemExit) as refused:
        draft.authorize(gyms/'ornith-landing', plain)
    assert 'git repository' in str(refused.value) and (gyms/'ornith-landing').exists()


def test_the_prompt_is_composed_from_the_folder_in_order(tmp_path: Path) -> None:
    text = deputy.policy_text()
    files = sorted(p for p in deputy.PROMPT_DIR.glob('*.md') if p.name != 'README.md')
    assert [p.name[:2] for p in files] == sorted(p.name[:2] for p in files) and len(files) >= 6
    assert text.startswith('You are the Deputy.') and text.rstrip().endswith('this policy do.')
    assert 'composed from these files' not in text   # README.md stays out
    # Each file's opening line lands in order, one blank line between subjects.
    heads = [p.read_text().strip().split('\n')[0] for p in files]
    assert [text.index(h) for h in heads] == sorted(text.index(h) for h in heads)
    assert '\n\n\n' not in text
    # A config may point the folder elsewhere.
    (tmp_path/'one.md').write_text('Only this.\n')
    cfg = dict(mizpah=dict(deputy_prompt_dir='prompt'), mizpah_config_path=str(tmp_path/'x'/'config.json'))
    (tmp_path/'x').mkdir()
    (tmp_path/'x'/'prompt').symlink_to(tmp_path)
    assert deputy.policy_text(cfg) == 'Only this.\n'


def test_a_gym_is_set_up_in_an_environment_chosen_out_loud(gyms: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mizpah import bases
    monkeypatch.setattr(bases, 'bases_root', lambda: tmp_path/'bases')
    (tmp_path/'bases').mkdir()
    bases.create('piano', note='a piano studio')
    # Nothing named: the default, which is `bare` (made on first use) until another is set.
    assert draft.new('quiet', 'Quiet', 'm')['environment'] == 'bare'
    assert [e['name'] for e in draft.environments() if e['default']] == ['bare']
    bases.set_default('piano')
    assert draft.new('quiet2', 'Quiet', 'm')['environment'] == 'piano'
    assert [e['name'] for e in draft.environments()][0] == 'piano'
    (tmp_path/'bases'/'default.json').write_text('{"name": "gone"}')
    assert bases.default_name() == 'bare'   # a default that no longer exists falls back
    with pytest.raises(SystemExit) as unknown:
        draft.new('loud', 'Loud', 'm', 'organ')
    assert 'no saved environment' in str(unknown.value)
    out = draft.new('etude', 'Etude', 'm', 'piano')
    assert out['environment'] == 'piano'
    bare = draft.new('loud', 'Loud', 'm', 'none')
    assert bare['environment'] == 'bare'


def test_signing_records_the_environment_on_the_gym_itself(gyms: Path, homes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mizpah import bases, init as init_module
    monkeypatch.setattr(bases, 'bases_root', lambda: tmp_path/'bases')
    (tmp_path/'bases').mkdir()
    bases.create('piano', note='a piano studio')
    draft.new('etude', 'Etude', 'm', 'piano')
    out = draft.authorize(gyms/'etude')
    assert init_module.project_config(Path(out['project']))['base'] == 'piano'


def test_reset_clears_the_office_and_keeps_the_record_aside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MIZPAH_DEPUTY_ROOT', str(tmp_path/'deputy'))
    root = deputy.deputy_root()
    deputy._turn(root, 'user', 'hello')
    (root/'showing.json').write_text('{"draft": "a"}\n')
    out = deputy.reset({})
    assert out['status'] == 'ok'
    assert not (root/'turns.jsonl').exists() and not (root/'showing.json').exists()
    aside = Path(out['aside'])
    assert (aside/'turns.jsonl').read_text().count('hello') == 1
    assert deputy.turns(root) == []


def test_an_environment_gym_is_bare_reaches_package_hosts_and_names_its_base(gyms: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mizpah import bases, init as init_module
    monkeypatch.setattr(bases, 'bases_root', lambda: tmp_path/'bases')
    (tmp_path/'bases').mkdir()
    bases.create('piano', note='a piano studio')
    with pytest.raises(SystemExit) as wrong:
        draft.new('env-browser', 'Browser environment', 'm', 'piano', builds='browser')
    assert 'set up bare' in str(wrong.value)
    out = draft.new('env-browser', 'Browser environment', 'm', 'none', builds='browser')
    assert out['builds'] == 'browser' and 'pypi.org' in out['allowed_domains']
    pc = init_module.project_config(gyms/'env-browser')
    assert pc['builds_base'] == 'browser' and pc['sandbox']['network']['allowed_domains'] == list(draft.BUILD_DOMAINS)
    bases.create('browser', note='x')
    with pytest.raises(SystemExit) as taken:
        draft.new('env-browser2', 'B', 'm', 'none', builds='browser')
    assert 'exists already' in str(taken.value)


def test_a_project_adds_domains_without_replacing_the_host_network_policy(gyms: Path) -> None:
    from mizpah import init as init_module
    draft.new('env-x', 'X', 'm', 'none', builds='x')
    config = dict(mizpah=dict(sandbox=dict(network=dict(allowed_domains=['pypi.org'], proxy_port=3128, unshare='/u', nsenter='/n', socat='/s'))))
    out = init_module.apply_project_config(config, gyms/'env-x')
    net = out['mizpah']['sandbox']['network']
    assert net['proxy_port'] == 3128 and net['unshare'] == '/u' and 'github.com' in net['allowed_domains']
    assert out['mizpah']['builds_base'] == 'x'


def test_adopt_copies_a_green_environment_gym_into_the_bases(gyms: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mizpah import bases
    monkeypatch.setattr(bases, 'bases_root', lambda: tmp_path/'bases')
    (tmp_path/'bases').mkdir()
    draft.new('env-x', 'X', 'm', 'none', builds='x')
    gym = gyms/'env-x'
    (gym/'venv'/'bin').mkdir(parents=True)
    (gym/'venv'/'bin'/'tool').write_text('#!/work/venv/bin/python3\nprint(1)\n')
    (gym/'base.json').write_text(json.dumps(dict(name='x', note='a tool', env={'TOOL_HOME': '$BASE/tools'})))
    with pytest.raises(ValueError, match='relocatable'):
        bases.adopt(gym)
    (gym/'venv'/'bin'/'tool').write_text('#!/usr/bin/env python3\nprint(1)\n')
    (gym/'bin').mkdir()
    (gym/'bin'/'run').write_text('#!/bin/sh\nexec "$(dirname "$0")/../venv/bin/python" "$@"\n')
    adopted = bases.adopt(gym)
    base = Path(adopted['path'])
    assert base == tmp_path/'bases'/'x' and (base/'venv'/'bin'/'tool').exists() and (base/'bin'/'run').exists()
    assert not (base/'.mizpah').exists() and not (base/'.git').exists()
    assert bases.load('x')['note'] == 'a tool' and adopted['built_from'] == str(gym)
    with pytest.raises(FileExistsError):
        bases.adopt(gym)
    assert bases.adopt(gym, replace=True)['name'] == 'x'
    # apply puts bin/ and venv/bin on PATH and fills $BASE
    cfg = dict(mizpah=dict(sandbox=dict(read_only_binds=[], environment=dict(PATH='/usr/bin'))))
    bases.apply(cfg, 'x')
    env = cfg['mizpah']['sandbox']['environment']
    assert env['PATH'].split(':')[:2] == [str(base/'venv'/'bin'), str(base/'bin')] and env['TOOL_HOME'] == str(base/'tools')
    assert str(base) in cfg['mizpah']['sandbox']['read_only_binds']


def test_the_seat_hears_the_desk_with_every_line(gyms: Path, tmp_path: Path) -> None:
    root = tmp_path/'deputy'
    root.mkdir()
    assert deputy.situation(root) == '[Desk, from the host: no drafts; the desk is clear]'
    draft.new('etude', 'Etude', 'm', 'none')
    (root/'showing.json').write_text('{"draft": "etude"}\n')
    line = deputy.situation(root)
    assert line.startswith('[Desk, from the host: drafts: etude (Etude, 0 needs, 0 deliverables, env ') and line.endswith('; on the desk: etude]')
