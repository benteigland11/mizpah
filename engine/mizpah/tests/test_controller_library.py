"""The controller searches the library itself and names what fits on a work order."""
import json
from pathlib import Path

from mizpah import controller, worker


def test_library_tools_take_read_verbs_only(tmp_path):
    config = dict(mizpah=dict(playbook='/bin/echo', cartograph='/bin/echo', widget_library=''))
    assert controller.run_tool(config, tmp_path, None, 'playbook', dict(args='search "rubato phrase"')) == 'search rubato phrase'
    assert 'refused' in controller.run_tool(config, tmp_path, None, 'playbook', dict(args='add-step x'))
    assert 'refused' in controller.run_tool(config, tmp_path, None, 'cartograph', dict(args='checkin cg/x'))
    # A widget search stays in the local library.
    assert controller.run_tool(config, tmp_path, None, 'cartograph', dict(args='search grid')).endswith('--local-only')


def test_a_work_order_carries_the_widgets_it_names(tmp_path):
    library = tmp_path/'lib'
    (library/'data-grid-lock-python').mkdir(parents=True)
    (library/'data-grid-lock-python'/'widget.json').write_text('{}')
    observation = dict(brief=dict(needs=['n'], deliverables=['d'], non_goals=[]), knowns=[], tasks=[],
                       unknowns=[dict(id='grid_lock', status='open', type='number', claim='c', notes='cites need:1')],
                       widget_library=str(library), methods=[], playbook_store='')
    accepted, _ = controller.guard(dict(tasks=[dict(id='read_it', title='t', unknowns=['grid_lock'], bucket='low', deps=[],
                                                   widgets=['data-grid-lock-python', 'data-nowhere-python'])]), observation)
    assert accepted['tasks'][0]['widgets'] == ['data-grid-lock-python']
    assert any('data-nowhere-python' in c for c in accepted.get('cautions') or [])
    assert worker.assigned_widgets(dict(acceptance=['unknown:x', 'widget:data-grid-lock-python'])) == ['data-grid-lock-python']
