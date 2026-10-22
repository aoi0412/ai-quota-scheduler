import json
from datetime import UTC, datetime

from ai_quota_scheduler.adapters import create_adapter
from ai_quota_scheduler.adapters.claude_usage_api import (
    ClaudeUsageApiAdapter,
    load_token,
    parse_usage_response,
)
from conftest import default_cfg

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def test_parse_top_level_windows_iso_and_epoch():
    body = {
        "five_hour": {"utilization": 62.0, "resets_at": "2026-10-21T14:00:00+00:00"},
        "seven_day": {"used_percentage": 22, "resets_at": 1793090602},
    }
    snap = parse_usage_response(body, NOW)
    assert snap.windows["five_hour"].used_pct == 62.0
    assert snap.windows["five_hour"].resets_at == datetime(2026, 10, 21, 14, tzinfo=UTC)
    assert snap.windows["five_hour"].window_minutes == 300
    assert snap.windows["seven_day"].resets_at == datetime.fromtimestamp(1793090602, UTC)
    assert snap.observed_at == NOW


def test_parse_includes_model_scoped_limits():
    body = {
        "five_hour": {"utilization": 1.0, "resets_at": 1792580676},
        "seven_day": {"utilization": 2.0, "resets_at": 1793090602},
        "seven_day_opus": {"utilization": 40.0, "resets_at": 1793090602},
        "limits": [
            {"name": "seven_day_sonnet", "utilization": 7.5, "resets_at": 1793090602,
             "window_minutes": 10080},
        ],
    }
    snap = parse_usage_response(body, NOW)
    assert snap.windows["seven_day_opus"].used_pct == 40.0
    assert snap.windows["seven_day_opus"].window_minutes == 10080
    assert snap.windows["seven_day_sonnet"].used_pct == 7.5


def test_parse_skips_null_windows():
    body = {"five_hour": {"utilization": 1.0, "resets_at": 1792580676}, "seven_day_opus": None}
    assert set(parse_usage_response(body, NOW).windows) == {"five_hour"}


def test_fetch_error_returns_none(tmp_path):
    def boom(url):
        raise OSError("network down")

    cfg = default_cfg(tmp_path, usage_api_url="https://example.invalid/usage")
    assert ClaudeUsageApiAdapter(cfg, fetch=boom).read() is None


def test_unparseable_body_returns_none(tmp_path):
    cfg = default_cfg(tmp_path, usage_api_url="https://example.invalid/usage")
    assert ClaudeUsageApiAdapter(cfg, fetch=lambda url: {"unexpected": 1}).read() is None


def test_empty_url_returns_none(tmp_path):
    called = []
    adapter = ClaudeUsageApiAdapter(default_cfg(tmp_path), fetch=lambda url: called.append(url))
    assert adapter.read() is None
    assert called == []


def test_fetch_receives_configured_url(tmp_path):
    seen = []

    def fetch(url):
        seen.append(url)
        return {"five_hour": {"utilization": 3.0, "resets_at": 1792580676}}

    cfg = default_cfg(tmp_path, usage_api_url="https://example.invalid/usage")
    assert ClaudeUsageApiAdapter(cfg, fetch=fetch).read().windows["five_hour"].used_pct == 3.0
    assert seen == ["https://example.invalid/usage"]


def test_load_token_from_credentials_json_or_plain(tmp_path):
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-a"}}))
    plain = tmp_path / "token.txt"
    plain.write_text("tok-b\n")
    assert load_token(creds) == "tok-a"
    assert load_token(plain) == "tok-b"


def test_registered(tmp_path):
    assert create_adapter("claude_usage_api", default_cfg(tmp_path)).read() is None
