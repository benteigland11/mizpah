"""A streak of dropped connections, a rate limit, a success, and a request that can never work."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.failure_streak_patience import FailureStreak, PatiencePolicy

now = [0.0]
streak = FailureStreak(PatiencePolicy(patience_seconds=600), clock=lambda: now[0], rng=lambda: 0.5)
for failure in ('transient', 'transient', 'rate_limited', 'transient'):
    decision = streak.failed(failure, retry_after_seconds=20 if failure == 'rate_limited' else None)
    print(failure, '->', decision)
    now[0] += decision.delay_seconds
streak.succeeded()
print('after a success:', streak.failed('transient'))
print('bad request:', streak.failed('fatal'))
