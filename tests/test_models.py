from datetime import UTC, datetime, timedelta, timezone

import pytest

from ai_quota_scheduler.models import (
    FIVE_HOUR,
    SEVEN_DAY,
    Decision,
    QuotaSnapshot,
    WindowState,
)


def test_window_requires_aware_utc():
    with pytest.raises(ValueError):
        WindowState("five_hour", 10.0, datetime(2026, 1, 1), 300)


def test_window_normalises_offset_to_utc():
    jst = timezone(timedelta(hours=9))
    w = WindowState(FIVE_HOUR, 10.0, datetime(2026, 1, 1, 9, tzinfo=jst), 300)
    assert w.resets_at == datetime(2026, 1, 1, 0, tzinfo=UTC)
    assert w.resets_at.tzinfo is UTC


def test_snapshot_requires_aware_observed_at():
    with pytest.raises(ValueError):
        QuotaSnapshot("claude", datetime(2026, 1, 1), {}, "test")


def test_snapshot_windows_are_read_only():
    w = WindowState(SEVEN_DAY, 1.0, datetime(2026, 1, 1, tzinfo=UTC), 10080)
    snap = QuotaSnapshot("claude", datetime(2026, 1, 1, tzinfo=UTC), {SEVEN_DAY: w}, "test")
    with pytest.raises(TypeError):
        snap.windows["x"] = w  # type: ignore[index]


def test_decision_requires_aware_time():
    with pytest.raises(ValueError):
        Decision(datetime(2026, 1, 1), 0, "x", {})
