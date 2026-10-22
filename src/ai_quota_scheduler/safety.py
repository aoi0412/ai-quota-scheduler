"""Stop gate: any doubt about the quota state forces parallelism to 0."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from ai_quota_scheduler.config import SchedulerConfig
from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY, QuotaSnapshot

__all__ = [
    "COLD_START",
    "KILL_SWITCH",
    "NO_OBSERVATION",
    "RATE_LIMITED",
    "REQUIRED_WINDOWS",
    "STALE",
    "WINDOW_EXPIRED",
    "gate",
]

KILL_SWITCH = "kill_switch"
NO_OBSERVATION = "no_observation"
STALE = "stale_observation"
WINDOW_EXPIRED = "window_expired"
RATE_LIMITED = "rate_limited"
COLD_START = "cold_start"

REQUIRED_WINDOWS = (FIVE_HOUR, SEVEN_DAY)


def gate(
    now: datetime,
    snap: QuotaSnapshot | None,
    cfg: SchedulerConfig,
    first_seen: datetime | None,
    kill_file: Path,
    rate_limited_until: datetime | None,
) -> str | None:
    """Reason to stop the harness, or ``None`` when it may run.

    A window whose ``resets_at`` has passed means the observation predates the
    reset: its usage is unknown, not 0, so that also stops the harness.
    """
    if kill_file.exists():
        return KILL_SWITCH
    if snap is None or any(name not in snap.windows for name in REQUIRED_WINDOWS):
        return NO_OBSERVATION
    if now - snap.observed_at > timedelta(seconds=cfg.stale_after_s):
        return STALE
    if any(w.resets_at <= now for w in snap.windows.values()):
        return WINDOW_EXPIRED
    if rate_limited_until is not None and rate_limited_until > now:
        return RATE_LIMITED
    if first_seen is None or now - first_seen < timedelta(days=cfg.cold_start_days):
        return COLD_START
    return None
