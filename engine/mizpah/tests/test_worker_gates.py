"""Worker gates read the map on disk; the fixtures here are the files, not a run."""
from __future__ import annotations

import json
from pathlib import Path

from mizpah import worker


def _known(project: Path, kid: str, value: float, run_id: str, inputs: dict | None) -> None:
    (project/'.terra'/'map'/'knowns').mkdir(parents=True, exist_ok=True)
    (project/'.terra'/'map'/'knowns'/(kid+'.json')).write_text(json.dumps(dict(
        id=kid, type='number', status='resolved', primary_run_id=run_id, run_ids=[run_id],
        stats=dict(kind='number', mean=value, n=3))))
    run = project/'.terra'/'map'/'runs'/run_id
    run.mkdir(parents=True, exist_ok=True)
    meta = dict(id=run_id, probe_id=kid+'_probe', status='ok')
    if inputs is not None:
        meta['input_bindings'] = {k: 'known:'+k for k in inputs}
        meta['inputs'] = inputs
    (run/'meta.json').write_text(json.dumps(meta))


def test_identical_floats_pass_only_when_the_same_declared_inputs_explain_them(tmp_path: Path) -> None:
    project = tmp_path
    _known(project, 'heap_contrast', 2.114721681067717, 'r1', {'fill': {'value': '#19324a'}})
    _known(project, 'tower_contrast', 2.114721681067717, 'r2', {'fill': {'value': '#19324a'}})
    assert worker.duplicate_reading_problems(project, ['heap_contrast', 'tower_contrast']) == []
    _known(project, 'body_contrast', 16.075361130695477, 'r3', None)
    _known(project, 'accent_contrast', 16.075361130695477, 'r4', None)
    problems = worker.duplicate_reading_problems(project, ['body_contrast', 'accent_contrast'])
    assert len(problems) == 1 and 'declare it on both probes' in problems[0]
    _known(project, 'coin_contrast', 2.114721681067717, 'r5', {'fill': {'value': '#e3a83b'}})
    problems = worker.duplicate_reading_problems(project, ['heap_contrast', 'coin_contrast'])
    assert len(problems) == 1   # same number, different declared input: not explained


def test_the_verdict_is_terras_gate_on_the_task_map(tmp_path: Path, monkeypatch) -> None:
    """task_gate no longer reconstructs the route's refusals (cites, med, adopted); it reports the route's state,
    the host's honesty checks, and Terra's gate on the work order's map — each violation as [kind] id: why."""
    project = tmp_path
    (project/'.terra').mkdir()
    (project/'.terra'/'route.json').write_text(json.dumps(dict(tasks=[dict(id='t', status='done', map_id='u', acceptance=[],
                                                                          evidence=[dict(runs=['r1'], knowns=['u'])])])))
    calls = []

    def fake_terra(config, project, *args):
        calls.append(args)
        return dict(violations=[dict(kind='known_stale', id='u', map_id='t_t', why='file moved')])

    monkeypatch.setattr(worker, 'terra', fake_terra)
    monkeypatch.setattr(worker, 'vacuous_truth_problems', lambda *a: [])
    monkeypatch.setattr(worker, 'duplicate_reading_problems', lambda *a: [])
    monkeypatch.setattr(worker, 'readopt_retaken', lambda *a: [])
    monkeypatch.setattr(worker, 'artifact_agreement_problems', lambda *a: [])
    monkeypatch.setattr(worker, 'unread_input_problems', lambda *a: [])
    gate = worker.task_gate({}, project, dict(id='t', map_id='u'), 't_t')
    assert calls == [('gate', '--map', 't_t')]
    assert gate['ok'] is False and gate['problems'] == ['[known_stale] u: file moved'] and gate['knowns'] == ['u']


def test_tick_shape_guards_are_scaffolding_off_by_default():
    """After the walk's plan the model closes steps by its own judgement: loops, lists and tick-only commands
    pass unless `scaffolding.tick_guards` puts the rail back."""
    import re
    bulk = ['for n in 1 2 3; do playbook tick w.md --done $n --note ok; done',
            'playbook tick w.md --skip 4 5 6 --because "solo piano"',
            'playbook tick w.md --done 1; playbook tick w.md --done 2; playbook tick w.md --done 3; playbook tick w.md --done 4']
    def refused(scaffolding):
        patterns = worker.refused_patterns(dict(mizpah=dict(scaffolding=scaffolding)))
        return [cmd for cmd in bulk if any(re.search(p, cmd) for p, _ in patterns)]
    assert refused({}) == []
    assert refused(dict(tick_guards=True)) == bulk
    assert any('pip' in p for p, _ in worker.refused_patterns(dict(mizpah=dict(scaffolding={}))))   # verification stays


def test_a_formula_unknown_reaches_the_task_map_with_its_expression_and_vars():
    """The copy onto a task map dropped expression and vars; the first composed target died there (2026-09-22)."""
    u = dict(id='target_visible', type='formula', expression='v >= 0.95 and s >= 10',
             vars={'v': {'known_id': 'collect_visible_fraction'}, 's': {'quantity': 'staff_px', 'kind': 'number'}})
    assert worker.formula_args(u) == ['--expression', 'v >= 0.95 and s >= 10',
                                      '--var', 'v=known:collect_visible_fraction', '--var', 's=staff_px:number']
    assert worker.formula_args(dict(id='x', type='number')) == []


def test_the_remeasure_takes_the_inputs_of_a_target_not_the_target():
    assert worker.composed(dict(id='target_visible', type='formula', expression='v >= 0.95'))
    assert not worker.composed(dict(id='collect_visible_fraction', type='number'))
