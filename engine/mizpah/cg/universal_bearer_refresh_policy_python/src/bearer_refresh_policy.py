"""When to refresh a bearer credential, and when to stop trying.

Three rules, stated once so every provider client behaves the same:

* **proactive** - refresh when the credential expires inside a skew window,
  so a request is never sent with a token that dies mid-flight;
* **reactive** - a 401 earns exactly one refresh-and-retry; a second 401 in
  the same exchange is a real failure;
* **terminal** - a refresh answered with ``invalid_grant`` or any 4xx means
  the grant is dead. Report it as terminal so the caller quarantines the
  credential instead of hammering the token endpoint.

The credential and the refresh call are opaque to this module; it only sees
``expires_at`` and whatever the refresh callable returns or raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Generic, TypeVar

DEFAULT_SKEW_SECONDS = 300
TERMINAL_OAUTH_ERRORS = frozenset({"invalid_grant", "invalid_client", "unauthorized_client", "access_denied"})

Credential = TypeVar("Credential")


class RefreshFailed(RuntimeError):
    """A refresh attempt failed. ``terminal`` says whether to give up on the grant."""

    def __init__(self, message: str, *, terminal: bool, error_code: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.terminal = terminal
        self.error_code = error_code
        self.status = status


def classify_refresh_failure(status: int | None, payload: dict[str, Any] | None) -> RefreshFailed:
    """Turn a token endpoint's failure into a ``RefreshFailed`` with the right ``terminal`` bit."""
    code = str((payload or {}).get("error") or "") or None
    description = str((payload or {}).get("error_description") or code or f"http {status}")
    terminal = code in TERMINAL_OAUTH_ERRORS or (status is not None and 400 <= status < 500 and status != 429)
    return RefreshFailed(description, terminal=terminal, error_code=code, status=status)


@dataclass(frozen=True)
class RefreshPolicy:
    skew_seconds: int = DEFAULT_SKEW_SECONDS
    retry_on_unauthorized: bool = True

    def needs_refresh(self, expires_at: datetime | None, now: datetime | None = None) -> bool:
        if expires_at is None:
            return False
        reference = (now or datetime.now(timezone.utc)).timestamp()
        return expires_at.timestamp() <= reference + self.skew_seconds


@dataclass
class BearerSession(Generic[Credential]):
    """Holds the current credential and applies the policy around a request.

    ``refresh`` takes the current credential and returns the next one, or
    raises ``RefreshFailed``. ``expires_at_of`` reads the expiry. ``on_refreshed``
    is where the caller persists the new credential.
    """

    credential: Credential
    refresh: Callable[[Credential], Credential]
    expires_at_of: Callable[[Credential], datetime | None]
    policy: RefreshPolicy = RefreshPolicy()
    on_refreshed: Callable[[Credential], None] | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def ensure_fresh(self) -> Credential:
        """Refresh proactively if inside the skew window; return a usable credential."""
        if self.policy.needs_refresh(self.expires_at_of(self.credential), self.clock()):
            self._refresh_now()
        return self.credential

    def call(self, send: Callable[[Credential], tuple[int, Any]]) -> tuple[int, Any]:
        """Send with a fresh credential; on 401, refresh once and resend."""
        status, result = send(self.ensure_fresh())
        if status != 401 or not self.policy.retry_on_unauthorized:
            return status, result
        self._refresh_now()
        return send(self.credential)

    def _refresh_now(self) -> None:
        self.credential = self.refresh(self.credential)
        if self.on_refreshed is not None:
            self.on_refreshed(self.credential)
