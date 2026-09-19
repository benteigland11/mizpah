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
