from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ai_quota_scheduler.forecast import UsageForecaster

TOKYO = ZoneInfo("Asia/Tokyo")
STEP = timedelta(minutes=5)
# Monday 2026-09-28 00:00 JST == Sunday 2026-09-27 15:00 UTC
START = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)


def history(weeks: int, pct_at_noon_jst: float = 1.0) -> list[tuple[datetime, float]]:
    out = []
    t = START
    while t < START + timedelta(weeks=weeks):
        pct = pct_at_noon_jst if t.astimezone(TOKYO).hour == 12 else 0.0
        out.append((t, pct))
        t += STEP
    return out


def test_upper_uses_local_hour_bucket():  # Review Focus 4
    f = UsageForecaster(TOKYO)
    f.fit(history(4))
    # Monday 12:00-13:00 JST == Monday 03:00-04:00 UTC
    noon = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
    # Monday 03:00-04:00 JST == Sunday 18:00-19:00 UTC
    night = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)
    assert f.upper(noon, noon + timedelta(hours=1), 0.95) == pytest.approx(12.0)
    assert f.upper(night, night + timedelta(hours=1), 0.95) == 0.0


def test_partial_interval_is_prorated():
    f = UsageForecaster(TOKYO)
    f.fit(history(4))
    noon = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
    assert f.upper(noon, noon + timedelta(minutes=15), 0.95) == pytest.approx(3.0)


def test_sparse_bucket_falls_back_to_global():
    f = UsageForecaster(TOKYO)
    # One week → every bucket has a single hourly total (< 3). Even local hours
    # use 1%/5min (12% per hour), odd hours nothing.
    samples = [
        (START + i * STEP, 1.0 if (START + i * STEP).astimezone(TOKYO).hour % 2 == 0 else 0.0)
        for i in range(12 * 24 * 7)
    ]
    f.fit(samples)
    # Monday 03:00 JST (odd, unused hour) still gets the global q0.95 = 12%.
    night = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)
    assert f.upper(night, night + timedelta(hours=1), 0.95) == pytest.approx(12.0)


def test_upper_monotone_in_q():
    f = UsageForecaster(TOKYO)
    samples = [(START + i * timedelta(hours=1), float(i % 7)) for i in range(24 * 7 * 4)]
    f.fit(samples)
    t = START + timedelta(weeks=5)
    assert f.upper(t, t + timedelta(days=1), 0.95) >= f.upper(t, t + timedelta(days=1), 0.90)


def test_upper_zero_length_interval_is_zero():
    f = UsageForecaster(TOKYO)
    f.fit(history(4))
    t = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
    assert f.upper(t, t, 0.95) == 0.0


def test_no_history_is_zero():
    t = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
    assert UsageForecaster(TOKYO).upper(t, t + timedelta(hours=1), 0.95) == 0.0


def test_naive_input_rejected():
    with pytest.raises(ValueError):
        UsageForecaster(TOKYO).upper(datetime(2026, 1, 1), datetime(2026, 1, 2), 0.95)
