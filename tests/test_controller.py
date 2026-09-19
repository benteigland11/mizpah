"""Controller glue without a model: observation, rendering, the structural guard, and apply."""
import json
from pathlib import Path
import subprocess

import pytest

from mizpah import controller
from mizpah.controller import apply, guard, observe, render_observation, step
from mizpah.worker import load_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'engine'/'mizpah'/'config.gemma.json'
TERRA = ROOT/'.venv'/'bin'/'terra'


def terra(project: Path, *args: str) -> str:
    return subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def config() -> dict:
    return load_config(CONFIG)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project = tmp_path/'proj'
    project.mkdir()
    (project/'data.txt').write_text('3\n5\n7\n')
    terra(project, 'init')
    terra(project, 'brief', 'init', '--title', 'Example', '--mission', 'Know the shape of the numbers in data.txt')
    terra(project, 'brief', 'set', '--need', 'Know the mean of data.txt', '--need', 'Know whether every value is positive',
          '--deliverable', 'A one-page summary of data.txt')
    terra(project, 'route', 'init')
    return project


def test_observation_and_rendering_carry_brief_map_and_route(config: dict, project: Path):
    terra(project, 'unknown', 'create', 'sample_mean', '--claim', 'The mean is unknown', '--evidence', 'a run',
          '--type', 'number', '--quantity', 'sample_mean')
    terra(project, 'route', 'add', 'measure_mean', '--title', 'Measure the mean', '--bucket', 'low', '--map', 'sample_mean')
    observation = observe(config, project)
    assert observation['brief']['needs'] == ['Know the mean of data.txt', 'Know whether every value is positive']
    assert observation['unknowns'][0]['id'] == 'sample_mean' and observation['tasks'][0]['unknown'] == 'sample_mean'
    assert observation['gate']['ok'] is False
    assert observation['knowns'] == []
    text = render_observation(observation, 'route')
    assert '  need:1 Know the mean of data.txt' in text and '  deliverable:1 A one-page summary' in text
    assert 'Gate: red' in text and 'measure_mean [ready, low] → sample_mean' in text
    assert text.rstrip().endswith('Mint the unknowns and one task each.')
    assert 'project eval' in render_observation(observation, 'eval')
    assert 'resubmit only corrected items' in render_observation(observation, 'eval', ['unknown x: nope'])


def good_unknown(**overrides) -> dict:
    return dict(id='all_positive', claim='Whether every value in data.txt is positive is unknown',
                evidence_needed='A probe reading data.txt reports all_positive', type='boolean',
                source='data.txt', cites='need:2') | overrides


def test_guard_refuses_uncited_duplicate_and_unrouted_items(config: dict, project: Path):
    terra(project, 'unknown', 'create', 'sample_mean', '--claim', 'The mean is unknown', '--evidence', 'a run',
          '--type', 'number', '--quantity', 'sample_mean')
    terra(project, 'route', 'add', 'measure_mean', '--title', 'Measure the mean', '--bucket', 'low', '--map', 'sample_mean')
    observation = observe(config, project)
    decision = dict(
        unknowns=[good_unknown(), good_unknown(id='sample_mean'), good_unknown(id='no_cite', cites='need:9'),
                  good_unknown(id='Bad-Id'), good_unknown(id='no_task'), good_unknown(id='wrong_type', type='formula'),
                  good_unknown(id='second_ok', cites='need:1'), good_unknown(id='third_ok', cites='deliverable:1')],
        tasks=[dict(id='check_positive', title='Check every value', unknowns=['all_positive'], bucket='low', deps=[]),
               dict(id='measure_again', title='Measure again', unknown='sample_mean', bucket='low', deps=[]),
               dict(id='orphan', title='x', unknowns=['ghost'], bucket='low', deps=[]),
               dict(id='bad_bucket', title='x', unknowns=['second_ok'], bucket='huge', deps=[]),
               dict(id='bad_dep', title='x', unknowns=['third_ok'], bucket='low', deps=['nope']),
               dict(id='no_unknowns', title='x', unknowns=[], bucket='low', deps=[])],
        proposals=[dict(summary='Add a need for the median', need='Know the median of data.txt', evidence='known sample_mean'),
                   dict(summary='no evidence', need='x'), dict(summary='changes nothing', evidence='y'),
                   dict(summary='by number', need='need:2', evidence='z'),
                   dict(summary='Add a need for the median', need='Know the median', evidence='dup')],
        why='the second need is unmeasured')
    accepted, refusals = guard(decision, observation, project)
    assert [u['id'] for u in accepted['unknowns']] == ['all_positive']
    assert accepted['unknowns'][0]['quantity'] == 'all_positive' and accepted['unknowns'][0]['source'] == 'data.txt'
    assert [t['id'] for t in accepted['tasks']] == ['check_positive']
    assert accepted['proposals'] == [dict(summary='Add a need for the median', evidence='known sample_mean',
                                          need='Know the median of data.txt')]
    assert accepted['why'] == 'the second need is unmeasured'
    joined = '\n'.join(refusals)
    for expected in ('sample_mean: already exists', "no_cite: cites 'need:9'", "'Bad-Id': id must match",
                     'wrong_type: type must be', 'no_task: minted without a task',
                     'measure_again: unknown sample_mean already has an open task', "orphan: unknown 'ghost'",
                     'bad_bucket: bucket must be', 'second_ok: minted without a task', 'bad_dep: unknown dependency nope', 'evidence is required',
                     'changes nothing',
                     'not its number', 'an open proposal already says this', 'no_unknowns: list the unknowns'):
        assert expected in joined, expected


def test_apply_writes_through_terra_and_queues_proposals(config: dict, project: Path):
    accepted = dict(unknowns=[good_unknown(unit='', quantity='all_positive')], proposals=[dict(summary='Add the median', need='Know the median',
                                                                       evidence='known sample_mean')],
                    tasks=[dict(id='check_positive', title='Check every value', unknown='all_positive', bucket='medium',
                                deps=[])], why='')
    done = apply(config, project, accepted)
    assert done == dict(unknowns=['all_positive'], tasks=['check_positive'], proposals=['Add the median'], rebucket=[])
    unknown = json.loads((project/'.terra'/'map'/'unknowns'/'all_positive.json').read_text())
    assert unknown['type'] == 'boolean' and unknown['notes'] == 'cites need:2; source data.txt'
    assert unknown['quantity'] == 'all_positive'
    task = next(t for t in json.loads((project/'.terra'/'route.json').read_text())['tasks'] if t['id'] == 'check_positive')
    assert task['map_id'] == 'all_positive' and task['bucket'] == 'medium' and task['status'] == 'ready'
    brief = json.loads((project/'.terra'/'brief.json').read_text())
    assert len(brief['proposals']) == 1 and brief['needs'] == ['Know the mean of data.txt', 'Know whether every value is positive']
    assert 'evidence: known sample_mean' in brief['proposals'][0]['summary']


def test_step_resubmits_refusals_once_and_journals(config: dict, project: Path, tmp_path: Path, monkeypatch):
    replies = [
        json.dumps(dict(unknowns=[good_unknown(), good_unknown(id='vague', cites='deliverable:4')],
                        tasks=[dict(id='check_positive', title='Check', unknown='all_positive', bucket='low', deps=[]),
                               dict(id='t_vague', title='x', unknown='vague', bucket='low', deps=[])],
                        proposals=[], why='first')),
        'Sure! ' + json.dumps(dict(unknowns=[good_unknown(id='vague', cites='deliverable:1')],
                                   tasks=[dict(id='t_vague', title='Write it', unknown='vague', bucket='low', deps=[])],
                                   proposals=[], why='second')) + ' done',
    ]
    seen = []

    class Client:
        def complete(self, payload, purpose):
            seen.append(payload['messages'][1]['content'])
            return dict(choices=[dict(message=dict(role='assistant', content=replies[len(seen)-1]), finish_reason='stop')],
                        usage=dict(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(controller, 'model_client', lambda config: Client())
    journal = tmp_path/'controller.jsonl'
    result = step(config, project, journal, 'route')
    assert result['applied'] == dict(unknowns=['all_positive', 'vague'], tasks=['check_positive', 't_vague'], proposals=[], rebucket=[])
    assert result['refused'] == [] and result['why'] == 'second'
    assert 'resubmit only corrected items' in seen[1] and "vague: cites 'deliverable:4'" in seen[1]
    assert 'all_positive [open]' in seen[1]  # the second observation already holds what was applied
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert len(records) == 1 and len(records[0]['attempts']) == 2 and records[0]['mode'] == 'route'


def test_observation_reads_known_values_from_the_nested_record(config: dict, project: Path):
    terra(project, 'unknown', 'create', 'sample_mean', '--claim', 'The mean is unknown', '--evidence', 'a run',
          '--type', 'number', '--quantity', 'sample_mean')
    terra(project, 'probe', 'create', 'mean_probe', '--purpose', 'Read data.txt', '--kind', 'run')
    path = project/'.terra'/'map'/'probes'/'mean_probe'/'probe.py'
    path.write_text(path.read_text().replace(
        '    raise NotImplementedError("TODO: implement measure()")  # scaffold stub',
        '    return {"sample_mean": 5.0 if ctx else 0.0}'))
    terra(project, 'probe', 'validate', 'mean_probe')
    output = terra(project, 'probe', 'run', 'mean_probe')
    run_id = next(line.split()[1] for line in output.splitlines() if line.startswith('run '))
    terra(project, 'unknown', 'link-run', 'sample_mean', run_id)
    terra(project, 'unknown', 'graduate', 'sample_mean')
    known = observe(config, project)['knowns']
    assert known == [dict(id='sample_mean', type='number', status='provisional', confidence='low', n=1, mean=5.0,
                          rate=None, claim='The mean is unknown', stale=False, stale_reasons=[])]
    assert 'sample_mean = 5.0 (low, n=1)' in render_observation(observe(config, project), 'eval')


def test_rebucket_only_for_budget_blocked_tasks_and_only_upward(config: dict, project: Path):
    terra(project, 'unknown', 'create', 'sample_mean', '--claim', 'The mean is unknown', '--evidence', 'a run',
          '--type', 'number', '--quantity', 'sample_mean')
    terra(project, 'route', 'add', 'measure_mean', '--title', 'Measure the mean', '--bucket', 'low', '--map', 'sample_mean')
    terra(project, 'route', 'add', 'other', '--title', 'Other', '--bucket', 'low', '--map', 'sample_mean')
    terra(project, 'route', 'start', 'measure_mean')
    terra(project, 'route', 'block', 'measure_mean', '--reason', 'worker budget exhausted at 60 turns; gate: x')
    terra(project, 'route', 'start', 'other')
    terra(project, 'route', 'block', 'other', '--reason', 'needs data we do not have')
    observation = observe(config, project)
    assert 'resumes from where it stopped if you re-bucket it' in render_observation(observation, 'eval')
    decision = dict(rebucket=[dict(task='measure_mean', bucket='medium', why='still owed'),
                              dict(task='measure_mean', bucket='low', why='x'),
                              dict(task='other', bucket='medium', why='x'), dict(task='ghost', bucket='high')])
    accepted, refusals = guard(decision, observation)
    assert accepted['rebucket'] == [dict(task='measure_mean', bucket='medium', why='still owed')]
    joined = '\n'.join(refusals)
    assert 'measure_mean: bucket must be above low' in joined and 'other: only a task blocked on budget' in joined
    assert "'ghost': no such task" in joined
    done = apply(config, project, dict(unknowns=[], tasks=[], proposals=[], rebucket=accepted['rebucket']))
    assert done['rebucket'] == ['measure_mean\u2192medium']
    task = next(t for t in json.loads((project/'.terra'/'route.json').read_text())['tasks'] if t['id'] == 'measure_mean')
    assert task['status'] == 'ready' and task['bucket'] == 'medium' and task['points'] == 8


def test_artifact_tasks_must_depend_on_reading_tasks(config: dict, project: Path):
    observation = observe(config, project)
    decision = dict(
        unknowns=[good_unknown(id='row_count', type='number', source='data.txt', cites='need:1'),
                  good_unknown(id='cli_prints_count', type='boolean', source='', creates='tool/cli.py', cites='deliverable:1',
                               evidence_needed='tool/cli.py prints row_count')],
        tasks=[dict(id='count_rows', title='Count', unknowns=['row_count'], bucket='low', deps=[]),
               dict(id='build_cli', title='Build', unknowns=['cli_prints_count'], bucket='medium', deps=[])],
        proposals=[], why='')
    accepted, refusals = guard(decision, observation, project)
    assert [t['id'] for t in accepted['tasks']] == ['count_rows']
    assert any('build_cli: it builds an artifact that must agree with the map' in r and 'count_rows' in r for r in refusals)
    assert accepted['unknowns'][0]['id'] == 'row_count'  # the orphaned artifact unknown is dropped with its task
    decision['tasks'][1]['deps'] = ['count_rows']
    accepted, refusals = guard(decision, observation, project)
    assert [t['id'] for t in accepted['tasks']] == ['count_rows', 'build_cli'] and refusals == []
    assert accepted['unknowns'][1]['source'] == 'tool/cli.py' and accepted['unknowns'][1]['creates'] == 'tool/cli.py'


def test_artifact_unknowns_must_name_what_they_agree_with(config: dict, project: Path):
    """'report/climate.md contains the required table' verifies nothing: the table's rows must be named knowns."""
    observation = observe(config, project)
    decision = dict(
        unknowns=[good_unknown(id='row_count', type='number', source='data.txt', cites='need:1'),
                  good_unknown(id='report_table', type='boolean', source='', creates='report/climate.md', cites='deliverable:1',
                               claim='The report content is unknown', evidence_needed='report/climate.md contains the required table'),
                  good_unknown(id='report_count', type='boolean', source='', creates='report/climate.md', cites='deliverable:1',
                               claim='Whether the report states the row count is unknown',
                               evidence_needed='The count in report/climate.md matches row_count')],
        tasks=[dict(id='count_rows', title='Count', unknowns=['row_count'], bucket='low', deps=[]),
               dict(id='write_report', title='Report', unknowns=['report_table', 'report_count'], bucket='medium', deps=['count_rows'])],
        proposals=[], why='')
    accepted, refusals = guard(decision, observation, project)
    assert [u['id'] for u in accepted['unknowns']] == ['row_count', 'report_count']
    assert accepted['tasks'][1]['unknowns'] == ['report_count']
    assert any(r.startswith('unknown report_table: it is about report/climate.md but names no known or unknown') for r in refusals)
    decision = dict(unknowns=[good_unknown(id='tests_exit_zero', type='boolean', source='environment', cites='deliverable:1',
                                           claim='Whether `python3 -m unittest` exits 0 is unknown', evidence_needed='the exit code is 0')],
                    tasks=[dict(id='run_tests', title='Tests', unknowns=['tests_exit_zero'], bucket='low', deps=[])], proposals=[], why='')
    accepted, refusals = guard(decision, observation, project)
    assert accepted['unknowns'] == [] and any(r.startswith('unknown tests_exit_zero: it is about deliverable:1') for r in refusals)


def test_a_task_keeps_its_accepted_unknowns_when_one_is_refused(config: dict, project: Path):
    (project/'readings').mkdir()
    (project/'readings'/'a.csv').write_text('x\n')
    observation = observe(config, project)
    decision = dict(
        unknowns=[good_unknown(id='rows', type='number', source='readings/*.csv', cites='need:1'),
                  good_unknown(id='lazy', evidence_needed='true', source='readings/*.csv', cites='need:2'),
                  good_unknown(id='nowhere', source='missing/*.csv', cites='need:99')],
        tasks=[dict(id='readings', title='Readings', unknowns=['rows', 'lazy', 'nowhere'], bucket='medium', deps=[])],
        proposals=[], why='')
    accepted, refusals = guard(decision, observation, project)
    assert [u['id'] for u in accepted['unknowns']] == ['rows', 'lazy']
    assert accepted['tasks'][0]['unknowns'] == ['rows', 'lazy']
    assert accepted['unknowns'][1]['evidence_needed'].startswith('A probe that reads readings/*.csv and reports the boolean lazy')
    joined = '\n'.join(refusals)
    assert "nowhere: cites 'need:99'" in joined and "readings: dropped unknown 'nowhere'" in joined


def test_deliverables_render_with_the_unknowns_that_cite_them(config: dict, project: Path):
    terra(project, 'unknown', 'create', 'cli_ok', '--claim', 'The CLI prints the count', '--evidence', 'run it',
          '--type', 'boolean', '--quantity', 'cli_ok', '--notes', 'cites deliverable:1; creates tool/cli.py')
    text = render_observation(observe(config, project), 'eval')
    assert '  deliverable:1 A one-page summary of data.txt' in text
    assert '      ↳ cli_ok [open]: The CLI prints the count' in text


def test_step_asks_once_when_nothing_is_minted_without_done(config: dict, project: Path, tmp_path: Path, monkeypatch):
    replies = [json.dumps(dict(unknowns=[], tasks=[], proposals=[], why='the README is still missing')),
               json.dumps(dict(unknowns=[], tasks=[], proposals=[], done=True, why='all met'))]
    seen = []

    class Client:
        def complete(self, payload, purpose):
            seen.append(payload['messages'][1]['content'])
            return dict(choices=[dict(message=dict(role='assistant', content=replies[len(seen)-1]), finish_reason='stop')],
                        usage=dict(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(controller, 'model_client', lambda config: Client())
    result = step(config, project, tmp_path/'controller.jsonl', 'eval')
    assert len(seen) == 2 and 'did not say "done": true' in seen[1]
    assert result['done'] is True and result['applied'] == dict(unknowns=[], tasks=[], proposals=[], rebucket=[])


def test_done_is_refused_while_a_deliverable_names_uncovered_things(config: dict, project: Path):
    terra(project, 'brief', 'set', '--deliverable', 'tool/cli.py with commands `stations` and `summary`; run as `python3 -m tool <command>`')
    terra(project, 'unknown', 'create', 'cli_ok', '--claim', 'The CLI output matches the map for all commands',
          '--evidence', 'run it', '--type', 'boolean', '--quantity', 'cli_ok', '--notes', 'cites deliverable:2; creates tool/cli.py')
    observation = observe(config, project)
    accepted, refusals = guard(dict(unknowns=[], tasks=[], proposals=[], done=True, why='all met'), observation, project)
    assert accepted['done'] is False
    assert any('deliverable:2 names `stations`, `summary`' in r and 'done refused' in r for r in refusals)
    decision = dict(done=True, why='', proposals=[],
                    unknowns=[dict(id='cli_stations_prints_count', claim='`python3 -m tool stations` prints the station count',
                                   evidence_needed='its output equals the map (see cli_ok)', type='boolean', creates='tool/cli.py', cites='deliverable:2'),
                              dict(id='cli_summary_prints_stats', claim='`summary` prints mean, min and max equal to the map',
                                   evidence_needed='its output equals the map (see cli_ok)', type='boolean', creates='tool/cli.py', cites='deliverable:2')],
                    tasks=[dict(id='cli_commands', title='CLI commands', unknowns=['cli_stations_prints_count', 'cli_summary_prints_stats'],
                                bucket='medium', deps=[])])
    accepted, refusals = guard(decision, observation, project)
    assert [t['id'] for t in accepted['tasks']] == ['cli_commands'] and not any('done refused' in r for r in refusals), refusals


def test_a_deliverable_path_that_does_not_exist_yet_is_taken_as_creates(config: dict, project: Path):
    """`source: readme.md` citing "README.md documenting each command" is the file the task builds, not a missing source."""
    terra(project, 'brief', 'set', '--deliverable', 'README.md documenting each command with its actual output')
    terra(project, 'unknown', 'create', 'cli_ok', '--claim', 'The CLI prints the count', '--evidence', 'run it',
          '--type', 'boolean', '--quantity', 'cli_ok', '--notes', 'cites deliverable:1; creates tool/cli.py')
    observation = observe(config, project)
    decision = dict(unknowns=[good_unknown(id='readme_count', type='boolean', source='readme.md', cites='deliverable:2',
                                           claim='Whether README.md shows the count is unknown', evidence_needed='its output block matches cli_ok')],
                    tasks=[dict(id='write_readme', title='README', unknowns=['readme_count'], bucket='low', deps=[])], proposals=[], why='')
    accepted, refusals = guard(decision, observation, project)
    assert accepted['unknowns'][0]['creates'] == 'README.md' and accepted['unknowns'][0]['source'] == 'README.md'
    assert not any('readme_count' in r for r in refusals)


def test_controller_can_look_at_files_before_deciding(config: dict, project: Path):
    from mizpah.controller import read_looks, render_observation, repo_digest
    (project/'src').mkdir()
    (project/'src'/'a.py').write_text('\n'.join('line %d' % i for i in range(200)))
    looked: dict = {}
    refused = read_looks(project, ['src/*.py', '../etc/passwd', 'nope.txt', 'data.txt'], looked)
    assert set(looked) == {'src/a.py', 'data.txt'}
    assert looked['src/a.py'].endswith('… (80 more lines)') and looked['data.txt'].startswith('3')
    assert refused == ["look '../etc/passwd': not a relative path inside the project", "look 'nope.txt': no such file"]
    observation = observe(config, project) | dict(looked=looked)
    text = render_observation(observation, 'route')
    assert '# src/a.py (you asked to see this)' in text and '# Project files (' in text and 'src/a.py (1 KB)' in text
    assert repo_digest(project)['kinds'] == {'.py': 1, '.txt': 1}


def test_a_placeholder_term_is_covered_by_its_prefix(config: dict, project: Path):
    """`python3 -m weather <command>` is a family; unknowns naming `python3 -m weather stations` cover it."""
    from mizpah.controller import uncovered_deliverable_terms
    observation = dict(brief=dict(deliverables=['tool/cli.py runnable as `python3 -m tool <command>` with `stations`']),
                       unknowns=[dict(id='cli_stations_ok', claim='`python3 -m tool stations` prints the count', notes='cites deliverable:1')])
    assert uncovered_deliverable_terms(observation) == []
    observation['unknowns'][0]['claim'] = 'the stations command prints the count'
    assert uncovered_deliverable_terms(observation) == ['deliverable:1 names `python3 -m tool <command>` but no unknown citing it mentions them']
