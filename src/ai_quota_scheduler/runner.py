"""Starts queued harness2 jobs and steers them through the control file.

At most one job runs at a time; parallelism inside it is harness2's job,
bounded by ``max_parallel`` in the control file.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ai_quota_scheduler.config import SchedulerConfig
from ai_quota_scheduler.control import read_control, write_control
from ai_quota_scheduler.store import Store

__all__ = ["JOB_DIR_ENV", "JOB_LOG", "ProcLike", "Runner"]

log = logging.getLogger(__name__)

JOB_DIR_ENV = "AQS_JOB_DIR"
JOB_LOG = "job.log"


class ProcLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...


Spawn = Callable[[list[str], dict[str, str], Path], ProcLike]


def _spawn(argv: list[str], env: dict[str, str], log_path: Path) -> ProcLike:
    with log_path.open("ab") as out:
        # Own session: a daemon restart must not take the job down with it.
        return subprocess.Popen(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Runner:
    def __init__(
        self,
        cfg: SchedulerConfig,
        store: Store,
        spawn: Spawn = _spawn,
        pid_alive: Callable[[int], bool] = _pid_alive,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self._spawn = spawn
        self._pid_alive = pid_alive
        self._procs: dict[str, ProcLike] = {}

    def job_dir(self, job_id: str) -> Path:
        return self.cfg.state_dir / "jobs" / job_id

    def job_log(self, job_id: str) -> Path:
        return self.job_dir(job_id) / JOB_LOG

    def reconcile(self) -> None:
        """On startup: adopt jobs still alive, fail the ones that died meanwhile."""
        for job_id, pid in self.store.running_jobs():
            if self._pid_alive(pid):
                log.info("adopting running job %s (pid %d)", job_id, pid)
            else:
                log.warning("job %s (pid %d) died while the daemon was down", job_id, pid)
                self.store.set_job_state(job_id, "failed", pid)

    def _refresh(self) -> None:
        for job_id, pid in self.store.running_jobs():
            proc = self._procs.get(job_id)
            if proc is not None:
                rc = proc.poll()
                if rc is None:
                    continue
                del self._procs[job_id]
                state = "done" if rc == 0 else "failed"
                log.info("job %s exited with %d", job_id, rc)
            elif self._pid_alive(pid):
                continue
            else:
                state = "exited"  # adopted job: exit status unknown
                log.info("adopted job %s is gone", job_id)
            self.store.set_job_state(job_id, state, pid)

    def _current_bound(self, job_id: str) -> int:
        ctl = read_control(self.job_dir(job_id))
        if ctl is None or not isinstance(ctl.get("max_parallel"), int):
            return self.cfg.max_parallel
        return int(ctl["max_parallel"])

    def _start(self, job_id: str, args: Sequence[str], parallel: int, now: datetime) -> None:
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        # Control file first, so harness2 never starts without a bound.
        write_control(job_dir, paused=False, max_parallel=parallel, now=now)
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(self.cfg.harness_config_dir),
            JOB_DIR_ENV: str(job_dir),
        }
        argv = [*self.cfg.job_command, *args]
        try:
            proc = self._spawn(argv, env, job_dir / JOB_LOG)
        except OSError as exc:
            log.error("cannot start job %s: %s", job_id, exc)
            self.store.set_job_state(job_id, "failed", None)
            return
        self._procs[job_id] = proc
        self.store.set_job_state(job_id, "running", proc.pid)
        log.info("started job %s (pid %d): %s", job_id, proc.pid, argv)

    def apply(self, parallel: int, now: datetime) -> None:
        """Make the running job (if any) follow ``parallel``; start one if idle."""
        self._refresh()
        running = self.store.running_jobs()
        if parallel <= 0:
            for job_id, _ in running:
                # Keep the previous bound: in-flight rollouts still count in reserve_pct.
                write_control(self.job_dir(job_id), True, self._current_bound(job_id), now)
            return
        if running:
            for job_id, _ in running:
                write_control(self.job_dir(job_id), False, parallel, now)
            return
        nxt = self.store.next_queued()
        if nxt is not None:
            self._start(nxt[0], nxt[1], parallel, now)

    def reserve_pct(self, r: float) -> float:
        """Worst-case % still to be spent by rollouts already in flight."""
        return sum(
            self._current_bound(job_id) * r * self.cfg.rollout_max_min
            for job_id, _ in self.store.running_jobs()
        )
