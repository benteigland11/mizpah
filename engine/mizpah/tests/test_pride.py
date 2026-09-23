"""At a landing the controller looks at the delivered assets alone and says whether it is proud of them."""
import json
import struct
from pathlib import Path

from mizpah import controller, pride, prompts

from test_source_artifact import CONFIG, gym  # noqa: F401 — the fixture

PROMPTS = Path(__file__).resolve().parents[3]/'prompts'


def midi(notes):
    track = b''
    for delta, status, a, b in [(0, 0xFF, None, None)]:
        track += bytes([0, 0xFF, 0x58, 4, 3, 2, 24, 8])
    for start, pitch, length, velocity in notes:
        track += bytes([0, 0x90, pitch, velocity]) + bytes([length, 0x80, pitch, 0])
    track += bytes([0, 0xFF, 0x2F, 0])
    return b'MThd'+struct.pack('>IHHH', 6, 0, 1, 96)+b'MTrk'+struct.pack('>I', len(track))+track


class Client:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, payload, kind):
        self.calls.append(payload)
        return dict(choices=[dict(message=dict(role='assistant', content=self.reply), finish_reason='stop')], usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2))


def test_midi_is_read_as_bars_of_notes():
    text = pride.midi_text(midi([(0, 64, 96, 70), (0, 67, 96, 50)]))
    assert text.startswith('meter 3/4') and 'bar 1: E4@1.0/1.0v70' in text and 'G4@2.0/1.0v50' in text


def test_the_verdict_is_asked_of_the_assets_alone_and_kept_by_content(tmp_path):
    prompts.set_messages_dir(PROMPTS)
    (tmp_path/'piece.mid').write_bytes(midi([(0, 64, 96, 70)]))
    brief = dict(deliverables=['piece.mid', 'notes.md: the plan'], needs=['moving'])
    client = Client('{"proud": false, "proudest": null, "why": "a grid", "holds_it_back": "bar 1", "would_make_me_proud": "a line"}')
    config = dict(controller=dict(generation=dict(model='m')), mizpah={})
    root = tmp_path/'sess'
    root.mkdir()
    first = pride.review(client, config, tmp_path, root, brief)
    assert first['proud'] is False and first['files'] == ['piece.mid'] and first['fresh']
    sent = client.calls[0]['messages']
    assert len(sent) == 1 and 'Are you proud of this work?' in sent[0]['content'] and '- moving' in sent[0]['content']
    assert 'gate' not in sent[0]['content'].split('Are you proud')[1].lower()
    again = pride.review(client, config, tmp_path, root, brief)
    assert not again['fresh'] and len(client.calls) == 1
    (tmp_path/'piece.mid').write_bytes(midi([(0, 65, 96, 70)]))
    pride.review(client, config, tmp_path, root, brief)
    assert len(client.calls) == 2


def test_an_unreadable_verdict_is_said_not_kept(tmp_path):
    prompts.set_messages_dir(PROMPTS)
    (tmp_path/'notes.md').write_text('# plan')
    root = tmp_path/'sess'
    root.mkdir()
    out = pride.review(Client('I like it'), dict(controller=dict(generation={}), mizpah={}), tmp_path, root, dict(deliverables=['notes.md']))
    assert out['proud'] is None and not (root/pride.VERDICTS).exists()


def test_not_proud_leads_what_is_asked(gym: Path):  # noqa: F811
    observation = controller.observe(CONFIG, gym)
    observation['pride'] = dict(proud=False, files=['piece.mid'], why='a sequencer grid', holds_it_back='every bar the same',
                                would_make_me_proud='a line that breathes')
    text = controller.render_observation(observation, 'eval')
    happened, asked = text.split('# What is asked of you')
    assert 'NOT proud of it' in happened and 'a line that breathes' in happened
    assert asked.lstrip().startswith('You are not proud of what was delivered')
    observation['pride'] = dict(proud=True, files=['piece.mid'], proudest='bar 9')
    text = controller.render_observation(observation, 'eval')
    assert 'You are proud of it' in text and 'You are not proud' not in text



def test_the_judge_sees_its_last_three_looks_as_history(tmp_path):
    prompts.set_messages_dir(PROMPTS)
    (tmp_path/'video.md').write_text('v1')
    root = tmp_path/'sess'
    root.mkdir()
    client = Client('{"proud": false, "why": "one visual idea", "holds_it_back": "the ending just stops"}')
    config = dict(controller=dict(generation={}), mizpah={})
    brief = dict(deliverables=['video.md'])
    for n in range(1, 6):
        (tmp_path/'video.md').write_text(f'v{n}')
        pride.review(client, config, tmp_path, root, brief)
    first, last = client.calls[0]['messages'][0]['content'], client.calls[-1]['messages'][0]['content']
    assert 'Earlier looks' not in first
    assert last.count('not proud. one visual idea') == 3 and 'context, not a verdict to repeat' in last
