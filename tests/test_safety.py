from datetime import UTC, datetime, timedelta

from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY, QuotaSnapshot, WindowState
from ai_quota_scheduler.safety import (
    COLD_START,
    KILL_SWITCH,
    NO_OBSERVATION,
    RATE_LIMITED,
    STALE,
    WINDOW_EXPIRED,
    gate,
)
from conftest import default_cfg

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=30)


def snap(observed_at=NOW, five_reset=NOW + timedelta(hours=2), week_reset=NOW + timedelta(days=3)):
    return QuotaSnapshot(
        "claude",
        observed_at,
        {
            FIVE_HOUR: WindowState(FIVE_HOUR, 10, five_reset, 300),
            SEVEN_DAY: WindowState(SEVEN_DAY, 10, week_reset, 10080),
        },
        "test",
    )


def run(tmp_path, s=None, first_seen=LONG_AGO, kill=False, rl=None, **cfg_kw):
    kill_file = tmp_path / "kill"
    if kill:
        kill_file.touch()
    return gate(NOW, s, default_cfg(tmp_path, **cfg_kw), first_seen, kill_file, rl)


def test_all_clear(tmp_path):
    assert run(tmp_path, snap()) is None


def test_no_observation(tmp_path):
    assert run(tmp_path, None) == NO_OBSERVATION


def test_missing_required_window_is_no_observation(tmp_path):
    s = QuotaSnapshot("claude", NOW, {FIVE_HOUR: snap().windows[FIVE_HOUR]}, "test")
    assert run(tmp_path, s) == NO_OBSERVATION


def test_stale(tmp_path):
    assert run(tmp_path, snap(observed_at=NOW - timedelta(seconds=901))) == STALE
    assert run(tmp_path, snap(observed_at=NOW - timedelta(seconds=900))) is None


def test_window_expired_before_new_observation(tmp_path):  # Review Focus 1
    assert run(tmp_path, snap(five_reset=NOW - timedelta(seconds=1))) == WINDOW_EXPIRED
    assert run(tmp_path, snap(week_reset=NOW)) == WINDOW_EXPIRED


def test_cold_start(tmp_path):
    assert run(tmp_path, snap(), first_seen=NOW - timedelta(days=6, hours=23)) == COLD_START
    assert run(tmp_path, snap(), first_seen=None) == COLD_START
    assert run(tmp_path, snap(), first_seen=NOW - timedelta(days=7)) is None


def test_kill_switch(tmp_path):
    assert run(tmp_path, None, kill=True) == KILL_SWITCH


def test_rate_limited(tmp_path):
    assert run(tmp_path, snap(), rl=NOW + timedelta(minutes=1)) == RATE_LIMITED
    assert run(tmp_path, snap(), rl=NOW) is None


def test_order_kill_before_everything(tmp_path):
    assert run(tmp_path, snap(observed_at=NOW - timedelta(days=1)), kill=True,
               rl=NOW + timedelta(hours=1), first_seen=None) == KILL_SWITCH


def test_order_rate_limited_before_cold_start(tmp_path):
    assert run(tmp_path, snap(), rl=NOW + timedelta(hours=1), first_seen=None) == RATE_LIMITED
