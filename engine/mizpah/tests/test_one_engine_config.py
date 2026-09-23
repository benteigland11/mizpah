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
