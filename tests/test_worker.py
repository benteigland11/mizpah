"""Worker glue without a model: task map, assignment text, workspace round trip, gate, harvest."""
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from mizpah.worker import (
    green_message, harvest_playbook, load_config, open_task_map, pack_workspace, pick_task, procedures_created,
    procedures_used, protected_probes, read_unknown, red_message, render_assignment, render_reference, task_gate, tool_fight, worker_blocked, writeback,
    PLAYBOOK_PREFIX,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'engine'/'mizpah'/'config.gemma.json'
TERRA = ROOT/'.venv'/'bin'/'terra'
MAP = 't_measure_mean'


def terra(project: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True, check=True,
                          env=env).stdout


def on_map(project: Path, *args: str) -> str:
    return terra(project, '--map', MAP, *args)


@pytest.fixture
def config() -> dict:
    return load_config(CONFIG)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project = tmp_path/'proj'
    project.mkdir()
    (project/'data.txt').write_text('3\n5\n7\n')
    terra(project, 'init')
    terra(project, 'brief', 'init', '--title', 'Example', '--mission', 'Know the mean')
    terra(project, 'unknown', 'create', 'sample_mean', '--claim', 'The mean is unknown',
          '--evidence', 'A run reporting sample_mean', '--type', 'number', '--quantity', 'sample_mean', '--unit', '1',
          '--notes', 'cites need:1; source data.txt')
    terra(project, 'route', 'init')
    terra(project, 'route', 'add', 'measure_mean', '--title', 'Measure the mean', '--bucket', 'low',
          '--skill', 'terra-probe', '--map', 'sample_mean')
    return project


def started(config: dict, project: Path) -> dict:
    task = pick_task(config, project)
    open_task_map(config, project, task)
    return task


def probe(project: Path) -> None:
    terra(project, 'probe', 'create', 'mean_probe', '--purpose', 'Read data.txt', '--kind', 'run')
    path = project/'.terra'/'map'/'probes'/'mean_probe'/'probe.py'
    path.write_text(path.read_text().replace(
        '    raise NotImplementedError("TODO: implement measure()")  # scaffold stub',
        '    values = [float(x) for x in Path("data.txt").read_text().split()]\n'
        '    return {"sample_mean": sum(values) / len(values)}'))
    terra(project, 'probe', 'validate', 'mean_probe')


def sample(project: Path) -> str:
    output = on_map(project, 'probe', 'run', 'mean_probe')
    run_id = next(line.split()[1] for line in output.splitlines() if line.startswith('run '))
    # Before graduation runs attach to the unknown; afterwards to the known.
    node = 'known' if (project/'.terra'/'map'/'sessions'/MAP/'knowns'/'sample_mean.json').exists() else 'unknown'
    on_map(project, node, 'link-run', 'sample_mean', run_id)
    return run_id


def test_pick_task_opens_a_task_map_with_the_unknown(config: dict, project: Path):
    task = started(config, project)
    assert task['id'] == 'measure_mean' and task['map_id'] == 'sample_mean'
    route = json.loads((project/'.terra'/'route.json').read_text())
    entry = next(t for t in route['tasks'] if t['id'] == 'measure_mean')
    assert entry['status'] == 'in_progress' and entry['owner_agent'] == config['mizpah']['agent']
    local = read_unknown(project, 'sample_mean', MAP)
    assert local['claim'] == 'The mean is unknown' and local['quantity'] == 'sample_mean' and local['status'] == 'open'
    assert local['notes'] == 'cites need:1; source data.txt'  # the check-in reference needs the citation
    assert open_task_map(config, project, task) == MAP  # idempotent
    probe = json.loads((project/'.terra'/'map'/'probes'/'sample_mean_probe'/'probe.json').read_text())
    assert probe['measures'] == ['sample_mean'] and probe['kind'] == 'run'
    with pytest.raises(RuntimeError, match='No pickable'):
        pick_task(config, project)


def test_assignment_names_task_unknown_and_map_only(project: Path):
    task = dict(id='measure_mean', bucket='low', title='Measure the mean', acceptance=['n >= 1'])
    text = render_assignment(task, [read_unknown(project, 'sample_mean')], MAP)
    assert 'measure_mean' in text and 'sample_mean' in text and 'The mean is unknown' in text
    assert 'Acceptance: n >= 1' in text and 'Your map is `t_measure_mean`' in text and 'read it from: data.txt' in text
    assert 'known adopt <known> --from t_measure_mean' in text and '--known sample_mean' in text
    assert 'probes already exist' in text and '`.terra/map/probes/sample_mean_probe/`' in text
    assert 'playbook' not in text and 'mission' not in text.lower()


def test_pack_carries_the_playbook_copy_and_skips_git(project: Path, tmp_path: Path):
    (project/'.git').mkdir()
    (project/'.git'/'HEAD').write_text('ref')
    store = tmp_path/'store'
    store.mkdir()
    (store/'example-procedure.json').write_text('{"id": "example-procedure"}')
    snapshot = pack_workspace(project, store)
    names = {member.name for member in tarfile.open(fileobj=io.BytesIO(snapshot), mode='r:')}
    assert '.terra/route.json' in names and 'data.txt' in names and not any(n.startswith('.git') for n in names)
    assert PLAYBOOK_PREFIX+'/playbook/procedures/example-procedure.json' in names


def snapshot_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def test_writeback_touches_only_what_the_worker_is_entitled_to(config: dict, project: Path):
    task = started(config, project)
    # Meanwhile the controller minted another unknown, a task and a proposal on the live project.
    terra(project, 'unknown', 'create', 'other', '--claim', 'Something else', '--evidence', 'x', '--type', 'boolean',
          '--quantity', 'other')
    terra(project, 'route', 'add', 'later', '--title', 'Later', '--bucket', 'low', '--map', 'other')
    terra(project, 'brief', 'propose', '--summary', 'add a need', '--need', 'n')
    stale_route = json.loads((project/'.terra'/'route.json').read_text())
    stale_route['tasks'] = [dict(t, status='done', evidence=[dict(runs=['r1'], knowns=['sample_mean'])])
                            for t in stale_route['tasks'] if t['id'] == 'measure_mean']  # the worker never saw 'later'
    adopted = dict(id='sample_mean', adopted_from=dict(map=MAP), run_ids=['r1'], confidence='med')
    foreign = dict(id='other_known', run_ids=['r2'])
    files = {
        'data.txt': b'3\n5\n7\n9\n',
        'notes.md': b'worker notes',
        '.tool-output/x.stdout': b'noise',
        PLAYBOOK_PREFIX+'/playbook/procedures/p.json': b'{}',
        '.terra/brief.json': b'{"tampered": true}',
        '.terra/route.json': json.dumps(stale_route).encode(),
        '.terra/map/sessions/'+MAP+'/knowns/sample_mean.json': b'{"id": "sample_mean"}',
        '.terra/map/sessions/'+MAP+'/runs/r1/meta.json': b'{}',
        '.terra/map/probes/mean_probe/probe.py': b'print(1)',
        '.terra/map/knowns/sample_mean.json': json.dumps(adopted).encode(),
        '.terra/map/knowns/other_known.json': json.dumps(foreign).encode(),
        '.terra/map/runs/r1/meta.json': b'{"id": "r1"}',
        '.terra/map/runs/r2/meta.json': b'{"id": "r2"}',
        '.terra/map/unknowns/sample_mean.json': json.dumps(dict(id='sample_mean', status='resolved')).encode(),
        '.terra/map/unknowns/other.json': b'{"id": "other", "status": "resolved"}',
    }
    written = writeback(snapshot_of(files), project, task, MAP)
    assert sorted(written) == sorted([
        'data.txt', 'notes.md', '.terra/map/sessions/'+MAP+'/knowns/sample_mean.json',
        '.terra/map/sessions/'+MAP+'/runs/r1/meta.json', '.terra/map/probes/mean_probe/probe.py',
        '.terra/map/knowns/sample_mean.json', '.terra/map/runs/r1/meta.json', '.terra/map/unknowns/sample_mean.json',
        '.terra/route.json#measure_mean'])
    assert not (project/'.tool-output').exists() and not (project/PLAYBOOK_PREFIX).exists()
    assert not (project/'.terra'/'map'/'knowns'/'other_known.json').exists()
    assert not (project/'.terra'/'map'/'runs'/'r2').exists()
    assert json.loads((project/'.terra'/'map'/'unknowns'/'other.json').read_text())['status'] == 'open'
    brief = json.loads((project/'.terra'/'brief.json').read_text())
    assert 'tampered' not in brief and len(brief['proposals']) == 1
    route = json.loads((project/'.terra'/'route.json').read_text())
    by_id = {t['id']: t for t in route['tasks']}
    assert by_id['measure_mean']['status'] == 'done' and by_id['later']['status'] == 'ready'


def test_writeback_leaves_an_unresolved_unknown_alone(config: dict, project: Path):
    task = started(config, project)
    files = {'.terra/map/unknowns/sample_mean.json': json.dumps(dict(id='sample_mean', status='probing')).encode(),
             '.terra/map/knowns/sample_mean.json': json.dumps(dict(id='sample_mean', run_ids=['r1'])).encode()}
    assert writeback(snapshot_of(files), project, task, MAP) == []
    assert read_unknown(project, 'sample_mean')['status'] == 'open'


def test_reference_holds_the_cited_brief_entry_task_and_unknown(config: dict, project: Path):
    terra(project, 'brief', 'set', '--need', 'Know the mean of data.txt', '--need', 'Know the spread too')
    unknown = read_unknown(project, 'sample_mean')
    task = dict(id='measure_mean', bucket='low', title='Measure the mean')
    text = render_reference(project, task, [unknown])
    assert text.startswith('Brief entry served: need:1 — Know the mean of data.txt')
    assert 'Know the spread too' not in text and 'Mission: Know the mean' in text
    assert 'Unknown `sample_mean` (number, quantity sample_mean, unit 1)' in text
    assert 'read it from: data.txt' in text and 'returns a constant' in text
    assert 'After the gate is green' in text and 'not a departure' in text
    assert 'cites no brief entry' in render_reference(project, task, [dict(unknown, notes='')])


def test_gate_is_red_until_adopted_and_names_the_delta(config: dict, project: Path):
    task = started(config, project)
    gate = task_gate(config, project, task, MAP)
    assert not gate['ok'] and 'route task measure_mean is in_progress, not done' in gate['problems']
    assert any(p.startswith('known sample_mean has not been graduated on map t_measure_mean') and 'terra known ladder sample_mean' in p for p in gate['problems'])
    probe(project)
    run_id = sample(project)
    on_map(project, 'unknown', 'graduate', 'sample_mean')
    on_map(project, 'route', 'complete', 'measure_mean', '--run', run_id, '--known', 'sample_mean')
    gate = task_gate(config, project, task, MAP)
    assert not gate['ok']
    assert any(p.startswith('known sample_mean is on t_measure_mean (n=1, confidence low) below the adoption bar')
               and 'terra known ladder sample_mean' in p for p in gate['problems'])
    assert 'project unknown sample_mean is open' in gate['problems']
    text = red_message(gate, project, MAP, ['sample_mean'])
    assert text.startswith('Gate red. Missing:') and 'Keep: known sample_mean on t_measure_mean with its 1 linked run(s); probe mean_probe' in text
    assert 'do not start over' in text
    # The delta closes: two more readings, promote, adopt.
    sample(project)
    sample(project)
    on_map(project, 'known', 'promote', 'sample_mean', 'med')
    terra(project, 'known', 'adopt', 'sample_mean', '--from', MAP)
    gate = task_gate(config, project, task, MAP)
    assert gate == dict(ok=True, problems=[], knowns=['sample_mean'], runs=[run_id], foreign_violations=[])
    assert 'playbook create' in green_message(gate, 'sample_mean')
    assert 'You followed `measure-mean-from-file`' in green_message(gate, 'sample_mean', ['measure-mean-from-file'])


def test_gate_rejects_freehand_and_ignores_other_tasks_debts(config: dict, project: Path):
    terra(project, 'unknown', 'create', 'other', '--claim', 'Something else', '--evidence', 'x', '--type', 'boolean',
          '--quantity', 'other')
    task = started(config, project)
    on_map(project, 'route', 'complete', 'measure_mean', '--freehand', 'looked fine')
    gate = task_gate(config, project, task, MAP)
    assert not gate['ok']
    assert 'task completed freehand (looked fine); freehand is not evidence' in gate['problems']
    assert not any('other' in p for p in gate['problems'])
    assert [v['id'] for v in gate['foreign_violations']] == ['other']


def test_harvest_installs_only_valid_new_or_changed_procedures(config: dict, tmp_path: Path):
    store = tmp_path/'xdg'/'playbook'/'procedures'
    store.mkdir(parents=True)
    existing = json.loads((Path(config['mizpah']['playbook_store'])/'author-procedure.json').read_text())
    (store/'author-procedure.json').write_text(json.dumps(existing))
    changed = dict(existing, description=existing['description']+' (improved)')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        for name, body in (('author-procedure.json', json.dumps(changed).encode()),
                           ('broken.json', b'{"id": "broken"}'), ('data.txt', b'3')):
            info = tarfile.TarInfo(PLAYBOOK_PREFIX+'/playbook/procedures/'+name if name != 'data.txt' else name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    harvest_config = dict(config, mizpah=dict(config['mizpah'], playbook_store=str(store)))
    result = harvest_playbook(buffer.getvalue(), store, harvest_config)
    assert result['installed'] == ['author-procedure'] and len(result['rejected']) == 1 and result['ignored'] == []
    assert result['rejected'][0].startswith('broken:')
    assert not (store/'broken.json').exists() and not (store/'broken.json.staged').exists()
    assert json.loads((store/'author-procedure.json').read_text())['description'].endswith('(improved)')
    (store/'author-procedure.json').write_text(json.dumps(existing))
    limited = harvest_playbook(buffer.getvalue(), store, harvest_config, allowed=('something-else',))
    assert limited == dict(installed=[], rejected=[], ignored=['author-procedure', 'broken'])
    assert json.loads((store/'author-procedure.json').read_text()) == existing


def test_procedures_used_come_from_the_session_journal(tmp_path: Path):
    root = tmp_path/'session'
    (root/'events').mkdir(parents=True)
    calls = ['playbook start mizpah-resolve-unknown', 'playbook search "x"',
             'playbook start measure-mean-from-file --title "Create probe"',
             'playbook load other-procedure', 'playbook start measure-mean-from-file --title "Run"']
    rows = [dict(event_type='worker_turn', payload=dict(response=dict(tool_calls=[
        dict(function=dict(name='bash', arguments=json.dumps(dict(command=c))))]))) for c in calls]
    rows.insert(1, dict(event_type='checkpoint', payload={}))
    (root/'events'/'session.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    assert procedures_used(root) == ['measure-mean-from-file', 'other-procedure']
    assert procedures_used(tmp_path/'nowhere') == []


def test_writeback_refuses_changes_to_probes_that_predate_the_session(config: dict, project: Path):
    task = started(config, project)
    (project/'.terra'/'map'/'probes'/'old_probe').mkdir(parents=True)
    (project/'.terra'/'map'/'probes'/'old_probe'/'probe.py').write_bytes(b'original')
    files = {'.terra/map/probes/old_probe/probe.py': b'changed', '.terra/map/probes/old_probe/notes.txt': b'new file',
             '.terra/map/probes/new_probe/probe.py': b'fresh'}
    written = writeback(snapshot_of(files), project, task, MAP, protected_probes=('old_probe',))
    assert sorted(written) == sorted(['refused:.terra/map/probes/old_probe/notes.txt',
                                      'refused:.terra/map/probes/old_probe/probe.py', '.terra/map/probes/new_probe/probe.py'])
    assert (project/'.terra'/'map'/'probes'/'old_probe'/'probe.py').read_bytes() == b'original'


def journal_with(root: Path, calls: list) -> None:
    (root/'events').mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (name, args, result) in enumerate(calls):
        rows.append(dict(event_type='worker_turn', payload=dict(
            response=dict(tool_calls=[dict(id='c%d' % i, function=dict(name=name, arguments=json.dumps(args)))]),
            tool_results=[dict(call_id='c%d' % i, name=name, result=result)])))
    (root/'events'/'session.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n')


def test_procedures_created_are_found_inside_chained_commands(tmp_path: Path):
    journal_with(tmp_path, [('bash', dict(command='cd /work && playbook create weather-cli-reading \\\n  --title "x"'), dict(exit_code=0)),
                            ('bash', dict(command='playbook create --help'), dict(exit_code=0)),
                            ('bash', dict(command='playbook create failed-one --title y'), dict(exit_code=1))])
    from mizpah.worker import procedures_created
    assert procedures_created(tmp_path) == ['weather-cli-reading']


def test_tool_fight_groups_failures_by_procedure_step_and_created_ids(tmp_path: Path):
    root = tmp_path/'session'
    ok, err = dict(status='ok', exit_code=0), dict(status='error')
    journal_with(root, [
        ('bash', dict(command='playbook start mean-proc --title "Implement probe"'), ok),
        ('write', dict(path='p.py', content='x'), dict(status='rejected')),
        ('edit', dict(path='p.py', old_text='a', new_text='b'), err),
        ('edit', dict(path='p.py', old_text='a', new_text='b'), err),
        ('bash', dict(command='terra probe validate p'), dict(status='ok', exit_code=1)),
        ('bash', dict(command='playbook start mean-proc --title "Run probe"'), ok),
        ('bash', dict(command='terra probe run p'), ok),
        ('bash', dict(command='playbook create new-proc --title t --description d --tags x'), ok),
        ('bash', dict(command='playbook create failed-proc --title t'), dict(status='ok', exit_code=2)),
    ])
    assert tool_fight(root) == {'Implement probe': {'write': 1, 'edit': 2, 'bash terra': 1}, 'Run probe': {'bash playbook': 1}}
    assert procedures_used(root) == ['mean-proc'] and procedures_created(root) == ['new-proc']
    text = green_message(dict(ok=True), 'sample_mean', ['mean-proc'], tool_fight(root))
    assert "while on 'Implement probe': 2× edit, 1× write, 1× bash terra" in text and 'the step is what needs rewriting' in text


def test_worker_blocked_is_read_from_the_route(config: dict, project: Path):
    task = started(config, project)
    assert worker_blocked(project, task) is None
    on_map(project, 'route', 'block', 'measure_mean', '--reason', 'data.txt names no users to ask')
    assert worker_blocked(project, task) == 'data.txt names no users to ask'
    assert 'A task blocked by its worker' in __import__('mizpah.controller', fromlist=['x']).render_observation(
        __import__('mizpah.controller', fromlist=['x']).observe(config, project), 'eval')


def test_widgets_touched_reads_create_and_edits_from_the_journal(tmp_path: Path):
    root = tmp_path/'session'
    ok = dict(status='ok', exit_code=0)
    journal_with(root, [
        ('bash', dict(command='cartograph create column-mean --language python --domain data'),
         dict(status='ok', exit_code=0, stdout='{\n  "status": "success",\n  "path": "/work/cg/data_column_mean_python",\n  "item_id": "data-column-mean-python"\n}')),
        ('edit', dict(path='cg/data_csv_normalizer_python/src/csv_normalizer.py', old_text='a', new_text='b'), ok),
        ('write', dict(path='cg/other/src/x.py', content='x'), dict(status='error')),
        ('bash', dict(command='cartograph validate cg/data_column_mean_python'), ok),
        ('bash', dict(command='ls cg/'), ok),
    ])
    from mizpah.worker import widgets_touched
    assert widgets_touched(root) == ['data_column_mean_python', 'data_csv_normalizer_python']


def test_harvest_widgets_validates_and_checks_in_locally(tmp_path: Path, config: dict, monkeypatch):
    """A new widget in the workspace is validated and checked in to a scratch library; nothing is published."""
    from mizpah import worker as worker_module
    root = tmp_path/'session'
    journal_with(root, [('bash', dict(command='cartograph create column-mean --language python --domain data'),
                         dict(status='ok', exit_code=0, stdout='"path": "/work/cg/data_column_mean_python"'))])
    library = tmp_path/'library'
    library.mkdir()
    widget = dict(meta=dict(id='data-column-mean-python', name='Column Mean', version='1.0.0', domain='data',
                            tags=['csv', 'statistics', 'mean']),
                  description='Mean of one numeric column of CSV rows given as dicts; blank cells are skipped.',
                  tech_stack=dict(language='python', dependencies=[]), custom_notes='')
    src = ('"""Mean of one numeric column across CSV-style row dicts."""\nfrom __future__ import annotations\n\n\n'
           'def column_mean(rows: list[dict[str, str]], column: str) -> float | None:\n'
           '    """Return the mean of the numeric values in `column`, skipping blanks; None when no values."""\n'
           "    values = [float(row[column]) for row in rows if row.get(column) not in (None, '')]\n"
           '    return sum(values) / len(values) if values else None\n')
    test = ('import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))\n'
            'from src.column_mean import column_mean\n\n\ndef test_mean_skips_blanks():\n'
            '    assert column_mean([{"v": "3"}, {"v": ""}, {"v": "5"}], "v") == 4.0\n')
    example = ('import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))\n'
               'from src.column_mean import column_mean\n\nprint(column_mean([{"v": "1"}, {"v": "3"}], "v"))\n')
    files = {'cg/data_column_mean_python/widget.json': json.dumps(widget).encode(),
             'cg/data_column_mean_python/src/__init__.py': b'',
             'cg/data_column_mean_python/src/column_mean.py': src.encode(),
             'cg/data_column_mean_python/tests/test_column_mean.py': test.encode(),
             'cg/data_column_mean_python/examples/example_usage.py': example.encode(),
             'cg/data_column_mean_python/.venv/bin/python': b'not shipped'}
    monkeypatch.setenv('WIDGET_LIBRARY_PATH', str(library))
    harvest_config = dict(config, mizpah=dict(config['mizpah'], widget_library=str(library)))
    result = worker_module.harvest_widgets(snapshot_of(files), root, harvest_config)
    assert result == dict(checked_in=['data-column-mean-python'], rejected=[], unchanged=[]), result
    assert (library/'data-column-mean-python'/'src'/'column_mean.py').exists()
    again = worker_module.harvest_widgets(snapshot_of(files), root, harvest_config)
    assert again['unchanged'] == ['data-column-mean-python'] or again['checked_in'] == ['data-column-mean-python']
    broken = dict(files, **{'cg/data_column_mean_python/src/column_mean.py': b'def column_mean(:\n'})
    (library/'data-column-mean-python'/'src'/'column_mean.py').write_bytes(src.encode())
    result = worker_module.harvest_widgets(snapshot_of(broken), root, harvest_config)
    assert result['checked_in'] == [] and result['rejected'] and 'data-column-mean-python' in result['rejected'][0]


def test_writeback_covers_every_unknown_the_task_carries(config: dict, project: Path):
    terra(project, 'unknown', 'create', 'second', '--claim', 'x', '--evidence', 'y', '--type', 'number', '--quantity', 'second')
    terra(project, 'route', 'add', 'pair', '--title', 'Pair', '--bucket', 'low', '--map', 'sample_mean', '--accept', 'unknown:second')
    task = pick_task(config, project, 'pair')
    files = {'.terra/map/unknowns/sample_mean.json': json.dumps(dict(id='sample_mean', status='resolved')).encode(),
             '.terra/map/unknowns/second.json': json.dumps(dict(id='second', status='resolved')).encode(),
             '.terra/map/unknowns/other.json': json.dumps(dict(id='other', status='resolved')).encode()}
    written = writeback(snapshot_of(files), project, task, 't_pair')
    assert sorted(written) == ['.terra/map/unknowns/sample_mean.json', '.terra/map/unknowns/second.json']


def test_gate_is_red_while_a_touched_widget_does_not_validate(config: dict, project: Path, tmp_path: Path):
    task = started(config, project)
    root = tmp_path/'session'
    journal_with(root, [('bash', dict(command='cartograph create column-mean --language python --domain data'),
                         dict(status='ok', exit_code=0, stdout='"path": "/work/cg/data_column_mean_python"'))])
    widget = project/'cg'/'data_column_mean_python'
    (widget/'src').mkdir(parents=True)
    (widget/'widget.json').write_text(json.dumps(dict(meta=dict(id='data-column-mean-python', name='Column Mean', version='1.0.0',
                                                                 domain='data', tags=['[TODO: add 3-5 tags]']),
                                                       description='[TODO] Describe', tech_stack=dict(language='python', dependencies=[]))))
    (widget/'src'/'column_mean.py').write_text('def column_mean(v):\n    return v\n')
    from mizpah.worker import widget_problems
    problems = widget_problems(config, project, root)
    assert len(problems) == 1 and problems[0].startswith('widget cg/data_column_mean_python does not validate: ')
    gate = task_gate(config, project, task, MAP, root)
    assert not gate['ok'] and any('does not validate' in p for p in gate['problems'])


def test_task_probes_are_the_workers_own_and_measure_py_writes_back(config: dict, project: Path):
    """The host scaffolds the task's probes before the session; they must not count as predating it,
    or every measure.py the worker writes is refused and the project map keeps instruments without methods."""
    task = started(config, project)
    (project/'.terra'/'map'/'probes'/'old_probe').mkdir(parents=True)
    assert protected_probes(project, task) == ('old_probe',)
    files = {'.terra/map/probes/sample_mean_probe/measure.py': b'def measure(ctx): ...'}
    written = writeback(snapshot_of(files), project, task, MAP, protected_probes(project, task))
    assert written == ['.terra/map/probes/sample_mean_probe/measure.py']


def test_scaffold_declares_knowns_named_in_the_evidence_as_inputs(config: dict, project: Path):
    """"A comparison of wind_alert_count and gale_reading_count" names two knowns without the word `known`;
    the probe must still declare them, so Terra can refuse a measure() that hardcodes or re-derives them."""
    terra(project, 'probe', 'create', 'sample_count_probe', '--purpose', 'count', '--kind', 'run', '--measure', 'sample_count')
    (project/'.terra'/'map'/'probes'/'sample_count_probe'/'measure.py').write_text(
        'from pathlib import Path\ndef measure(ctx):\n    return {"sample_count": len(Path("data.txt").read_text().split())}\n')
    terra(project, 'probe', 'validate', 'sample_count_probe')
    terra(project, 'unknown', 'create', 'sample_count', '--claim', 'The count is unknown', '--evidence', 'A run',
          '--type', 'number', '--quantity', 'sample_count')
    output = terra(project, 'probe', 'run', 'sample_count_probe')
    run_id = next(line.split()[1] for line in output.splitlines() if line.startswith('run '))
    terra(project, 'unknown', 'link-run', 'sample_count', run_id)
    terra(project, 'unknown', 'graduate', 'sample_count')
    terra(project, 'unknown', 'create', 'count_is_three', '--claim', 'Whether the count is three is unknown',
          '--evidence', 'A comparison of sample_count and 3', '--type', 'boolean', '--quantity', 'count_is_three')
    terra(project, 'route', 'add', 'compare', '--title', 'Compare', '--bucket', 'low', '--skill', 'terra-probe',
          '--map', 'count_is_three')
    task = pick_task(config, project, 'compare')
    open_task_map(config, project, task)
    probe = json.loads((project/'.terra'/'map'/'probes'/'count_is_three_probe'/'probe.json').read_text())
    assert probe['inputs'] == {'sample_count': 'known:sample_count'}
    measure = project/'.terra'/'map'/'probes'/'count_is_three_probe'/'measure.py'
    measure.write_text('from pathlib import Path\ndef measure(ctx):\n'
                       '    return {"count_is_three": len(Path("data.txt").read_text().split()) == 3}\n')  # re-derived
    result = subprocess.run([str(TERRA), 'probe', 'validate', 'count_is_three_probe'], cwd=project, capture_output=True, text=True)
    assert result.returncode != 0 and 'never reads ctx["inputs"]' in result.stdout+result.stderr
    measure.write_text('def measure(ctx):\n    return {"count_is_three": ctx["inputs"]["sample_count"] == 3}\n')
    terra(project, 'probe', 'validate', 'count_is_three_probe')


def test_a_task_this_agent_started_is_resumed_not_restarted(config: dict, project: Path, tmp_path: Path):
    """A killed run leaves the task in_progress under our agent; the next run takes it back before anything new."""
    from mizpah.loop import pickable
    task = started(config, project)
    assert pick_task(config, project, task['id'])['status'] == 'in_progress'   # no second `route start`
    with pytest.raises(RuntimeError, match='No pickable'):
        pick_task(config, project)
    assert pickable(config, project, tmp_path) == []                            # no session on disk: nothing to resume
    (tmp_path/'tasks'/task['id']).mkdir(parents=True)
    (tmp_path/'tasks'/task['id']/'state.sqlite3').write_bytes(b'')
    assert [t['id'] for t in pickable(config, project, tmp_path)] == [task['id']]


def test_open_checklists_hold_the_gate_until_every_step_is_ticked(tmp_path: Path):
    from mizpah.worker import open_checklists
    files = {'.playbook/open/mizpah-resolve-unknown--means.md': (
        '# Resolve\n\n- [x] **1. Find a method**\n\n  do\n\n- [ ] **2. Find the parts**\n\n  do\n\n- [-] **3. Make a widget**\n\n  do\n').encode(),
        '.playbook/open/csv-counting--rows.md': b'- [x] **1. Scaffold**\n\n- [x] **2. Run**\n',
        '.playbook/playbook/procedures/csv-counting.json': b'{}'}
    problems = open_checklists(snapshot_of(files))
    assert problems == ['checklist .playbook/open/mizpah-resolve-unknown--means.md has 1 unticked step(s): 2. Find the parts'
                        ' — tick each `[x]` (done) or `[-]` (not needed)']
    files['.playbook/open/mizpah-resolve-unknown--means.md'] = files['.playbook/open/mizpah-resolve-unknown--means.md'].replace(b'- [ ]', b'- [x]')
    assert open_checklists(snapshot_of(files)) == []


def test_green_message_hands_back_the_steps_a_walk_skipped(tmp_path: Path):
    from mizpah.worker import checklist_skips
    files = {'.playbook/open/csv-counting--rows.md': b'- [x] **1. Scaffold**\n\n- [-] **2. Extract a widget**\n\n- [x] **3. Run**\n\n- [-] **6. Cross-check**\n'}
    skips = checklist_skips(snapshot_of(files))
    assert skips == {'csv-counting': ['2. Extract a widget', '6. Cross-check']}
    text = green_message(dict(ok=True, problems=[], knowns=['a'], runs=['r']), ['a'], ['csv-counting'], None, skips)
    assert 'marked `[-]` not needed' in text and 'csv-counting: 2. Extract a widget; 6. Cross-check' in text
    assert 'not needed on this walk' in text and 'not needed in general' in text
