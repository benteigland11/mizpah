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


class Scripted:
    """A model that answers each request with the next scripted message."""
    def __init__(self, *messages):
        self.messages, self.payloads = list(messages), []

    def complete(self, payload, kind):
        self.payloads.append(payload)
        message = self.messages.pop(0)
        return dict(choices=[dict(message=dict(role='assistant', **message), finish_reason='stop')],
                    usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2))


def call(name, args):
    return dict(id='c_'+name, type='function', function=dict(name=name, arguments=json.dumps(args)))


CONFIG = dict(controller=dict(generation={}), mizpah=dict(terra='/bin/echo', playbook='/bin/echo', cartograph='/bin/echo', widget_library=''))


def test_the_step_ends_with_a_decide_call(tmp_path):
    decision = dict(unknowns=[], tasks=[], memory='nothing new', why='the route covers it')
    client = Scripted(dict(content='', tool_calls=[call('terra', dict(args='gate'))]),
                      dict(content='', tool_calls=[call('decide', decision)]))
    got, raw = controller.decide(client, CONFIG, 'system', 'user', project=tmp_path)
    assert got == decision and json.loads(raw) == decision
    assert any(t['function']['name'] == 'decide' for t in client.payloads[0]['tools'])


def test_prose_is_asked_once_for_the_decision(tmp_path):
    decision = dict(memory='m', why='w')
    client = Scripted(dict(content='Decided. The map is empty and five entries are owed.'),
                      dict(content='', tool_calls=[call('decide', decision)]))
    got, _ = controller.decide(client, CONFIG, 'system', 'user', project=tmp_path)
    assert got == decision
    assert client.payloads[1]['messages'][-1]['content'].startswith('End the step by calling `decide`')


def test_a_json_reply_still_decides(tmp_path):
    client = Scripted(dict(content='{"memory": "m", "why": "w"}'))
    got, _ = controller.decide(client, CONFIG, 'system', 'user', project=tmp_path)
    assert got == dict(memory='m', why='w') and len(client.payloads) == 1


def test_minting_through_terra_is_pointed_at_decide(tmp_path):
    out = controller.run_tool(CONFIG, tmp_path, None, 'terra', dict(args='unknown mint baseline_x id=x'))
    assert out.startswith('refused') and '`decide`' in out
    assert '`decide`' not in controller.run_tool(CONFIG, tmp_path, None, 'terra', dict(args='known frobnicate'))


def test_a_seat_sets_its_own_output_cap():
    base = dict(mizpah=dict(controller_output_tokens=8192))
    assert controller.output_tokens(dict(base, controller=dict(output_tokens=-1))) == 8192
    assert controller.output_tokens(dict(base, controller=dict(output_tokens=32768))) == 32768
