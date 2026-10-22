import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ai_quota_scheduler.adapters.codex_log import CodexLogAdapter, parse_codex_session

FIXTURE = Path(__file__).parent.parent / "fixtures" / "codex_session.jsonl"


def test_codex_last_rate_limits_wins():
    snap = parse_codex_session(FIXTURE)
    five = snap.windows["five_hour"]
    assert five.used_pct == 62.0
    assert five.window_minutes == 300
    assert five.resets_at == datetime.fromtimestamp(1792580676, UTC)
    assert snap.windows["seven_day"].used_pct == 22.0
    assert snap.observed_at == datetime(2026, 10, 21, 3, 15, tzinfo=UTC)
    assert snap.platform == "codex"


def test_session_without_rate_limits_is_none(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"type":"session_meta"}\nnot json\n')
    assert parse_codex_session(p) is None


def test_adapter_reads_newest_session(tmp_path):
    day = tmp_path / "2026" / "10" / "21"
    day.mkdir(parents=True)
    old = day / "rollout-old.jsonl"
    new = day / "rollout-new.jsonl"
    old.write_text('{"payload":{"rate_limits":{"primary":{"used_percent":1.0,'
                   '"window_minutes":300,"resets_at":1792570000}}}}\n')
    shutil.copy(FIXTURE, new)
    os.utime(old, (1000, 1000))
    assert CodexLogAdapter(tmp_path).read().windows["five_hour"].used_pct == 62.0


def test_adapter_empty_dir_is_none(tmp_path):
    assert CodexLogAdapter(tmp_path / "missing").read() is None
