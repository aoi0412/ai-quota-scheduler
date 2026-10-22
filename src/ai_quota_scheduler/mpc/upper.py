"""Upper level: weekly window → harness budget for the current 5h block."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from ai_quota_scheduler.models import WindowState
from ai_quota_scheduler.mpc.simulate import LIMIT_PCT, UserUpper

__all__ = ["BLOCK", "block_budget", "weekly_budget"]

BLOCK = timedelta(hours=5)


def weekly_budget(now: datetime, week: WindowState, user_upper: UserUpper) -> float:
    """% the harness may spend before the weekly reset.

    The user's upper-quantile forecast until the reset is a hard reservation;
    everything else is harness budget, aiming for ~0% left at the reset.
    """
    reserved = user_upper(now, week.resets_at) if week.resets_at > now else 0.0
    return max(0.0, LIMIT_PCT - week.used_pct - reserved)


def block_budget(
    now: datetime, week_budget_pct: float, week: WindowState, five: WindowState
) -> float:
    """Share of the weekly budget for the 5h block ending at ``five.resets_at``.

    Split evenly over this block and the blocks left before the weekly reset;
    unspent share flows back into the weekly budget at the next replan.
    """
    if week.resets_at <= five.resets_at:
        return week_budget_pct
    later_blocks = math.ceil((week.resets_at - five.resets_at) / BLOCK)
    return min(week_budget_pct, week_budget_pct / (1 + later_blocks))
