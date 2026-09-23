"""One engine config for every model: the project's pinned seat carries what used to need a file per model."""
import json

from mizpah import init, ops


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
