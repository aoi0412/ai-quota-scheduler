import pytest

from ai_quota_scheduler.adapters import create_adapter, register_adapter
from conftest import default_cfg


def test_unknown_adapter_raises():
    with pytest.raises(KeyError):
        create_adapter("gemini", default_cfg())


def test_register_and_create(tmp_path):
    @register_adapter("dummy_for_test")
    class Dummy:
        platform = "dummy"

        def __init__(self, cfg):
            self.cfg = cfg

        @classmethod
        def from_config(cls, cfg):
            return cls(cfg)

        def read(self):
            return None

    cfg = default_cfg(tmp_path)
    adapter = create_adapter("dummy_for_test", cfg)
    assert isinstance(adapter, Dummy)
    assert adapter.cfg is cfg


def test_duplicate_registration_rejected():
    @register_adapter("dup_for_test")
    class A:
        pass

    with pytest.raises(ValueError):

        @register_adapter("dup_for_test")
        class B:
            pass
