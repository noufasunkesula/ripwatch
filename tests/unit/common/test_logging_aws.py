import io
import json
import logging

import pytest
from moto import mock_aws

from rw.common.aws import client, client_config, resource
from rw.common.logging import setup_logging


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_setup_logging_writes_json_lines_with_extras(env, restore_root_logger):
    env()
    out = io.StringIO()
    log = setup_logging("rw-ingest", stream=out)
    log.info("clip done", extra={"trace_id": "tr_1", "camera_id": "cam-01", "mode": "video"})
    log.debug("hidden at INFO")

    (line,) = _lines(out)
    assert line["level"] == "INFO"
    assert line["service"] == "rw-ingest"
    assert line["msg"] == "clip done"
    assert (line["trace_id"], line["camera_id"], line["mode"]) == ("tr_1", "cam-01", "video")
    assert line["ts"].endswith("+00:00")


def test_setup_logging_level_from_env_and_exceptions(env, restore_root_logger):
    env(RW_LOG_LEVEL="DEBUG")
    out = io.StringIO()
    log = setup_logging("rw-agent", stream=out)
    log.debug("detail", extra={"job_id": "job_1"})
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed")

    debug, error = _lines(out)
    assert debug["level"] == "DEBUG" and debug["job_id"] == "job_1"
    assert error["level"] == "ERROR" and "RuntimeError: boom" in error["exc"]


def test_setup_logging_is_idempotent(env, restore_root_logger):
    env()
    setup_logging("a", stream=io.StringIO())
    setup_logging("b", stream=io.StringIO())
    assert len(logging.getLogger().handlers) == 1


def test_client_config_retries_and_timeouts(env):
    env(RW_AWS_READ_TIMEOUT_S="7")
    cfg = client_config("s3")
    assert cfg.retries == {"mode": "adaptive", "max_attempts": 3}
    assert cfg.read_timeout == 7
    assert client_config("bedrock-runtime").read_timeout == 20


def test_client_honors_endpoint_url(env):
    env(RW_AWS_ENDPOINT_URL="http://127.0.0.1:5000", AWS_REGION="us-east-1")
    sqs = client("sqs")
    assert sqs.meta.endpoint_url == "http://127.0.0.1:5000"
    assert sqs.meta.region_name == "us-east-1"


@mock_aws
def test_client_and_resource_work_against_moto(env):
    env(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing")  # noqa: S106
    s3 = client("s3")
    s3.create_bucket(Bucket="rw-data-123456789012")
    assert [b["Name"] for b in s3.list_buckets()["Buckets"]] == ["rw-data-123456789012"]

    ddb = resource("dynamodb")
    ddb.create_table(
        TableName="rw-jobs",
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    assert ddb.Table("rw-jobs").table_status == "ACTIVE"
