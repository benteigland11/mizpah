import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.bearer_refresh_policy import BearerSession, RefreshFailed, RefreshPolicy, classify_refresh_failure  # noqa: E402

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Token:
    value: str
    expires_at: datetime | None


def _session(token: Token, refreshed: list[Token], **kwargs) -> BearerSession[Token]:
    def refresh(current: Token) -> Token:
        return Token(current.value + "+", NOW + timedelta(hours=1))

    return BearerSession(token, refresh, lambda t: t.expires_at, on_refreshed=refreshed.append, clock=lambda: NOW, **kwargs)


def test_needs_refresh_inside_skew() -> None:
    policy = RefreshPolicy(skew_seconds=60)
    assert policy.needs_refresh(NOW + timedelta(seconds=30), NOW)
    assert not policy.needs_refresh(NOW + timedelta(seconds=90), NOW)
    assert not policy.needs_refresh(None, NOW)


def test_ensure_fresh_refreshes_proactively_and_persists() -> None:
    refreshed: list[Token] = []
    session = _session(Token("a", NOW + timedelta(seconds=10)), refreshed, policy=RefreshPolicy(skew_seconds=60))
    assert session.ensure_fresh().value == "a+"
    assert refreshed == [session.credential]
    assert session.ensure_fresh().value == "a+"


def test_call_retries_once_on_401() -> None:
    refreshed: list[Token] = []
    session = _session(Token("a", NOW + timedelta(hours=1)), refreshed)
    seen: list[str] = []

    def send(token: Token) -> tuple[int, str]:
        seen.append(token.value)
        return (401, "nope") if token.value == "a" else (200, "ok")

    assert session.call(send) == (200, "ok")
    assert seen == ["a", "a+"] and len(refreshed) == 1


def test_call_does_not_loop_on_repeated_401() -> None:
    session = _session(Token("a", None), [])
    calls = [0]

    def send(token: Token) -> tuple[int, str]:
        calls[0] += 1
        return 401, "still no"

    assert session.call(send) == (401, "still no")
    assert calls[0] == 2


def test_call_respects_retry_switch() -> None:
    session = _session(Token("a", None), [], policy=RefreshPolicy(retry_on_unauthorized=False))
    assert session.call(lambda t: (401, "x")) == (401, "x")
    assert session.credential.value == "a"


def test_refresh_failure_propagates() -> None:
    def refresh(current: Token) -> Token:
        raise RefreshFailed("dead", terminal=True, error_code="invalid_grant")

    session = BearerSession(Token("a", NOW), refresh, lambda t: t.expires_at, clock=lambda: NOW)
    with pytest.raises(RefreshFailed) as info:
        session.ensure_fresh()
    assert info.value.terminal and info.value.error_code == "invalid_grant"


@pytest.mark.parametrize(
    "status, payload, terminal, code",
    [
        (400, {"error": "invalid_grant", "error_description": "revoked"}, True, "invalid_grant"),
        (401, {}, True, None),
        (403, {"error": "access_denied"}, True, "access_denied"),
        (429, {}, False, None),
        (500, {"error": "server_error"}, False, "server_error"),
        (None, None, False, None),
    ],
)
def test_classify_refresh_failure(status, payload, terminal, code) -> None:
    failure = classify_refresh_failure(status, payload)
    assert (failure.terminal, failure.error_code, failure.status) == (terminal, code, status)
    assert str(failure)
