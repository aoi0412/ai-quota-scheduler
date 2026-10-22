"""Control-file protocol between the scheduler and a running harness2 job.

harness2 reads ``<job_dir>/aqs_control.json``::

    {"version": 1, "paused": bool, "max_parallel": int, "updated_at": float}

* ``paused`` true: start no new rollout or improver session; work already
  running finishes.
* ``max_parallel``: upper bound on concurrent rollouts.
* ``updated_at``: epoch seconds of the write.

The scheduler is the only writer; writes are atomic (temp file + rename).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_quota_scheduler.fileio import atomic_write_json

__all__ = ["CONTROL_FILENAME", "PROTOCOL_VERSION", "read_control", "write_control"]

log = logging.getLogger(__name__)

CONTROL_FILENAME = "aqs_control.json"
PROTOCOL_VERSION = 1


def write_control(job_dir: Path, paused: bool, max_parallel: int, now: datetime) -> None:
    if max_parallel < 0:
        raise ValueError(f"max_parallel must be >= 0: {max_parallel}")
    atomic_write_json(
        job_dir / CONTROL_FILENAME,
        {
            "version": PROTOCOL_VERSION,
            "paused": paused,
            "max_parallel": max_parallel,
            "updated_at": now.timestamp(),
        },
    )


def read_control(job_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((job_dir / CONTROL_FILENAME).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("unreadable control file in %s: %s", job_dir, exc)
        return None
    return data if isinstance(data, dict) else None
