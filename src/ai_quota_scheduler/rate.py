"""Harness consumption rate r (% per minute per parallel worker)."""

from __future__ import annotations

__all__ = ["RateEstimator"]

EMA_ALPHA = 0.2


class RateEstimator:
    """EMA of measured %/worker-minute, starting from a conservative guess.

    Floored at ``r_init / 4`` so a run of zero-delta ticks (whole-percent
    rounding) cannot convince the controller the harness is free.
    """

    def __init__(self, r_init: float) -> None:
        if r_init <= 0:
            raise ValueError(f"r_init must be > 0: {r_init}")
        self.r = r_init
        self._floor = r_init / 4

    def observe(self, pct: float, worker_minutes: float) -> None:
        if worker_minutes <= 0:
            return
        sample = pct / worker_minutes
        self.r = max(self._floor, (1 - EMA_ALPHA) * self.r + EMA_ALPHA * sample)
