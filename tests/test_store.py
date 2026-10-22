from datetime import UTC, datetime, timedelta

from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY, Decision, QuotaSnapshot, WindowState
from ai_quota_scheduler.store import Store

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def snap(at: datetime, five: float = 10.0, platform: str = "claude") -> QuotaSnapshot:
    return QuotaSnapshot(
        platform,
        at,
        {
            FIVE_HOUR: WindowState(FIVE_HOUR, five, at + timedelta(hours=3), 300),
            SEVEN_DAY: WindowState(SEVEN_DAY, 22.5, at + timedelta(days=4), 10080),
        },
        "test",
    )


def test_roundtrip_snapshot(tmp_path):
    store = Store(tmp_path / "aqs.db")
    s = snap(T0)
    store.add_snapshot(s)
    got = store.latest_snapshot("claude")
    assert got == s
    assert got.observed_at.tzinfo is UTC
    assert got.windows[FIVE_HOUR].resets_at.tzinfo is UTC


def test_latest_snapshot_per_platform(tmp_path):
    store = Store(tmp_path / "aqs.db")
    store.add_snapshot(snap(T0, 1.0))
    store.add_snapshot(snap(T0 + timedelta(minutes=5), 2.0))
    store.add_snapshot(snap(T0 + timedelta(minutes=10), 9.0, platform="codex"))
    assert store.latest_snapshot("claude").windows[FIVE_HOUR].used_pct == 2.0
    assert store.latest_snapshot("codex").windows[FIVE_HOUR].used_pct == 9.0
    assert store.latest_snapshot("gemini") is None


def test_snapshots_between(tmp_path):
    store = Store(tmp_path / "aqs.db")
    for i in range(4):
        store.add_snapshot(snap(T0 + timedelta(minutes=5 * i), float(i)))
    got = store.snapshots_between("claude", T0 + timedelta(minutes=5), T0 + timedelta(minutes=15))
    assert [s.windows[FIVE_HOUR].used_pct for s in got] == [1.0, 2.0]


def test_harness_tokens_sum_range(tmp_path):
    store = Store(tmp_path / "aqs.db")
    store.add_harness_usage("j1", T0, 100)
    store.add_harness_usage("j1", T0 + timedelta(minutes=1), 20)
    store.add_harness_usage("j2", T0 + timedelta(minutes=2), 3)
    assert store.harness_tokens_between(T0, T0 + timedelta(minutes=2)) == 120
    assert store.harness_tokens_between(T0 + timedelta(minutes=3), T0 + timedelta(minutes=4)) == 0


def test_first_snapshot_at_empty_is_none(tmp_path):
    store = Store(tmp_path / "aqs.db")
    assert store.first_snapshot_at() is None
    store.add_snapshot(snap(T0 + timedelta(minutes=5)))
    store.add_snapshot(snap(T0))
    assert store.first_snapshot_at() == T0


def test_add_decision_persists(tmp_path):
    store = Store(tmp_path / "aqs.db")
    d = Decision(T0, 1, "ok", {"budget": 3.5})
    store.add_decision(d)
    assert store.latest_decision() == d


def test_reopen_keeps_data(tmp_path):
    Store(tmp_path / "aqs.db").add_snapshot(snap(T0))
    assert Store(tmp_path / "aqs.db").latest_snapshot("claude") is not None
