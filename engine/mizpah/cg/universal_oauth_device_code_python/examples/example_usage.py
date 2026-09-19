"""Drive a device-code grant against a fake authorization server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.oauth_device_code import DeviceAuthorization, DeviceCodeConfig, RequestDescriptor, run_device_flow

config = DeviceCodeConfig(
    device_authorization_endpoint="https://auth.example.org/oauth/device/code",
    token_endpoint="https://auth.example.org/oauth/token",
    client_id="example-public-client",
    scopes=("openid", "profile", "offline_access"),
)

# A fake server: hands out a code, says "pending" twice, then grants.
answers = iter([
    (200, {"device_code": "dev-123", "user_code": "WXYZ-1234", "verification_uri": "https://auth.example.org/activate",
           "expires_in": 600, "interval": 5}),
    (400, {"error": "authorization_pending"}),
    (400, {"error": "slow_down"}),
    (200, {"access_token": "access-abc", "refresh_token": "refresh-def", "expires_in": 3600, "token_type": "bearer"}),
])


def fake_post(request: RequestDescriptor) -> tuple[int, dict]:
    print(f"POST {request.url}  {request.body}")
    return next(answers)


def show(auth: DeviceAuthorization) -> None:
    print(f"\nOpen {auth.verification_uri} and enter code {auth.user_code}\n")


clock = [0.0]


def sleep(seconds: float) -> None:
    print(f"(sleep {seconds}s)")
    clock[0] += seconds


token = run_device_flow(config, fake_post, sleep=sleep, clock=lambda: clock[0], on_authorization=show)
print("granted:", {k: v for k, v in token.items() if k != "access_token"})
