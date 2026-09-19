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
    assert copilot.auth.kind == 'device_code' and 'MIZPAH_GITHUB_CLIENT_ID' in (providers.missing_client_id(copilot) or '')
    assert copilot.models_url == 'https://api.githubcopilot.com/models'
    anthropic = reg.get('anthropic_api')
    assert anthropic.headers_for('k') == {'anthropic-version': '2023-06-01', 'Authorization': 'Bearer k', 'x-api-key': 'k'}
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
    assert out['default_model'] == 'grok-4.6' and out['models'] == [] and out['source'] == 'signed_out'

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
    monkeypatch.setattr(providers.ProviderSession, 'list_models', lambda self: ['grok-4.6', 'grok-4.5'])
    monkeypatch.setattr(providers, 'credential_path', lambda config=None: tmp_path / 'c.json')
    assert provider_cli.main(['--config', str(engine), 'use', 'xai_api', 'nope']) == 2
    assert 'not one of' in json.loads(capsys.readouterr().out)['error']
    assert provider_cli.main(['--config', str(engine), 'use', 'xai_api', 'grok-4.5', '--role', 'worker']) == 0
    assert json.loads(capsys.readouterr().out)['roles'] == ['worker']
    written = json.loads(harness.read_text())
    worker = written['worker']
    assert worker['provider'] == 'subscription' and worker['subscription'] == 'xai_api' and 'known_issues' not in worker
    assert worker['endpoint']['base_url'] == 'https://api.x.ai/v1' and worker['endpoint']['maximum_response_bytes'] == 5
    assert worker['generation'] == {'model': 'grok-4.5', 'temperature': 0.5}
    assert written['controller'] == {'generation': {'model': '/x.gguf'}}
    monkeypatch.setattr(providers.ProviderSession, 'list_models', lambda self: ['gpt-6-astra'])
    providers.session_for('openai_chatgpt', {}).store.put('openai_chatgpt', {'access_token': 't'}, {})
    assert provider_cli.main(['use', 'openai_chatgpt', '--harness', str(harness)]) == 0
    assert json.loads(harness.read_text())['controller']['generation']['model'] == 'gpt-6-astra'


def test_models_merge_live_list_when_signed_in(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    session = providers.session_for('mistral_api', {})
    assert providers.available_models(session) == ([], 'signed_out')
    session.login(api_key='sk-1')

    def fake_http(method, url, headers, body, timeout) -> HttpResponse:
        assert method == 'GET' and url == 'https://api.mistral.ai/v1/models'
        return HttpResponse(200, {}, json.dumps({'data': [{'id': 'brand-new'}, {'id': 'mistral-large-latest'}]}).encode())

    session.http = fake_http
    merged, source = providers.available_models(session)
    assert source == 'live' and merged == ['brand-new', 'mistral-large-latest']  # default first when present

    def broken(*args) -> HttpResponse:
        raise OSError('down')

    session.http = broken
    hints, source = providers.available_models(session)
    assert hints[0] == 'mistral-medium-latest' and source.startswith('list_failed: OSError')
