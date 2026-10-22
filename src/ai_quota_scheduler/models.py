"""Quota observation and control-decision value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

__all__ = ["FIVE_HOUR", "SEVEN_DAY", "Decision", "QuotaSnapshot", "WindowState", "to_utc"]

FIVE_HOUR = "five_hour"
SEVEN_DAY = "seven_day"


def to_utc(value: datetime, field: str) -> datetime:
    """Return ``value`` converted to UTC; reject naive datetimes."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware: {value!r}")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class WindowState:
    """One rate-limit window: usage percentage and when it resets."""

    name: str
    used_pct: float
    resets_at: datetime
    window_minutes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "resets_at", to_utc(self.resets_at, "resets_at"))
        object.__setattr__(self, "used_pct", float(self.used_pct))


@dataclass(frozen=True)
class QuotaSnapshot:
    """All windows of one platform observed at one moment."""

    platform: str
    observed_at: datetime
    windows: Mapping[str, WindowState]
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", to_utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "windows", MappingProxyType(dict(self.windows)))


@dataclass(frozen=True)
class Decision:
    """Parallelism chosen in one control tick, with its reasoning."""

    at: datetime
    parallel: int
    reason: str
    detail: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", to_utc(self.at, "at"))
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))
