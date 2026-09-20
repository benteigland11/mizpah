import base64
import json
import sys
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.subscription_provider_session import (  # noqa: E402
    HttpResponse,
    LoginError,
    LoginPrompt,
    NotSignedIn,
    ProviderSession,
    urllib_http,
)

# The blueprint's own public surface accepts profiles and stores by value; build them through the
# façade's exposed types so tests never reach past it.
from src.subscription_provider_session import CredentialStore, ProviderProfile  # noqa: E402
from src.subscription_provider_session import ApiKeyAuth, AuthorizedUserFileAuth, DeviceCodeFlow, NoAuth, OAuthPkceFlow  # noqa: E402
from src.subscription_provider_session import QuarantinedCredential, RefreshFailed, RefreshPolicy  # noqa: E402

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def _jwt(payload: dict) -> str:
    seg = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    return f"{seg({'alg': 'none'})}.{seg(payload)}.sig"


class FakeHttp:
    """Plays the token endpoint and the model endpoint; records everything."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.token_answers: list[tuple[int, dict]] = []
        self.model_answers: list[HttpResponse] = []

    def __call__(self, method: str, url: str, headers, body: bytes | None, timeout: float) -> HttpResponse:
        record = {"method": method, "url": url, "headers": dict(headers), "body": body, "timeout": timeout}
        self.calls.append(record)
        if "/token" in url or "/device" in url:
            status, payload = self.token_answers.pop(0)
            return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode())
        return self.model_answers.pop(0)


def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


COMPLETED = {"type": "response.completed", "response": {
    "id": "r1", "model": "m", "status": "completed",
    "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}],
    "usage": {"input_tokens": 40, "output_tokens": 2, "total_tokens": 42}}}


def pkce_profile() -> ProviderProfile:
    return ProviderProfile(
        "sub", "Sub", OAuthPkceFlow(authorization_endpoint="https://auth.example.org/authorize",
                                    token_endpoint="https://auth.example.org/token", client_id="app_1",
                                    scopes=("openid", "offline_access"), extra_authorization_params={"prompt": "login"},
                                    account_claim_namespace="https://example.org/auth", account_claim_key="account_id"),
        "https://api.example.org/backend", "/responses", wire="responses",
        static_headers={"originator": "example"}, credential_headers={"Authorization": "Bearer {token}", "x-account": "{account_id}"},
        models=("m",), default_model="m")


def device_profile() -> ProviderProfile:
    return ProviderProfile(
        "dev", "Dev", DeviceCodeFlow(device_authorization_endpoint="https://auth.example.org/device/code",
                                     token_endpoint="https://auth.example.org/token", client_id="c"),
        "https://api.example.org/v1", "/chat/completions", wire="chat_completions",
        credential_headers={"Authorization": "Bearer {token}"}, models=("m",), default_model="m")


@pytest.fixture
def store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(tmp_path / "creds.json", clock=lambda: NOW)


def _session(profile: ProviderProfile, store: CredentialStore, http: FakeHttp, **kwargs) -> ProviderSession:
    return ProviderSession(profile, store, http=http, open_browser=False, clock=lambda: NOW, sleep=lambda s: None, **kwargs)


def test_pkce_login_round_trip(store: CredentialStore) -> None:
    http = FakeHttp()
    id_token = _jwt({"email": "user@example.org", "https://example.org/auth": {"account_id": "acct-1"}})
    http.token_answers.append((200, {"access_token": "secret-access", "refresh_token": "rt", "expires_in": 3600, "id_token": id_token}))
    session = _session(pkce_profile(), store, http)
    prompts: list[LoginPrompt] = []

    def on_prompt(prompt: LoginPrompt) -> None:
        prompts.append(prompt)
        parsed = urlparse(prompt.url)
        query = parse_qs(parsed.query)
        assert parsed.netloc == "auth.example.org" and query["prompt"] == ["login"] and query["code_challenge_method"] == ["S256"]
        redirect = query["redirect_uri"][0]
        # The browser "comes back" on another thread.
        threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect}?code=the-code&state={query['state'][0]}", timeout=5).read()).start()

    record = session.login(on_prompt=on_prompt)
    assert prompts[0].kind == "browser" and not prompts[0].browser_opened
    exchange = parse_qs(http.calls[0]["body"].decode())
    assert exchange["grant_type"] == ["authorization_code"] and exchange["code"] == ["the-code"] and "code_verifier" in exchange
    assert record.secret["refresh_token"] == "rt"
    assert record.metadata["account_id"] == "acct-1" and record.metadata["email"] == "user@example.org"
    status = session.status()
    assert status["signed_in"] and status["expired"] is False and "secret-access" not in json.dumps(status)


def test_pkce_login_rejects_state_mismatch_and_token_error(store: CredentialStore) -> None:
    http = FakeHttp()
    session = _session(pkce_profile(), store, http, login_timeout_seconds=5)

    def wrong_state(prompt: LoginPrompt) -> None:
        redirect = parse_qs(urlparse(prompt.url).query)["redirect_uri"][0]
        threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect}?code=c&state=bogus", timeout=5).read()).start()

    with pytest.raises(ValueError, match="state"):
        session.login(on_prompt=wrong_state)

    http.token_answers.append((400, {"error": "invalid_grant", "error_description": "bad code"}))

    def ok_state(prompt: LoginPrompt) -> None:
        query = parse_qs(urlparse(prompt.url).query)
        threading.Thread(target=lambda: urllib.request.urlopen(f"{query['redirect_uri'][0]}?code=c&state={query['state'][0]}", timeout=5).read()).start()

    with pytest.raises(LoginError, match="bad code"):
        session.login(on_prompt=ok_state)
    assert session.status()["signed_in"] is False


def test_device_login(store: CredentialStore) -> None:
    http = FakeHttp()
    http.token_answers += [
        (200, {"device_code": "dc", "user_code": "AB-12", "verification_uri": "https://auth.example.org/activate", "expires_in": 600, "interval": 1}),
        (400, {"error": "authorization_pending"}),
        (200, {"access_token": "at", "refresh_token": "rt", "expires_in": 60}),
    ]
    opened: list[str] = []
    session = ProviderSession(device_profile(), store, http=http, open_browser=True, browser_opener=opened.append,
                              clock=lambda: NOW, sleep=lambda s: None)
    prompts: list[LoginPrompt] = []
    session.login(on_prompt=prompts.append)
    assert prompts[0].kind == "device" and prompts[0].user_code == "AB-12" and prompts[0].browser_opened
    assert opened == ["https://auth.example.org/activate"]
    assert session.status()["signed_in"]


def test_device_login_denied(store: CredentialStore) -> None:
    http = FakeHttp()
    http.token_answers += [
        (200, {"device_code": "dc", "user_code": "AB-12", "verification_uri": "https://auth.example.org/activate", "expires_in": 600}),
        (400, {"error": "access_denied"}),
    ]
    with pytest.raises(LoginError, match="access_denied"):
        _session(device_profile(), store, http).login()


def test_api_key_login_and_logout(store: CredentialStore) -> None:
    profile = ProviderProfile("keyed", "Keyed", ApiKeyAuth(environment_variable="K"), "https://api.example.org", "/v1/chat/completions",
                              credential_headers={"Authorization": "Bearer {token}"})
    session = _session(profile, store, FakeHttp())
    with pytest.raises(LoginError):
        session.login()
    session.login(api_key="sk-1")
    assert session.status()["signed_in"]
    assert session.logout() is True and session.status()["signed_in"] is False


def test_transport_translates_responses_wire_and_calibrates(store: CredentialStore) -> None:
    http = FakeHttp()
    store.put("sub", {"access_token": "at", "refresh_token": "rt"}, {"account_id": "acct-1", "expires_at": (NOW + timedelta(hours=1)).isoformat()})
    session = _session(pkce_profile(), store, http)
    http.model_answers.append(HttpResponse(200, {"content-type": "text/event-stream"}, _sse([COMPLETED])))
    transport = session.transport()
    payload = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u" * 100}], "top_k": 3}
    assert transport.request_metadata("/responses", payload) == {"provider": "sub", "wire": "responses",
                                                                   "url": "https://api.example.org/backend/responses",
                                                                   "dropped_fields": ["top_k"]}
    response = transport("/responses", payload, timeout_seconds=30)
    assert response.status == 200 and response.error is None
    chat = json.loads(response.body)
    assert chat["choices"][0]["message"]["content"] == "hi" and chat["usage"]["prompt_tokens"] == 40
    sent = http.calls[0]
    assert sent["headers"]["Authorization"] == "Bearer at" and sent["headers"]["x-account"] == "acct-1"
    assert sent["headers"]["originator"] == "example" and sent["timeout"] == 30
    body = json.loads(sent["body"])
    assert body["instructions"] == "s" and body["model"] == "m" and body["stream"] is True and "top_k" not in body
    assert session.count(payload)["calibrated"] is True


def test_transport_refreshes_on_401_and_persists(store: CredentialStore) -> None:
    http = FakeHttp()
    store.put("sub", {"access_token": "old", "refresh_token": "rt"}, {"expires_at": (NOW + timedelta(hours=1)).isoformat()})
    session = _session(pkce_profile(), store, http)
    http.model_answers += [HttpResponse(401, {}, b'{"error": "expired"}'),
                           HttpResponse(200, {"content-type": "application/json"},
                                        json.dumps(COMPLETED["response"]).encode())]
    http.token_answers.append((200, {"access_token": "new", "expires_in": 3600}))
    response = session.transport()("/responses", {"messages": [{"role": "user", "content": "u"}]})
    assert response.status == 200 and json.loads(response.body)["choices"][0]["message"]["content"] == "hi"
    assert [c["headers"].get("Authorization") for c in http.calls if "backend" in c["url"]] == ["Bearer old", "Bearer new"]
    refresh = parse_qs([c for c in http.calls if "/token" in c["url"]][0]["body"].decode())
    assert refresh["grant_type"] == ["refresh_token"] and refresh["refresh_token"] == ["rt"]
    stored = store.get("sub")
    assert stored is not None and stored.secret == {"access_token": "new", "token_type": "bearer", "refresh_token": "rt"}


def test_transport_refreshes_proactively(store: CredentialStore) -> None:
    http = FakeHttp()
    store.put("dev", {"access_token": "old", "refresh_token": "rt"}, {"expires_at": (NOW + timedelta(seconds=10)).isoformat()})
    session = _session(device_profile(), store, http, refresh_policy=RefreshPolicy(skew_seconds=60))
    http.token_answers.append((200, {"access_token": "new", "refresh_token": "rt2", "expires_in": 3600}))
    http.model_answers.append(HttpResponse(200, {"content-type": "application/json"}, json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 1}}).encode()))
    response = session.transport()("/chat/completions", {"messages": [{"role": "user", "content": "u"}], "top_k": 1})
    assert json.loads(response.body)["choices"][0]["message"]["content"] == "ok"
    model_call = [c for c in http.calls if "/chat/completions" in c["url"]][0]
    assert model_call["headers"]["Authorization"] == "Bearer new"
    assert json.loads(model_call["body"])["top_k"] == 1  # chat_completions wire passes through untouched
    assert store.get("dev").secret["refresh_token"] == "rt2"


def test_terminal_refresh_failure_quarantines(store: CredentialStore) -> None:
    http = FakeHttp()
    store.put("sub", {"access_token": "old", "refresh_token": "rt"}, {"expires_at": NOW.isoformat()})
    session = _session(pkce_profile(), store, http)
    http.token_answers.append((400, {"error": "invalid_grant", "error_description": "revoked"}))
    response = session.transport()("/responses", {"messages": [{"role": "user", "content": "u"}]})
    assert response.status == 401 and response.failure_kind == "authentication" and response.definitive
    assert "revoked" in response.error
    with pytest.raises(QuarantinedCredential):
        session.credential()
    status = session.status()
    assert status["signed_in"] is False and status["quarantined"] and "revoked" in status["quarantine_reason"]
    again = session.transport()("/responses", {"messages": [{"role": "user", "content": "u"}]})
    assert again.failure_kind == "authentication" and again.status is None


def test_transport_without_credential_and_with_bad_payload(store: CredentialStore) -> None:
    session = _session(pkce_profile(), store, FakeHttp())
    response = session.transport()("/responses", {"messages": [{"role": "user", "content": "u"}]})
    assert response.failure_kind == "authentication" and "not signed in" in response.error
    with pytest.raises(NotSignedIn):
        session.credential()
    store.put("sub", {"access_token": "at"}, {})
    response = session.transport()("/responses", {"messages": []})
    assert response.failure_kind == "codec"


def test_transport_surfaces_provider_errors(store: CredentialStore) -> None:
    http = FakeHttp()
    store.put("sub", {"access_token": "at"}, {})
    session = _session(pkce_profile(), store, http)
    http.model_answers.append(HttpResponse(429, {}, b'{"error": {"message": "slow down"}}'))
    response = session.transport()("/responses", {"messages": [{"role": "user", "content": "u"}]})
    assert response.status == 429 and response.error is None and "slow down" in response.body
    http.model_answers.append(HttpResponse(200, {"content-type": "text/event-stream"},
                                           _sse([{"type": "error", "code": "server_error", "message": "boom"}])))
    response = session.transport()("/responses", {"messages": [{"role": "user", "content": "u"}]})
    assert response.failure_kind == "provider_error" and response.error == "boom"
    http.model_answers.append(HttpResponse(200, {}, b"not json"))
    response = session.transport()("/responses", {"messages": [{"role": "user", "content": "u"}]})
    assert response.error == "Response is not a JSON object"

    def broken(*args):
        raise OSError("connection reset")

    session = _session(pkce_profile(), store, broken)
    response = session.transport()("/responses", {"messages": [{"role": "user", "content": "u"}]})
    assert response.status is None and "connection reset" in response.error


def test_list_models(store: CredentialStore) -> None:
    listed = ProviderProfile("keyed", "Keyed", ApiKeyAuth(environment_variable="K"), "https://api.example.org/v1", "/chat/completions",
                             credential_headers={"Authorization": "Bearer {token}"}, models_path="/models")
    http = FakeHttp()
    session = _session(listed, store, http)
    with pytest.raises(NotSignedIn):
        session.list_models()
    session.login(api_key="sk-1")
    http.model_answers.append(HttpResponse(200, {}, json.dumps({"data": [{"id": "models/m-1"}, {"id": "m-2"}, {"object": "x"}]}).encode()))
    assert session.list_models() == ["m-1", "m-2"]
    call = http.calls[-1]
    assert call["method"] == "GET" and call["url"] == "https://api.example.org/v1/models" and call["headers"]["Authorization"] == "Bearer sk-1"
    http.model_answers.append(HttpResponse(200, {}, json.dumps({"models": [{"slug": "big", "visibility": "list"},
                                                                          {"slug": "secret", "visibility": "hide"}]}).encode()))
    assert session.list_models() == ["big"]
    http.model_answers.append(HttpResponse(200, {}, json.dumps({"models": {"a": {"info": {}}, "b": {"hidden": True}}}).encode()))
    assert session.list_models() == ["a"]
    http.model_answers.append(HttpResponse(403, {}, b"no"))
    with pytest.raises(LookupError, match="403"):
        session.list_models()
    http.model_answers.append(HttpResponse(200, {}, b"[]garbage"))
    with pytest.raises(LookupError):
        session.list_models()
    assert _session(pkce_profile(), store, http).list_models() == []


def test_model_info_carries_efforts() -> None:
    from src.subscription_provider_session import _model_infos
    codex = {"models": [{"slug": "big", "visibility": "list", "context_window": 272000,
                         "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}], "default_reasoning_level": "high"},
                        {"slug": "hidden", "visibility": "hide"}]}
    (info,) = _model_infos(codex)
    assert info.as_dict() == {"id": "big", "efforts": ["low", "high"], "default_effort": "high", "context_window": 272000}
    proxy = {"models": {"g": {"info": {"reasoning_efforts": [{"id": "xhigh"}, {"id": "low"}], "reasoning_effort": "xhigh",
                                       "supports_reasoning_effort": True, "context_window": 500000}},
                        "plain": {"info": {"supports_reasoning_effort": False, "reasoning_efforts": [{"id": "low"}]}}}}
    by_id = {i.id: i for i in _model_infos(proxy)}
    assert by_id["g"].efforts == ("xhigh", "low") and by_id["g"].default_effort == "xhigh"
    assert by_id["plain"].efforts == () and by_id["plain"].default_effort is None
    (openai,) = _model_infos({"data": [{"id": "m", "object": "model"}]})
    assert openai.efforts == () and openai.context_window is None
    published = _model_infos({"publisherModels": [{"name": "publishers/vendor/models/big-1"}, {"name": "publishers/vendor/models/small-1"}]})
    assert [i.id for i in published] == ["big-1", "small-1"]


def test_model_id_prefix_is_applied(store: CredentialStore) -> None:
    prefixed = ProviderProfile("v", "V", ApiKeyAuth(environment_variable="K"), "https://api.example.org/v1", "/chat/completions",
                               credential_headers={"Authorization": "Bearer {token}"},
                               models_path="https://list.example.org/publishers/models", model_id_prefix="vendor/")
    http = FakeHttp()
    session = _session(prefixed, store, http)
    session.login(api_key="sk-1")
    http.model_answers.append(HttpResponse(200, {}, json.dumps({"publisherModels": [{"name": "publishers/vendor/models/big-1"}]}).encode()))
    assert session.list_models() == ["vendor/big-1"] and http.calls[-1]["url"] == "https://list.example.org/publishers/models"


def test_no_auth_profile_is_signed_in_when_reachable(store: CredentialStore) -> None:
    local = ProviderProfile("local", "Local", NoAuth(), "http://127.0.0.1:9", "/v1/chat/completions", models_path="/v1/models",
                            models=("m",), default_model="m")
    http = FakeHttp()
    session = _session(local, store, http)
    http.model_answers.append(HttpResponse(200, {}, json.dumps({"data": [{"id": "m-local"}]}).encode()))
    status = session.status()
    assert status["signed_in"] and status["reachable"] and status["auth_kind"] == "none"
    http.model_answers.append(HttpResponse(200, {}, json.dumps({"data": [{"id": "m-local"}]}).encode()))
    assert session.list_models() == ["m-local"]
    assert "Authorization" not in http.calls[-1]["headers"]
    http.model_answers.append(HttpResponse(200, {"content-type": "application/json"}, json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()))
    response = session.transport()("/v1/chat/completions", {"messages": [{"role": "user", "content": "u"}]})
    assert response.status == 200
    http.model_answers.append(HttpResponse(401, {}, b"nope"))
    response = session.transport()("/v1/chat/completions", {"messages": [{"role": "user", "content": "u"}]})
    assert response.status == 401 and response.failure_kind == "authentication" and not response.definitive
    assert session.store.peek("local") is None  # nothing stored, nothing quarantined

    def down(*args) -> HttpResponse:
        raise OSError("refused")

    session = _session(local, store, down)
    status = session.status()
    assert status["signed_in"] is False and "refused" in status["reason"]
    assert session.login().metadata == {"auth_kind": "none"}


def test_session_header_is_stable_per_transport(store: CredentialStore) -> None:
    keyed = ProviderProfile("keyed", "Keyed", ApiKeyAuth(environment_variable="K"), "https://api.example.org/v1", "/chat/completions",
                            credential_headers={"Authorization": "Bearer {token}", "x-session": "{session}"})
    http = FakeHttp()
    session = _session(keyed, store, http)
    session.login(api_key="sk-1")
    chat = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
    first = session.transport()
    http.model_answers += [HttpResponse(200, {}, chat)] * 3
    first("/chat/completions", {"messages": [{"role": "user", "content": "a"}]})
    first("/chat/completions", {"messages": [{"role": "user", "content": "b"}]})
    session.transport(session_id="fixed")("/chat/completions", {"messages": [{"role": "user", "content": "c"}]})
    ids = [c["headers"]["x-session"] for c in http.calls if "/chat/completions" in c["url"]]
    assert ids[0] == ids[1] == first.session_id and ids[2] == "fixed" and ids[0] != "fixed"


def test_pkce_device_flow_and_per_user_host(store: CredentialStore) -> None:
    profile = ProviderProfile("per_user", "Per user", DeviceCodeFlow(device_authorization_endpoint="https://auth.example.org/device/code",
                                                                      token_endpoint="https://auth.example.org/token", client_id="c", pkce=True),
                              "https://{resource_url}/v1", "/chat/completions", models_path="/models",
                              credential_headers={"Authorization": "Bearer {token}"}, token_metadata_fields=("resource_url",),
                              models=("m",), default_model="m")
    http = FakeHttp()
    http.token_answers += [
        (200, {"device_code": "dc", "user_code": "AB-12", "verification_uri": "https://auth.example.org/activate", "expires_in": 600}),
        (200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "resource_url": "portal.example.org"}),
    ]
    session = _session(profile, store, http)
    record = session.login()
    device = parse_qs(http.calls[0]["body"].decode())
    poll = parse_qs(http.calls[1]["body"].decode())
    assert device["code_challenge_method"] == ["S256"] and len(device["code_challenge"][0]) > 20
    assert "code_verifier" in poll and poll["code_verifier"][0] != device["code_challenge"][0]
    assert record.metadata["resource_url"] == "portal.example.org"
    http.model_answers.append(HttpResponse(200, {}, json.dumps({"data": [{"id": "m"}]}).encode()))
    assert session.list_models() == ["m"] and http.calls[-1]["url"] == "https://portal.example.org/v1/models"
    http.model_answers.append(HttpResponse(200, {"content-type": "application/json"}, json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()))
    session.transport()("/chat/completions", {"messages": [{"role": "user", "content": "u"}]})
    assert http.calls[-1]["url"] == "https://portal.example.org/v1/chat/completions"
    # a refresh keeps the per-user host
    http.token_answers.append((200, {"access_token": "at2", "expires_in": 3600}))
    refreshed = session._refresh(session.credential())
    assert refreshed.metadata["resource_url"] == "portal.example.org" and refreshed.secret["refresh_token"] == "rt"


def test_authorized_user_file_login_and_refresh(store: CredentialStore, tmp_path: Path) -> None:
    adc = tmp_path / "adc.json"
    profile = ProviderProfile("cloud", "Cloud", AuthorizedUserFileAuth(path=str(adc), token_endpoint="https://oauth.example.org/token"),
                              "https://region.example.org/v1/projects/p/endpoints/openapi", "/chat/completions",
                              credential_headers={"Authorization": "Bearer {token}"})
    http = FakeHttp()
    session = _session(profile, store, http)
    with pytest.raises(LoginError, match="no credentials file"):
        session.login()
    adc.write_text(json.dumps({"type": "service_account", "client_email": "x"}))
    with pytest.raises(LoginError, match="service_account"):
        session.login()
    adc.write_text(json.dumps({"type": "authorized_user", "client_id": "cid", "client_secret": "csec", "refresh_token": "rt"}))
    http.token_answers.append((200, {"access_token": "minted", "expires_in": 3600, "token_type": "Bearer"}))
    record = session.login()
    form = parse_qs(http.calls[0]["body"].decode())
    assert form["grant_type"] == ["refresh_token"] and form["client_secret"] == ["csec"] and form["client_id"] == ["cid"]
    assert record.secret["access_token"] == "minted" and record.secret["client_secret"] == "csec"
    assert record.metadata["source_file"] == str(adc) and session.status()["signed_in"]
    http.token_answers.append((400, {"error": "invalid_grant", "error_description": "revoked"}))
    with pytest.raises(RefreshFailed):
        session._refresh(session.credential())
    assert session.status()["quarantined"]


def test_endpoint_and_count(store: CredentialStore) -> None:
    session = _session(pkce_profile(), store, FakeHttp())
    assert session.endpoint() == {"base_url": "https://api.example.org/backend", "completion_path": "/responses",
                                  "timeout_seconds": 600.0, "headers": {"originator": "example"}}
    assert session.count({"messages": [{"role": "user", "content": "x" * 360}]})["tokens"] > 0


def test_urllib_http_against_loopback() -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            self.send_response(418)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        http = urllib_http(maximum_response_bytes=1000)
        response = http("POST", f"http://127.0.0.1:{server.server_address[1]}/x", {"Content-Type": "application/json"}, b'{"a":1}', 5)
        assert response.status == 418 and response.body == b'{"a":1}' and response.headers["content-type"] == "application/json"
        with pytest.raises(OSError):
            urllib_http(maximum_response_bytes=2)("POST", f"http://127.0.0.1:{server.server_address[1]}/x", {}, b"12345", 5)
    finally:
        server.shutdown()
        server.server_close()
