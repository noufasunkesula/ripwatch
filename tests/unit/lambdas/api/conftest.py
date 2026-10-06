"""rw-api is imported the way Lambda runs it: from a folder laid out like its zip.

The zip root holds lambdas/api/*.py plus rw_shared/enums.py and rw_shared/lifecycle_rules.py
(infra/stacks/serverless api_shared), so the modules import each other by bare name.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[4]
SHARED = {
    "rw_shared/enums.py": ROOT / "rw/contracts/enums.py",
    "rw_shared/lifecycle_rules.py": ROOT / "rw/agent/lifecycle_rules.py",
}
API_MODULES = ("auth", "presign", "routes", "handler")
REGION = "us-east-1"
DATA = "rw-data-123456789012"
ARTIFACTS = "rw-artifacts-123456789012"


def build_zip_dir(target: Path) -> Path:
    for source in (ROOT / "lambdas/api").glob("*.py"):
        shutil.copy(source, target / source.name)
    for zip_path, source in SHARED.items():
        (target / zip_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target / zip_path)
    return target


@pytest.fixture(scope="session")
def api(tmp_path_factory):
    """The handler and routes modules, imported from the zip layout."""
    build = build_zip_dir(tmp_path_factory.mktemp("rw-api-zip"))
    names = (*API_MODULES, "rw_shared", "rw_shared.enums", "rw_shared.lifecycle_rules")
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    sys.path.insert(0, str(build))
    try:
        import handler
        import routes

        yield handler, routes
    finally:
        sys.path.remove(str(build))
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


@pytest.fixture
def aws(api, monkeypatch):
    """Moto with the tables, buckets and topic rw-api uses."""
    for key in ("AWS_PROFILE", "RW_AWS_ENDPOINT_URL", "RW_VERSION"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("RW_DATA_BUCKET", DATA)
    monkeypatch.setenv("RW_ARTIFACTS_BUCKET", ARTIFACTS)
    monkeypatch.setenv("RW_ALLOWED_ORIGIN", "https://dashboard.example")
    _, routes = api
    with mock_aws():
        routes.reset_clients()
        ddb = boto3.resource("dynamodb", region_name=REGION)
        tables = {}
        specs = {
            "rw-cameras": ("camera_id", None, None),
            "rw-jobs": ("job_id", None, None),
            "rw-detections": ("camera_id", "ts_result", None),
            "rw-incidents": ("incident_id", None, ("status-index", "status", "created_at")),
            "rw-agent-trace": ("trace_key", "step", None),
            "rw-approvals": ("incident_id", "ts", None),
        }
        for name, (hash_key, range_key, gsi) in specs.items():
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
            tables[name] = ddb.create_table(
                TableName=name,
                KeySchema=keys,
                AttributeDefinitions=[
                    {"AttributeName": k, "AttributeType": t} for k, t in attrs.items()
                ],
                BillingMode="PAY_PER_REQUEST",
                **extra,
            )
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=DATA)
        s3.create_bucket(Bucket=ARTIFACTS)
        sns = boto3.client("sns", region_name=REGION)
        topic = sns.create_topic(Name="rw-lifeguard-alerts")["TopicArn"]
        sqs = boto3.client("sqs", region_name=REGION)
        inbox = sqs.create_queue(QueueName="lifeguard-inbox")["QueueUrl"]
        inbox_arn = sqs.get_queue_attributes(QueueUrl=inbox, AttributeNames=["QueueArn"])
        sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=inbox_arn["Attributes"]["QueueArn"])
        monkeypatch.setenv("RW_TOPIC_LIFEGUARD_ARN", topic)
        yield {"tables": tables, "s3": s3, "sqs": sqs, "inbox": inbox}
        routes.reset_clients()
