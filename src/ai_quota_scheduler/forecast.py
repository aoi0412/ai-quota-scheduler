"""User-usage forecast: upper quantiles per local (weekday, hour).

Samples (one per control interval) are summed into hourly totals per local
calendar hour; each (weekday, hour) bucket keeps the distribution of those
totals across weeks. A bucket with fewer than ``MIN_BUCKET_SAMPLES`` totals
falls back to the distribution over all hours, which is dominated by busy
hours and so errs on the side of reserving more for the user.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ai_quota_scheduler.models import to_utc

__all__ = ["MIN_BUCKET_SAMPLES", "UsageForecaster", "quantile"]

MIN_BUCKET_SAMPLES = 3

Bucket = tuple[int, int]  # (local weekday, local hour)


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated empirical quantile of ``values`` (non-empty)."""
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


class UsageForecaster:
    def __init__(self, tz: ZoneInfo) -> None:
        self.tz = tz
        self._buckets: dict[Bucket, list[float]] = {}
        self._all: list[float] = []

    def _local_hour_start(self, t: datetime) -> datetime:
        local = t.astimezone(self.tz)
        return local.replace(minute=0, second=0, microsecond=0)

    def fit(self, samples: Sequence[tuple[datetime, float]]) -> None:
        """Replace the model with ``samples`` of (interval start, user %)."""
        hourly: dict[datetime, float] = defaultdict(float)
        for start, pct in samples:
            hourly[self._local_hour_start(to_utc(start, "sample start"))] += pct
        buckets: dict[Bucket, list[float]] = defaultdict(list)
        for hour_start, total in hourly.items():
            buckets[(hour_start.weekday(), hour_start.hour)].append(total)
        self._buckets = dict(buckets)
        self._all = list(hourly.values())

    def _hourly_upper(self, bucket: Bucket, q: float) -> float:
        values = self._buckets.get(bucket, [])
        if len(values) < MIN_BUCKET_SAMPLES:
            values = self._all
        return quantile(values, q) if values else 0.0

    def upper(self, start: datetime, end: datetime, q: float) -> float:
        """Upper-``q`` user usage (%) over ``[start, end)``, prorated by minute."""
        t = to_utc(start, "start")
        end = to_utc(end, "end")
        total = 0.0
        while t < end:
            local = t.astimezone(self.tz)
            hour_end = t + timedelta(minutes=60 - local.minute, seconds=-local.second,
                                     microseconds=-local.microsecond)
            piece_end = min(hour_end, end)
            fraction = (piece_end - t) / timedelta(hours=1)
            total += self._hourly_upper((local.weekday(), local.hour), q) * fraction
            t = piece_end
        return total
