import pytest

from ai_quota_scheduler.rate import RateEstimator


def test_rate_ema_and_floor():
    est = RateEstimator(r_init=0.4)
    assert est.r == 0.4
    est.observe(10.0, 10.0)  # sample 1.0
    assert est.r == pytest.approx(0.8 * 0.4 + 0.2 * 1.0)
    for _ in range(200):
        est.observe(0.0, 10.0)
    assert est.r == pytest.approx(0.1)  # floor r_init / 4


def test_no_upper_cap():
    est = RateEstimator(r_init=0.1)
    for _ in range(100):
        est.observe(50.0, 1.0)
    assert est.r == pytest.approx(50.0, rel=1e-6)


def test_zero_worker_minutes_ignored():
    est = RateEstimator(r_init=0.4)
    est.observe(5.0, 0.0)
    assert est.r == 0.4


def test_r_init_must_be_positive():
    with pytest.raises(ValueError):
        RateEstimator(r_init=0.0)
