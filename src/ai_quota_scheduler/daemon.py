"""Control loop: observe → learn → gate → plan → act → record, every interval."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from ai_quota_scheduler.adapters import QuotaAdapter, create_adapter
from ai_quota_scheduler.attribution import TokenToPct, estimate_user_usage
from ai_quota_scheduler.config import SchedulerConfig
from ai_quota_scheduler.forecast import UsageForecaster
from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY, Decision, QuotaSnapshot
from ai_quota_scheduler.mpc.lower import choose_parallel
from ai_quota_scheduler.mpc.upper import block_budget, weekly_budget
from ai_quota_scheduler.rate import RateEstimator
from ai_quota_scheduler.runner import Runner
from ai_quota_scheduler.safety import gate
from ai_quota_scheduler.store import Store

__all__ = [
    "KILL_FILENAME",
    "DaemonContext",
    "build_context",
    "run_forever",
    "safe_tick",
    "tick",
]

log = logging.getLogger(__name__)

KILL_FILENAME = "KILL"
RATE_LIMIT_PAUSE = timedelta(minutes=15)
RATE_LIMIT_RE = re.compile(rb"\b429\b|rate_limit", re.IGNORECASE)
FORECAST_HISTORY = timedelta(weeks=8)
IDLE_FORECAST_PCT = 0.1  # forecast user use below this counts as "user idle"
INITIAL_COEF = 1e-5  # %/token before any learning


class RunnerLike(Protocol):
    def apply(self, parallel: int, now: datetime) -> None: ...

    def reserve_pct(self, r: float) -> float: ...

    def active_logs(self) -> list[Path]: ...


class ForecasterLike(Protocol):
    def fit(self, samples: Sequence[tuple[datetime, float]]) -> None: ...

    def upper(self, start: datetime, end: datetime, q: float) -> float: ...


@dataclass
class DaemonContext:
    cfg: SchedulerConfig
    store: Store
    adapters: Sequence[QuotaAdapter]
    forecaster: ForecasterLike
    conv: TokenToPct
    rate: RateEstimator
    runner: RunnerLike
    kill_file: Path
    rate_limited_until: datetime | None = None
    log_offsets: dict[Path, int] = field(default_factory=dict)


def build_context(cfg: SchedulerConfig) -> DaemonContext:
    store = Store(cfg.db_path)
    runner = Runner(cfg, store)
    runner.reconcile()
    return DaemonContext(
        cfg=cfg,
        store=store,
        adapters=[create_adapter(name, cfg) for name in cfg.adapters],
        forecaster=UsageForecaster(ZoneInfo(cfg.timezone)),
        conv=TokenToPct(coef=INITIAL_COEF),
        rate=RateEstimator(cfg.r_init_pct_per_min),
        runner=runner,
        kill_file=cfg.state_dir / KILL_FILENAME,
    )


def _observe(ctx: DaemonContext) -> QuotaSnapshot | None:
    for adapter in ctx.adapters:
        try:
            snap = adapter.read()
        except Exception:  # noqa: BLE001 - a broken adapter just means "try the next"
            log.exception("adapter %s failed", type(adapter).__name__)
            continue
        if snap is not None:
            return snap
    return None


def _scan_rate_limits(now: datetime, ctx: DaemonContext) -> None:
    """Pause for RATE_LIMIT_PAUSE when a running job logged a 429 since last scan."""
    for path in ctx.runner.active_logs():
        try:
            with path.open("rb") as f:
                f.seek(ctx.log_offsets.get(path, 0))
                chunk = f.read()
                ctx.log_offsets[path] = f.tell()
        except OSError:
            continue
        if RATE_LIMIT_RE.search(chunk):
            log.warning("rate limit seen in %s; pausing harness", path)
            ctx.rate_limited_until = now + RATE_LIMIT_PAUSE


def _learn(prev: QuotaSnapshot | None, snap: QuotaSnapshot, ctx: DaemonContext) -> None:
    """Update user-usage history, token→% coefficient and harness rate."""
    if prev is None or FIVE_HOUR not in prev.windows or FIVE_HOUR not in snap.windows:
        return
    before, after = prev.windows[FIVE_HOUR], snap.windows[FIVE_HOUR]
    if before.resets_at != after.resets_at or snap.observed_at <= prev.observed_at:
        return  # window reset in between: the delta is meaningless
    delta = after.used_pct - before.used_pct
    tokens = ctx.store.harness_tokens_between(prev.observed_at, snap.observed_at)
    user = estimate_user_usage(delta, tokens, ctx.conv)
    ctx.store.add_user_usage(prev.observed_at, user)
    idle = (
        ctx.forecaster.upper(prev.observed_at, snap.observed_at, ctx.cfg.quantile)
        < IDLE_FORECAST_PCT
    )
    ctx.conv.update(delta, tokens, user_idle=idle)
    last = ctx.store.latest_decision()
    if last is not None and last.parallel > 0:
        minutes = (snap.observed_at - prev.observed_at).total_seconds() / 60
        ctx.rate.observe(ctx.conv.pct(tokens), last.parallel * minutes)


def _plan(now: datetime, snap: QuotaSnapshot, ctx: DaemonContext) -> tuple[int, str, dict[str, float]]:
    cfg = ctx.cfg

    def user_upper(start: datetime, end: datetime) -> float:
        return ctx.forecaster.upper(start, end, cfg.quantile)

    five = snap.windows[FIVE_HOUR]
    # Every non-5h window (seven_day plus model-scoped weekly limits) is a
    # weekly constraint; the tightest one sets the block budget.
    budgets = [
        block_budget(now, weekly_budget(now, w, user_upper), w, five)
        for name, w in snap.windows.items()
        if name != FIVE_HOUR
    ]
    budget = min(budgets)
    reserve = ctx.runner.reserve_pct(ctx.rate.r)
    parallel, reason = choose_parallel(now, five, budget, ctx.rate.r, user_upper, cfg, reserve)
    detail = {
        "five_hour_used_pct": five.used_pct,
        "seven_day_used_pct": snap.windows[SEVEN_DAY].used_pct,
        "block_budget_pct": budget,
        "reserve_pct": reserve,
        "r_pct_per_min": ctx.rate.r,
        "token_coef": ctx.conv.coef,
        "user_upper_to_5h_reset_pct": user_upper(now, five.resets_at),
    }
    return parallel, reason, detail


def tick(now: datetime, ctx: DaemonContext) -> Decision:
    prev = ctx.store.latest_snapshot("claude")
    snap = _observe(ctx)
    if snap is not None:
        ctx.store.add_snapshot(snap)
        _learn(prev, snap, ctx)
        ctx.forecaster.fit(ctx.store.user_usage_between(now - FORECAST_HISTORY, now))
    _scan_rate_limits(now, ctx)
    stop = gate(now, snap, ctx.cfg, ctx.store.first_snapshot_at(), ctx.kill_file, ctx.rate_limited_until)
    if stop is not None or snap is None:
        parallel, reason, detail = 0, stop or "no_observation", {}
    else:
        parallel, reason, detail = _plan(now, snap, ctx)
    ctx.runner.apply(parallel, now)
    decision = Decision(now, parallel, reason, detail)
    ctx.store.add_decision(decision)
    log.info("decision parallel=%d reason=%s", parallel, reason)
    return decision


def safe_tick(now: datetime, ctx: DaemonContext) -> Decision:
    """``tick`` that turns any failure into parallel=0."""
    try:
        return tick(now, ctx)
    except Exception as exc:  # noqa: BLE001 - the loop must survive and fail safe
        log.exception("tick failed; stopping harness")
        decision = Decision(now, 0, f"error: {type(exc).__name__}", {})
        try:
            ctx.runner.apply(0, now)
        finally:
            ctx.store.add_decision(decision)
        return decision


def run_forever(ctx: DaemonContext) -> None:
    while True:
        safe_tick(datetime.now(UTC), ctx)
        time.sleep(ctx.cfg.replan_interval_s)
