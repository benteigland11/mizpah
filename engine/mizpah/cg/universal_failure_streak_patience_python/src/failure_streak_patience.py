"""How long to keep trying after a run of failures, and how long to wait before the next try.

A streak is the failures since the last success. Patience is spent in time, not attempts: a streak ends when the
next wait would carry it past `patience_seconds`, however many tries that took. A success ends the streak, so
isolated failures hours apart each start from the quickest delay instead of climbing a shared ladder.

Each failure is reported with a class the caller decides:
  transient     retry: the quick delays first, then exponential backoff to a cap, with jitter
  rate_limited  retry after the server's own delay when it gave one (capped), else as transient
  immediate     retry at once (the failure says nothing about the server, e.g. an oversized reply)
  fatal         never retry: retrying cannot change the answer
"""
from __future__ import annotations

from dataclasses import dataclass
import random
import time
from typing import Callable

TRANSIENT = 'transient'
RATE_LIMITED = 'rate_limited'
IMMEDIATE = 'immediate'
FATAL = 'fatal'
CLASSES = (TRANSIENT, RATE_LIMITED, IMMEDIATE, FATAL)


@dataclass(frozen=True)
class PatiencePolicy:
    quick_delays: tuple[float, ...] = (2.0, 5.0, 15.0)
    base_seconds: float = 30.0
    factor: float = 2.0
    cap_seconds: float = 300.0
    patience_seconds: float = 1800.0
    jitter: float = 0.2
    rate_limit_cap_seconds: float = 900.0
    maximum_immediate: int = 3

    def __post_init__(self) -> None:
        if (any(d < 0 for d in self.quick_delays) or self.base_seconds < 0 or self.factor < 1 or self.cap_seconds < 0
                or self.patience_seconds < 0 or not 0 <= self.jitter < 1 or self.rate_limit_cap_seconds < 0
                or self.maximum_immediate < 0):
            raise ValueError('invalid patience policy')

    def delay(self, attempt: int) -> float:
        """The unjittered wait before retry `attempt` (1-based) of a transient streak."""
        if attempt <= len(self.quick_delays):
            return min(self.cap_seconds, self.quick_delays[attempt-1])
        step = attempt-len(self.quick_delays)-1
        return min(self.cap_seconds, self.base_seconds*(self.factor**step))


@dataclass(frozen=True)
class Decision:
    retry: bool
    delay_seconds: float
    attempt: int
    elapsed_seconds: float
    reason: str


class FailureStreak:
    """Failures since the last success; `failed` says whether to try again and after how long."""

    def __init__(self, policy: PatiencePolicy | None = None, *, clock: Callable[[], float] | None = None,
                 rng: Callable[[], float] | None = None) -> None:
        self.policy = policy or PatiencePolicy()
        self.clock = clock or time.monotonic
        self.rng = rng or random.random
        self.attempts = 0
        self.immediate = 0
        self.started_at: float | None = None

    @property
    def elapsed_seconds(self) -> float:
        return 0.0 if self.started_at is None else max(0.0, self.clock()-self.started_at)

    def succeeded(self) -> None:
        self.attempts = 0
        self.immediate = 0
        self.started_at = None

    def failed(self, failure_class: str, *, retry_after_seconds: float | None = None) -> Decision:
        if failure_class not in CLASSES:
            raise ValueError(f'unknown failure class {failure_class!r}')
        if self.started_at is None:
            self.started_at = self.clock()
        self.attempts += 1
        elapsed = self.elapsed_seconds
        if failure_class == FATAL:
            return Decision(False, 0.0, self.attempts, elapsed, 'not retryable')
        if failure_class == IMMEDIATE:
            self.immediate += 1
            if self.immediate > self.policy.maximum_immediate:
                return Decision(False, 0.0, self.attempts, elapsed, 'immediate retries spent')
            return Decision(True, 0.0, self.attempts, elapsed, 'retry at once')
        transient_attempt = self.attempts-self.immediate
        if failure_class == RATE_LIMITED and retry_after_seconds is not None and retry_after_seconds >= 0:
            wait = min(self.policy.rate_limit_cap_seconds, float(retry_after_seconds))
            reason = 'the server asked for this wait'
        else:
            base = self.policy.delay(transient_attempt)
            wait = base*(1+self.policy.jitter*(2*self.rng()-1))
            reason = 'backoff'
        if elapsed+wait > self.policy.patience_seconds:
            return Decision(False, 0.0, self.attempts, elapsed, 'patience spent')
        return Decision(True, round(wait, 3), self.attempts, elapsed, reason)
