import json
from datetime import UTC, datetime

import pytest

from ai_quota_scheduler import fileio
from ai_quota_scheduler.control import CONTROL_FILENAME, read_control, write_control

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def test_roundtrip(tmp_path):
    write_control(tmp_path, paused=False, max_parallel=2, now=NOW)
    assert read_control(tmp_path) == {
        "version": 1,
        "paused": False,
        "max_parallel": 2,
        "updated_at": NOW.timestamp(),
    }


def test_version_field_is_1(tmp_path):
    write_control(tmp_path, paused=True, max_parallel=0, now=NOW)
    assert json.loads((tmp_path / CONTROL_FILENAME).read_text())["version"] == 1


def test_atomic_write_no_partial(tmp_path, monkeypatch):
    write_control(tmp_path, paused=False, max_parallel=1, now=NOW)

    def broken_dump(data, f):
        f.write('{"version": 1, "pau')
        raise OSError("disk full")

    monkeypatch.setattr(fileio.json, "dump", broken_dump)
    with pytest.raises(OSError):
        write_control(tmp_path, paused=True, max_parallel=0, now=NOW)
    assert read_control(tmp_path)["paused"] is False
    assert [p.name for p in tmp_path.iterdir()] == [CONTROL_FILENAME]


def test_read_missing_or_corrupt_is_none(tmp_path):
    assert read_control(tmp_path) is None
    (tmp_path / CONTROL_FILENAME).write_text("{")
    assert read_control(tmp_path) is None


def test_negative_parallel_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_control(tmp_path, paused=False, max_parallel=-1, now=NOW)
