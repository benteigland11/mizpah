"""Two tasks improve the same procedure in parallel: the second's harvest must not erase the first's. Steps merge
three-way by id (base = the store as the task received it, theirs = the store now, yours = the workspace); a step
both changed differently keeps theirs and comes back as a conflict for a merge round, with the merged copy written
to the worker's store."""
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


def _proc(steps: list[tuple[str, str, str]], description='validate a midi', title='Validate MIDI', tags=('midi',)) -> dict:
    return dict(id='midi-check', title=title, description=description, tags=list(tags),
                steps=[dict(id=i, title=t, do=d) for i, t, d in steps])


BASE = _proc([('s1', 'Parse', 'Read the file with mido.'), ('s2', 'Count', 'Count the onsets.'), ('s3', 'Report', 'Print the count.')])


def test_merge_takes_each_side_where_only_one_changed_and_keeps_theirs_on_a_clash():
    theirs = _proc([('s1', 'Parse', 'Read the file with mido, ticks absolute.'), ('s2', 'Count', 'Count the onsets.'),
                    ('s3', 'Report', 'Print the count.'), ('s4', 'Accents', 'Read the accent pattern.')], description='validate a midi and its accents')
    yours = _proc([('s1', 'Parse', 'Read the file with mido.'), ('s2', 'Count', 'Count the onsets per bar.'),
                   ('s2b', 'Rests', 'Count the rests too.'), ('s3', 'Report', 'Print the count and the rests.')], tags=('midi', 'rhythm'))
    merged, conflicts = worker.merge_procedure(BASE, theirs, yours)
    assert conflicts == []
    assert [s['id'] for s in merged['steps']] == ['s1', 's2', 's2b', 's3', 's4']   # yours added after the step it followed
    by = {s['id']: s['do'] for s in merged['steps']}
    assert by['s1'].endswith('ticks absolute.') and by['s2'] == 'Count the onsets per bar.' and by['s3'] == 'Print the count and the rests.'
    assert merged['description'] == 'validate a midi and its accents' and merged['tags'] == ['midi', 'rhythm']
    # Both changed s2 differently: theirs kept, reported.
    clash = _proc([('s1', 'Parse', 'Read the file with mido.'), ('s2', 'Count', 'Count the onsets with pretty_midi.'), ('s3', 'Report', 'Print the count.')])
    merged, conflicts = worker.merge_procedure(BASE, clash, yours)
    assert {s['id']: s['do'] for s in merged['steps']}['s2'] == 'Count the onsets with pretty_midi.'
    assert len(conflicts) == 1 and conflicts[0].startswith('step "Count"') and 'per bar' in conflicts[0]
    # You removed s3 and they left it alone: it goes. They changed it: it stays.
    removed = _proc([('s1', 'Parse', 'Read the file with mido.'), ('s2', 'Count', 'Count the onsets.')])
    assert [s['id'] for s in worker.merge_procedure(BASE, BASE, removed)[0]['steps']] == ['s1', 's2']
    they_changed = _proc([('s1', 'Parse', 'Read the file with mido.'), ('s2', 'Count', 'Count the onsets.'), ('s3', 'Report', 'Print it as JSON.')])
    assert [s['id'] for s in worker.merge_procedure(BASE, they_changed, removed)[0]['steps']] == ['s1', 's2', 's3']


def _snapshot(doc: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        data = json.dumps(doc).encode()
        info = tarfile.TarInfo(worker.PLAYBOOK_PREFIX+'/playbook/procedures/midi-check.json')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_harvest_merges_onto_a_store_that_moved(tmp_path: Path, monkeypatch):
    store = tmp_path/'data'/'playbook'/'procedures'
    store.mkdir(parents=True)
    base = tmp_path/'task'/worker.PLAYBOOK_BASE
    base.mkdir(parents=True)
    ws = tmp_path/'ws'
    ws.mkdir()
    (store/'midi-check.json').write_text(json.dumps(BASE))
    (base/'midi-check.json').write_text(json.dumps(BASE))
    # Another task lands first.
    theirs = _proc([('s1', 'Parse', 'Read the file with mido, ticks absolute.'), ('s2', 'Count', 'Count the onsets.'), ('s3', 'Report', 'Print the count.')])
    (store/'midi-check.json').write_text(json.dumps(theirs))
    validated = []
    monkeypatch.setattr(worker.subprocess, 'run', lambda *a, **k: (validated.append(a[0][-1]), type('R', (), dict(returncode=0, stdout='', stderr=''))())[1])
    config = {'mizpah': {'playbook': 'playbook'}}
    yours = _proc([('s1', 'Parse', 'Read the file with mido.'), ('s2', 'Count', 'Count the onsets per bar.'), ('s3', 'Report', 'Print the count.')])
    out = worker._harvest_playbook(_snapshot(yours), store, config, allowed=('midi-check',), base=base, workspace_store=ws)
    assert out['merged'] == ['midi-check'] and out['installed'] == ['midi-check'] and out['conflicts'] == []
    landed = json.loads((store/'midi-check.json').read_text())
    assert {s['id']: s['do'] for s in landed['steps']} == {'s1': 'Read the file with mido, ticks absolute.', 's2': 'Count the onsets per bar.', 's3': 'Print the count.'}
    assert json.loads((ws/'midi-check.json').read_text()) == landed and json.loads((base/'midi-check.json').read_text()) == theirs
    # A clash: theirs stays in the store, the merged copy (theirs on the clash) goes to the workspace, and the
    # conflict is returned for the merge round.
    (store/'midi-check.json').write_text(json.dumps(_proc([('s1', 'Parse', 'Read the file with mido, ticks absolute.'), ('s2', 'Count', 'Count with pretty_midi.'), ('s3', 'Report', 'Print the count.')])))
    # The worker's copy is the merged one; it edits s2 again (the store moved on s2 too).
    mine = json.loads((ws/'midi-check.json').read_text())
    mine['steps'][1]['do'] = 'Count the onsets per bar and per beat.'
    out = worker._harvest_playbook(_snapshot(mine), store, config, allowed=("midi-check",), base=base, workspace_store=ws)
    assert out['installed'] == [] and out['merged'] == [] and out['conflicts'][0]['id'] == 'midi-check' and 'Count' in out['conflicts'][0]['conflicts'][0]
    assert {s['id']: s['do'] for s in json.loads((store/'midi-check.json').read_text())['steps']}['s2'] == 'Count with pretty_midi.'
    assert {s['id']: s['do'] for s in json.loads((ws/'midi-check.json').read_text())['steps']}['s2'] == 'Count with pretty_midi.'
    # Unchanged by you while the store moved: nothing to do.
    (store/'midi-check.json').write_text(json.dumps(_proc([('s1', 'Parse', 'Read the file with mido, ticks absolute.'), ('s2', 'Count', 'Count with pretty_midi, per beat.'), ('s3', 'Report', 'Print the count.')])))
    out = worker._harvest_playbook(_snapshot(json.loads((base/'midi-check.json').read_text())), store, config, allowed=('midi-check',), base=base, workspace_store=ws)
    assert out['installed'] == [] and out['conflicts'] == []
    assert 'edit-step' in worker.procedure_merge_message([dict(id='midi-check', conflicts=['step "Count": theirs "a" / yours "b"'])])


def test_a_procedure_linking_one_created_in_the_same_task_is_installed(tmp_path: Path, monkeypatch):
    """The archive is alphabetical: the general procedure that links the new one is validated first. It must not be
    refused for a link the same harvest is about to install (rhythm gym, 2026-09-22: the method landed an orphan)."""
    store = tmp_path/'data'/'playbook'/'procedures'
    store.mkdir(parents=True)
    general = _proc([('s1', 'Hear the form', 'Hear the form before writing a note.')])
    general['id'] = 'compose-romantic-piano-midi'
    (store/'compose-romantic-piano-midi.json').write_text(json.dumps(general))
    linked = json.loads(json.dumps(general))
    linked['steps'].append(dict(id='s2', title='Shape the returning cell', do='Walk the cell.', procedure='shape-a-returning-rhythmic-cell'))
    leaf = _proc([('c1', 'Name the cell', 'Hear the opening figure as ratios.')])
    leaf['id'] = 'shape-a-returning-rhythmic-cell'

    def validate(args, **kwargs):   # what `playbook validate` checks here: every linked procedure is in the store
        doc = json.loads((store/(args[-1]+'.json')).read_text())
        missing = [s['procedure'] for s in doc['steps'] if s.get('procedure') and not (store/(s['procedure']+'.json')).exists()]
        return type('R', (), dict(returncode=1 if missing else 0, stdout='', stderr=f"linked procedure '{missing[0]}' does not exist" if missing else ''))()
    monkeypatch.setattr(worker.subprocess, 'run', validate)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        for doc in (linked, leaf):   # alphabetical, as the workspace archive is
            data = json.dumps(doc).encode()
            info = tarfile.TarInfo(worker.PLAYBOOK_PREFIX+'/playbook/procedures/'+doc['id']+'.json')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    out = worker._harvest_playbook(buffer.getvalue(), store, {'mizpah': {'playbook': 'playbook'}},
                                   allowed=('compose-romantic-piano-midi', 'shape-a-returning-rhythmic-cell'))
    assert out['rejected'] == []
    assert out['created'] == ['shape-a-returning-rhythmic-cell'] and out['improved'] == ['compose-romantic-piano-midi']
    assert json.loads((store/'compose-romantic-piano-midi.json').read_text())['steps'][-1]['procedure'] == 'shape-a-returning-rhythmic-cell'
    # A link to a procedure nobody installs still fails, with the validator's reason, and the store keeps its copy.
    (store/'compose-romantic-piano-midi.json').write_text(json.dumps(general))
    (store/'shape-a-returning-rhythmic-cell.json').unlink()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:') as archive:
        data = json.dumps(linked).encode()
        info = tarfile.TarInfo(worker.PLAYBOOK_PREFIX+'/playbook/procedures/compose-romantic-piano-midi.json')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    out = worker._harvest_playbook(buffer.getvalue(), store, {'mizpah': {'playbook': 'playbook'}}, allowed=('compose-romantic-piano-midi',))
    assert out['installed'] == [] and len(out['rejected']) == 1 and 'does not exist' in out['rejected'][0]
    assert json.loads((store/'compose-romantic-piano-midi.json').read_text()) == general


def test_a_procedure_edited_by_name_is_harvested_even_when_the_task_never_opened_it(tmp_path: Path):
    """Linking a new method up from its general procedure is an add-step on a procedure the task did not open;
    the harvest must take it (Block C, 2026-09-22: two methods landed orphans)."""
    root = tmp_path/'task'
    (root/'events').mkdir(parents=True)
    def call(i, name, args, code=0):
        return dict(id=f'c{i}', function=dict(name=name, arguments=json.dumps(args))), dict(call_id=f'c{i}', result=dict(exit_code=code))
    calls = [call(1, 'bash', dict(command='playbook open compose-romantic-piano-midi --for piece')),
             call(2, 'bash', dict(command='playbook validate x && playbook add-step compose-a-character-piece --title T --do D --procedure shape-a-returning-rhythmic-cell')),
             call(3, 'playbook_add_step', dict(id='corroborate-a-midi-reading', title='T', do='D')),
             call(4, 'playbook_edit_step', dict(walk='.playbook/open/w.md', step=2, do='D')),   # walk-scoped: the opened one
             call(5, 'bash', dict(command='playbook edit-step measure-count-from-file --title T --do D'), code=1),   # failed
             call(6, 'bash', dict(command='playbook add-step mizpah-resolve-unknown --title T --do D'))]   # bootstrap
    lines = [json.dumps(dict(event_type='worker_turn', payload=dict(response=dict(tool_calls=[c]), tool_results=[r]))) for c, r in calls]
    (root/'events'/'session.jsonl').write_text('\n'.join(lines)+'\n')
    assert worker.procedures_edited(root) == ['compose-a-character-piece', 'corroborate-a-midi-reading']
    assert worker.procedures_used(root) == ['compose-romantic-piano-midi']
