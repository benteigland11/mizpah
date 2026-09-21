"""Two tasks improve the same widget in parallel: the second's check-in must not erase the first's. The harvest
sees the base moved, stages the library's copy at cg/<dir>/.upstream/ and returns a conflict for a merge round;
a copy that starts from the library's version checks in as usual."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT.parent.parent))
from mizpah import worker  # noqa: E402

WIDGET = 'data-thing-python'
DIR = 'data_thing_python'


def _widget_files(version: str, body: str) -> dict[str, str]:
    meta = dict(id=WIDGET, name='thing', version=version, domain='data', tags=['x'])
    return {
        'widget.json': json.dumps(dict(meta=meta, description='a thing', language='python', entrypoint='src/thing.py')),
        'src/__init__.py': '',
        'src/thing.py': body,
        'tests/test_thing.py': 'from src.thing import f\n\ndef test_f():\n    assert f(1) == 2\n',
        'README.md': '# thing\n',
        'changelog.json': json.dumps([dict(version=version, reason='library', timestamp='t')]),
    }


def _snapshot(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo('cg/'+DIR+'/'+name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _journal(root: Path) -> None:
    (root/'events').mkdir(parents=True, exist_ok=True)
    turn = dict(event_type='worker_turn', payload=dict(response=dict(tool_calls=[dict(id='c1', function=dict(
        name='edit', arguments=json.dumps(dict(path='cg/'+DIR+'/src/thing.py'))))]), tool_results=[dict(call_id='c1', result=dict(status='ok'))]))
    (root/'events'/'session.jsonl').write_text(json.dumps(turn)+'\n')


def test_a_moved_base_is_staged_for_merge_not_overwritten(tmp_path: Path, monkeypatch) -> None:
    library = tmp_path/'library'
    shipped = library/WIDGET
    for name, text in _widget_files('1.0.1', 'def f(x):\n    return x+1\n\ndef g(x):\n    return x*2   # their improvement\n').items():
        (shipped/name).parent.mkdir(parents=True, exist_ok=True)
        (shipped/name).write_text(text)
    (shipped/'changelog.json').write_text(json.dumps([dict(version='1.0.1', reason='other task added g', timestamp='t2'),
                                                       dict(version='1.0.0', reason='created', timestamp='t1')]))
    config = dict(mizpah=dict(widget_library=str(library), cartograph='/bin/false'))
    root = tmp_path/'sess'/'tasks'/'t1'
    _journal(root)
    project = tmp_path/'project'
    (project/'cg'/DIR).mkdir(parents=True)
    # The session installed 1.0.0 and improved f; it never saw g.
    mine = _widget_files('1.0.0', 'def f(x):\n    return x+1   # mine: documented\n')
    result = worker.harvest_widgets(_snapshot(mine), root, config, project)
    assert result['checked_in'] == [] and result['rejected'] == []
    [conflict] = result['conflicts']
    assert conflict['id'] == WIDGET and conflict['base'] == '1.0.0' and conflict['library'] == '1.0.1'
    assert conflict['changes'] == ['1.0.1: other task added g']
    assert 'library/src/thing.py' in conflict['diff'] and 'mine: documented' in conflict['diff'] and 'their improvement' in conflict['diff']
    staged = project/'cg'/DIR/worker.UPSTREAM
    assert (staged/'src'/'thing.py').read_text().endswith('# their improvement\n')
    message = worker.merge_message(result['conflicts'])
    assert 'started from 1.0.0' in message and 'other task added g' in message and worker.UPSTREAM in message
    # Merged onto their version: no conflict any more; it goes to validate/check-in (here refused by /bin/false, not a conflict).
    merged = _widget_files('1.0.1', 'def f(x):\n    return x+1   # mine: documented\n\ndef g(x):\n    return x*2   # their improvement\n')
    merged['cg-junk'] = ''
    again = worker.harvest_widgets(_snapshot({k: v for k, v in merged.items() if k != 'cg-junk'}), root, config, project)
    assert again['conflicts'] == [] and again['rejected'] and 'validate' not in again['checked_in']


def test_a_moved_base_merges_three_way_when_the_library_keeps_the_base(tmp_path: Path) -> None:
    """With the version the session started from under history/, files merge three-way: hunks that do not
    overlap (their new function, your documented one) land with no worker round; the same lines changed by
    both go to the worker with markers in the file."""
    library = tmp_path/'library'
    shipped = library/WIDGET
    base_body = 'def f(x):\n    return x+1\n'
    for name, text in _widget_files('1.0.1', base_body+'\ndef g(x):\n    return x*2   # their improvement\n').items():
        (shipped/name).parent.mkdir(parents=True, exist_ok=True)
        (shipped/name).write_text(text)
    for name, text in _widget_files('1.0.0', base_body).items():
        (shipped/'history'/'1.0.0'/name).parent.mkdir(parents=True, exist_ok=True)
        (shipped/'history'/'1.0.0'/name).write_text(text)
    (shipped/'changelog.json').write_text(json.dumps([dict(version='1.0.1', reason='other task added g', timestamp='t2'),
                                                       dict(version='1.0.0', reason='created', timestamp='t1')]))
    config = dict(mizpah=dict(widget_library=str(library), cartograph='/bin/false'))
    root = tmp_path/'sess'/'tasks'/'t1'
    _journal(root)
    project = tmp_path/'project'
    (project/'cg'/DIR).mkdir(parents=True)
    # Non-overlapping: you documented f at the top; they added g below. Merges clean → validate (refused here by /bin/false).
    mine = _widget_files('1.0.0', '"""thing: documented"""\n'+base_body)
    result = worker.harvest_widgets(_snapshot(mine), root, config, project)
    assert result['conflicts'] == [] and result['merged'] == [WIDGET] and result['rejected']
    # Overlapping: both rewrote f's return line differently → a conflict with markers in the workspace file.
    theirs = _widget_files('1.0.1', 'def f(x):\n    return x+2   # theirs\n')
    for name, text in theirs.items():
        (shipped/name).write_text(text)
    mine = _widget_files('1.0.0', 'def f(x):\n    return x+3   # mine\n')
    result = worker.harvest_widgets(_snapshot(mine), root, config, project)
    [conflict] = result['conflicts']
    assert conflict['clashes'] == ['src/thing.py'] and result['merged'] == []
    text = (project/'cg'/DIR/'src'/'thing.py').read_text()
    assert '<<<<<<< yours' in text and '>>>>>>> library' in text and 'x+3   # mine' in text and 'x+2   # theirs' in text
    assert 'conflict markers' in worker.merge_message(result['conflicts'])


def test_a_validation_stamp_and_a_staged_upstream_are_not_the_widget(tmp_path: Path) -> None:
    """The stamp `cartograph validate` writes and the copy a merge round stages are Cartograph's files about
    the widget, not the widget: a workspace whose only difference from the library is its stamp is unchanged,
    and stamps both sides wrote are not a conflict (that "conflict" cost a nine-turn repair round)."""
    library = tmp_path/'library'
    shipped = library/WIDGET
    body = 'def f(x):\n    return x+1\n'
    for name, text in _widget_files('1.0.2', body).items():
        (shipped/name).parent.mkdir(parents=True, exist_ok=True)
        (shipped/name).write_text(text)
    for name, text in _widget_files('1.0.1', body).items():
        (shipped/'history'/'1.0.1'/name).parent.mkdir(parents=True, exist_ok=True)
        (shipped/'history'/'1.0.1'/name).write_text(text)
    (shipped/'.validation_stamp.json').write_text('{"at": "library"}')
    config = dict(mizpah=dict(widget_library=str(library), cartograph='/bin/false'))
    root = tmp_path/'sess'/'tasks'/'t1'
    _journal(root)
    project = tmp_path/'project'
    (project/'cg'/DIR).mkdir(parents=True)
    mine = _widget_files('1.0.1', body)
    mine['.validation_stamp.json'] = '{"at": "workspace"}'
    mine[worker.UPSTREAM+'/src/thing.py'] = body
    result = worker.harvest_widgets(_snapshot(mine), root, config, project)
    assert result['unchanged'] == [WIDGET] and result['conflicts'] == [] and result['merged'] == []
