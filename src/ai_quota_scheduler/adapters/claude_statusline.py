"""Claude Code statusline JSON → snapshot file → QuotaSnapshot.

Claude Code pipes a JSON document to the statusline command on every
refresh. Its ``rate_limits.five_hour`` / ``rate_limits.seven_day`` entries
carry ``used_percentage`` and ``resets_at`` (epoch seconds). The hook stores
them in a small JSON file; the adapter reads that file and uses its mtime as
the observation time.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from ai_quota_scheduler.adapters import register_adapter
from ai_quota_scheduler.config import SchedulerConfig
from ai_quota_scheduler.fileio import atomic_write_json
from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY, QuotaSnapshot, WindowState

__all__ = ["SNAPSHOT_FILENAME", "ClaudeStatuslineAdapter", "statusline_hook"]

log = logging.getLogger(__name__)

SNAPSHOT_FILENAME = "claude_statusline.json"
WINDOW_MINUTES = {FIVE_HOUR: 300, SEVEN_DAY: 10080}


def statusline_hook(stdin_json: str, out_path: Path) -> None:
    """Persist the rate-limit windows of one statusline payload."""
    try:
        payload = json.loads(stdin_json)
    except json.JSONDecodeError:
        log.warning("statusline payload is not JSON")
        return
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return
    windows: dict[str, dict[str, float]] = {}
    for name in WINDOW_MINUTES:
        w = limits.get(name)
        if isinstance(w, dict) and "used_percentage" in w and "resets_at" in w:
            windows[name] = {
                "used_pct": float(w["used_percentage"]),
                "resets_at": float(w["resets_at"]),
            }
    if windows:
        atomic_write_json(out_path, windows)


@register_adapter("claude_statusline")
class ClaudeStatuslineAdapter:
    platform = "claude"

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_config(cls, cfg: SchedulerConfig) -> Self:
        return cls(cfg.state_dir / SNAPSHOT_FILENAME)

    def read(self) -> QuotaSnapshot | None:
        try:
            mtime = self.path.stat().st_mtime
            raw = json.loads(self.path.read_text())
            windows = {
                name: WindowState(
                    name,
                    w["used_pct"],
                    datetime.fromtimestamp(w["resets_at"], UTC),
                    WINDOW_MINUTES[name],
                )
                for name, w in raw.items()
                if name in WINDOW_MINUTES
            }
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("unreadable statusline snapshot %s: %s", self.path, exc)
            return None
        if not windows:
            return None
        return QuotaSnapshot(
            self.platform, datetime.fromtimestamp(mtime, UTC), windows, "claude_statusline"
        )
