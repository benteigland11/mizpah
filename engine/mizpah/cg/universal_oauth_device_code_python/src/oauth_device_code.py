"""RFC 8628 device authorization grant as a pure state machine.

The grant has three moments: ask the authorization server for a device code,
show the user a code and a URL, then poll the token endpoint until the user
finishes or the code dies. This module owns the request shapes and the
response interpretation; the caller owns the HTTP transport, the sleeping
and whatever shows the code to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_INTERVAL_SECONDS = 5
SLOW_DOWN_INCREMENT_SECONDS = 5
FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}


@dataclass(frozen=True)
class DeviceCodeConfig:
    device_authorization_endpoint: str
    token_endpoint: str
    client_id: str
    scopes: tuple[str, ...] = ()
    extra_authorization_fields: tuple[tuple[str, str], ...] = ()
    extra_token_fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RequestDescriptor:
    url: str
    method: str
    headers: dict[str, str]
    body: str


@dataclass(frozen=True)
class DeviceAuthorization:
    """What the authorization server handed back; show ``user_code`` and a URI."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int = DEFAULT_INTERVAL_SECONDS
    verification_uri_complete: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class PollOutcome:
    """One token-endpoint answer, classified.

    ``state`` is one of ``granted`` (``token`` holds the payload), ``pending``
    (ask again after ``next_interval`` seconds) or ``failed`` (``error`` says
    why and polling must stop).
    """

    state: str
    next_interval: int
    token: dict[str, Any] | None = None
    error: str | None = None
    error_description: str | None = None


class DeviceFlowError(RuntimeError):
    """The grant ended without a token; ``error`` carries the server's code."""

    def __init__(self, error: str, description: str | None = None) -> None:
        super().__init__(description or error)
        self.error = error
        self.description = description


def build_device_authorization_request(config: DeviceCodeConfig) -> RequestDescriptor:
    fields: list[tuple[str, str]] = [("client_id", config.client_id)]
    if config.scopes:
        fields.append(("scope", " ".join(config.scopes)))
    fields.extend(config.extra_authorization_fields)
    return RequestDescriptor(config.device_authorization_endpoint, "POST", dict(FORM_HEADERS), urlencode(fields))


def parse_device_authorization(payload: dict[str, Any]) -> DeviceAuthorization:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dictionary")
    if payload.get("error"):
        raise DeviceFlowError(str(payload["error"]), payload.get("error_description"))
    missing = [key for key in ("device_code", "user_code", "verification_uri", "expires_in") if not payload.get(key)]
    if missing:
        raise ValueError(f"device authorization response is missing {', '.join(missing)}")
    interval = int(payload.get("interval") or DEFAULT_INTERVAL_SECONDS)
    return DeviceAuthorization(
        device_code=str(payload["device_code"]),
        user_code=str(payload["user_code"]),
        verification_uri=str(payload["verification_uri"]),
        expires_in=int(payload["expires_in"]),
        interval=max(interval, 1),
        verification_uri_complete=payload.get("verification_uri_complete"),
        raw=dict(payload),
    )


def build_token_poll_request(config: DeviceCodeConfig, device_code: str) -> RequestDescriptor:
    fields: list[tuple[str, str]] = [
        ("grant_type", GRANT_TYPE),
        ("device_code", device_code),
        ("client_id", config.client_id),
    ]
    fields.extend(config.extra_token_fields)
    return RequestDescriptor(config.token_endpoint, "POST", dict(FORM_HEADERS), urlencode(fields))


def interpret_poll_response(status: int, payload: dict[str, Any], *, interval: int) -> PollOutcome:
    """Classify one token-endpoint answer per RFC 8628 §3.5."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dictionary")
    error = payload.get("error")
    if status == 200 and not error and payload.get("access_token"):
        return PollOutcome("granted", interval, token=dict(payload))
    if error == "authorization_pending":
        return PollOutcome("pending", interval)
    if error == "slow_down":
        return PollOutcome("pending", interval + SLOW_DOWN_INCREMENT_SECONDS)
    description = payload.get("error_description")
    if error:
        return PollOutcome("failed", interval, error=str(error), error_description=description)
    return PollOutcome("failed", interval, error=f"http_{status}", error_description=description or "unexpected token response")


Post = Callable[[RequestDescriptor], tuple[int, dict[str, Any]]]


def run_device_flow(
    config: DeviceCodeConfig,
    post: Post,
    *,
    sleep: Callable[[float], None],
    on_authorization: Callable[[DeviceAuthorization], None] | None = None,
    clock: Callable[[], float],
) -> dict[str, Any]:
    """Drive the whole grant with injected transport, sleep and clock.

    ``post`` sends a descriptor and returns ``(status, json_payload)``.
    ``on_authorization`` is where the caller shows the user code. Returns the
    raw token payload; raises ``DeviceFlowError`` when the server ends it.
    """
    status, payload = post(build_device_authorization_request(config))
    if status != 200 and not payload.get("error"):
        raise DeviceFlowError(f"http_{status}", "device authorization request failed")
    authorization = parse_device_authorization(payload)
    if on_authorization is not None:
        on_authorization(authorization)
    deadline = clock() + authorization.expires_in
    interval = authorization.interval
    while True:
        if clock() >= deadline:
            raise DeviceFlowError("expired_token", "the device code expired before the user finished")
        sleep(interval)
        status, payload = post(build_token_poll_request(config, authorization.device_code))
        outcome = interpret_poll_response(status, payload, interval=interval)
        if outcome.state == "granted":
            assert outcome.token is not None
            return outcome.token
        if outcome.state == "failed":
            raise DeviceFlowError(outcome.error or "unknown", outcome.error_description)
        interval = outcome.next_interval
