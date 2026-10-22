from pathlib import Path
from typing import Any

import pytest


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_toml(dir_: Path, values: dict[str, Any]) -> Path:
    path = dir_ / "config.toml"
    path.write_text("".join(f"{k} = {_toml_value(v)}\n" for k, v in values.items()))
    return path


@pytest.fixture
def toml_writer():
    return write_toml


def default_cfg(tmp: Path | None = None, **overrides: Any):
    from ai_quota_scheduler.config import SchedulerConfig

    if tmp is not None:
        overrides.setdefault("state_dir", tmp)
        overrides.setdefault("db_path", tmp / "aqs.db")
        overrides.setdefault("harness_config_dir", tmp / "claude")
    return SchedulerConfig(**overrides)
