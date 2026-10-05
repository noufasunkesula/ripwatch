"""Create every RipWatch resource in moto server for local runs (sprint-1.md N-13, J-01).

    make local-up      # starts moto on :5000, then runs this

Mirrors infra/stacks/data and the lifeguard topic: buckets, the 6 tables (GSIs, TTL), queues with
DLQs, the S3 upload event to rw-jobs, SSM /rw parameters, and rw-lifeguard-alerts. Seeds rw-cameras
with cam-01. Idempotent. Refuses to run unless RW_AWS_ENDPOINT_URL points at a local endpoint, so
it can never touch the real account.
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from urllib.parse import urlparse

from rw.common.aws import client, resource
from rw.common.config import get_settings

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOCAL_ACCOUNT = "123456789012"  # moto's account

# (hash, range, ttl, gsis) per table; same as infra/stacks/data/main.tf.
TABLES = {
    "cameras": ("camera_id", None, None, []),
    "jobs": ("job_id", None, "expires_at", [("camera-index", "camera_id", "created_at")]),
    "detections": ("camera_id", "ts_result", "expires_at", []),
    "incidents": ("incident_id", None, None, [("status-index", "status", "created_at")]),
    "trace": ("trace_key", ("step", "N"), None, []),
    "approvals": ("incident_id", "ts", None, []),
}
QUEUES = {"jobs": 300, "candidates": 120}
UPLOAD_SUFFIXES = (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".zip")
SSM_PARAMETERS = {
    "release": "none",
    "bedrock/model_id": "amazon.nova-lite-v1:0",
    "vision/frame_width": "640",
    "vision/flow_fps": "5",
    "vision/flow_scale": "0.5",
    "vision/rip_threshold": "0.70",
    "vision/uncertain_threshold": "0.40",
    "vision/min_rip_area_px": "400",
    "agent/max_tool_calls": "6",
    "agent/cooldown_s": "60",
    "noaa/station_id": "unset",
    "nws/zone_id": "unset",
    "cool/enabled": "false",
    "risk/thresholds": "{}",
    "ocean/latest": "{}",
}


def require_local_endpoint() -> str:
    endpoint = get_settings().aws_endpoint_url
    host = urlparse(endpoint or "").hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(
            f"Refused: RW_AWS_ENDPOINT_URL={endpoint!r} is not local. local_seed.py only seeds "
            "moto (make local-up)."
        )
    return endpoint  # type: ignore[return-value]


def _exists(error: Exception, *codes: str) -> bool:
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
    return code in codes


def create_buckets(account: str) -> dict[str, str]:
    s3 = client("s3")
    names = {k: f"rw-{k}-{account}" for k in ("data", "artifacts", "frontend")}
    for name in names.values():
        try:
            s3.create_bucket(Bucket=name)
        except s3.exceptions.ClientError as error:
            if not _exists(error, "BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise
    return names


def create_tables() -> list[str]:
    settings = get_settings()
    names = {
        "cameras": settings.table_cameras,
        "jobs": settings.table_jobs,
        "detections": settings.table_detections,
        "incidents": settings.table_incidents,
        "trace": settings.table_trace,
        "approvals": settings.table_approvals,
    }
    ddb = client("dynamodb")
    existing = set(ddb.list_tables()["TableNames"])
    for key, (hash_key, range_key, ttl, gsis) in TABLES.items():
        name = names[key]
        if name in existing:
            continue
        attrs = {hash_key: "S"}
        schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
        if range_key:
            r_name, r_type = range_key if isinstance(range_key, tuple) else (range_key, "S")
            attrs[r_name] = r_type
            schema.append({"AttributeName": r_name, "KeyType": "RANGE"})
        indexes = []
        for index, g_hash, g_range in gsis:
            attrs[g_hash] = "S"
            attrs[g_range] = "S"
            indexes.append(
                {
                    "IndexName": index,
                    "KeySchema": [
                        {"AttributeName": g_hash, "KeyType": "HASH"},
                        {"AttributeName": g_range, "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            )
        kwargs = {
            "TableName": name,
            "KeySchema": schema,
            "AttributeDefinitions": [
                {"AttributeName": a, "AttributeType": t} for a, t in attrs.items()
            ],
            "BillingMode": "PAY_PER_REQUEST",
        }
        if indexes:
            kwargs["GlobalSecondaryIndexes"] = indexes
        ddb.create_table(**kwargs)
        if ttl:
            ddb.update_time_to_live(
                TableName=name, TimeToLiveSpecification={"Enabled": True, "AttributeName": ttl}
            )
    return list(names.values())


def create_queues() -> dict[str, str]:
    sqs = client("sqs")
    urls = {}
    for name, visibility in QUEUES.items():
        dlq_url = sqs.create_queue(QueueName=f"rw-{name}-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]
        urls[name] = sqs.create_queue(
            QueueName=f"rw-{name}",
            Attributes={
                "VisibilityTimeout": str(visibility),
                "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": 3}),
            },
        )["QueueUrl"]
    return urls


def connect_uploads(data_bucket: str, jobs_url: str) -> None:
    sqs = client("sqs")
    jobs_arn = sqs.get_queue_attributes(QueueUrl=jobs_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    client("s3").put_bucket_notification_configuration(
        Bucket=data_bucket,
        NotificationConfiguration={
            "QueueConfigurations": [
                {
                    "Id": f"incoming{suffix.replace('.', '-')}",
                    "QueueArn": jobs_arn,
                    "Events": ["s3:ObjectCreated:*"],
                    "Filter": {
                        "Key": {
                            "FilterRules": [
                                {"Name": "prefix", "Value": "incoming/"},
                                {"Name": "suffix", "Value": suffix},
                            ]
                        }
                    },
                }
                for suffix in UPLOAD_SUFFIXES
            ]
        },
    )


def put_parameters() -> None:
    ssm = client("ssm")
    prefix = get_settings().ssm_prefix.rstrip("/")
    for name, value in SSM_PARAMETERS.items():
        ssm.put_parameter(Name=f"{prefix}/{name}", Value=value, Type="String", Overwrite=True)


def seed_cameras() -> dict:
    """cam-01 replays clips from replay/clips/cam-01/. Beach coordinates come from Daksh (D-08);
    set RW_DEMO_LAT / RW_DEMO_LON to include them."""
    camera = {
        "camera_id": "cam-01",
        "name": "Demo beach camera 1",
        "enabled": True,
        "replay_prefix": "replay/clips/cam-01/",
        "seaward_vector": [Decimal("0"), Decimal("-1")],
        "beach_flag": "green",
    }
    lat, lon = os.environ.get("RW_DEMO_LAT"), os.environ.get("RW_DEMO_LON")
    if lat and lon:
        camera["lat"], camera["lon"] = Decimal(lat), Decimal(lon)
    resource("dynamodb").Table(get_settings().table_cameras).put_item(Item=camera)
    return camera


def seed() -> dict:
    endpoint = require_local_endpoint()
    account = get_settings().aws_account_id or LOCAL_ACCOUNT
    buckets = create_buckets(account)
    tables = create_tables()
    queues = create_queues()
    connect_uploads(buckets["data"], queues["jobs"])
    put_parameters()
    topic = client("sns").create_topic(Name="rw-lifeguard-alerts")["TopicArn"]
    camera = seed_cameras()
    return {
        "endpoint": endpoint,
        "buckets": buckets,
        "tables": tables,
        "queues": queues,
        "topic": topic,
        "camera": camera["camera_id"],
    }


def main() -> int:
    summary = seed()
    print(json.dumps(summary, indent=2, default=str))
    print("Add to .env for local runs:")
    print(f"RW_QUEUE_JOBS_URL={summary['queues']['jobs']}")
    print(f"RW_QUEUE_CANDIDATES_URL={summary['queues']['candidates']}")
    print(f"RW_TOPIC_LIFEGUARD_ARN={summary['topic']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
