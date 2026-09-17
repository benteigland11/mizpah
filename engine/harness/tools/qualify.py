#!/usr/bin/env python3
"""Exercise the actual launcher and OS sandbox using scripted loopback responses."""
from copy import deepcopy
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from threading import Thread
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cg.bp_focused_agent_session_python.src.focused_agent_session import (
    SessionEventLog, read_workspace_file, workspace_files,
)
from cg.backend_async_job_runner_python.src.async_job_runner import JobRunner, JobSpec


def response(content, calls=None):
    # Every scripted assistant turn carries reasoning so retention can be probed on the wire.
    message = dict(role='assistant', content=content, reasoning_content='scripted reasoning for: '+content[:40])
    if calls:
        message['tool_calls'] = calls
    return dict(choices=[dict(message=message, finish_reason='tool_calls' if calls else 'stop')],
                usage=dict(prompt_tokens=100, completion_tokens=20))


def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    out = ROOT/'artifacts/focused-harness/qualification'/stamp
    out.mkdir(parents=True)
    private = 'PRIVATE_REFERENCE_CPU_QUALIFICATION_SENTINEL'
    (out/'reference.txt').write_text(private+'\nProduce a verified report of the persistent file.\n')
    (out/'assignment.txt').write_text('Exercise the workspace and verify its file before finishing.\n')
    calls, reviews, requests, handoffs = [], [], [], []
    stalled_worker_requests = []
    file_requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(dict(path=self.path, body=deepcopy(payload)))
            if self.path == '/apply-template':
                # Deliberately small synthetic token counts force several handoffs.
                count = 600 if 'tools' in payload and len(payload['messages']) > 9 else 100
                value = dict(prompt='x'*count)
            elif self.path == '/tokenize':
                value = dict(tokens=[1]*len(payload['content']))
            elif payload['model'] == 'scripted-stalling-controller':
                envelope = json.loads(payload['messages'][1]['content'])
                step = envelope['proposed_input']['review_state']['model_calls']
                name, arguments = ('project_read', dict(offset=0)) if step else ('project_edit',
                    dict(expected_revision=0, old_text='', new_text='# Project\nReview is still investigating.'))
                value = response('Continue investigating without making a decision.', [dict(id='stalled-'+str(step),
                    type='function', function=dict(name=name, arguments=json.dumps(arguments)))])
                time.sleep(0.1)
            elif payload['model'] == 'scripted-stalling-worker':
                stalled_worker_requests.append(deepcopy(payload))
                value = response('Create the observed file.', [dict(id='stalled-worker', type='function',
                    function=dict(name='bash', arguments=json.dumps(dict(command='printf observed > item.txt'))))])
            elif payload['model'] == 'scripted-controller':
                envelope = json.loads(payload['messages'][1]['content'])
                turn = envelope['completed_turns']
                if len(payload['messages']) == 2:
                    reviews.append(envelope)
                    prior = envelope['project_document']['text']
                    old = '' if not prior else 'Current: Observed through turn '+str(envelope['last_review_turn'])
                    new = 'Current: Observed through turn '+str(turn)
                    if not prior:
                        new = ('# Project lifecycle\nPRIVATE_PROJECT_DOCUMENT_SENTINEL\n'
                            'Outcome: a verified persistent file.\n'
                            '- [x] Explore: initial file written at turn 1.\n'
                            '- [ ] Exercise persistence through restarts.\n'
                            '- [ ] Verify and deliver the result.\n'+new)
                    # Budgets: bootstrap 0 (all investigative calls refused), periodic 2 (list and
                    # history succeed, read and check refused), completion 6 (all succeed).
                    commands = [
                        ('investigate', dict(concern='Confirm the persistent file really holds the numbered lines the traces claim.')),
                        ('workspace_list', dict(prefix='', offset=0)),
                        ('history_read', dict(start_turn=2 if turn > 2 else 1, end_turn=2 if turn > 2 else 1, offset=0)),
                        ('workspace_read', dict(path='progress.txt', offset=0)),
                        ('project_edit', dict(expected_revision=envelope['project_document']['revision'], old_text=old, new_text=new)),
                        ('run_check', dict(command='set -e; test ! -e '+shlex.quote(str(out/'reference.txt'))+
                            '; test ! -e /home; test "$(wc -l < progress.txt)" -eq '+str(min(turn, 22))+
                            '; printf inspection-only > controller-only.txt; printf changed-by-check > progress.txt')),
                    ]
                    if turn == 23:
                        commands.append(('project_edit', dict(expected_revision=envelope['project_document']['revision']+1,
                            old_text='- [ ] Exercise persistence through restarts.\n- [ ] Verify and deliver the result.',
                            new_text='- [x] Exercise persistence through restarts: file retained.\n- [x] Verify and deliver: 22 numbered lines checked.')))
                    value = response('Inspect the project and update its lifecycle document.', [
                        dict(id='review-'+str(turn)+'-'+str(i), type='function',
                             function=dict(name=name, arguments=json.dumps(arguments)))
                        for i,(name,arguments) in enumerate(commands)])
                elif turn == 1 and envelope['proposed_input']['review_state']['model_calls'] < 20:
                    step = envelope['proposed_input']['review_state']['model_calls']
                    name, arguments = 'project_read', dict(offset=0)
                    if step == 8:
                        name, arguments = 'project_edit', dict(expected_revision=envelope['project_document']['revision'],
                            old_text='an anchor absent from the document', new_text='recovered')
                    elif step == 9:
                        page = envelope['project_document']
                        name, arguments = 'project_edit_range', dict(expected_revision=page['revision'],
                            start_offset=page['end_offset'], end_offset=page['end_offset'],
                            new_text='\nEstablished: review evidence recovered after an edit conflict.\n')
                    elif step == 10:
                        name, arguments = 'review_history_read', dict(start_message=2, end_message=7, offset=0)
                    value = response('Complete the qualification investigation.', [dict(id='extended-'+str(step),
                        type='function', function=dict(name=name, arguments=json.dumps(arguments)))])
                else:
                    correction = 'Check the persistent file before concluding.' if len(reviews) == 1 else 'None'
                    value = response(json.dumps(dict(correction=correction,
                        evidence='The first turn wrote the file.' if correction != 'None' else '',
                        warrant='The reference asks for a verified report.' if correction != 'None' else '')))
            elif payload['model'] == 'scripted-file-worker' and 'tools' not in payload:
                value = response('Worker-owned handoff: notes.py holds alpha and beta; continue verifying it.')
            elif payload['model'] == 'scripted-file-worker':
                file_requests.append(deepcopy(payload))
                script = [
                    ('bash', dict(command="cat << 'EOF' > notes.py\n"+('value = 1\n'*60)+'EOF\n')),
                    ('write', dict(path='notes.py', content='alpha = 1\nbeta = 1\n')),
                    ('edit', dict(path='notes.py', old_text='beta = 1', new_text='beta = 2')),
                    ('edit', dict(path='notes.py', old_text='= ', new_text='=')),
                    ('bash', dict(command='cat notes.py && wc -c notes.py')),
                ]
                step = len(file_requests)-1
                if step < len(script):
                    name, arguments = script[step]
                    value = response('File action '+str(step+1), [dict(id='file-'+str(step+1), type='function',
                        function=dict(name=name, arguments=json.dumps(arguments)))])
                else:
                    value = response('notes.py verified: alpha = 1, beta = 2.')
            elif 'tools' not in payload:
                handoffs.append(deepcopy(payload))
                value = response('Worker-owned handoff: progress.txt persists. Continue with the next numbered turn.')
            else:
                number = len(calls)+1
                if number > 22:
                    value = response('Verified 22 numbered lines in progress.txt.')
                else:
                    command = f"printf 'turn {number}\\n' >> progress.txt; test \"$(wc -l < progress.txt)\" -eq {number}; cat progress.txt"
                    if number == 3:
                        command = 'test ! -e '+shlex.quote(str(out/'reference.txt'))+' && test ! -e /home && '+command
                    command = 'set -e; '+command
                    calls.append(command)
                    value = response('Write and verify the persistent file.', [dict(id='call-'+str(number),
                        type='function', function=dict(name='bash', arguments=json.dumps(dict(command=command))))])
            streaming = self.path == '/v1/chat/completions' and payload.get('stream') is True
            if streaming:
                choice = value['choices'][0]
                message = deepcopy(choice['message'])
                for index, call in enumerate(message.get('tool_calls', [])):
                    call['index'] = index
                events = [dict(choices=[dict(index=0, delta=message, finish_reason=None)]),
                    dict(choices=[dict(index=0, delta={}, finish_reason=choice['finish_reason'])]),
                    dict(choices=[], usage=value['usage'])]
                body = ''.join('data: '+json.dumps(event)+'\n\n' for event in events).encode()+b'data: [DONE]\n\n'
            else:
                body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if streaming else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                # Expected when the independent deadline closes its model client.
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = json.loads((ROOT/'tools/focused-harness/config.example.json').read_text())
    for role in ('worker', 'controller'):
        config[role]['endpoint']['base_url'] = 'http://127.0.0.1:'+str(server.server_port)
        config[role]['generation']['model'] = 'scripted-'+role
    config['controller'].update(policy_file=str(ROOT/'tools/focused-harness/controller.md'),
                                context_capacity=20000, output_tokens=1000,
                                output_headroom_tokens=1000, input_target_tokens=12000)
    config['session_policy'].update(context_capacity=2000, rollover_threshold=500,
        worker_output_tokens=100, handoff_output_tokens=100, output_headroom_tokens=100,
        recent_result_characters=2000)
    config['shell']['limits'].update(memory_bytes=512*1024**2, workspace_bytes=16*1024**2,
        temporary_bytes=16*1024**2, output_bytes=262144, visible_output_bytes=16384,
        processes=32, cpu_percent=100, command_seconds=15, max_files=5000)
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    commands = []

    def launch(action, *extra):
        command = [sys.executable, str(ROOT/'tools/focused-harness/run_session.py'), action,
                   '--config', str(out/'config.json'), '--root', str(out/'session'), *map(str, extra)]
        result = subprocess.run(command, text=True, capture_output=True, timeout=240)
        commands.append(dict(action=action, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr))
        print(action, result.returncode, flush=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout if action == 'project' else json.loads(result.stdout)

    passed = False
    try:
        paused = launch('start', '--assignment', out/'assignment.txt', '--reference', out/'reference.txt', '--pause-after', 13)
        assert paused['status'] == 'paused' and paused['completed_worker_turns'] == 13
        assert launch('status') == {key:value for key,value in paused.items() if key != 'status'} | dict(status='ready')
        paused_document = launch('project')
        assert 'Explore: initial file written at turn 1.' in paused_document
        finished = launch('resume')
        assert finished['status'] == 'complete' and finished['completed_worker_turns'] == 23
        assert finished['handoffs'] == len(handoffs) >= 2
        launch('export', '--output', out/'workspace.tar')
        launch('project', '--output', out/'project.md')
        document = (out/'project.md').read_text()
        assert 'Explore: initial file written at turn 1.' in document
        assert '- [x] Verify and deliver: 22 numbered lines checked.' in document
        assert 'Current: Observed through turn 23' in document
        assert finished['project_revision'] == 5
        workspace = (out/'workspace.tar').read_bytes()
        content = read_workspace_file(workspace, 'progress.txt', byte_limit=16*1024**2, file_limit=5000)
        assert 'controller-only.txt' not in workspace_files(workspace, byte_limit=16*1024**2, file_limit=5000)
        assert content.decode().splitlines() == ['turn '+str(i) for i in range(1, 23)]
        assert [r['completed_turns'] for r in reviews] == [1, 21, 23]
        assert [len(r['recent_turns']) for r in reviews] == [1, 10, 10]
        assert 'Current: Observed through turn 1' in reviews[1]['project_document']['text']
        assert all(private in r['reference'] for r in reviews)
        worker_requests = [r['body'] for r in requests if r['path'] == '/v1/chat/completions' and r['body']['model'] == 'scripted-worker']
        assert all(r['body']['stream'] is True and r['body']['stream_options']['include_usage'] is True
                   for r in requests if r['path'] == '/v1/chat/completions')
        assert all(private not in json.dumps(r) and 'PRIVATE_PROJECT_DOCUMENT_SENTINEL' not in json.dumps(r) for r in worker_requests)
        assert all(sum(m.get('content', '').startswith('Controller guidance:') for m in r['messages']) == 1 for r in worker_requests[1:])
        events = SessionEventLog(out/'session/events').read_strict('session')
        completions = [e.payload for e in events if e.event_type == 'model_response'
                       and e.payload['purpose'] in ('worker', 'controller', 'handoff')]
        assert {e['purpose'] for e in completions} == {'worker', 'controller', 'handoff'}
        assert all(e['evidence']['provider'] == 'llama_client' for e in completions)
        outcomes = [e.payload for e in events if e.event_type == 'tool_outcome']
        assert len(outcomes) == 22 and all(r['status'] == 'ok' and r['exit_code'] == 0 for r in outcomes), outcomes
        checks = [e.payload for e in events if e.event_type == 'controller_check']
        # Only the completion review (turn 23, budget 6) reaches run_check; turn 1 has no budget
        # and turn 21 spends its two calls on workspace_list and history_read.
        assert len(checks) == 1 and all(r['status'] == 'ok' and r['exit_code'] == 0 and r['workspace_changes_discarded'] for r in checks)
        concerns = [e.payload for e in events if e.event_type == 'controller_concern']
        assert [(c['turn'], c['kind'], c['budget']) for c in concerns] == [(1, 'bootstrap', 0), (21, 'periodic', 2), (23, 'completion', 6)]
        controller_tools_used = [e.payload for e in events if e.event_type == 'controller_tool']
        def statuses(turn_id):
            return {t['name']: t['result'].get('status', 'ok') for t in controller_tools_used if t['call_id'].startswith('review-'+str(turn_id)+'-')}
        assert statuses(1)['workspace_list'] == 'error' and statuses(1)['run_check'] == 'error'
        assert statuses(21)['workspace_list'] != 'error' and statuses(21)['history_read'] != 'error'
        assert statuses(21)['workspace_read'] == 'error' and statuses(21)['run_check'] == 'error'
        assert all(v != 'error' for k, v in statuses(23).items() if k != 'project_edit')
        historical = [e.payload for e in events if e.event_type == 'controller_tool' and e.payload['name'] == 'history_read']
        historical = [h for h in historical if h['result'].get('status') != 'error']
        assert json.loads(historical[0]['result']['text'])[0]['turn'] == 2
        errors = [e.payload for e in events if e.event_type == 'controller_tool'
                  and e.payload['result'].get('status') == 'error']
        conflict = next(e for e in errors if e['call_id'] == 'extended-8')
        assert conflict['result']['project_document']['revision'] == 1
        long_review = next(e.payload for e in events if e.event_type == 'controller_review')
        assert long_review['model_calls'] == 21 and long_review['tool_calls'] == 25
        assert long_review['termination'] == 'voluntary_decision'
        archives = [e.payload for e in events if e.event_type == 'worker_history_archive']
        assert len(archives) == len(handoffs)
        # Reasoning retention: the wire carries reasoning only on the final assistant
        # message of every worker, handoff and controller request; the raw audit and the
        # saved session retain all of it.
        assert config['session_policy']['reasoning_retention'] == 'latest'
        wire = [r['body'] for r in requests if r['path'] == '/v1/chat/completions']
        retained_counts = []
        for body in wire:
            assistants = [m for m in body['messages'] if m.get('role') == 'assistant']
            retained_counts.append(sum(1 for m in assistants if m.get('reasoning_content')))
            assert all('reasoning_content' not in m for m in assistants[:-1]), body['model']
        assert max(retained_counts) == 1 and len(wire) > 30
        raw_worker = [json.loads(e['body']) if isinstance(e['body'], str) else e['body'] for e in completions if e['purpose'] == 'worker']
        assert all(b['choices'][0]['message'].get('reasoning_content') for b in raw_worker)
        saved = launch('status')
        assert saved['completed_worker_turns'] == 23
        assert all(a['archive'] in workspace_files(workspace, byte_limit=16*1024**2, file_limit=5000) for a in archives)

        # A controller that never chooses a decision must be stopped by an
        # independent process timer, without converting the timeout to approval.
        stalled = out/'deadline-qualification'; stalled.mkdir()
        stalled_config = deepcopy(config)
        for role in ('worker', 'controller'):
            stalled_config[role]['generation']['model'] = 'scripted-stalling-'+role
        (stalled/'config.json').write_text(json.dumps(stalled_config, indent=2)+'\n')
        stalled_argv = [sys.executable, str(ROOT/'tools/focused-harness/run_session.py'), 'start',
            '--config', str(stalled/'config.json'), '--root', str(stalled/'session'),
            '--assignment', str(out/'assignment.txt'), '--reference', str(out/'reference.txt')]

        async def timed_review():
            runner = JobRunner(max_output_bytes=1_000_000, termination_grace_seconds=2)
            started = await runner.submit(JobSpec(argv=stalled_argv, cwd=str(ROOT), timeout_seconds=8))
            return await runner.wait(started.job_id)

        timed = asyncio.run(timed_review())
        assert timed.status == 'timed_out'
        (stalled/'stdout.log').write_bytes(timed.stdout)
        (stalled/'stderr.log').write_bytes(timed.stderr)
        (stalled/'job.json').write_text(json.dumps({k:v for k,v in asdict(timed).items()
            if k not in ('stdout', 'stderr')}, indent=2, default=str)+'\n')
        inspected = subprocess.run([sys.executable, str(ROOT/'tools/focused-harness/run_session.py'), 'status',
            '--config', str(stalled/'config.json'), '--root', str(stalled/'session')],
            text=True, capture_output=True, timeout=15, check=True)
        stopped = json.loads(inspected.stdout)
        assert stopped['status'] != 'complete' and stopped['controller_reviews'] == 0
        assert stopped['completed_worker_turns'] == 1 and len(stalled_worker_requests) == 1
        assert stopped['active_review']['model_calls'] > 0 and not stopped['final_text']
        (stalled/'status.json').write_text(json.dumps(stopped, indent=2)+'\n')

        # Bounded file actions through the real launcher and sandbox: an oversized
        # heredoc is rejected unexecuted; write and edit act on the snapshot; an
        # ambiguous edit changes nothing; bash then observes the edited file.
        bounded = out/'bounded-actions-qualification'; bounded.mkdir()
        bounded_config = deepcopy(config)
        bounded_config['worker']['generation']['model'] = 'scripted-file-worker'
        bounded_config.update(worker_tools=['bash', 'write', 'edit'], maximum_tool_argument_characters=300,
                              maximum_write_characters=200, maximum_edit_characters=100)
        (bounded/'config.json').write_text(json.dumps(bounded_config, indent=2)+'\n')
        bounded_run = subprocess.run([sys.executable, str(ROOT/'tools/focused-harness/run_session.py'), 'start',
            '--config', str(bounded/'config.json'), '--root', str(bounded/'session'),
            '--assignment', str(out/'assignment.txt')], text=True, capture_output=True, timeout=240)
        (bounded/'stdout.log').write_text(bounded_run.stdout)
        (bounded/'stderr.log').write_text(bounded_run.stderr)
        assert bounded_run.returncode == 0, bounded_run.stderr[-2000:]
        bounded_status = json.loads(bounded_run.stdout)
        assert bounded_status['status'] == 'complete' and bounded_status['completed_worker_turns'] == 6
        assert [tool['function']['name'] for tool in file_requests[0]['tools']] == ['bash', 'write', 'edit']
        bounded_events = SessionEventLog(bounded/'session/events').read_strict('session')
        bounded_turns = [e.payload for e in bounded_events if e.event_type == 'worker_turn']
        bounded_results = [r['result'] for t in bounded_turns for r in t['tool_results']]
        assert [r['status'] for r in bounded_results] == ['rejected', 'ok', 'ok', 'error', 'ok'], bounded_results
        assert bounded_results[0]['code'] == 'arguments_too_large' and bounded_results[3]['code'] == 'occurrence_mismatch'
        assert bounded_results[4]['stdout'].startswith('alpha = 1\nbeta = 2\n')
        rejected_events = [e.payload for e in bounded_events if e.event_type == 'tool_rejected']
        assert len(rejected_events) == 1 and rejected_events[0]['name'] == 'bash' and rejected_events[0]['bound'] == 300
        bounded_outcomes = [e.payload for e in bounded_events if e.event_type == 'tool_outcome']
        assert [o.get('tool', 'bash') for o in bounded_outcomes] == ['write', 'edit', 'edit', 'bash']
        bounded_workspace = subprocess.run([sys.executable, str(ROOT/'tools/focused-harness/run_session.py'), 'export',
            '--config', str(bounded/'config.json'), '--root', str(bounded/'session'),
            '--output', str(bounded/'workspace.tar')], text=True, capture_output=True, timeout=60, check=True)
        assert read_workspace_file((bounded/'workspace.tar').read_bytes(), 'notes.py',
            byte_limit=16*1024**2, file_limit=5000) == b'alpha = 1\nbeta = 2\n'
        (bounded/'status.json').write_text(json.dumps(bounded_status, indent=2)+'\n')
        passed = True
        summary = dict(passed=True, model='scripted loopback fixture; no live LLM', real_sandbox=True,
            worker_turns=23, worker_shell_commands=22, controller_checks=1, reviews=[1,21,23], handoffs=len(handoffs),
            investigation_budgets=dict(bootstrap=0, periodic=2, completion=6), concerns_stated=3,
            project_document_retained=True, project_revision=finished['project_revision'], check_mutations_discarded=True, historical_turn_retrieval=True,
            long_review_model_calls=21, long_review_tool_calls=25, range_edit_conflict_recovery=True,
            every_handoff_archived=True, independent_review_deadline=True, timeout_never_approved=True,
            process_reopen=True, private_reference_isolated=True, workspace_sha256=hashlib.sha256(workspace).hexdigest(),
            reasoning_retention='latest', wire_requests_probed=len(wire), max_reasoning_fields_per_request=max(retained_counts),
            bounded_actions=dict(tools=['bash', 'write', 'edit'], argument_bound=300, oversized_call_rejected_unexecuted=True,
                                 write_and_edit_applied=True, ambiguous_edit_refused=True),
            source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in ('cg/bp_focused_agent_session_python', 'cg/universal_controller_progress_python',
                               'cg/backend_persistent_model_session_python', 'tools/focused-harness')
                for p in (ROOT/folder).rglob('*.py') if '.venv' not in p.parts})
        (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps({key:value for key,value in summary.items() if key != 'source_hashes'}, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (out/'transport.json').write_text(json.dumps(requests, indent=2)+'\n')
        (out/'launcher.json').write_text(json.dumps(commands, indent=2)+'\n')
        print('PASS' if passed else 'FAIL', out, flush=True)


if __name__ == '__main__':
    main()
