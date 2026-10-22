from datetime import UTC, datetime, timedelta

from ai_quota_scheduler.models import WindowState
from conftest import default_cfg

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def win(used: float, resets_at: datetime, name: str = "w", minutes: int = 300) -> WindowState:
    return WindowState(name, used, resets_at, minutes)


def zero_user(start, end):
    return 0.0


def const_user(total: float):
    def f(start, end):
        return total

    return f


def cfg(**kw):
    return default_cfg(**kw)
