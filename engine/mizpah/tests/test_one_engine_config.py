"""One engine config for every model: the project's pinned seat carries what used to need a file per model."""
import json

from mizpah import init, ops, worker


def test_a_seat_with_a_smaller_window_fits_the_session_to_it():
    config = dict(worker=dict(context_window=65536), controller=dict(context_window=65536, context_capacity=131072),
                  session_policy=dict(context_capacity=110000, rollover_threshold=61440, output_headroom_tokens=10000))
    init.fit_windows(config)
    assert config['session_policy']['context_capacity'] == 65536
    assert config['session_policy']['rollover_threshold'] == 65536*4//5
    assert config['controller']['context_capacity'] == 65536
    hosted = dict(worker=dict(context_window=2000000), session_policy=dict(context_capacity=110000, rollover_threshold=61440))
    assert init.fit_windows(hosted)['session_policy'] == dict(context_capacity=110000, rollover_threshold=61440)


def test_a_project_carries_its_scaffolding_and_its_seat_its_server_unit(tmp_path):
    (tmp_path/'.mizpah').mkdir()
    (tmp_path/'.mizpah'/'config.json').write_text(json.dumps(dict(
        scaffolding=dict(checkins=False),
        models=dict(worker=dict(provider='llama_client', model_unit='bonsai2-gpu0.service', context_window=65536)))))
    config = dict(mizpah=dict(scaffolding=dict(checkins=True, bootstrap=True)), worker=dict(provider='subscription'),
                  session_policy=dict(context_capacity=110000, rollover_threshold=61440))
    init.apply_project_config(config, tmp_path)
    assert config['mizpah']['scaffolding'] == dict(checkins=False, bootstrap=True)
    assert ops.settings(config)['model_unit'] == 'bonsai2-gpu0.service'
    assert config['session_policy']['context_capacity'] == 65536


def test_a_task_keeps_the_crew_it_first_ran_with(tmp_path):
    engine = tmp_path/'engine.json'
    harness = tmp_path/'harness.json'
    harness.write_text(json.dumps(dict(worker=dict(provider='subscription', generation=dict(model='a')),
                                       controller=dict(provider='subscription', generation=dict(model='a')))))
    engine.write_text(json.dumps(dict(harness_config='harness.json')))
    project = tmp_path/'p'
    (project/'.mizpah').mkdir(parents=True)
    (project/'.mizpah'/'config.json').write_text(json.dumps(dict(models=dict(worker=dict(generation=dict(model='chosen'))))))
    init.pin_crew(project, engine)
    harness.write_text(json.dumps(dict(worker=dict(generation=dict(model='b')), controller=dict(generation=dict(model='b')))))
    init.main(['pin', '--config', str(engine), str(project)])   # a later default does not move it
    models = json.loads((project/'.mizpah'/'config.json').read_text())['models']
    assert models['worker']['generation']['model'] == 'chosen' and models['controller']['generation']['model'] == 'a'


def test_a_reopened_work_order_moves_its_last_report_aside(tmp_path):
    from mizpah import worker
    (tmp_path/'result.json').write_text('{"verdict": "blocked_by_worker"}')
    assert worker.archive_report(tmp_path).name == 'result.1.json'
    (tmp_path/'result.json').write_text('{"verdict": "incomplete"}')
    assert worker.archive_report(tmp_path).name == 'result.2.json'
    assert not (tmp_path/'result.json').exists() and worker.archive_report(tmp_path) is None


def test_a_restarted_work_order_says_why(tmp_path):
    from mizpah import worker
    root = tmp_path/'sess'/'tasks'/'repair'
    root.mkdir(parents=True)
    task = dict(id='repair', bucket='high')
    assert worker.restart_reason(root, task, 'medium', None, None) == ('rebucketed', 'bucket medium (8 points) → high (21 points)')
    report = root/'result.1.json'
    report.write_text('{"verdict": "blocked_by_worker"}')
    (tmp_path/'sess'/'controller.jsonl').write_text(json.dumps(dict(applied=dict(unblock=['repair after check_audio'])))+'\n')
    assert worker.restart_reason(root, task, 'high', report, None) == ('unblocked', 'released by the controller after check_audio')
    assert worker.restart_reason(root, task, 'high', None, {'call': 1})[0] == 'resumed'
    (root/'reopen.md').write_text('the reading no longer stands')
    assert worker.restart_reason(root, task, 'high', None, None) is None


def test_the_host_says_what_it_is_doing_between_seats(tmp_path):
    from mizpah import loop
    loop.host_step(tmp_path, 'closing work order w (complete)')
    loop.host_step(tmp_path, 're-taking stale reading 1 of 2: grid_lock')
    live = json.loads((tmp_path/'host.live.json').read_text())
    assert [s['text'] for s in live['steps']] == ['closing work order w (complete)', 're-taking stale reading 1 of 2: grid_lock']
    loop.host_step(tmp_path, None)
    assert not (tmp_path/'host.live.json').exists()


def test_a_resumed_landing_takes_the_projects_state_directory(tmp_path):
    """After a landing the controller reopened the route entry on the project; the session's saved copy still said
    done. The resume takes the project's state directory; the worker's own state directories are not touched."""
    from mizpah import layout
    project = tmp_path/'p'
    state = project/layout.dirname(project)
    (state/'map').mkdir(parents=True)
    (state/'route.json').write_text('{"tasks": [{"id": "compose", "status": "in_progress"}]}')
    (state/'map'/'map.json').write_text('{}')
    (project/'piece.mid').write_bytes(b'x')

    class Session:
        def replace_state(self, prefix, members):
            self.called = (prefix, members)
            return dict(prefix=prefix, replaced=len(members))
    session = Session()
    out = worker.refresh_project_state(session, project)
    prefix, members = session.called
    assert prefix == layout.dirname(project) and out['replaced'] == 2
    assert b'in_progress' in members[prefix+'/route.json'] and 'piece.mid' not in members


def test_the_green_after_a_reopen_reflects_on_what_the_reopen_asked(tmp_path):
    root = tmp_path/'task'
    root.mkdir()
    assert worker.reopen_lesson(root) == ''
    (root/'reopen.delivered.md').write_text('the left hand never changes job')
    lesson = worker.reopen_lesson(root)
    assert lesson.startswith('This work order was reopened') and '> the left hand never changes job' in lesson


def test_a_hold_after_route_complete_is_resumed_not_skipped(tmp_path, monkeypatch):
    from mizpah import loop
    root = tmp_path/'sess'
    for task, verdict in (('held', 'stopped'), ('landed', 'complete'), ('killed', None)):
        (root/'tasks'/task).mkdir(parents=True)
        (root/'tasks'/task/'state.sqlite3').write_text('')
        if verdict:
            (root/'tasks'/task/'result.json').write_text(json.dumps(dict(verdict=verdict)))
    route = [dict(id=t, status='done') for t in ('held', 'landed', 'killed')]
    monkeypatch.setattr(loop, 'terra', lambda config, project, *args: dict(tasks=route if 'status' in args else []))
    monkeypatch.setattr(loop.controller, 'ready_order', lambda project, tasks: [])
    picked = [t['id'] for t in loop.pickable(dict(mizpah=dict(agent='a')), tmp_path, root)]
    assert picked == ['held', 'killed']


def test_a_red_completion_says_what_terra_refused_and_that_nothing_retried_it(tmp_path):
    root = tmp_path/'task'
    (root/'events').mkdir(parents=True)
    call = dict(id='c1', type='function', function=dict(name='bash', arguments=json.dumps(
        dict(command='terra route complete audit --run r1 --known k'))))
    result = dict(call_id='c1', result=dict(status='ok', exit_code=1, stderr='TERRA ERROR [route_complete]: known k is low: med is the floor of belief'))
    (root/'events'/'session.jsonl').write_text(json.dumps(dict(event_type='worker_turn', payload=dict(
        response=dict(tool_calls=[call]), tool_results=[result])))+'\n')
    text = worker.completion_problem(root, dict(id='audit'), 'in_progress')
    assert 'known k is low' in text and 'Nothing has run it again since' in text
    assert 'you have not run it yet' in worker.completion_problem(tmp_path/'none', dict(id='audit'), 'in_progress')
