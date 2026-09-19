"""Sign in to a fake provider with a device code, then complete through the transport.

Everything is in-process: a fake HTTP callable plays the authorization
server and the model endpoint, and the credential store lives in a temp dir.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.subscription_provider_session import (
    CredentialStore,
    DeviceCodeFlow,
    HttpResponse,
    LoginPrompt,
    ProviderProfile,
    ProviderSession,
)

profile = ProviderProfile(
    name="example_grok_like",
    display_name="Example Subscription",
    auth=DeviceCodeFlow(
        device_authorization_endpoint="https://auth.example.org/oauth/device/code",
        token_endpoint="https://auth.example.org/oauth/token",
        client_id="example-public-client",
        scopes=("openid", "offline_access"),
    ),
    api_base_url="https://api.example.org/v1",
    completion_path="/responses",
    wire="responses",
    credential_headers={"Authorization": "Bearer {token}"},
    models=("example-model",),
    default_model="example-model",
)

answers = iter([
    (200, {"device_code": "dc-1", "user_code": "WXYZ-1234", "verification_uri": "https://auth.example.org/activate",
           "expires_in": 600, "interval": 1}),
    (400, {"error": "authorization_pending"}),
    (200, {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600}),
])
completed = {"type": "response.completed", "response": {
    "id": "resp_1", "model": "example-model", "status": "completed",
    "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Paris."}]}],
    "usage": {"input_tokens": 18, "output_tokens": 3, "total_tokens": 21}}}


def fake_http(method: str, url: str, headers, body, timeout: float) -> HttpResponse:
    if "auth.example.org" in url:
        status, payload = next(answers)
        return HttpResponse(status, {"content-type": "application/json"}, json.dumps(payload).encode())
    print(f"  model call -> {url} with Authorization={headers['Authorization']}")
    print(f"  responses body: {json.loads(body)['input']}")
    sse = f"event: response.completed\ndata: {json.dumps(completed)}\n\n".encode()
    return HttpResponse(200, {"content-type": "text/event-stream"}, sse)


def show(prompt: LoginPrompt) -> None:
    print(f"  open {prompt.url} and enter {prompt.user_code}")


with tempfile.TemporaryDirectory() as directory:
    session = ProviderSession(profile, CredentialStore(Path(directory) / "credentials.json"), http=fake_http,
                              open_browser=False, sleep=lambda seconds: None)
    print("login:")
    session.login(on_prompt=show)
    print("status:", json.dumps(session.status()))

    print("complete:")
    transport = session.transport()
    payload = {"messages": [{"role": "system", "content": "Answer in one word."},
                            {"role": "user", "content": "Capital of France?"}]}
    response = transport("/responses", payload)
    print("  chat completions view:", json.loads(response.body)["choices"][0]["message"])
    print("  token estimate after calibration:", session.count(payload))
    print("logout:", session.logout())
