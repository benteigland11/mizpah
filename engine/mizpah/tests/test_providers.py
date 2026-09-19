"""Engine glue for hosted providers: profiles, overrides, the CLI, and the client factory."""
import json
from pathlib import Path

import pytest

from cg.bp_subscription_provider_session_python.src.subscription_provider_session import HttpResponse
from mizpah import provider_cli, providers
from mizpah.worker import client_for


def test_shipped_profiles_load_and_xai_needs_a_client_id() -> None:
    reg = providers.registry({}, environ={})
    assert {'openai_api', 'openai_chatgpt', 'xai_api', 'xai_grok', 'zai_coding', 'kimi_coding', 'minimax_coding', 'deepseek_api'} <= set(reg.names())
    chatgpt = reg.get('openai_chatgpt')
    assert chatgpt.auth.kind == 'oauth_pkce' and chatgpt.auth.redirect_uri == 'http://localhost:1455/auth/callback'
    assert chatgpt.headers_for('t', {'account_id': 'a'})['chatgpt-account-id'] == 'a'
    assert providers.missing_client_id(chatgpt) is None
    grok = reg.get('xai_grok')
    assert grok.auth.kind == 'device_code' and 'GROK_OAUTH2_CLIENT_ID' in (providers.missing_client_id(grok) or '')


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
    assert provider_cli.main(['login', 'xai_grok']) == 2
    assert 'client id' in json.loads(capsys.readouterr().out)['error']
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
    assert client.capabilities()['context'] == 400000
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
