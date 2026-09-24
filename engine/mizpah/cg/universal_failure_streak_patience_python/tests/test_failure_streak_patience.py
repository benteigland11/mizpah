import pytest

from src.failure_streak_patience import FailureStreak, PatiencePolicy


def fixed(policy: PatiencePolicy | None = None) -> tuple[FailureStreak, list[float]]:
    now = [0.0]
    return FailureStreak(policy or PatiencePolicy(), clock=lambda: now[0], rng=lambda: 0.5), now


def test_quick_delays_then_exponential_to_the_cap() -> None:
    policy = PatiencePolicy()
    assert [policy.delay(n) for n in range(1, 9)] == [2, 5, 15, 30, 60, 120, 240, 300]


def test_transient_streak_waits_and_advances() -> None:
    streak, now = fixed()
    waits = []
    for _ in range(4):
        decision = streak.failed('transient')
        assert decision.retry
        waits.append(decision.delay_seconds)
        now[0] += decision.delay_seconds
    assert waits == [2, 5, 15, 30]


def test_success_resets_the_streak() -> None:
    streak, now = fixed()
    for _ in range(3):
        now[0] += streak.failed('transient').delay_seconds
    streak.succeeded()
    decision = streak.failed('transient')
    assert decision.attempt == 1 and decision.delay_seconds == 2


def test_patience_is_spent_in_time_not_attempts() -> None:
    streak, now = fixed(PatiencePolicy(patience_seconds=100))
    decisions = []
    while True:
        decision = streak.failed('transient')
        decisions.append(decision)
        if not decision.retry:
            break
        now[0] += decision.delay_seconds
    assert decisions[-1].reason == 'patience spent'
    assert sum(d.delay_seconds for d in decisions) <= 100


def test_fatal_never_retries() -> None:
    streak, _ = fixed()
    decision = streak.failed('fatal')
    assert not decision.retry and decision.reason == 'not retryable'


def test_rate_limited_honours_the_server_delay_capped() -> None:
    streak, _ = fixed(PatiencePolicy(rate_limit_cap_seconds=60))
    assert streak.failed('rate_limited', retry_after_seconds=12).delay_seconds == 12
    assert streak.failed('rate_limited', retry_after_seconds=999).delay_seconds == 60


def test_rate_limited_without_a_delay_backs_off() -> None:
    streak, _ = fixed()
    assert streak.failed('rate_limited').delay_seconds == 2


def test_immediate_retries_are_bounded_and_do_not_climb_the_ladder() -> None:
    streak, _ = fixed(PatiencePolicy(maximum_immediate=2))
    assert streak.failed('immediate').delay_seconds == 0
    assert streak.failed('immediate').retry
    assert streak.failed('transient').delay_seconds == 2
    assert not streak.failed('immediate').retry


def test_jitter_stays_within_bounds() -> None:
    low = FailureStreak(PatiencePolicy(jitter=0.2), rng=lambda: 0.0).failed('transient').delay_seconds
    high = FailureStreak(PatiencePolicy(jitter=0.2), rng=lambda: 0.999999).failed('transient').delay_seconds
    assert low == pytest.approx(1.6) and high == pytest.approx(2.4, abs=1e-3)


def test_invalid_input_is_refused() -> None:
    with pytest.raises(ValueError):
        PatiencePolicy(factor=0.5)
    with pytest.raises(ValueError):
        FailureStreak().failed('sometimes')
