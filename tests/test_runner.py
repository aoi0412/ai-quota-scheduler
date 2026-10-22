from datetime import UTC, datetime

import pytest

from ai_quota_scheduler.control import read_control
from ai_quota_scheduler.runner import JOB_DIR_ENV, Runner
from ai_quota_scheduler.store import Store
from conftest import default_cfg

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


class FakeProc:
    def __init__(self, pid, returncode=None):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode


class FakeSpawner:
    def __init__(self):
        self.calls = []
        self.next_pid = 1000

    def __call__(self, argv, env, log_path):
        self.next_pid += 1
        proc = FakeProc(self.next_pid)
        self.calls.append({"argv": argv, "env": env, "log_path": log_path, "proc": proc})
        return proc


@pytest.fixture
def env(tmp_path):
    cfg = default_cfg(tmp_path, job_command=("harness2",), rollout_max_min=60)
    store = Store(cfg.db_path)
    spawner = FakeSpawner()
    alive: set[int] = set()
    runner = Runner(cfg, store, spawn=spawner, pid_alive=lambda pid: pid in alive)
    return cfg, store, spawner, alive, runner


def test_enqueue_fifo(env):
    _, store, *_ = env
    store.enqueue("a", ["--bench", "jb"])
    store.enqueue("b", [])
    assert store.next_queued() == ("a", ["--bench", "jb"])


def test_starts_queued_job_with_isolated_config_dir(env):
    cfg, store, spawner, _, runner = env
    store.enqueue("j1", ["--bench", "jb", "--k", "1"])
    runner.apply(2, NOW)
    (call,) = spawner.calls
    assert call["argv"] == ["harness2", "--bench", "jb", "--k", "1"]
    assert call["env"]["CLAUDE_CONFIG_DIR"] == str(cfg.harness_config_dir)
    job_dir = cfg.state_dir / "jobs" / "j1"
    assert call["env"][JOB_DIR_ENV] == str(job_dir)
    assert read_control(job_dir)["paused"] is False
    assert read_control(job_dir)["max_parallel"] == 2
    assert store.running_jobs() == [("j1", call["proc"].pid)]
    assert store.next_queued() is None


def test_no_start_when_parallel_zero(env):
    _, store, spawner, _, runner = env
    store.enqueue("j1", [])
    runner.apply(0, NOW)
    assert spawner.calls == []


def test_at_most_one_running_job(env):
    _, store, spawner, _, runner = env
    store.enqueue("j1", [])
    store.enqueue("j2", [])
    runner.apply(1, NOW)
    runner.apply(3, NOW)
    assert len(spawner.calls) == 1
    assert read_control(runner.job_dir("j1"))["max_parallel"] == 3


def test_pause_writes_control(env):
    _, store, _, _, runner = env
    store.enqueue("j1", [])
    runner.apply(2, NOW)
    runner.apply(0, NOW)
    ctl = read_control(runner.job_dir("j1"))
    assert ctl["paused"] is True
    assert ctl["max_parallel"] == 2  # keep the bound so in-flight work stays accounted


def test_finished_child_is_marked_and_next_starts(env):
    _, store, spawner, _, runner = env
    store.enqueue("j1", [])
    store.enqueue("j2", [])
    runner.apply(1, NOW)
    spawner.calls[0]["proc"].returncode = 0
    runner.apply(1, NOW)
    assert store.job_state("j1") == "done"
    assert [c["argv"] for c in spawner.calls] == [["harness2"], ["harness2"]]
    assert store.running_jobs()[0][0] == "j2"


def test_failed_child_is_marked_failed(env):
    _, store, spawner, _, runner = env
    store.enqueue("j1", [])
    runner.apply(1, NOW)
    spawner.calls[0]["proc"].returncode = 2
    runner.apply(0, NOW)
    assert store.job_state("j1") == "failed"


def test_reconcile_marks_dead_pid_failed(env):
    _, store, _, _, runner = env
    store.enqueue("j1", [])
    store.set_job_state("j1", "running", 4242)
    runner.reconcile()
    assert store.job_state("j1") == "failed"
    assert store.running_jobs() == []


def test_reconcile_adopts_live_pid_no_double_start(env):  # Review Focus 3
    _, store, spawner, alive, runner = env
    store.enqueue("j1", [])
    store.enqueue("j2", [])
    store.set_job_state("j1", "running", 4242)
    alive.add(4242)
    runner.reconcile()
    runner.apply(1, NOW)
    assert spawner.calls == []
    assert store.running_jobs() == [("j1", 4242)]
    assert read_control(runner.job_dir("j1"))["max_parallel"] == 1


def test_adopted_job_exit_detected(env):
    _, store, spawner, alive, runner = env
    store.enqueue("j1", [])
    store.set_job_state("j1", "running", 4242)
    alive.add(4242)
    runner.reconcile()
    alive.clear()
    runner.apply(0, NOW)
    assert store.job_state("j1") == "exited"


def test_reserve_zero_when_idle(env):
    *_, runner = env
    assert runner.reserve_pct(1.0) == 0.0


def test_reserve_counts_running_parallel(env):
    _, store, _, _, runner = env
    store.enqueue("j1", [])
    runner.apply(2, NOW)
    assert runner.reserve_pct(0.5) == pytest.approx(2 * 0.5 * 60)


def test_reserve_when_control_missing_assumes_max_parallel(env):
    cfg, store, _, alive, runner = env
    store.enqueue("j1", [])
    store.set_job_state("j1", "running", 4242)
    alive.add(4242)
    assert runner.reserve_pct(1.0) == pytest.approx(cfg.max_parallel * 1.0 * 60)


def test_spawn_failure_marks_failed(tmp_path):
    cfg = default_cfg(tmp_path)
    store = Store(cfg.db_path)

    def boom(argv, env, log_path):
        raise FileNotFoundError(argv[0])

    store.enqueue("j1", [])
    Runner(cfg, store, spawn=boom).apply(1, NOW)
    assert store.job_state("j1") == "failed"


def test_pause_without_prior_control_file(env):
    _, store, _, alive, runner = env
    store.enqueue("j1", [])
    store.set_job_state("j1", "running", 4242)
    alive.add(4242)
    runner.apply(0, NOW)
    ctl = read_control(runner.job_dir("j1"))
    assert ctl["paused"] is True
    assert ctl["max_parallel"] == runner.cfg.max_parallel
