"""Lower level: 5h window → number of parallel harness workers."""

from __future__ import annotations

from datetime import datetime, timedelta

from ai_quota_scheduler.config import SchedulerConfig
from ai_quota_scheduler.models import WindowState
from ai_quota_scheduler.mpc.simulate import UserUpper, simulate

__all__ = ["SIM_STEP", "choose_parallel"]

SIM_STEP = timedelta(minutes=1)


def choose_parallel(
    now: datetime,
    five: WindowState,
    budget_pct: float,
    r: float,
    user_upper: UserUpper,
    cfg: SchedulerConfig,
    reserve_pct: float,
) -> tuple[int, str]:
    """Largest u in 0..max_parallel keeping the user's wait ≤ W within budget.

    Feasible u: the window either never fills before reset, or fills no more
    than ``cfg.wait_limit_min`` before it (the user may hit the limit but must
    not wait longer than W), and the harness spends at most ``budget_pct``.
    """
    wait_limit = timedelta(minutes=cfg.wait_limit_min)
    best = 0
    best_reason = "parallel=0: no feasible parallelism"
    for u in range(cfg.max_parallel + 1):
        sim = simulate(now, five.used_pct, five.resets_at, u, r, user_upper, SIM_STEP, reserve_pct)
        wait = five.resets_at - sim.hit_at if sim.hit_at is not None else timedelta(0)
        if wait <= wait_limit and sim.harness_pct <= budget_pct:
            best = u
            best_reason = (
                f"parallel={u}: wait {wait.total_seconds() / 60:.1f}min <= "
                f"{cfg.wait_limit_min:g}min, harness {sim.harness_pct:.2f}% <= "
                f"budget {budget_pct:.2f}%"
            )
    return best, best_reason
