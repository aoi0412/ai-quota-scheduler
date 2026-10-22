"""SQLite persistence for observations, harness usage and decisions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ai_quota_scheduler.models import Decision, QuotaSnapshot, WindowState

__all__ = ["JOB_STATES", "Store"]

JOB_STATES = ("queued", "running", "done", "failed", "exited")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    observed_at REAL NOT NULL,
    source TEXT NOT NULL,
    windows TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_platform_at ON snapshots (platform, observed_at);
CREATE TABLE IF NOT EXISTS harness_usage (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL,
    at REAL NOT NULL,
    tokens INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS harness_usage_at ON harness_usage (at);
CREATE TABLE IF NOT EXISTS jobs (
    seq INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    args TEXT NOT NULL,
    state TEXT NOT NULL,
    pid INTEGER
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    parallel INTEGER NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


def _ts(value: datetime) -> float:
    return value.timestamp()


def _dt(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


def _windows_to_json(snap: QuotaSnapshot) -> str:
    return json.dumps(
        {
            name: {
                "used_pct": w.used_pct,
                "resets_at": _ts(w.resets_at),
                "window_minutes": w.window_minutes,
            }
            for name, w in snap.windows.items()
        }
    )


def _windows_from_json(raw: str) -> dict[str, WindowState]:
    return {
        name: WindowState(name, w["used_pct"], _dt(w["resets_at"]), w["window_minutes"])
        for name, w in json.loads(raw).items()
    }


class Store:
    """Thin wrapper over one SQLite file (WAL mode)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _row_to_snapshot(self, row: tuple[str, float, str, str]) -> QuotaSnapshot:
        platform, observed_at, source, windows = row
        return QuotaSnapshot(platform, _dt(observed_at), _windows_from_json(windows), source)

    def add_snapshot(self, s: QuotaSnapshot) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO snapshots (platform, observed_at, source, windows) VALUES (?, ?, ?, ?)",
                (s.platform, _ts(s.observed_at), s.source, _windows_to_json(s)),
            )

    def latest_snapshot(self, platform: str) -> QuotaSnapshot | None:
        row = self._conn.execute(
            "SELECT platform, observed_at, source, windows FROM snapshots "
            "WHERE platform = ? ORDER BY observed_at DESC, id DESC LIMIT 1",
            (platform,),
        ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def snapshots_between(
        self, platform: str, start: datetime, end: datetime
    ) -> list[QuotaSnapshot]:
        """Snapshots with ``start <= observed_at < end``, oldest first."""
        rows = self._conn.execute(
            "SELECT platform, observed_at, source, windows FROM snapshots "
            "WHERE platform = ? AND observed_at >= ? AND observed_at < ? "
            "ORDER BY observed_at, id",
            (platform, _ts(start), _ts(end)),
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def first_snapshot_at(self) -> datetime | None:
        row = self._conn.execute("SELECT MIN(observed_at) FROM snapshots").fetchone()
        return _dt(row[0]) if row and row[0] is not None else None

    def add_harness_usage(self, job_id: str, at: datetime, tokens: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO harness_usage (job_id, at, tokens) VALUES (?, ?, ?)",
                (job_id, _ts(at), tokens),
            )

    def harness_tokens_between(self, start: datetime, end: datetime) -> int:
        """Total harness tokens with ``start <= at < end``."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM harness_usage WHERE at >= ? AND at < ?",
            (_ts(start), _ts(end)),
        ).fetchone()
        return int(row[0])

    def add_decision(self, d: Decision) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO decisions (at, parallel, reason, detail) VALUES (?, ?, ?, ?)",
                (_ts(d.at), d.parallel, d.reason, json.dumps(dict(d.detail))),
            )

    def latest_decision(self) -> Decision | None:
        row = self._conn.execute(
            "SELECT at, parallel, reason, detail FROM decisions ORDER BY at DESC, id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return Decision(_dt(row[0]), row[1], row[2], json.loads(row[3]))

    def enqueue(self, job_id: str, args: Sequence[str]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO jobs (job_id, args, state, pid) VALUES (?, ?, 'queued', NULL)",
                (job_id, json.dumps(list(args))),
            )

    def next_queued(self) -> tuple[str, list[str]] | None:
        row = self._conn.execute(
            "SELECT job_id, args FROM jobs WHERE state = 'queued' ORDER BY seq LIMIT 1"
        ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def set_job_state(self, job_id: str, state: str, pid: int | None) -> None:
        if state not in JOB_STATES:
            raise ValueError(f"unknown job state: {state}")
        with self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET state = ?, pid = ? WHERE job_id = ?", (state, pid, job_id)
            )
        if cur.rowcount != 1:
            raise KeyError(f"unknown job: {job_id}")

    def job_state(self, job_id: str) -> str | None:
        row = self._conn.execute("SELECT state FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return row[0] if row else None

    def running_jobs(self) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT job_id, pid FROM jobs WHERE state = 'running' ORDER BY seq"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def list_jobs(self) -> list[dict[str, object]]:
        rows = self._conn.execute("SELECT job_id, args, state, pid FROM jobs ORDER BY seq").fetchall()
        return [
            {"job_id": r[0], "args": json.loads(r[1]), "state": r[2], "pid": r[3]} for r in rows
        ]
