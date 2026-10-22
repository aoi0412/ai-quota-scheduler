"""Claude subscription usage via the (non-public) usage API.

The endpoint is undocumented and may change without notice, so nothing about
it is hard-coded: the URL, the bearer-token file and any extra headers come
from the config (``usage_api_url``, ``usage_api_token_file``,
``usage_api_headers``). An empty URL disables the adapter. Any failure —
network, HTTP status, or an unrecognised body — yields ``None`` so the safety
gate stops the harness rather than guessing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import httpx

from ai_quota_scheduler.adapters import register_adapter
from ai_quota_scheduler.config import SchedulerConfig
from ai_quota_scheduler.models import FIVE_HOUR, QuotaSnapshot, WindowState

__all__ = ["ClaudeUsageApiAdapter", "load_token", "parse_usage_response"]

log = logging.getLogger(__name__)

Fetch = Callable[[str], Mapping[str, Any]]

_PCT_KEYS = ("utilization", "used_percentage", "used_pct")


def _window_minutes(name: str, entry: Mapping[str, Any]) -> int:
    if "window_minutes" in entry:
        return int(entry["window_minutes"])
    return 300 if name == FIVE_HOUR else 10080


def _parse_time(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"resets_at without timezone: {value!r}")
    return parsed


def _parse_window(name: str, entry: Any) -> WindowState | None:
    if not isinstance(entry, Mapping) or entry.get("resets_at") is None:
        return None
    pct = next((entry[k] for k in _PCT_KEYS if entry.get(k) is not None), None)
    if pct is None:
        return None
    return WindowState(name, float(pct), _parse_time(entry["resets_at"]), _window_minutes(name, entry))


def parse_usage_response(body: Mapping[str, Any], observed_at: datetime) -> QuotaSnapshot:
    """Every window in ``body``: top-level ``five_hour`` / ``seven_day*`` objects
    plus each element of a ``limits`` array (model-scoped limits included)."""
    windows: dict[str, WindowState] = {}
    for name, entry in body.items():
        if name == "limits":
            continue
        w = _parse_window(name, entry)
        if w is not None:
            windows[name] = w
    for entry in body.get("limits") or ():
        if isinstance(entry, Mapping):
            name = str(entry.get("name") or entry.get("id") or "")
            w = _parse_window(name, entry) if name else None
            if w is not None:
                windows[name] = w
    if not windows:
        raise ValueError(f"no usage windows in response (keys: {sorted(body)})")
    return QuotaSnapshot("claude", observed_at, windows, "claude_usage_api")


def load_token(path: Path) -> str:
    """Bearer token from a Claude credentials JSON or a plain-text file."""
    text = path.read_text().strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    return str(data["claudeAiOauth"]["accessToken"])


def _http_fetch(cfg: SchedulerConfig) -> Fetch:
    def fetch(url: str) -> Mapping[str, Any]:
        headers: dict[str, str] = {}
        for line in cfg.usage_api_headers:
            key, _, value = line.partition(":")
            headers[key.strip()] = value.strip()
        if cfg.usage_api_token_file:
            token = load_token(Path(cfg.usage_api_token_file).expanduser())
            headers["Authorization"] = f"Bearer {token}"
        resp = httpx.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, Mapping):
            raise ValueError("usage API body is not an object")
        return body

    return fetch


@register_adapter("claude_usage_api")
class ClaudeUsageApiAdapter:
    platform = "claude"

    def __init__(self, cfg: SchedulerConfig, fetch: Fetch | None = None) -> None:
        self.url = cfg.usage_api_url
        self._fetch = fetch or _http_fetch(cfg)

    @classmethod
    def from_config(cls, cfg: SchedulerConfig) -> Self:
        return cls(cfg)

    def read(self) -> QuotaSnapshot | None:
        if not self.url:
            return None
        try:
            return parse_usage_response(self._fetch(self.url), datetime.now(UTC))
        except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
            # Never log the exception's request details: they may carry the token.
            log.warning("usage API read failed: %s", type(exc).__name__)
            return None
