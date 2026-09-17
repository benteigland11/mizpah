"""Create, complete and reopen a session with offline scripted model responses."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.focused_agent_session import (
    ControllerSettings, EndpointConfig, FocusedSession, ModelClient, ReviewPolicy,
    SandboxedShell, SessionPolicy, SessionSettings, ShellConfig, ShellLimits, WireResponse,
)

def transport(path, payload):
    if path == '/template':
        value = dict(prompt='scripted prompt')
    elif path == '/tokenize':
        value = dict(tokens=[1, 2, 3])
    else:
        content = 'The answer is 4.'
        if payload['model'] == 'controller':
            content = json.dumps(dict(correction='None', evidence='', warrant=''))
        value = dict(choices=[dict(message=dict(role='assistant', content=content), finish_reason='stop')],
                     usage=dict(prompt_tokens=3, completion_tokens=10))
    return WireResponse(200, json.dumps(value), 0)


with TemporaryDirectory() as directory:
    endpoint = EndpointConfig('http://example.invalid', 10, 100000, {}, '/complete', '/template', '/tokenize', False, True)
    worker = ModelClient(endpoint, transport=transport)
    controller = ModelClient(endpoint, transport=transport)
    # No shell commands are requested by this offline example.
    shell = SandboxedShell(ShellConfig('/usr/bin/bwrap', '/usr/bin/systemd-run', '/usr/bin/systemctl',
        '/usr', directory, ShellLimits(1000000, 1000000, 1000000, 10000, 2000, 20, 100, 5, 2, 1000)))
    settings = SessionSettings('What is 2 + 2?', 'Answer the assignment.', 'The answer must equal four.',
        dict(model='worker'), SessionPolicy(2000, 1000, 100, 100, 2, 1000,
            'Prepare your own handoff.', 'Resume from your handoff.', {}),
        ReviewPolicy(20, 10, 1, 2000, 500, 20000),
        ControllerSettings('Review the answer and maintain progress.', dict(model='controller'), 2000, 100, 4, 4, 2000),
        True, 'Controller guidance:', worker_tools=('bash', 'read', 'write', 'edit'),
        maximum_tool_argument_characters=2000, maximum_write_characters=1500, maximum_edit_characters=1000)
    session = FocusedSession.create(Path(directory)/'session', settings,
        worker=worker, shell=shell, controller=controller,
        initial_project_document='# Arithmetic task\n- [ ] Provide and verify the answer.\n')
    result = session.run()
    reopened = FocusedSession.open(Path(directory)/'session', worker=worker, shell=shell, controller=controller)
    assert reopened.status() == result
    assert result['controller_reviews'] == 1
    print(result['final_text'])
