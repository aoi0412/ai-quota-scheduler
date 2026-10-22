import pytest

from ai_quota_scheduler.attribution import TokenToPct, estimate_user_usage


def test_user_usage_is_total_minus_harness():
    conv = TokenToPct(coef=1e-5)
    assert estimate_user_usage(3.0, 100_000, conv) == pytest.approx(2.0)


def test_never_negative():
    assert estimate_user_usage(0.0, 100_000, TokenToPct(coef=1e-5)) == 0.0


def test_coef_learns_only_when_user_idle():
    conv = TokenToPct(coef=1e-5)
    conv.update(5.0, 100_000, user_idle=False)
    assert conv.coef == 1e-5


def test_coef_moves_toward_observation_with_ema():
    conv = TokenToPct(coef=1e-5)
    conv.update(2.0, 100_000, user_idle=True)  # sample 2e-5
    assert conv.coef == pytest.approx(0.9 * 1e-5 + 0.1 * 2e-5)


def test_zero_delta_rounding_does_not_diverge():  # Review Focus 2
    conv = TokenToPct(coef=1e-5)
    for _ in range(100):
        conv.update(0.0, 50_000, user_idle=True)
    assert 0 < conv.coef < 1e-3


def test_zero_delta_tokens_carry_to_next_step():
    conv = TokenToPct(coef=1e-5)
    conv.update(0.0, 50_000, user_idle=True)
    conv.update(1.0, 50_000, user_idle=True)  # 1% over 100k tokens → 1e-5
    assert conv.coef == pytest.approx(1e-5)


def test_negative_delta_window_reset_is_ignored():
    conv = TokenToPct(coef=1e-5)
    conv.update(-60.0, 100_000, user_idle=True)
    assert conv.coef == 1e-5


def test_delta_without_harness_tokens_is_ignored():
    conv = TokenToPct(coef=1e-5)
    conv.update(1.0, 0, user_idle=True)
    assert conv.coef == 1e-5


def test_pct_scales_tokens():
    assert TokenToPct(coef=2e-5).pct(50_000) == pytest.approx(1.0)


def test_coef_must_be_positive():
    with pytest.raises(ValueError):
        TokenToPct(coef=0.0)
