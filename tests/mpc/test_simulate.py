from datetime import UTC, datetime, timedelta

import pytest

from ai_quota_scheduler.mpc.simulate import simulate

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)


def zero_user(start, end):
    return 0.0


def test_no_hit_when_idle():
    res = simulate(NOW, 50.0, NOW + 120 * MIN, 0, 1.0, zero_user, MIN, 0.0)
    assert res.hit_at is None
    assert res.harness_pct == 0.0
    assert res.final_used_pct == pytest.approx(50.0)


def test_hit_time_matches_rate():
    res = simulate(NOW, 50.0, NOW + 120 * MIN, 1, 1.0, zero_user, MIN, 0.0)
    assert res.hit_at == NOW + 50 * MIN
    assert res.harness_pct == pytest.approx(50.0)


def test_reserve_moves_hit_earlier():
    base = simulate(NOW, 50.0, NOW + 120 * MIN, 1, 1.0, zero_user, MIN, 0.0)
    reserved = simulate(NOW, 50.0, NOW + 120 * MIN, 1, 1.0, zero_user, MIN, 10.0)
    assert reserved.hit_at == NOW + 40 * MIN
    assert reserved.hit_at < base.hit_at


def test_user_usage_counts():
    def user(start, end):
        return (end - start) / MIN * 0.5

    res = simulate(NOW, 50.0, NOW + 120 * MIN, 0, 1.0, user, MIN, 0.0)
    assert res.hit_at == NOW + 100 * MIN


def test_last_step_is_clipped_at_reset():
    res = simulate(NOW, 0.0, NOW + 7 * MIN, 1, 1.0, zero_user, 5 * MIN, 0.0)
    assert res.hit_at is None
    assert res.harness_pct == pytest.approx(7.0)


def test_already_past_reset():
    res = simulate(NOW, 99.0, NOW - MIN, 3, 1.0, zero_user, MIN, 0.0)
    assert res.hit_at is None and res.harness_pct == 0.0
