import io
import json
from datetime import UTC, datetime, timedelta

from ai_quota_scheduler.cli import main
from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY, Decision, QuotaSnapshot, WindowState
from ai_quota_scheduler.store import Store
from conftest import write_toml

NOW = datetime.now(UTC)


def config(tmp_path):
    return write_toml(tmp_path, {"state_dir": str(tmp_path), "db_path": str(tmp_path / "aqs.db")})


def run(capsys, *argv):
    rc = main(list(argv))
    return rc, capsys.readouterr().out


def test_status_json_keys(tmp_path, capsys):
    cfg = config(tmp_path)
    store = Store(tmp_path / "aqs.db")
    store.add_snapshot(
        QuotaSnapshot(
            "claude",
            NOW,
            {
                FIVE_HOUR: WindowState(FIVE_HOUR, 62, NOW + timedelta(hours=2), 300),
                SEVEN_DAY: WindowState(SEVEN_DAY, 22, NOW + timedelta(days=3), 10080),
            },
            "test",
        )
    )
    store.add_decision(Decision(NOW, 0, "cold_start", {"token_coef": 1e-5}))
    store.add_harness_usage("j1", NOW - timedelta(hours=1), 100_000)
    rc, out = run(capsys, "status", "--json", "--config", str(cfg))
    data = json.loads(out)
    assert rc == 0
    assert set(data) >= {"windows", "decision", "jobs", "harness_week_pct"}
    assert data["windows"]["five_hour"]["used_pct"] == 62
    assert data["decision"]["reason"] == "cold_start"
    assert data["harness_week_pct"] == 1.0


def test_status_text_without_data(tmp_path, capsys):
    rc, out = run(capsys, "status", "--config", str(config(tmp_path)))
    assert rc == 0
    assert "no observation" in out


def test_queue_add_then_list(tmp_path, capsys):
    cfg = str(config(tmp_path))
    rc, out = run(capsys, "queue", "add", "--config", cfg, "--", "--bench", "jb", "--k", "1")
    assert rc == 0
    job_id = out.strip()
    rc, out = run(capsys, "queue", "list", "--config", cfg)
    assert job_id in out
    assert "--bench jb --k 1" in out
    assert "queued" in out


def test_kill_creates_file(tmp_path, capsys):
    cfg = str(config(tmp_path))
    assert run(capsys, "kill", "--config", cfg)[0] == 0
    assert (tmp_path / "KILL").exists()
    assert run(capsys, "unkill", "--config", cfg)[0] == 0
    assert not (tmp_path / "KILL").exists()


def test_statusline_hook_cli_writes_snapshot(tmp_path, capsys, monkeypatch):
    payload = {
        "rate_limits": {
            "five_hour": {"used_percentage": 62, "resets_at": 1792580676},
            "seven_day": {"used_percentage": 22, "resets_at": 1793090602},
        }
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out_path = tmp_path / "snap.json"
    rc, out = run(capsys, "statusline-hook", "--out", str(out_path))
    assert rc == 0
    assert json.loads(out_path.read_text())["five_hour"]["used_pct"] == 62
    assert out.strip() == "5h 62% · 7d 22%"


def test_missing_config_uses_defaults(tmp_path):
    from ai_quota_scheduler.cli import load_cli_config
    from ai_quota_scheduler.config import SchedulerConfig

    assert load_cli_config(tmp_path / "nope.toml") == SchedulerConfig()
