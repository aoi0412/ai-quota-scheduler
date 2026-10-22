import json
import os
from datetime import UTC, datetime

from ai_quota_scheduler.adapters import create_adapter
from ai_quota_scheduler.adapters.claude_statusline import ClaudeStatuslineAdapter, statusline_hook
from conftest import default_cfg

SAMPLE = {
    "rate_limits": {
        "five_hour": {"used_percentage": 62, "resets_at": 1792580676},
        "seven_day": {"used_percentage": 22, "resets_at": 1793090602},
    }
}


def test_hook_writes_snapshot(tmp_path):
    out = tmp_path / "snap.json"
    statusline_hook(json.dumps(SAMPLE), out)
    snap = ClaudeStatuslineAdapter(out).read()
    assert snap.windows["five_hour"].used_pct == 62
    assert snap.windows["five_hour"].window_minutes == 300
    assert snap.windows["seven_day"].resets_at == datetime.fromtimestamp(1793090602, UTC)
    assert snap.windows["seven_day"].window_minutes == 10080
    assert snap.platform == "claude"


def test_hook_without_rate_limits_keeps_previous(tmp_path):
    out = tmp_path / "snap.json"
    statusline_hook(json.dumps(SAMPLE), out)
    statusline_hook(json.dumps({"model": {}}), out)
    assert ClaudeStatuslineAdapter(out).read().windows["five_hour"].used_pct == 62


def test_hook_ignores_invalid_json(tmp_path):
    out = tmp_path / "snap.json"
    statusline_hook("not json", out)
    assert not out.exists()


def test_observed_at_is_file_mtime(tmp_path):
    out = tmp_path / "snap.json"
    statusline_hook(json.dumps(SAMPLE), out)
    os.utime(out, (1792570000, 1792570000))
    assert ClaudeStatuslineAdapter(out).read().observed_at == datetime.fromtimestamp(1792570000, UTC)


def test_missing_or_corrupt_file_reads_none(tmp_path):
    out = tmp_path / "snap.json"
    assert ClaudeStatuslineAdapter(out).read() is None
    out.write_text("{broken")
    assert ClaudeStatuslineAdapter(out).read() is None


def test_created_from_registry_uses_state_dir(tmp_path):
    statusline_hook(json.dumps(SAMPLE), tmp_path / "claude_statusline.json")
    adapter = create_adapter("claude_statusline", default_cfg(tmp_path))
    assert adapter.read().windows["five_hour"].used_pct == 62
