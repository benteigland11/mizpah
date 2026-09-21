"""Engine glue for hosted providers: profiles, overrides, the CLI, and the client factory."""
import json
from pathlib import Path

import pytest

from cg.bp_subscription_provider_session_python.src.subscription_provider_session import HttpResponse
from mizpah import provider_cli, providers
from mizpah.worker import client_for


def test_shipped_profiles_load_and_xai_needs_a_client_id() -> None:
    reg = providers.registry({}, environ={})
    assert {'openai_api', 'openai_chatgpt', 'xai_api', 'xai_grok', 'zai_coding', 'kimi_coding', 'minimax_coding', 'deepseek_api',
            'github_copilot', 'google_gemini', 'anthropic_api', 'mistral_api', 'openrouter_api', 'groq_api'} <= set(reg.names())
    copilot = reg.get('github_copilot')
    assert copilot.auth.kind == 'device_code' and providers.missing_client_id(copilot) is None
    assert copilot.models_url == 'https://api.githubcopilot.com/models'
    anthropic = reg.get('anthropic_api')
    assert anthropic.wire == 'messages' and anthropic.completion_path == '/messages'
    assert anthropic.headers_for('k') == {'anthropic-version': '2023-06-01', 'x-api-key': 'k'}
    assert anthropic.models_url == 'https://api.anthropic.com/v1/models'
    chatgpt = reg.get('openai_chatgpt')
    assert chatgpt.auth.kind == 'oauth_pkce' and chatgpt.auth.redirect_uri == 'http://localhost:1455/auth/callback'
    assert chatgpt.headers_for('t', {'account_id': 'a'})['chatgpt-account-id'] == 'a'
    assert providers.missing_client_id(chatgpt) is None
    grok = reg.get('xai_grok')
    assert grok.auth.kind == 'device_code' and providers.missing_client_id(grok) is None
    assert grok.api_base_url == 'https://cli-chat-proxy.grok.com/v1' and grok.auth.client_id


def test_overrides_and_environment_fill_the_profile() -> None:
    config = {'mizpah': {'providers': {'xai_grok': {'default_model': 'grok-4.5', 'auth': {'scopes': ['openid']}},
                                       'custom': {'display_name': 'Custom', 'api_base_url': 'https://llm.example.org',
                                                  'completion_path': '/v1/chat/completions',
                                                  'auth': {'kind': 'api_key', 'environment_variable': 'CUSTOM_KEY'}}}}}
    reg = providers.registry(config, environ={'GROK_OAUTH2_CLIENT_ID': 'cid-1'})
    grok = reg.get('xai_grok')
    assert grok.auth.client_id == 'cid-1' and grok.auth.scopes == ('openid',) and grok.default_model == 'grok-4.5'
    assert grok.auth.token_endpoint == 'https://auth.x.ai/oauth2/token'
    assert providers.missing_client_id(grok) is None
    assert reg.get('custom').wire == 'chat_completions'


def test_credential_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    assert providers.credential_path({}) == tmp_path / 'mizpah' / 'credentials.json'
    assert providers.credential_path({'mizpah': {'credentials_file': '~/x.json'}}) == Path.home() / 'x.json'


def test_cli_login_status_logout_for_api_key(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    monkeypatch.delenv('XAI_API_KEY', raising=False)
    assert provider_cli.main(['login', 'xai_api']) == 2
    assert 'XAI_API_KEY' in json.loads(capsys.readouterr().out)['error']
    assert provider_cli.main(['login', 'xai_api', '--api-key', 'sk-test']) == 0
    assert json.loads(capsys.readouterr().out)['signed_in'] is True
    assert provider_cli.main(['status', 'xai_api']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['signed_in'] and 'sk-test' not in json.dumps(out)
    assert provider_cli.main(['logout', 'xai_api']) == 0
    assert json.loads(capsys.readouterr().out)['removed'] is True
    assert provider_cli.main(['list']) == 0
    assert len(json.loads(capsys.readouterr().out)['providers']) >= 8


def test_client_for_subscription(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    spec = {'provider': 'subscription', 'subscription': 'openai_api', 'endpoint': {'timeout_seconds': 30}}
    with pytest.raises(RuntimeError, match='not signed in'):
        client_for(spec, None, {})
    providers.session_for('openai_api', {}).login(api_key='sk-1')
    client = client_for(spec, None, {})
    assert client.config.base_url == 'https://api.openai.com/v1' and client.config.timeout_seconds == 30
    assert client.capabilities()['context'] == 272000
    assert client.count({'messages': [{'role': 'user', 'content': 'x' * 400}]}, 'worker')['tokens'] > 0

    def fake_http(method, url, headers, body, timeout) -> HttpResponse:
        assert headers['Authorization'] == 'Bearer sk-1' and url == 'https://api.openai.com/v1/chat/completions'
        return HttpResponse(200, {'content-type': 'application/json'}, json.dumps(
            {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hi'}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': 7, 'completion_tokens': 1}}).encode())

    client.session.http = fake_http
    response = client.complete({'messages': [{'role': 'user', 'content': 'hello'}]}, 'worker')
    assert response['choices'][0]['message']['content'] == 'hi'
    assert client.count({'messages': [{'role': 'user', 'content': 'hello'}]}, 'worker')['calibrated'] is True


def test_cli_models_and_use(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    assert provider_cli.main(['models', 'xai_grok']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['default_model'] is None and out['models'] == [] and out['source'] == 'signed_out'

    harness = tmp_path / 'harness.json'
    harness.write_text(json.dumps({'worker': {'provider': 'llama_client', 'known_issues': {'repetition': {}},
                                              'endpoint': {'base_url': 'http://127.0.0.1:1', 'maximum_response_bytes': 5},
                                              'generation': {'model': '/x.gguf', 'top_k': 4, 'temperature': 0.5}},
                                   'controller': {'generation': {'model': '/x.gguf'}}}))
    engine = tmp_path / 'engine.json'
    engine.write_text(json.dumps({'harness_config': 'harness.json'}))
    assert provider_cli.main(['--config', str(engine), 'use', 'xai_grok', 'grok-4.5']) == 2
    assert 'sign in' in json.loads(capsys.readouterr().out)['error']
    keyed = providers.session_for('xai_api', {'mizpah': {'credentials_file': str(tmp_path / 'c.json')}})
    keyed.login(api_key='k')
    from cg.bp_subscription_provider_session_python.src.subscription_provider_session import ModelInfo
    monkeypatch.setattr(providers.ProviderSession, 'list_model_info', lambda self: [ModelInfo('grok-4.6'), ModelInfo('grok-4.5')])
    monkeypatch.setattr(providers, 'credential_path', lambda config=None: tmp_path / 'c.json')
    assert provider_cli.main(['--config', str(engine), 'use', 'xai_api', 'nope']) == 2
    assert 'listed' in json.loads(capsys.readouterr().out)['error']
    assert provider_cli.main(['--config', str(engine), 'use', 'xai_api', 'grok-4.5', '--role', 'worker']) == 0
    assert json.loads(capsys.readouterr().out)['roles'] == ['worker']
    written = json.loads(harness.read_text())
    worker = written['worker']
    assert worker['provider'] == 'subscription' and worker['subscription'] == 'xai_api' and 'known_issues' not in worker
    assert worker['endpoint']['base_url'] == 'https://api.x.ai/v1' and worker['endpoint']['maximum_response_bytes'] == 5
    # Sampling knobs do not travel to a hosted seat: the codex backend answers 'Unsupported parameter: temperature'.
    assert worker['generation'] == {'model': 'grok-4.5'}
    assert written['controller'] == {'generation': {'model': '/x.gguf'}}
    monkeypatch.setattr(providers.ProviderSession, 'list_model_info', lambda self: [ModelInfo('gpt-6-astra')])
    providers.session_for('openai_chatgpt', {}).store.put('openai_chatgpt', {'access_token': 't'}, {})
    assert provider_cli.main(['use', 'openai_chatgpt', '--harness', str(harness)]) == 0
    assert json.loads(harness.read_text())['controller']['generation']['model'] == 'gpt-6-astra'


def test_models_merge_live_list_when_signed_in(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    session = providers.session_for('mistral_api', {})
    assert providers.available_models(session) == ([], 'signed_out')
    assert session.profile.models == ()
    session.login(api_key='sk-1')

    def fake_http(method, url, headers, body, timeout) -> HttpResponse:
        assert method == 'GET' and url == 'https://api.mistral.ai/v1/models'
        return HttpResponse(200, {}, json.dumps({'data': [{'id': 'brand-new'}, {'id': 'mistral-large-latest'}]}).encode())

    session.http = fake_http
    merged, source = providers.available_models(session)
    assert source == 'live' and [r['id'] for r in merged] == ['brand-new', 'mistral-large-latest']

    def broken(*args) -> HttpResponse:
        raise OSError('down')

    session.http = broken
    hints, source = providers.available_models(session)
    assert hints == [] and source.startswith('list_failed: OSError')  # nothing invented; the person types the id


def _serve(handler_body: bytes, status: int = 200):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(handler_body)

        do_GET = do_POST = _reply

        def log_message(self, *args) -> None:
            return

    server = HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_address[1]}'


def test_added_local_server_follows_reachability_and_use_writes_llama_client(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    engine = tmp_path / 'engine.json'
    harness = tmp_path / 'harness.json'
    harness.write_text(json.dumps({'worker': {'generation': {'model': '/x.gguf', 'top_k': 4}}, 'controller': {}}))
    engine.write_text(json.dumps({'harness_config': 'harness.json'}))
    assert provider_cli.main(['--config', str(engine), 'list']) == 0
    assert not any(p['auth_kind'] == 'none' for p in json.loads(capsys.readouterr().out)['providers'])  # none shipped
    assert provider_cli.main(['--config', str(engine), 'add-local', 'openai_api', '--base-url', 'http://x']) == 2
    assert 'shipped' in json.loads(capsys.readouterr().out)['error']
    assert provider_cli.main(['--config', str(engine), 'add-local', 'box', '--base-url', 'x']) == 2
    capsys.readouterr()
    assert provider_cli.main(['--config', str(engine), 'add-local', 'box', '--base-url', 'http://127.0.0.1:9/', '--kind', 'llama',
                              '--display-name', 'The box']) == 0
    added = json.loads(capsys.readouterr().out)
    assert added['event'] == 'added' and added['custom'] and added['auth_kind'] == 'none' and added['signed_in'] is False
    assert added['api_base_url'] == 'http://127.0.0.1:9' and added['display_name'] == 'The box'
    assert provider_cli.main(['--config', str(engine), 'models', 'box']) == 0
    assert json.loads(capsys.readouterr().out)['source'] == 'unreachable'
    assert provider_cli.main(['--config', str(engine), 'use', 'box', 'anything']) == 2
    assert 'not answering' in json.loads(capsys.readouterr().out)['error']

    server, base = _serve(json.dumps({'data': [{'id': 'local-7b'}, {'id': 'local-70b'}]}).encode())
    try:
        assert provider_cli.main(['--config', str(engine), 'configure', 'box', '--base-url', base + '/']) == 0
        out = json.loads(capsys.readouterr().out)
        assert out['override']['api_base_url'] == base and out['override']['local_kind'] == 'llama' and out['reachable'] is True
        assert provider_cli.main(['--config', str(engine), 'models', 'box']) == 0
        out = json.loads(capsys.readouterr().out)
        assert out['source'] == 'live' and out['models'] == ['local-7b', 'local-70b'] and out['details'][0]['efforts'] == []
        assert provider_cli.main(['--config', str(engine), 'use', 'box', 'local-70b', '--effort', 'high']) == 0
        out = json.loads(capsys.readouterr().out)
        assert out['transport'] == 'llama_client' and out['effort'] is None  # no ladder: effort is not sent
        worker = json.loads(harness.read_text())['worker']
        assert worker['provider'] == 'llama_client' and 'subscription' not in worker
        assert worker['endpoint']['base_url'] == base and worker['endpoint']['tokenize_path'] == '/tokenize'
        assert worker['generation'] == {'model': 'local-70b', 'top_k': 4} and worker['known_issues']['repetition']
        assert provider_cli.main(['--config', str(engine), 'list']) == 0
        rows = {p['provider']: p for p in json.loads(capsys.readouterr().out)['providers']}
        assert rows['box']['custom'] is True and rows['openai_api']['custom'] is False
    finally:
        server.shutdown()
        server.server_close()

    assert provider_cli.main(['--config', str(engine), 'remove', 'openai_api']) == 2
    capsys.readouterr()
    assert provider_cli.main(['--config', str(engine), 'remove', 'box']) == 0
    assert json.loads(capsys.readouterr().out) == {'event': 'removed', 'provider': 'box'}
    assert 'box' not in json.loads(engine.read_text())['providers']
    assert provider_cli.main(['--config', str(engine), 'remove', 'box']) == 2


def test_local_kind_is_detected_from_tokenize(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    class LlamaLike(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200 if self.path.startswith('/tokenize') else 404)
            self.end_headers()
            self.wfile.write(b'{"tokens": []}' if self.path.startswith('/tokenize') else b'{}')

        def log_message(self, *args) -> None:
            return

    server = HTTPServer(('127.0.0.1', 0), LlamaLike)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        assert providers.detect_local_kind(base) == 'llama'
        assert providers.detect_local_kind(base + '/v1') == 'llama'
        assert providers.local_profile('x', base + '/v1')['api_base_url'] == base  # llama root, not /v1
        assert providers.local_profile('x', base)['local_kind'] == 'llama'
    finally:
        server.shutdown()
        server.server_close()
    openai_server, openai_base = _serve(b'{}', status=404)
    try:
        assert providers.detect_local_kind(openai_base) == 'openai'
        assert providers.local_profile('y', openai_base)['local_kind'] == 'openai'
    finally:
        openai_server.shutdown()
        openai_server.server_close()
    assert providers.detect_local_kind('http://127.0.0.1:9') is None
    assert providers.local_profile('z', 'http://127.0.0.1:9')['local_kind'] == 'openai'  # not up yet: the safe default
    with pytest.raises(ValueError):
        providers.local_profile('z', 'http://127.0.0.1:9', 'weird')


def test_openai_local_kind_goes_through_the_session_transport(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    engine = tmp_path / 'engine.json'
    harness = tmp_path / 'harness.json'
    harness.write_text(json.dumps({'worker': {}, 'controller': {}}))
    engine.write_text(json.dumps({'harness_config': 'harness.json'}))
    server, base = _serve(json.dumps({'data': [{'id': 'm'}]}).encode())
    try:
        assert provider_cli.main(['--config', str(engine), 'add-local', 'ollama', '--base-url', base, '--kind', 'openai']) == 0  # the stub answers everything, so say so
        capsys.readouterr()
        assert provider_cli.main(['--config', str(engine), 'use', 'ollama', 'm']) == 0
        assert json.loads(capsys.readouterr().out)['transport'] == 'subscription'
        worker = json.loads(harness.read_text())['worker']
        assert worker['provider'] == 'subscription' and worker['subscription'] == 'ollama'
        assert worker['endpoint']['completion_path'] == '/chat/completions'
    finally:
        server.shutdown()
        server.server_close()


def test_use_writes_effort_when_the_model_takes_one(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from cg.bp_subscription_provider_session_python.src.subscription_provider_session import ModelInfo
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    harness = tmp_path / 'harness.json'
    harness.write_text(json.dumps({'worker': {'generation': {'model': 'old', 'reasoning_effort': 'low'}}, 'controller': {}}))
    providers.session_for('openai_chatgpt', {}).store.put('openai_chatgpt', {'access_token': 't'}, {})
    monkeypatch.setattr(providers.ProviderSession, 'list_model_info',
                        lambda self: [ModelInfo('gpt-x', ('low', 'high', 'ultra'), 'high', 272000), ModelInfo('gpt-plain')])
    assert provider_cli.main(['models', 'openai_chatgpt']) == 0
    details = json.loads(capsys.readouterr().out)['details']
    assert details[0] == {'id': 'gpt-x', 'efforts': ['low', 'high', 'ultra'], 'default_effort': 'high', 'context_window': 272000}
    assert details[1]['efforts'] == ['low', 'medium', 'high', 'xhigh']  # inherits the profile ladder
    assert json.loads(open(providers.SHIPPED_PROFILES).read())['profiles'] and all(
        not p['models'] and p['default_model'] is None for p in json.loads(open(providers.SHIPPED_PROFILES).read())['profiles'])

    assert provider_cli.main(['use', 'openai_chatgpt', 'gpt-x', '--harness', str(harness), '--role', 'worker']) == 0
    assert json.loads(capsys.readouterr().out)['effort'] == 'high'  # the model's default
    assert json.loads(harness.read_text())['worker']['generation']['reasoning_effort'] == 'high'
    assert provider_cli.main(['use', 'openai_chatgpt', 'gpt-x', '--harness', str(harness), '--effort', 'max']) == 2
    assert 'not one of' in json.loads(capsys.readouterr().out)['error']
    assert provider_cli.main(['use', 'openai_chatgpt', 'gpt-nope', '--harness', str(harness)]) == 2
    assert 'listed' in json.loads(capsys.readouterr().out)['error']
    assert provider_cli.main(['use', 'openai_chatgpt', '--harness', str(harness)]) == 0  # first listed
    assert json.loads(capsys.readouterr().out)['model'] == 'gpt-x'
    assert provider_cli.main(['use', 'openai_chatgpt', 'gpt-x', '--harness', str(harness), '--effort', 'none']) == 0
    capsys.readouterr()
    assert 'reasoning_effort' not in json.loads(harness.read_text())['worker']['generation']
    assert provider_cli.main(['use', 'openai_chatgpt', 'gpt-x', '--harness', str(harness), '--effort', 'ultra', '--role', 'controller']) == 0
    capsys.readouterr()
    assert json.loads(harness.read_text())['controller']['generation'] == {'model': 'gpt-x', 'reasoning_effort': 'ultra'}


def test_configure_set_edits_any_endpoint_field(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    engine = tmp_path / 'engine.json'
    harness = tmp_path / 'harness.json'
    harness.write_text(json.dumps({'worker': {}, 'controller': {}}))
    engine.write_text(json.dumps({'harness_config': 'harness.json'}))
    server, base = _serve(json.dumps({'data': [{'id': 'm'}]}).encode())
    try:
        assert provider_cli.main(['--config', str(engine), 'add-local', 'box', '--base-url', base, '--kind', 'llama']) == 0
        capsys.readouterr()
        assert provider_cli.main(['--config', str(engine), 'configure', 'box', '--set', 'completion_path=v2/chat', '--set', 'models_path=/v2/models',
                                  '--set', 'tokenize_path=/tok', '--set', 'template_path=/tpl', '--set', 'context_window=32768',
                                  '--set', 'timeout_seconds=120', '--set', 'static_headers={"X-Box": "1"}', '--set', 'display_name=Box 2']) == 0
        out = json.loads(capsys.readouterr().out)
        assert out['override']['completion_path'] == '/v2/chat' and out['override']['context_window'] == 32768
        assert provider_cli.main(['--config', str(engine), 'status', 'box']) == 0
        status = json.loads(capsys.readouterr().out)
        assert status['display_name'] == 'Box 2' and status['endpoint'] == {
            'api_base_url': base, 'address_template': base, 'settings': {}, 'needs': [],
            'completion_path': '/v2/chat', 'models_path': '/v2/models', 'wire': 'chat_completions',
            'context_window': 32768, 'timeout_seconds': 120, 'static_headers': {'X-Box': '1'}, 'tokenize_path': '/tok',
            'template_path': '/tpl', 'local_kind': 'llama'}
        assert provider_cli.main(['--config', str(engine), 'use', 'box', 'm']) == 0
        capsys.readouterr()
        worker = json.loads(harness.read_text())['worker']
        assert worker['endpoint']['completion_path'] == '/v2/chat' and worker['endpoint']['tokenize_path'] == '/tok'
        assert worker['endpoint']['template_path'] == '/tpl' and worker['endpoint']['headers'] == {'Content-Type': 'application/json', 'X-Box': '1'}
        assert worker['endpoint']['timeout_seconds'] == 120
        # invalid results are refused and nothing is written
        assert provider_cli.main(['--config', str(engine), 'configure', 'box', '--set', 'wire=grpc']) == 2
        assert 'valid profile' in json.loads(capsys.readouterr().out)['error']
        assert json.loads(engine.read_text())['providers']['box']['wire'] == 'chat_completions'
        assert provider_cli.main(['--config', str(engine), 'configure', 'box', '--set', 'colour=red']) == 2
        capsys.readouterr()
        assert provider_cli.main(['--config', str(engine), 'configure', 'box', '--set', 'tokenize_path=']) == 0
        assert 'tokenize_path' not in json.loads(capsys.readouterr().out)['override']
        assert provider_cli.main(['--config', str(engine), 'configure', 'openai_chatgpt', '--set', 'auth.client_id=app_test']) == 0
        assert json.loads(capsys.readouterr().out)['override'] == {'auth': {'client_id': 'app_test'}}
    finally:
        server.shutdown()
        server.server_close()


def test_placeholder_addresses_block_and_new_kinds_load() -> None:
    reg = providers.registry({}, environ={})
    assert providers.missing_client_id(reg.get('vertex_ai')) == 'Google Vertex AI needs your Google Cloud project id.'
    assert providers.missing_client_id(reg.get('azure_openai')) == 'Azure OpenAI needs your resource name.'
    assert providers.missing_client_id(reg.get('bedrock_api')) is None  # region has a default
    qwen = reg.get('qwen_oauth')
    assert qwen.auth.kind == 'device_code' and qwen.auth.pkce and providers.missing_client_id(qwen) is None
    assert qwen.base_url_for({'resource_url': 'portal.qwen.ai'}) == 'https://portal.qwen.ai/v1'
    assert reg.get('bedrock_api').auth.environment_variable == 'AWS_BEARER_TOKEN_BEDROCK'
    fixed = providers.registry({'mizpah': {'providers': {'vertex_ai': {'template_values': {'project': 'p1', 'region': 'eu'}}}}}, environ={})
    vertex = fixed.get('vertex_ai')
    assert providers.missing_client_id(vertex) is None
    assert vertex.base_url_for() == 'https://eu-aiplatform.googleapis.com/v1/projects/p1/locations/eu/endpoints/openapi'
    assert vertex.models_url == 'https://eu-aiplatform.googleapis.com/v1beta1/publishers/google/models'


def test_settings_are_set_one_at_a_time(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    engine = tmp_path / 'engine.json'
    engine.write_text(json.dumps({'harness_config': 'harness.json'}))
    assert provider_cli.main(['--config', str(engine), 'status', 'vertex_ai']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['endpoint']['settings'] == {'region': 'us-central1', 'project': ''} and out['endpoint']['needs'] == ['project']
    assert provider_cli.main(['--config', str(engine), 'configure', 'vertex_ai', '--set', 'template_values.project=p1']) == 0
    capsys.readouterr()
    assert provider_cli.main(['--config', str(engine), 'status', 'vertex_ai']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['blocked'] is None and out['endpoint']['needs'] == [] and out['endpoint']['settings']['region'] == 'us-central1'
    assert out['api_base_url'] == 'https://us-central1-aiplatform.googleapis.com/v1/projects/p1/locations/us-central1/endpoints/openapi'
