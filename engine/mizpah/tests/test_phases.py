"""A phased brief is routed one phase at a time and closes mechanically.

No model: the controller's observe/render/guard/close are driven directly against a real Terra project.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT.parent.parent))   # cg namespace for the harness widgets the controller imports

from mizpah import controller, phases  # noqa: E402

TERRA = ROOT.parent.parent/'.venv'/'bin'/'terra'
import tempfile
CONFIG = dict(mizpah=dict(terra=str(TERRA), brief_library=False, capability_store=tempfile.mkdtemp(prefix='mizpah-caps-')))


def terra(project: Path, *args: str) -> dict:
    out = subprocess.run([str(TERRA), *args], cwd=project, capture_output=True, text=True)
    text = out.stdout.strip()
    start = text.find('{')
    if start < 0:
        assert out.returncode == 0, out.stderr
        return {}
    payload = json.loads(text[start:])
    assert payload.get('status') == 'success', payload
    return payload['data']


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path/'phased'
    p.mkdir()
    terra(p, 'init')
    terra(p, 'brief', 'init', '--title', 'Two phases', '--mission', 'survey then change')
    terra(p, 'brief', 'set', '--status', 'active', '--budget-points', '100',
          '--need', 'Know the number of lines', '--need', 'Know the branch count before',
          '--need', 'Know the branch count after, against branch_count_before',
          '--deliverable', 'report/survey.md: the survey', '--deliverable', 'the function refactored (need 3 against need 2)')
    terra(p, 'brief', 'phase', 'survey', '--title', 'Survey', '--needs', '1-2', '--deliverables', '1')
    terra(p, 'brief', 'phase', 'change', '--title', 'Change', '--needs', '3', '--deliverables', '2')
    terra(p, 'route', 'init')
    return p


def test_render_marks_current_and_later(project: Path) -> None:
    observation = controller.observe(CONFIG, project)
    text = controller.render_observation(observation, 'route')
    assert 'survey [CURRENT]' in text and 'change [later]' in text
    assert 'need:3 Know the branch count after, against branch_count_before  (phase change, not open yet' in text
    assert 'need:1 Know the number of lines\n' in text


def test_guard_refuses_a_later_phase_cite(project: Path) -> None:
    observation = controller.observe(CONFIG, project)
    decision = dict(unknowns=[
        dict(id='line_count', cites='need:1', type='number', claim='lines under src', evidence_needed='wc -l over src'),
        dict(id='branch_count_after', cites='need:3', type='number', claim='branches after', evidence_needed='ast walk'),
    ], tasks=[dict(id='t1', unknowns=['line_count'], bucket='low', title='count lines'),
              dict(id='t2', unknowns=['branch_count_after'], bucket='low', title='count after')])
    accepted, refusals = controller.guard(decision, observation, project)
    assert [u['id'] for u in accepted['unknowns']] == ['line_count']
    assert any('branch_count_after' in r and 'phase change' in r for r in refusals)


def test_tasks_carry_the_phase_and_close_moves_on(project: Path) -> None:
    observation = controller.observe(CONFIG, project)
    decision = dict(unknowns=[
        dict(id='line_count', cites='need:1', type='number', claim='lines under src', evidence_needed='wc -l over src'),
        dict(id='branch_count_before', cites='need:2', type='number', claim='branches before', evidence_needed='ast walk'),
        dict(id='survey_report', cites='deliverable:1', type='boolean', creates='report/survey.md',
             claim='report/survey.md states line_count and branch_count_before', evidence_needed='read and compare'),
    ], tasks=[dict(id='count', unknowns=['line_count', 'branch_count_before'], bucket='low', title='count'),
              dict(id='write', unknowns=['survey_report'], bucket='low', title='write', deps=['count'])])
    accepted, refusals = controller.guard(decision, observation, project)
    assert not refusals, refusals
    controller.apply(CONFIG, project, accepted)
    tasks = terra(project, 'route', 'status')['tasks']
    assert {t['phase'] for t in tasks} == {'survey'}
    # Nothing resolved: the phase stays open and says why.
    outcome = controller.close_ready_phase(CONFIG, project)
    assert outcome and not outcome['closed'] and any('need:1' in p for p in outcome['problems'])
    # Resolve everything the phase owns by hand (the worker's job in a run); the tasks close.
    for uid in ('line_count', 'branch_count_before', 'survey_report'):
        path = project/'.terra'/'map'/'unknowns'/(uid+'.json')
        doc = json.loads(path.read_text())
        doc['status'] = 'resolved'
        path.write_text(json.dumps(doc))
    for tid in ('count', 'write'):
        terra(project, 'route', 'cancel', tid, '--reason', 'test')
    outcome = controller.close_ready_phase(CONFIG, project)
    assert outcome == dict(phase='survey', closed=True, next='change')
    brief = terra(project, 'brief', 'show')
    assert phases.current(brief)['id'] == 'change'
    text = controller.render_observation(controller.observe(CONFIG, project), 'eval')
    assert 'survey [closed]' in text and 'change [CURRENT]' in text and '(phase survey, closed)' in text
    # A need of the now-current phase is mintable; the closed phase's need still is too.
    observation = controller.observe(CONFIG, project)
    accepted, refusals = controller.guard(dict(unknowns=[
        dict(id='branch_count_after', cites='need:3', type='number', claim='after', evidence_needed='ast walk')],
        tasks=[dict(id='after', unknowns=['branch_count_after'], bucket='low', title='after')]), observation, project)
    assert not refusals, refusals


@pytest.fixture
def instrumented(tmp_path: Path) -> Path:
    p = tmp_path/'instrumented'
    p.mkdir()
    terra(p, 'init')
    terra(p, 'brief', 'init', '--title', 'Needs an instrument', '--mission', 'measure a page')
    terra(p, 'brief', 'set', '--status', 'active', '--budget-points', '100',
          '--need', 'Know the number of files', '--need', 'Know the contrast ratio as rendered (page_readings)',
          '--deliverable', 'report/x.md: the readings',
          '--enabler', 'page_readings:Headless page readings:cg/frontend-headless-page-cli-python')
    terra(p, 'route', 'init')
    terra(p, 'brief', 'phase', 'all', '--title', 'All', '--needs', '1-2', '--deliverables', '1')
    terra(p, 'route', 'sector-add', 'all', '--title', 'All', '--points', '60')
    return p


def test_enabler_gates_the_needs_that_name_it(instrumented: Path) -> None:
    observation = controller.observe(CONFIG, instrumented)
    text = controller.render_observation(observation, 'route')
    assert 'page_readings [needed] Headless page readings — at cg/frontend-headless-page-cli-python' in text
    assert '(waits for enabler page_readings)' in text
    decision = dict(unknowns=[
        dict(id='file_count', cites='need:1', type='number', claim='files', evidence_needed='count them'),
        dict(id='contrast', cites='need:2', type='number', claim='contrast', evidence_needed='render and read'),
        dict(id='page_reader_ready', cites='need:2', type='boolean', enabler='page_readings',
             claim='the page reader exists at its path and validates', evidence_needed='cartograph validate exits 0'),
    ], tasks=[dict(id='count', unknowns=['file_count'], bucket='low', title='count'),
              dict(id='measure', unknowns=['contrast'], bucket='low', title='measure'),
              dict(id='reader', unknowns=['page_reader_ready'], bucket='medium', title='page reader')])
    accepted, refusals = controller.guard(decision, observation, instrumented)
    assert {u['id'] for u in accepted['unknowns']} == {'file_count', 'page_reader_ready'}
    assert any('contrast' in r and 'names enabler page_readings (needed)' in r for r in refusals)
    reader = next(t for t in accepted['tasks'] if t['id'] == 'reader')
    assert reader['enabler'] == 'page_readings'
    controller.apply(CONFIG, instrumented, accepted)
    tasks = {t['id']: t for t in terra(instrumented, 'route', 'status')['tasks']}
    assert tasks['reader']['enabler_id'] == 'page_readings' and tasks['reader']['role'] == 'enabler'
    assert tasks['reader']['sector_id'] == 'all' and tasks['count']['sector_id'] == 'all'
    brief = terra(instrumented, 'brief', 'show')
    assert brief['enablers'][0]['status'] == 'building'
    # The sector caps the phase: a third task would exceed its 60 points (3 + 8 so far; high is 21 → 32, fine; two highs → over).
    with pytest.raises(AssertionError):
        terra(instrumented, 'route', 'add', 'big1', '--title', 'b', '--map', 'file_count', '--bucket', 'high', '--sector', 'all')
        terra(instrumented, 'route', 'add', 'big2', '--title', 'b', '--map', 'file_count', '--bucket', 'high', '--sector', 'all')
        terra(instrumented, 'route', 'add', 'big3', '--title', 'b', '--map', 'file_count', '--bucket', 'high', '--sector', 'all')
    # The enabler task completes with a harvested widget: ready, then graduated to that widget.
    from mizpah import loop
    outcome = loop.advance_enabler(CONFIG, instrumented, dict(enabler_id='page_readings'),
                                  dict(widgets=dict(checked_in=['frontend-headless-page-cli-python'])), instrumented/'errors.jsonl')
    assert outcome == dict(enabler='page_readings', status='graduated', widget='frontend-headless-page-cli-python')
    brief = terra(instrumented, 'brief', 'show')
    assert brief['enablers'][0]['status'] == 'graduated' and brief['enablers'][0]['graduates_to'] == 'frontend-headless-page-cli-python'
    # Now the contrast reading is mintable, and the need no longer waits.
    observation = controller.observe(CONFIG, instrumented)
    assert '(waits for enabler' not in controller.render_observation(observation, 'eval')
    accepted, refusals = controller.guard(dict(unknowns=[
        dict(id='contrast', cites='need:2', type='number', claim='contrast', evidence_needed='render and read')],
        tasks=[dict(id='measure', unknowns=['contrast'], bucket='low', title='measure')]), observation, instrumented)
    assert not refusals, refusals


def test_reading_of_a_built_file_waits_for_its_builder_and_unblock_needs_a_done_task(project: Path) -> None:
    observation = controller.observe(CONFIG, project)
    decision = dict(unknowns=[
        dict(id='line_count', cites='need:1', type='number', claim='lines under src', evidence_needed='wc -l over src'),
        dict(id='survey_report', cites='deliverable:1', type='boolean', creates='report/survey.md',
             claim='report/survey.md states line_count', evidence_needed='read and compare'),
        dict(id='report_headings', cites='need:2', type='number', claim='headings in report/survey.md', evidence_needed='count # lines',
             source='report/survey.md'),
    ], tasks=[dict(id='count', unknowns=['line_count'], bucket='low', title='count'),
              dict(id='write', unknowns=['survey_report'], bucket='low', title='write', deps=['count']),
              dict(id='audit', unknowns=['report_headings'], bucket='low', title='audit the report')])
    accepted, refusals = controller.guard(decision, observation, project)
    audit = next(t for t in accepted['tasks'] if t['id'] == 'audit')
    assert audit['deps'] == ['write'], (audit, refusals)
    assert any('audit' in r and 'added that dependency' in r for r in refusals)
    controller.apply(CONFIG, project, accepted)
    terra(project, 'route', 'block', 'count', '--reason', 'the file is absent')
    observation = controller.observe(CONFIG, project)
    accepted, refusals = controller.guard(dict(unblock=[dict(task='count', after='write')]), observation, project)
    assert not accepted['unblock'] and any('"after" must name a task that has since completed' in r for r in refusals)


def test_a_built_page_is_anchored_on_its_source_and_its_readings_follow_it(tmp_path: Path) -> None:
    p = tmp_path/'built'
    p.mkdir()
    (p/'content').mkdir()
    (p/'content'/'pitch.md').write_text('# Hero\n# How\n')
    terra(p, 'init')
    terra(p, 'brief', 'init', '--title', 'Page', '--mission', 'build and measure')
    terra(p, 'brief', 'set', '--status', 'active', '--budget-points', '100',
          '--need', 'Know the number of <section> elements site/index.html has',
          '--deliverable', 'site/index.html: a page whose sections follow content/pitch.md')
    terra(p, 'route', 'init')
    observation = controller.observe(CONFIG, p)
    decision = dict(unknowns=[
        dict(id='site_built', cites='deliverable:1', type='boolean', creates='site/index.html',
             claim='site/index.html exists and its section headings follow content/pitch.md', evidence_needed='compare headings'),
        dict(id='section_count', cites='need:1', type='number', claim='sections in site/index.html', evidence_needed='parse it',
             source='site/index.html'),
    ], tasks=[dict(id='build', unknowns=['site_built'], bucket='medium', title='build'),
              dict(id='count', unknowns=['section_count'], bucket='low', title='count')])
    accepted, refusals = controller.guard(decision, observation, p)
    assert {u['id'] for u in accepted['unknowns']} == {'site_built', 'section_count'}, refusals
    by_id = {t['id']: t for t in accepted['tasks']}
    assert by_id['build']['deps'] == [] and by_id['count']['deps'] == ['build'], (accepted['tasks'], refusals)


def test_registry_records_a_graduation_and_shows_it_to_the_next_brief(instrumented: Path, tmp_path: Path) -> None:
    from mizpah import capabilities, loop
    config = dict(CONFIG, mizpah=dict(CONFIG['mizpah'], capability_store=str(tmp_path/'registry')))
    outcome = loop.advance_enabler(config, instrumented, dict(enabler_id='page_readings'),
                                  dict(widgets=dict(checked_in=['frontend-headless-page-cli-python'])), tmp_path/'errors.jsonl')
    assert outcome['status'] == 'graduated'
    rows = capabilities.registered(config)
    assert [(r['id'], r['graduates_to'], r['project'], r['uses']) for r in rows] == \
        [('page_readings', 'frontend-headless-page-cli-python', 'instrumented', 1)]
    # A new brief declaring the same enabler is shown the registry entry.
    p = tmp_path/'next'
    p.mkdir()
    terra(p, 'init')
    terra(p, 'brief', 'init', '--title', 'Next', '--mission', 'measure another page')
    terra(p, 'brief', 'set', '--status', 'active', '--budget-points', '50', '--need', 'Know contrast (page_readings)',
          '--enabler', 'page_readings:Headless page readings:cg/frontend-headless-page-cli-python')
    terra(p, 'route', 'init')
    text = controller.render_observation(controller.observe(config, p), 'route')
    assert 'registry: page_readings Headless page readings — widget frontend-headless-page-cli-python, graduated by instrumented, used 1×' in text


def test_a_graduated_enabler_is_packed_and_installed_into_the_next_project(instrumented: Path, tmp_path: Path) -> None:
    from mizpah import capabilities, loop
    config = dict(CONFIG, mizpah=dict(CONFIG['mizpah'], capability_store=str(tmp_path/'registry2')))
    tool = instrumented/'cg'/'frontend-headless-page-cli-python'
    (tool/'src').mkdir(parents=True)
    (tool/'README.md').write_text('# page reader\n\n`python src/page.py text <url>` prints the page text.\n')
    (tool/'src'/'page.py').write_text('print("hi")\n')
    (tool/'__pycache__').mkdir()
    (tool/'__pycache__'/'x.pyc').write_bytes(b'')
    outcome = loop.advance_enabler(config, instrumented, dict(enabler_id='page_readings'),
                                  dict(widgets=dict(checked_in=['frontend-headless-page-cli-python'])), tmp_path/'errors.jsonl')
    assert outcome['status'] == 'graduated'
    pack = Path(config['mizpah']['capability_store'])/'page_readings'/'pack'
    assert (pack/'src'/'page.py').exists() and (pack/'README.md').exists() and not (pack/'__pycache__').exists()
    man = json.loads((pack/'enabler.json').read_text())
    assert man['id'] == 'page_readings' and man['interface'][0] == '# page reader' and 'src/page.py' in man['files']
    # The next project declares the same enabler: the loop installs the pack and it is graduated before any route step.
    p = tmp_path/'next2'
    p.mkdir()
    terra(p, 'init')
    terra(p, 'brief', 'init', '--title', 'Next', '--mission', 'measure another page')
    terra(p, 'brief', 'set', '--status', 'active', '--budget-points', '50', '--need', 'Know contrast (page_readings)',
          '--enabler', 'page_readings:Headless page readings:cg/frontend-headless-page-cli-python')
    terra(p, 'route', 'init')
    installed = loop.install_registered_enablers(config, p, tmp_path/'errors.jsonl')
    assert installed == [dict(enabler='page_readings', status='installed_from_registry', widget='frontend-headless-page-cli-python', by='instrumented')]
    assert (p/'cg'/'frontend-headless-page-cli-python'/'src'/'page.py').read_text() == 'print("hi")\n'
    brief = terra(p, 'brief', 'show')
    assert brief['enablers'][0]['status'] == 'graduated'
    assert capabilities.registered(config)[0]['uses'] == 2
    # And the need that names it is mintable straight away.
    observation = controller.observe(config, p)
    assert '(waits for enabler' not in controller.render_observation(observation, 'route')


def test_a_pack_ships_its_procedures_and_installs_them(instrumented: Path, tmp_path: Path) -> None:
    from mizpah import capabilities
    store = tmp_path/'playbook'
    store.mkdir()
    (store/'render-page-layout-readings.json').write_text(json.dumps(dict(id='render-page-layout-readings', steps=[])))
    config = dict(CONFIG, mizpah=dict(CONFIG['mizpah'], capability_store=str(tmp_path/'registry3'), playbook_store=str(store)))
    tool = instrumented/'cg'/'frontend-headless-page-cli-python'
    tool.mkdir(parents=True)
    (tool/'README.md').write_text('# reader\n')
    enabler = dict(id='page_readings', title='Headless page readings', path='cg/frontend-headless-page-cli-python')
    capabilities.record(config, instrumented, enabler, widget='w', procedures=['render-page-layout-readings', 'not-in-store'])
    pack = tmp_path/'registry3'/'page_readings'/'pack'
    assert (pack/'procedures'/'render-page-layout-readings.json').exists()
    assert json.loads((pack/'enabler.json').read_text())['procedures'] == ['render-page-layout-readings']
    # A fresh store on the next machine: installing the pack installs the procedure.
    other = tmp_path/'playbook2'
    other.mkdir()
    config2 = dict(config, mizpah=dict(config['mizpah'], playbook_store=str(other)))
    p = tmp_path/'next3'
    (p/'x').mkdir(parents=True)
    doc = capabilities.install(config2, p, enabler)
    assert doc['procedures_installed'] == ['render-page-layout-readings'] and (other/'render-page-layout-readings.json').exists()


def test_non_goals_reach_the_worker_and_refuse_artifacts_that_build_them(tmp_path: Path) -> None:
    from mizpah import worker
    p = tmp_path/'ng'
    p.mkdir()
    terra(p, 'init')
    terra(p, 'brief', 'init', '--title', 'Page', '--mission', 'build')
    terra(p, 'brief', 'set', '--status', 'active', '--budget-points', '50',
          '--need', 'Know the number of requests outside site/', '--deliverable', 'site/index.html: the page',
          '--non-goal', 'No `framework`: hand-written HTML')
    terra(p, 'route', 'init')
    observation = controller.observe(CONFIG, p)
    accepted, refusals = controller.guard(dict(unknowns=[
        dict(id='external_request_count', cites='need:1', type='number', claim='requests to any framework CDN', evidence_needed='count'),
        dict(id='framework_bundle', cites='deliverable:1', type='boolean', creates='site/framework.js',
             claim='site/framework.js bundles a framework for the page', evidence_needed='exists'),
    ], tasks=[dict(id='count', unknowns=['external_request_count'], bucket='low', title='count'),
              dict(id='bundle', unknowns=['framework_bundle'], bucket='low', title='bundle')]), observation, p)
    assert [u['id'] for u in accepted['unknowns']] == ['external_request_count']
    assert any('framework_bundle' in r and 'non-goal' in r for r in refusals)
    text = worker.render_reference(p, dict(id='count', bucket='low', title='count', map_id='external_request_count'),
                                   [dict(id='external_request_count', claim='c', type='number', evidence_needed='e',
                                         notes='cites need:1')])
    assert 'Non-goal: No `framework`' in text
    assignment = worker.render_assignment(dict(id='count', bucket='low', title='count', map_id='external_request_count'),
                                          [dict(id='external_request_count', claim='c', type='number', evidence_needed='e')], 'm')
    assert 'framework' not in assignment


def test_proposals_say_whether_they_block_and_keep_the_project_open(project: Path) -> None:
    from mizpah import loop
    observation = controller.observe(CONFIG, project)
    accepted, refusals = controller.guard(dict(proposals=[
        dict(summary='need 2 is unmeasurable as written', need='Know the branch count before, by ast', evidence='the worker blocked'),
    ], done=True), observation, project)
    assert accepted['proposals'][0]['blocking'] is False   # omitted means the work goes on around it
    accepted, refusals = controller.guard(dict(proposals=[
        dict(summary='need 2 is unmeasurable as written (again)', need='Know the branch count before, by ast', evidence='the worker blocked',
             blocking=False),
    ], done=True), observation, project)
    assert accepted['proposals'][0]['blocking'] is False and accepted['done'] is False
    assert any('done refused: a proposal is open' in r for r in refusals)
    controller.apply(CONFIG, project, accepted)
    assert loop.open_proposals(project) and not loop.blocking_proposal_open(project)
    observation = controller.observe(CONFIG, project)
    accepted, _ = controller.guard(dict(proposals=[dict(summary='the mission cannot be met', mission='x', evidence='e', blocking=True)]),
                                   observation, project)
    controller.apply(CONFIG, project, accepted)
    assert loop.blocking_proposal_open(project)
    text = controller.render_observation(controller.observe(CONFIG, project), 'eval')
    assert '[blocking] the mission cannot be met' in text


def test_a_number_citing_a_deliverable_is_a_reading_not_an_artifact(project: Path) -> None:
    observation = controller.observe(CONFIG, project)
    accepted, refusals = controller.guard(dict(unknowns=[
        dict(id='report_line_length', cites='deliverable:1', type='number', claim='mean line length of report/survey.md',
             evidence_needed='count characters per line', source='report/survey.md')],
        tasks=[dict(id='measure', unknowns=['report_line_length'], bucket='low', title='measure')]), observation, project)
    assert [u['id'] for u in accepted['unknowns']] == ['report_line_length'], refusals


def test_a_third_attempt_at_the_same_reading_is_refused(project: Path) -> None:
    observation = controller.observe(CONFIG, project)
    first = dict(unknowns=[dict(id='report_ok', cites='need:1', type='boolean', claim='ok', evidence_needed='read')],
                 tasks=[dict(id='t1', unknowns=['report_ok'], bucket='low', title='a')])
    accepted, _ = controller.guard(first, observation, project)
    controller.apply(CONFIG, project, accepted)
    observation = controller.observe(CONFIG, project)
    accepted, _ = controller.guard(dict(unknowns=[dict(id='report_ok_v2', cites='need:1', type='boolean', claim='ok', evidence_needed='read')],
                                        tasks=[dict(id='t2', unknowns=['report_ok_v2'], bucket='low', title='b')]), observation, project)
    controller.apply(CONFIG, project, accepted)
    observation = controller.observe(CONFIG, project)
    accepted, refusals = controller.guard(dict(unknowns=[dict(id='report_ok_current', cites='need:1', type='boolean', claim='ok', evidence_needed='read')],
                                               tasks=[dict(id='t3', unknowns=['report_ok_current'], bucket='low', title='c')]), observation, project)
    assert not accepted['unknowns'] and any('third attempt at report_ok' in r for r in refusals)
