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
CONFIG = dict(mizpah=dict(terra=str(TERRA), brief_library=False))


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
