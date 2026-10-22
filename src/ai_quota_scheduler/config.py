"""Scheduler configuration loaded from TOML."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["SchedulerConfig", "load_config"]

_STATE_DIR = Path("~/.local/state/ai-quota-scheduler").expanduser()
_CONFIG_DIR = Path("~/.config/ai-quota-scheduler").expanduser()

QUANTILE_MIN = 0.90
QUANTILE_MAX = 0.95


@dataclass(frozen=True)
class SchedulerConfig:
    """Tunables for the control loop. Defaults follow the design spec."""

    wait_limit_min: float = 15.0
    quantile: float = 0.95
    replan_interval_s: int = 300
    max_parallel: int = 1
    cold_start_days: int = 7
    stale_after_s: int = 900
    r_init_pct_per_min: float = 0.05
    rollout_max_min: int = 60
    db_path: Path = _STATE_DIR / "aqs.db"
    state_dir: Path = _STATE_DIR
    harness_config_dir: Path = _CONFIG_DIR / "claude"
    job_command: tuple[str, ...] = ("harness2",)
    adapters: tuple[str, ...] = ("claude_usage_api", "claude_statusline")
    usage_api_url: str = ""
    usage_api_token_file: str = ""
    usage_api_headers: tuple[str, ...] = ()
    codex_sessions_dir: Path = Path("~/.codex/sessions").expanduser()

    def __post_init__(self) -> None:
        if not QUANTILE_MIN <= self.quantile <= QUANTILE_MAX:
            raise ValueError(
                f"quantile must be within [{QUANTILE_MIN}, {QUANTILE_MAX}]: {self.quantile}"
            )
        if self.max_parallel < 0:
            raise ValueError(f"max_parallel must be >= 0: {self.max_parallel}")
        if self.wait_limit_min < 0:
            raise ValueError(f"wait_limit_min must be >= 0: {self.wait_limit_min}")
        if self.replan_interval_s <= 0:
            raise ValueError(f"replan_interval_s must be > 0: {self.replan_interval_s}")


def _coerce(name: str, value: Any, default: Any) -> Any:
    if isinstance(default, Path):
        return Path(str(value)).expanduser()
    if isinstance(default, tuple):
        if not isinstance(value, list):
            raise ValueError(f"{name} must be a list")
        return tuple(str(v) for v in value)
    if isinstance(default, float):
        return float(value)
    return value


def load_config(path: Path) -> SchedulerConfig:
    """Load ``path`` (TOML); keys left out keep their defaults."""
    with path.open("rb") as f:
        raw = tomllib.load(f)
    defaults = SchedulerConfig()
    known = {f.name for f in dataclasses.fields(SchedulerConfig)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    values = {k: _coerce(k, v, getattr(defaults, k)) for k, v in raw.items()}
    return SchedulerConfig(**values)
