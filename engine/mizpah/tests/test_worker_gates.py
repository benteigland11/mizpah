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
