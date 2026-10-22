import dataclasses
from pathlib import Path

import pytest

from ai_quota_scheduler.config import SchedulerConfig, load_config
from conftest import write_toml


def test_defaults_match_spec(tmp_path):
    cfg = load_config(write_toml(tmp_path, {}))
    assert cfg.wait_limit_min == 15.0
    assert cfg.quantile == 0.95
    assert cfg.replan_interval_s == 300
    assert cfg.cold_start_days == 7
    assert cfg.max_parallel == 1


def test_config_is_frozen(tmp_path):
    cfg = load_config(write_toml(tmp_path, {}))
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.wait_limit_min = 30


def test_quantile_out_of_range_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config(write_toml(tmp_path, {"quantile": 0.5}))


def test_values_override_defaults(tmp_path):
    cfg = load_config(
        write_toml(
            tmp_path,
            {
                "wait_limit_min": 20,
                "db_path": str(tmp_path / "x.db"),
                "job_command": ["harness2", "--bench", "jb"],
                "adapters": ["claude_statusline"],
            },
        )
    )
    assert cfg.wait_limit_min == 20.0
    assert cfg.db_path == tmp_path / "x.db"
    assert cfg.job_command == ("harness2", "--bench", "jb")
    assert cfg.adapters == ("claude_statusline",)


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_config(write_toml(tmp_path, {"no_such_key": 1}))


def test_paths_expand_user(tmp_path):
    cfg = load_config(write_toml(tmp_path, {"state_dir": "~/aqs"}))
    assert cfg.state_dir == Path("~/aqs").expanduser()


def test_default_instance_is_valid():
    assert SchedulerConfig().quantile == 0.95
