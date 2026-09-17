from copy import deepcopy
import json
import pytest

from src.context_payload_projection import fit_native_messages, project_native_messages


def test_projection_preserves_base_ids_order_and_originals():
    messages = [dict(role='system', content='fixed '*100), dict(role='user', content='assignment '*100),
        dict(role='assistant', content='large '*500, reasoning_content='reason '*500,
             tool_calls=[dict(id='call-a', type='function', function=dict(name='inspect',
                             arguments=json.dumps(dict(query='x'*2000))))]),
        dict(role='tool', tool_call_id='call-a', content='result '*500)]
    original = deepcopy(messages)
    projected = project_native_messages(messages, protected_prefix=2, max_field_characters=120,
                                         archive_reference='records/window.json')
    result = projected['messages']
    assert messages == original and result[:2] == original[:2]
    assert [item['role'] for item in result] == [item['role'] for item in original]
    call = result[2]['tool_calls'][0]
    assert call['id'] == result[3]['tool_call_id'] == 'call-a'
    assert call['function']['name'] == 'inspect'
    assert 'archived_original_arguments' in json.loads(call['function']['arguments'])
    assert 'Excerpt only' in result[2]['content'] and 'records/window.json' in result[3]['content']
    assert len(projected['replaced_fields']) == 4


def test_zero_excerpt_uses_explicit_pointer_and_keeps_nulls_and_small_arguments():
    messages = [dict(role='user', content='fixed'), dict(role='assistant', content=None),
                dict(role='tool', tool_call_id='value', content='too large')]
    result = project_native_messages(messages, protected_prefix=1, max_field_characters=0,
                                      archive_reference='saved.json')['messages']
    assert result[1]['content'] is None
    assert 'too large' not in result[2]['content'] and 'saved.json' in result[2]['content']


def messages_with_exchanges(count, size):
    messages = [dict(role='system', content='Fixed reference'), dict(role='user', content='Current project state')]
    for index in range(count):
        identity = 'call-'+str(index)
        messages.extend([dict(role='assistant', content='Inspect item '+str(index),
            tool_calls=[dict(id=identity, type='function', function=dict(name='inspect', arguments='{}'))]),
            dict(role='tool', tool_call_id=identity, content='Evidence '+str(index)+' '+('v'*size))])
    return messages


def fit(messages, budget, recent=1):
    return fit_native_messages(messages, count_tokens=lambda m:len(json.dumps(m)), token_budget=budget,
        protected_prefix=2, recent_exchanges=recent, max_field_characters=300,
        archive_reference='history_read(start_message=0,end_message=99)')


def test_whole_exchange_omission_keeps_latest_evidence_and_fixed_inputs():
    messages = messages_with_exchanges(24, 600)
    before = deepcopy(messages)
    result = fit(messages, 2600)
    assert messages == before
    assert result['projected'] and result['prompt_tokens'] <= 2600
    assert result['messages'][:2] == messages[:2]
    assert result['messages'][-2:] == messages[-2:]
    assert result['omitted_message_ranges']
    calls = [call['id'] for m in result['messages'] for call in m.get('tool_calls', [])]
    assert calls == [m['tool_call_id'] for m in result['messages'] if m['role'] == 'tool']
    assert 'history_read' in json.dumps(result['messages'])


def test_oversized_latest_batch_retains_ids_and_explicit_original_reference():
    messages = messages_with_exchanges(1, 20000)
    result = fit(messages, 1100)
    assert result['projected'] and result['prompt_tokens'] <= 1100
    assert result['messages'][-1]['tool_call_id'] == 'call-0'
    assert 'messages/3/content' in result['messages'][-1]['content']
    assert not result['omitted_message_ranges']


def test_fitting_small_history_is_lossless_and_oversized_prefix_fails():
    messages = messages_with_exchanges(1, 10)
    result = fit(messages, 10000)
    assert result['messages'] == messages and not result['projected']
    messages[0]['content'] = 'fixed '*2000
    with pytest.raises(ValueError, match='Fixed inputs'):
        fit(messages, 1000)


@pytest.mark.parametrize('mode', ['missing', 'orphan', 'reordered'])
def test_projection_does_not_hide_unresolved_or_invalid_native_calls(mode):
    messages = messages_with_exchanges(2, 10)
    if mode == 'missing':
        messages.pop()
    elif mode == 'orphan':
        del messages[2]
    else:
        messages[3]['tool_call_id'] = 'call-1'
    with pytest.raises(ValueError, match='tool|batches'):
        fit(messages, 10000)
