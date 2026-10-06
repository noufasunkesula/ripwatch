"""Local integration world (sprint-1.md D-09): moto server, seed, rw-mcp-tools over HTTP.

Everything talks to moto through RW_AWS_ENDPOINT_URL, as `make local-up` does. The seed below is
the subset of scripts/local_seed.py (N-13, not merged yet) these tests need; switch to
`local_seed.seed()` once it is on main.
"""

from __future__ import annotations

import dataclasses
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

import boto3
import cv2
import numpy as np
import pytest
from moto.server import ThreadedMotoServer

from rw.common import metrics
from rw.common.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
ACCOUNT = "123456789012"
DATA = f"rw-data-{ACCOUNT}"
ARTIFACTS = f"rw-artifacts-{ACCOUNT}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise TimeoutError(f"nothing listening on 127.0.0.1:{port}")


class FixedRechecker:
    """Stands in for VisionPipeline.recheck (N-11) on zoom_and_recheck."""

    confidence = 0.91

    def recheck(self, crops, result):
        return self.confidence


TABLES = {
    "rw-cameras": ("camera_id", None, None),
    "rw-jobs": ("job_id", None, ("camera-index", "camera_id", "created_at")),
    "rw-detections": ("camera_id", "ts_result", None),
    "rw-incidents": ("incident_id", None, ("status-index", "status", "created_at")),
    "rw-agent-trace": ("trace_key", "step", None),
    "rw-approvals": ("incident_id", "ts", None),
}


def seed(endpoint: str) -> dict[str, str]:
    kw = {"region_name": REGION, "endpoint_url": endpoint}
    ddb = boto3.resource("dynamodb", **kw)
    for name, (hash_key, range_key, gsi) in TABLES.items():
        keys = [{"AttributeName": hash_key, "KeyType": "HASH"}]
        attrs = {hash_key: "S"}
        if range_key:
            keys.append({"AttributeName": range_key, "KeyType": "RANGE"})
            attrs[range_key] = "N" if range_key == "step" else "S"
        extra = {}
        if gsi:
            index, gh, gr = gsi
            attrs |= {gh: "S", gr: "S"}
            extra["GlobalSecondaryIndexes"] = [
                {
                    "IndexName": index,
                    "KeySchema": [
                        {"AttributeName": gh, "KeyType": "HASH"},
                        {"AttributeName": gr, "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ]
        ddb.create_table(
            TableName=name,
            KeySchema=keys,
            AttributeDefinitions=[
                {"AttributeName": k, "AttributeType": t} for k, t in attrs.items()
            ],
            BillingMode="PAY_PER_REQUEST",
            **extra,
        )
    ddb.Table("rw-cameras").put_item(
        Item={"camera_id": "cam-01", "name": "Demo beach camera 1", "enabled": True,
              "beach_flag": "green"}
    )  # fmt: skip

    s3 = boto3.client("s3", **kw)
    for bucket in (DATA, ARTIFACTS):
        s3.create_bucket(Bucket=bucket)
    sqs = boto3.client("sqs", **kw)
    queues = {q: sqs.create_queue(QueueName=q)["QueueUrl"] for q in ("rw-jobs", "rw-candidates")}
    sns = boto3.client("sns", **kw)
    topic = sns.create_topic(Name="rw-lifeguard-alerts")["TopicArn"]
    inbox = sqs.create_queue(QueueName="lifeguard-inbox")["QueueUrl"]
    inbox_arn = sqs.get_queue_attributes(QueueUrl=inbox, AttributeNames=["QueueArn"])
    sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=inbox_arn["Attributes"]["QueueArn"])
    ssm = boto3.client("ssm", **kw)
    for name, value in {
        "/rw/agent/cooldown_s": "60",
        "/rw/agent/max_tool_calls": "6",
        "/rw/risk/thresholds": "{}",
        "/rw/ocean/latest": json.dumps(
            {"fetched_at": "2026-10-05T10:00:00Z", "station": "8729108", "tides": [],
             "tide_trend": "unknown", "alerts": [], "errors": []}
        ),
    }.items():  # fmt: skip
        ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
    return {"jobs": queues["rw-jobs"], "candidates": queues["rw-candidates"], "topic": topic,
            "inbox": inbox}  # fmt: skip


@pytest.fixture
def world(monkeypatch, tmp_path):
    """moto server + seed + rw-mcp-tools on its own port, wired through the env like local mode."""
    for key in ("AWS_PROFILE", "RW_RUNTIME", "AWS_LAMBDA_FUNCTION_NAME", "RW_VERSION"):
        monkeypatch.delenv(key, raising=False)
    moto_port = free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=moto_port, verbose=False)
    server.start()
    endpoint = f"http://127.0.0.1:{moto_port}"
    # moto keeps its backends per process, so a new server still sees the last test's tables
    reset = urllib.request.Request(f"{endpoint}/moto-api/reset", method="POST")  # noqa: S310
    urllib.request.urlopen(reset)  # noqa: S310 (local http moto server)
    env = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "AWS_ACCOUNT_ID": ACCOUNT,
        "RW_AWS_ENDPOINT_URL": endpoint,
        "RW_DATA_BUCKET": DATA,
        "RW_ARTIFACTS_BUCKET": ARTIFACTS,
        "RW_HEARTBEAT_DIR": str(tmp_path),
        "RW_LLM": "fake",
        "RW_ALLOWED_ORIGIN": "https://dashboard.example",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    try:
        urls = seed(endpoint)
        monkeypatch.setenv("RW_QUEUE_JOBS_URL", urls["jobs"])
        monkeypatch.setenv("RW_QUEUE_CANDIDATES_URL", urls["candidates"])
        monkeypatch.setenv("RW_TOPIC_LIFEGUARD_ARN", urls["topic"])
        get_settings.cache_clear()
        metrics.reset()

        from rw.mcp_tools.__main__ import build_deps
        from rw.mcp_tools.server import build_server

        deps = dataclasses.replace(build_deps(), rechecker=FixedRechecker())
        mcp_port = free_port()
        mcp = build_server(deps)
        threading.Thread(
            target=mcp.run,
            args=("streamable-http",),
            kwargs={"host": "127.0.0.1", "port": mcp_port},
            daemon=True,
        ).start()
        wait_for_port(mcp_port)
        yield {"endpoint": endpoint, "mcp_url": f"http://127.0.0.1:{mcp_port}/mcp", **urls}
    finally:
        server.stop()
        get_settings.cache_clear()
        metrics.reset()


def keyframe_jpeg() -> bytes:
    ok, jpeg = cv2.imencode(".jpg", np.full((360, 640, 3), 120, np.uint8))
    assert ok
    return jpeg.tobytes()
