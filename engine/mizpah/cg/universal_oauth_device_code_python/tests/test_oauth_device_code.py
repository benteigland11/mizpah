import sys
from pathlib import Path
from urllib.parse import parse_qs

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.oauth_device_code import (  # noqa: E402
    DeviceCodeConfig,
    DeviceFlowError,
    build_device_authorization_request,
    build_token_poll_request,
    interpret_poll_response,
    parse_device_authorization,
    run_device_flow,
)

CONFIG = DeviceCodeConfig(
    device_authorization_endpoint="https://auth.example.org/device/code",
    token_endpoint="https://auth.example.org/token",
    client_id="client-1",
    scopes=("openid", "offline_access"),
    extra_token_fields=(("audience", "api"),),
)
AUTHORIZATION = {"device_code": "dc", "user_code": "ABCD-EFGH", "verification_uri": "https://auth.example.org/activate",
                 "expires_in": 60, "interval": 2}


def test_authorization_request_shape() -> None:
    request = build_device_authorization_request(CONFIG)
    assert request.method == "POST" and request.url == CONFIG.device_authorization_endpoint
    assert parse_qs(request.body) == {"client_id": ["client-1"], "scope": ["openid offline_access"]}


def test_poll_request_shape() -> None:
    body = parse_qs(build_token_poll_request(CONFIG, "dc").body)
    assert body["grant_type"] == ["urn:ietf:params:oauth:grant-type:device_code"]
    assert body["device_code"] == ["dc"] and body["audience"] == ["api"]


def test_parse_authorization_defaults_interval() -> None:
    parsed = parse_device_authorization({k: v for k, v in AUTHORIZATION.items() if k != "interval"})
    assert parsed.interval == 5 and parsed.user_code == "ABCD-EFGH"


def test_parse_authorization_rejects_missing_and_error() -> None:
    with pytest.raises(ValueError):
        parse_device_authorization({"device_code": "x"})
    with pytest.raises(DeviceFlowError) as info:
        parse_device_authorization({"error": "invalid_client"})
    assert info.value.error == "invalid_client"


@pytest.mark.parametrize(
    "status, payload, state, interval",
    [
        (200, {"access_token": "t"}, "granted", 2),
        (400, {"error": "authorization_pending"}, "pending", 2),
        (400, {"error": "slow_down"}, "pending", 7),
        (400, {"error": "access_denied"}, "failed", 2),
        (400, {"error": "expired_token"}, "failed", 2),
        (500, {}, "failed", 2),
    ],
)
def test_interpret_poll_response(status: int, payload: dict, state: str, interval: int) -> None:
    outcome = interpret_poll_response(status, payload, interval=2)
    assert (outcome.state, outcome.next_interval) == (state, interval)


def test_run_device_flow_polls_until_granted() -> None:
    answers = iter([(200, AUTHORIZATION), (400, {"error": "authorization_pending"}), (400, {"error": "slow_down"}),
                    (200, {"access_token": "tok", "refresh_token": "ref"})])
    slept: list[float] = []
    shown: list[str] = []
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    token = run_device_flow(CONFIG, lambda request: next(answers), sleep=sleep, clock=clock,
                            on_authorization=lambda auth: shown.append(auth.user_code))
    assert token["access_token"] == "tok" and shown == ["ABCD-EFGH"]
    assert slept == [2, 2, 7]


def test_run_device_flow_stops_on_denial_and_expiry() -> None:
    denied = iter([(200, AUTHORIZATION), (400, {"error": "access_denied", "error_description": "no"})])
    with pytest.raises(DeviceFlowError, match="no"):
        run_device_flow(CONFIG, lambda r: next(denied), sleep=lambda s: None, clock=lambda: 0.0)

    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += 100

    pending = iter([(200, AUTHORIZATION)] + [(400, {"error": "authorization_pending"})] * 5)
    with pytest.raises(DeviceFlowError) as info:
        run_device_flow(CONFIG, lambda r: next(pending), sleep=sleep, clock=lambda: now[0])
    assert info.value.error == "expired_token"
