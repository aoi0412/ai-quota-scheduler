import pytest
from helpers import DAY, HOUR, NOW, const_user, win, zero_user

from ai_quota_scheduler.mpc.upper import block_budget, weekly_budget


def test_weekly_budget_reserves_user_upper():
    assert weekly_budget(NOW, win(60, NOW + DAY), const_user(30.0)) == pytest.approx(10.0)


def test_weekly_budget_never_negative():
    assert weekly_budget(NOW, win(90, NOW + DAY), const_user(30.0)) == 0.0


def test_week_nearly_exhausted_caps_block():  # Review Focus 5
    b = block_budget(
        NOW,
        weekly_budget(NOW, win(97, NOW + 2 * HOUR), const_user(2.0)),
        win(97, NOW + 2 * HOUR),
        win(10, NOW + 4 * HOUR),
    )
    assert b <= 1.0 + 1e-9


def test_block_budget_splits_over_remaining_blocks():
    # week resets in 25h, current 5h block ends in 5h → 1 + 4 more blocks
    week = win(0, NOW + 25 * HOUR, minutes=10080)
    five = win(0, NOW + 5 * HOUR)
    assert block_budget(NOW, 50.0, week, five) == pytest.approx(10.0)


def test_block_budget_rounds_partial_block_up():
    week = win(0, NOW + 7 * HOUR, minutes=10080)
    five = win(0, NOW + 5 * HOUR)  # 2h left after this block → one more block
    assert block_budget(NOW, 50.0, week, five) == pytest.approx(25.0)


def test_block_budget_all_when_week_ends_first():
    week = win(0, NOW + 3 * HOUR, minutes=10080)
    five = win(0, NOW + 5 * HOUR)
    assert block_budget(NOW, 12.0, week, five) == pytest.approx(12.0)


def test_weekly_budget_uses_horizon_until_reset():
    seen = []

    def user(start, end):
        seen.append((start, end))
        return 0.0

    weekly_budget(NOW, win(0, NOW + DAY), user)
    assert seen == [(NOW, NOW + DAY)]
    assert weekly_budget(NOW, win(0, NOW + DAY), zero_user) == 100.0
