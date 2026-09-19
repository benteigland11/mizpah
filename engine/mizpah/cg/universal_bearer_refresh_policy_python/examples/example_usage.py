"""A bearer session refreshing proactively and once on 401, with a fake token endpoint."""

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.bearer_refresh_policy import BearerSession, RefreshPolicy


@dataclass(frozen=True)
class Token:
    access: str
    expires_at: datetime


now = datetime(2030, 1, 1, tzinfo=timezone.utc)
counter = [0]


def refresh(current: Token) -> Token:
    counter[0] += 1
    print(f"  refreshing {current.access} -> access-{counter[0]}")
    return Token(f"access-{counter[0]}", now + timedelta(hours=1))


session = BearerSession(
    Token("access-0", now + timedelta(seconds=30)),
    refresh,
    lambda t: t.expires_at,
    policy=RefreshPolicy(skew_seconds=300),
    on_refreshed=lambda t: print(f"  persisted {t.access}"),
    clock=lambda: now,
)

print("proactive: expiring in 30s with a 300s skew")
print("  using", session.ensure_fresh().access)

print("reactive: the server answers 401 once")
answers = iter([(401, "expired"), (200, "hello")])
print("  result", session.call(lambda token: next(answers)))
