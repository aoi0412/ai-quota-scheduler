from helpers import MIN, NOW, cfg, win, zero_user

from ai_quota_scheduler.mpc.lower import choose_parallel


def test_lower_full_when_reset_near():
    # used 90%, reset in 20 min, r=1 per worker, no user → 1 worker hits in 10 min
    u, _ = choose_parallel(NOW, win(90, NOW + 20 * MIN), 100.0, 1.0, zero_user, cfg(max_parallel=2), 0.0)
    assert u == 2  # 2 workers: hit in 5 min → wait 15 ≤ 15 → accepted


def test_lower_zero_when_wait_exceeds_w():
    u, _ = choose_parallel(NOW, win(50, NOW + 300 * MIN), 100.0, 1.0, zero_user, cfg(max_parallel=1), 0.0)
    assert u == 0  # hit in 50 min → wait 250 > 15


def test_lower_respects_budget():
    u, _ = choose_parallel(NOW, win(10, NOW + 60 * MIN), 5.0, 1.0, zero_user, cfg(max_parallel=4), 0.0)
    assert u == 0  # even 1 worker uses 60% > budget 5%


def test_lower_picks_largest_feasible():
    # used 0, reset 30 min, budget 40%: u=1 → 30%, u=2 → 60% (> budget)
    u, reason = choose_parallel(NOW, win(0, NOW + 30 * MIN), 40.0, 1.0, zero_user, cfg(max_parallel=3), 0.0)
    assert u == 1
    assert "parallel=1" in reason


def test_reserve_counts_against_wait():
    # without reserve 1 worker hits at 10 min (wait 10); reserve 5% → hit at 5 min (wait 15)
    # reserve 8% → hit at 2 min (wait 18 > 15) → 0
    u, _ = choose_parallel(NOW, win(90, NOW + 20 * MIN), 100.0, 1.0, zero_user, cfg(max_parallel=1), 8.0)
    assert u == 0


def test_max_parallel_zero_means_zero():
    u, _ = choose_parallel(NOW, win(90, NOW + 20 * MIN), 100.0, 1.0, zero_user, cfg(max_parallel=0), 0.0)
    assert u == 0
