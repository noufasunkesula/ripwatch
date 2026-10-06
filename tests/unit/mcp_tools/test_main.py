import threading

from moto import mock_aws

from rw.common.config import get_settings
from rw.common.heartbeat import last_beat
from rw.mcp_tools import __main__ as entry


def test_build_deps_uses_rw_common_settings(monkeypatch):
    for key in ("AWS_PROFILE", "RW_AWS_ENDPOINT_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("RW_TABLE_DETECTIONS", "rw-detections-test")
    monkeypatch.setenv("RW_SSM_PREFIX", "/rw-test")
    get_settings.cache_clear()
    try:
        with mock_aws():
            deps = entry.build_deps()
            assert deps.ssm_prefix == "/rw-test"
            assert deps.store.table.name == "rw-detections-test"
            assert deps.s3.meta.region_name == "us-east-1"
            assert deps.ssm.meta.service_model.service_name == "ssm"
    finally:
        get_settings.cache_clear()


def test_heartbeat_thread_writes_and_stops(monkeypatch, tmp_path):
    monkeypatch.setenv("RW_HEARTBEAT_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        stop = threading.Event()
        worker = threading.Thread(target=entry._heartbeat, args=(stop,))  # noqa: SLF001
        worker.start()
        for _ in range(50):
            if last_beat("rw-mcp-tools", tmp_path) is not None:
                break
            threading.Event().wait(0.02)
        stop.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert last_beat("rw-mcp-tools", tmp_path) is not None
    finally:
        get_settings.cache_clear()
