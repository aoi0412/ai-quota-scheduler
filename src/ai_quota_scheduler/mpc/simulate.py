"""Forward simulation of one window under a constant parallelism."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["SimResult", "UserUpper", "simulate"]

UserUpper = Callable[[datetime, datetime], float]

LIMIT_PCT = 100.0


@dataclass(frozen=True)
class SimResult:
    hit_at: datetime | None  # when usage reaches 100%; None if it never does before reset
    harness_pct: float  # harness consumption until hit or reset
    final_used_pct: float


def simulate(
    now: datetime,
    used_pct: float,
    resets_at: datetime,
    parallel: int,
    r: float,
    user_upper: UserUpper,
    step: timedelta,
    reserve_pct: float,
) -> SimResult:
    """Step from ``now`` to ``resets_at`` adding forecast user use and harness use.

    ``reserve_pct`` (worst case of rollouts already running) is charged up
    front, so it eats into the harness's room rather than the user's.
    """
    used = used_pct + reserve_pct
    harness = 0.0
    t = now
    while t < resets_at:
        dt = min(step, resets_at - t)
        h = parallel * r * dt.total_seconds() / 60
        used += user_upper(t, t + dt) + h
        harness += h
        t += dt
        if used >= LIMIT_PCT:
            return SimResult(hit_at=t, harness_pct=harness, final_used_pct=used)
    return SimResult(hit_at=None, harness_pct=harness, final_used_pct=used)
