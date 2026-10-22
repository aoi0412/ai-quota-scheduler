"""Codex CLI quota from ``~/.codex/sessions/**/*.jsonl`` (optional, off by default).

Codex logs ``rate_limits.primary`` / ``secondary`` with each token-count
event. The values describe the moment Codex was last used, so the observation
time is the event's own timestamp, not "now" — the safety gate's staleness
check then decides whether it is still usable.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from ai_quota_scheduler.adapters import register_adapter
from ai_quota_scheduler.config import SchedulerConfig
from ai_quota_scheduler.models import FIVE_HOUR, SEVEN_DAY, QuotaSnapshot, WindowState

__all__ = ["CodexLogAdapter", "parse_codex_session"]

log = logging.getLogger(__name__)

_NAMES = {"primary": FIVE_HOUR, "secondary": SEVEN_DAY}


def _find_rate_limits(obj: Any) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        found = obj.get("rate_limits")
        if isinstance(found, Mapping):
            return found
        for value in obj.values():
            hit = _find_rate_limits(value)
            if hit is not None:
                return hit
    return None


def _event_time(line: Mapping[str, Any], fallback: datetime) -> datetime:
    raw = line.get("timestamp")
    if not raw:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else fallback


def _windows(limits: Mapping[str, Any], at: datetime) -> dict[str, WindowState]:
    windows: dict[str, WindowState] = {}
    for key, name in _NAMES.items():
        w = limits.get(key)
        if not isinstance(w, Mapping) or w.get("used_percent") is None:
            continue
        if w.get("resets_at") is not None:
            resets_at = datetime.fromtimestamp(float(w["resets_at"]), UTC)
        elif w.get("resets_in_seconds") is not None:
            resets_at = at + timedelta(seconds=float(w["resets_in_seconds"]))
        else:
            continue
        windows[name] = WindowState(
            name, float(w["used_percent"]), resets_at, int(w.get("window_minutes") or 0)
        )
    return windows


def parse_codex_session(path: Path) -> QuotaSnapshot | None:
    """Snapshot from the last ``rate_limits`` entry in one session log."""
    fallback = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    latest: QuotaSnapshot | None = None
    with path.open() as f:
        for raw in f:
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            limits = _find_rate_limits(line)
            if limits is None:
                continue
            at = _event_time(line, fallback)
            windows = _windows(limits, at)
            if windows:
                latest = QuotaSnapshot("codex", at, windows, "codex_log")
    return latest


@register_adapter("codex_log")
class CodexLogAdapter:
    platform = "codex"

    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir

    @classmethod
    def from_config(cls, cfg: SchedulerConfig) -> Self:
        return cls(cfg.codex_sessions_dir)

    def read(self) -> QuotaSnapshot | None:
        try:
            sessions = sorted(
                self.sessions_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            for path in sessions:
                snap = parse_codex_session(path)
                if snap is not None:
                    return snap
        except OSError as exc:
            log.warning("codex session scan failed: %s", exc)
        return None
