from datetime import UTC, datetime, timedelta

import pytest

from ai_quota_scheduler.attribution import TokenToPct
from ai_quota_scheduler.daemon import DaemonContext, build_context, safe_tick, tick
from ai_quota_scheduler.forecast import UsageForecaster
from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY, QuotaSnapshot, WindowState
from ai_quota_scheduler.rate import RateEstimator
from ai_quota_scheduler.safety import COLD_START, NO_OBSERVATION, RATE_LIMITED
from ai_quota_scheduler.store import Store
from conftest import default_cfg

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)


def snap(at=NOW, five=10.0, five_reset=NOW + 10 * MIN, week=50.0, week_reset=NOW + timedelta(days=3)):
    return QuotaSnapshot(
        "claude",
        at,
        {
            FIVE_HOUR: WindowState(FIVE_HOUR, five, five_reset, 300),
            SEVEN_DAY: WindowState(SEVEN_DAY, week, week_reset, 10080),
        },
        "fake",
    )


class FakeAdapter:
    platform = "claude"

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = 0

    def read(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


class FakeRunner:
    def __init__(self, logs=()):
        self.applied = []
        self.logs = list(logs)

    def apply(self, parallel, now):
        self.applied.append(parallel)

    def reserve_pct(self, r):
        return 0.0

    def active_logs(self):
        return self.logs


def make_ctx(tmp_path, adapters, runner=None, **cfg_kw):
    cfg = default_cfg(tmp_path, **cfg_kw)
    from zoneinfo import ZoneInfo

    return DaemonContext(
        cfg=cfg,
        store=Store(cfg.db_path),
        adapters=adapters,
        forecaster=UsageForecaster(ZoneInfo("Asia/Tokyo")),
        conv=TokenToPct(coef=1e-5),
        rate=RateEstimator(cfg.r_init_pct_per_min),
        runner=runner or FakeRunner(),
        kill_file=tmp_path / "kill",
    )


def seed_old_snapshot(ctx, days=8):
    ctx.store.add_snapshot(snap(at=NOW - timedelta(days=days), five_reset=NOW - timedelta(days=days) + MIN))


def test_cold_start_records_but_parallel_zero(tmp_path):
    ctx = make_ctx(tmp_path, [FakeAdapter(snap(five=99.6))])
    d = tick(NOW, ctx)
    assert d.parallel == 0 and d.reason == COLD_START
    assert ctx.store.latest_snapshot("claude") is not None
    assert ctx.runner.applied == [0]


def test_after_cold_start_runs_one_when_reset_near(tmp_path):
    # 0.4% left, r_init 0.05%/min → 1 worker fills it in 8 min, 2 min before reset
    ctx = make_ctx(tmp_path, [FakeAdapter(snap(five=99.6))])
    seed_old_snapshot(ctx)
    d = tick(NOW, ctx)
    assert d.parallel == 1, d.reason
    assert ctx.runner.applied == [1]


def test_far_from_reset_stays_zero(tmp_path):
    ctx = make_ctx(tmp_path, [FakeAdapter(snap(five=10.0, five_reset=NOW + 300 * MIN))])
    seed_old_snapshot(ctx)
    assert tick(NOW, ctx).parallel == 0


def test_adapter_failure_falls_back_to_next_adapter(tmp_path):
    first = FakeAdapter(exc=RuntimeError("boom"))
    second = FakeAdapter(snap(five=99.6))
    ctx = make_ctx(tmp_path, [first, second])
    seed_old_snapshot(ctx)
    assert tick(NOW, ctx).parallel == 1
    assert first.calls == 1 and second.calls == 1


def test_first_non_none_adapter_wins(tmp_path):
    second = FakeAdapter(snap(five=1.0))
    ctx = make_ctx(tmp_path, [FakeAdapter(None), FakeAdapter(snap(five=99.6)), second])
    tick(NOW, ctx)
    assert second.calls == 0


def test_all_adapters_fail_parallel_zero(tmp_path):
    ctx = make_ctx(tmp_path, [FakeAdapter(None), FakeAdapter(exc=OSError())])
    seed_old_snapshot(ctx)
    d = tick(NOW, ctx)
    assert d.parallel == 0 and d.reason == NO_OBSERVATION


def test_exception_in_tick_applies_zero(tmp_path):
    ctx = make_ctx(tmp_path, [FakeAdapter(snap(five=99.6))])
    seed_old_snapshot(ctx)

    class Broken:
        def upper(self, *a):
            raise ZeroDivisionError

        def fit(self, *a):
            pass

    ctx.forecaster = Broken()
    d = safe_tick(NOW, ctx)
    assert d.parallel == 0
    assert d.reason.startswith("error")
    assert ctx.runner.applied[-1] == 0
    assert ctx.store.latest_decision().reason.startswith("error")


def test_decision_logged_every_tick(tmp_path):
    ctx = make_ctx(tmp_path, [FakeAdapter(snap(five=99.6))])
    for i in range(3):
        tick(NOW + i * MIN, ctx)
    rows = ctx.store._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert rows == 3


def test_429_in_job_log_stops_for_15_minutes(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("starting\n")
    runner = FakeRunner(logs=[log])
    ctx = make_ctx(tmp_path, [FakeAdapter(snap(five=99.6))], runner=runner)
    seed_old_snapshot(ctx)
    assert tick(NOW, ctx).parallel == 1
    with log.open("a") as f:
        f.write("Error: 429 Too Many Requests\n")
    assert tick(NOW + MIN, ctx).reason == RATE_LIMITED
    assert ctx.rate_limited_until == NOW + MIN + timedelta(minutes=15)
    # the same line is not counted twice
    assert tick(NOW + 17 * MIN, ctx).reason != RATE_LIMITED


def test_model_scoped_weekly_window_limits_budget(tmp_path):
    s = snap(five=99.6)
    windows = dict(s.windows)
    windows["seven_day_opus"] = WindowState("seven_day_opus", 100.0, NOW + timedelta(days=3), 10080)
    ctx = make_ctx(tmp_path, [FakeAdapter(QuotaSnapshot("claude", NOW, windows, "fake"))])
    seed_old_snapshot(ctx)
    d = tick(NOW, ctx)
    assert d.parallel == 0
    assert d.detail["block_budget_pct"] == 0.0


def test_learning_updates_user_usage_and_rate(tmp_path):
    ctx = make_ctx(tmp_path, [])
    seed_old_snapshot(ctx)
    ctx.adapters = [FakeAdapter(snap(at=NOW, five=99.0))]
    tick(NOW, ctx)
    ctx.store.add_harness_usage("j", NOW + MIN, 10_000)  # 0.1% at coef 1e-5
    ctx.adapters = [FakeAdapter(snap(at=NOW + 5 * MIN, five=99.5))]
    tick(NOW + 5 * MIN, ctx)
    samples = ctx.store.user_usage_between(NOW, NOW + 10 * MIN)
    assert len(samples) == 1
    assert samples[0][1] == pytest.approx(0.4)


def test_build_context_uses_config_adapters(tmp_path):
    ctx = build_context(default_cfg(tmp_path, adapters=("claude_statusline",)))
    assert [type(a).__name__ for a in ctx.adapters] == ["ClaudeStatuslineAdapter"]
