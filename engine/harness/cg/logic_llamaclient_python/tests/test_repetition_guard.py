import random
import json
import pytest
from src.repetition_guard import RepetitionGuard


def test_loop_across_arbitrary_boundaries():
    text = ('I will now perform the requested action. Wait, let me check again.\n' * 400)
    counts=[]
    for size in [1, 7, 512, len(text)]:
        guard=RepetitionGuard();found=None
        for i in range(0,len(text),size):
            found=guard.feed('text',text[i:i+size])
            if found:break
        assert found and found.observed_chars <= 8704
        counts.append(found.observed_chars)
    assert len(set(counts))==1


def test_diverse_content_and_short_repeats_pass():
    guard=RepetitionGuard();rng=random.Random(3)
    text='\n'.join(f'item_{i} = {rng.getrandbits(128)}' for i in range(2000))
    assert guard.feed('content',text) is None
    assert RepetitionGuard().feed('content','return value;\n'*20) is None


def test_channels_do_not_mix_and_tool_arguments_are_inspected():
    guard=RepetitionGuard()
    for i in range(10):assert guard.feed(str(i),'same sentence '*100) is None
    event={'choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'function':{'arguments':'repeat this long sentence again and again. '*500}}]}}]}
    result=guard.observe(event)
    assert result and result.channel=='0:tool:0'


def test_policy_and_channel_limits():
    with pytest.raises(ValueError):RepetitionGuard(window_chars=2)
    guard=RepetitionGuard(max_channels=1)
    guard.feed('a','x')
    with pytest.raises(ValueError):guard.feed('b','x')


def test_identical_calls_across_indices_do_not_depend_on_chunks():
    for pieces in [('measure', '{"value":1}'), ('mea', '{"value":')]:
        guard = RepetitionGuard(identical_tool_calls=4)
        for i in range(4):
            first = {'index':i, 'function':{'name':pieces[0], 'arguments':pieces[1]}}
            event = {'choices':[{'index':0, 'delta':{'tool_calls':[first]}}]}
            result = guard.observe(event)
            if pieces[0] == 'mea':
                assert result is None
                result = guard.observe({'choices':[{'delta':{'tool_calls':[{'index':i,
                    'function':{'name':'sure', 'arguments':'1}'}}]}}]})
            if i < 3:
                assert result is None
        assert result.kind == 'identical_tool_calls' and result.repeated_calls == 4


def test_different_calls_and_repeated_deltas_are_not_repeated_calls():
    guard = RepetitionGuard(identical_tool_calls=4)
    for i in range(12):
        assert guard.observe({'choices':[{'delta':{'tool_calls':[{'index':i,
            'function':{'name':'measure', 'arguments':'{"value":'+str(i)+'}'}}]}}]}) is None
    guard = RepetitionGuard(identical_tool_calls=3)
    for _ in range(8):
        assert guard.observe({'choices':[{'delta':{'tool_calls':[{'index':0,
            'function':{'name':'measure', 'arguments':'{}'}}]}}]}) is None


def test_tool_limits_and_disabled_cross_call_detection():
    with pytest.raises(ValueError):
        RepetitionGuard(identical_tool_calls=1)
    with pytest.raises(ValueError):
        RepetitionGuard(maximum_tool_characters=0)
    guard = RepetitionGuard(identical_tool_calls=3, maximum_tool_characters=10)
    with pytest.raises(ValueError, match='bound'):
        guard.observe({'choices':[{'delta':{'tool_calls':[{'function':{'arguments':'x'*11}}]}}]})
    guard = RepetitionGuard()
    for i in range(20):
        assert guard.observe({'choices':[{'delta':{'tool_calls':[{'index':i,
            'function':{'name':'measure', 'arguments':'{}'}}]}}]}) is None


def test_long_source_and_structured_records_with_repeated_syntax_pass():
    rng = random.Random(7)
    source = '\n'.join(f'def measure_{i}(value):\n    return value + {rng.getrandbits(64)}\n'
                       for i in range(400))
    records = '\n'.join(json.dumps(dict(index=i, category='measurement',
        values=[rng.random() for _ in range(8)])) for i in range(400))
    assert RepetitionGuard().feed('content', source) is None
    assert RepetitionGuard().feed('content', records) is None


def test_policy_can_reconstruct_identical_detector():
    guard = RepetitionGuard(identical_tool_calls=8)
    assert RepetitionGuard(**guard.policy()).policy() == guard.policy()
