"""Interface every quota adapter implements."""

from __future__ import annotations

from typing import Protocol, Self

from ai_quota_scheduler.config import SchedulerConfig
from ai_quota_scheduler.models import QuotaSnapshot

__all__ = ["QuotaAdapter"]


class QuotaAdapter(Protocol):
    """Reads the current quota of one platform; ``None`` means unknown."""

    platform: str

    @classmethod
    def from_config(cls, cfg: SchedulerConfig) -> Self: ...

    def read(self) -> QuotaSnapshot | None: ...
