import pytest

from rw.common.config import MissingSettingError, Settings, SettingsError, get_settings


def test_defaults_match_the_naming_table():
    s = Settings.from_env({})
    assert s.aws_region == "us-east-1"
    assert s.ssm_prefix == "/rw"
    assert (s.table_cameras, s.table_jobs, s.table_detections) == (
        "rw-cameras",
        "rw-jobs",
        "rw-detections",
    )
    assert (s.table_incidents, s.table_trace, s.table_approvals) == (
        "rw-incidents",
        "rw-agent-trace",
        "rw-approvals",
    )
    assert s.worker_asg == "rw-worker-asg"
    assert s.metric_namespace == "RipWatch"
    assert s.llm == "fake"
    assert s.bedrock_model_id == "amazon.nova-lite-v1:0"
    assert s.bedrock_read_timeout_s == 20.0
    assert s.aws_endpoint_url is None
    assert s.data_bucket is None


def test_environment_overrides_defaults_and_unknown_keys_are_ignored():
    s = Settings.from_env(
        {
            "RW_TABLE_JOBS": "rw-jobs-test",
            "RW_AWS_ENDPOINT_URL": "http://127.0.0.1:5000",
            "RW_LLM": "bedrock",
            "RW_CAMERA_SIM_INTERVAL_S": "2.5",
            "PATH": "/usr/bin",
        }
    )
    assert s.table_jobs == "rw-jobs-test"
    assert s.aws_endpoint_url == "http://127.0.0.1:5000"
    assert s.llm == "bedrock"
    assert s.camera_sim_interval_s == 2.5


def test_empty_values_count_as_unset():
    s = Settings.from_env({"RW_TABLE_JOBS": "", "RW_AWS_ENDPOINT_URL": "  "})
    assert s.table_jobs == "rw-jobs"
    assert s.aws_endpoint_url is None


def test_bucket_names_derive_from_account_id():
    s = Settings.from_env({"AWS_ACCOUNT_ID": "123456789012"})
    assert s.data_bucket == "rw-data-123456789012"
    assert s.artifacts_bucket == "rw-artifacts-123456789012"

    explicit = Settings.from_env({"AWS_ACCOUNT_ID": "123456789012", "RW_DATA_BUCKET": "mine"})
    assert explicit.data_bucket == "mine"


def test_require_names_the_missing_variable():
    s = Settings.from_env({})
    with pytest.raises(MissingSettingError, match="RW_QUEUE_JOBS_URL is not set"):
        s.require("queue_jobs_url")
    with pytest.raises(MissingSettingError, match=r"RW_DATA_BUCKET is not set \(or set AWS_ACC"):
        s.require("data_bucket")
    with pytest.raises(AttributeError, match="no field 'nope'"):
        s.require("nope")
    assert s.require("table_jobs") == "rw-jobs"


@pytest.mark.parametrize(
    ("env", "names"),
    [
        ({"RW_LLM": "openai"}, "RW_LLM"),
        ({"RW_RUNTIME": "gpu"}, "RW_RUNTIME"),
        ({"RW_CAMERA_SIM_INTERVAL_S": "soon"}, "RW_CAMERA_SIM_INTERVAL_S"),
        ({"RW_AWS_READ_TIMEOUT_S": "0"}, "RW_AWS_READ_TIMEOUT_S"),
        ({"AWS_ACCOUNT_ID": "12345"}, "AWS_ACCOUNT_ID"),
    ],
)
def test_bad_values_raise_naming_the_variable(env, names):
    with pytest.raises(SettingsError, match=names):
        Settings.from_env(env)


def test_settings_are_frozen():
    s = Settings.from_env({})
    with pytest.raises(Exception, match="frozen"):
        s.table_jobs = "other"


def test_get_settings_is_cached_and_reads_os_environ(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("RW_TABLE_INCIDENTS", "rw-incidents-x")
    try:
        first = get_settings()
        assert first.table_incidents == "rw-incidents-x"
        monkeypatch.setenv("RW_TABLE_INCIDENTS", "changed")
        assert get_settings() is first
    finally:
        get_settings.cache_clear()
