"""A context transition using an explicit worker-authored handoff, with wire-only reasoning retention."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.persistent_model_session import PersistentSession, SessionPolicy

policy = SessionPolicy(32000, 20000, 4096, 1536, 3, 6000, 'Summarize work for the next context.',
                       'Continue the same investigation in /work.', {}, reasoning_retention='latest')
session = PersistentSession([{'role':'user', 'content':'Investigate the measurements.'}], [], {}, policy)

# Two completed steps: stored messages keep every reasoning field; the request keeps only the latest.
usage = dict(prompt_tokens=10, completion_tokens=4)
session.accept(dict(choices=[dict(message=dict(role='assistant', content='Reading notes.', reasoning_content='plan A'),
                                  finish_reason='stop')], usage=usage))
session.accept(dict(choices=[dict(message=dict(role='assistant', content='Notes read.', reasoning_content='plan B'),
                                  finish_reason='stop')], usage=usage))
print('stored reasoning:', [m.get('reasoning_content') for m in session.messages if m['role'] == 'assistant'])
print('sent reasoning:  ', [m.get('reasoning_content') for m in session.payload()['messages'] if m['role'] == 'assistant'])

print(session.rollover('The latest measurements and open question are saved in notes.txt.',
                       source_archive='history/window-0.json'))
