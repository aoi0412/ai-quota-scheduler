"""Adapter registry: adapters register by name and are built from config."""

from __future__ import annotations

from collections.abc import Callable

from ai_quota_scheduler.adapters.base import QuotaAdapter
from ai_quota_scheduler.config import SchedulerConfig

__all__ = ["QuotaAdapter", "create_adapter", "register_adapter", "registered_adapters"]

_REGISTRY: dict[str, type] = {}


def register_adapter(name: str) -> Callable[[type], type]:
    """Class decorator registering an adapter under ``name``."""

    def decorator(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"adapter already registered: {name}")
        _REGISTRY[name] = cls
        return cls

    return decorator


def create_adapter(name: str, cfg: SchedulerConfig) -> QuotaAdapter:
    """Build the adapter registered as ``name``; ``KeyError`` if unknown."""
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown adapter: {name}") from None
    return cls.from_config(cfg)  # type: ignore[attr-defined, no-any-return]


def registered_adapters() -> list[str]:
    return sorted(_REGISTRY)


# Built-in adapters register themselves on import.
from ai_quota_scheduler.adapters import claude_statusline as _claude_statusline  # noqa: E402,F401
