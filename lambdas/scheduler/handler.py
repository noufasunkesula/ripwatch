"""rw-scheduler: wake, sleep, status and nightly export (sprint-1.md N-12).

Event: {"action": "wake" | "sleep" | "status" | "export"}
- wake / sleep: desired capacity of rw-worker-asg to 1 / 0 (make wake, make sleep, rw-sleep-nightly)
- status: the ASG's desired capacity and instances with their lifecycle state
- export: rw-incidents, rw-agent-trace, rw-approvals as JSON Lines to
  rw-artifacts/traces/<YYYY-MM-DD>/<table>.jsonl (rw-export-nightly), so evidence survives teardown

Lambda runtime only: stdlib + boto3. One JSON log line per action.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3

EXPORT_TABLES = ("RW_TABLE_INCIDENTS", "RW_TABLE_TRACE", "RW_TABLE_APPROVALS")
DEFAULT_TABLES = {
    "RW_TABLE_INCIDENTS": "rw-incidents",
    "RW_TABLE_TRACE": "rw-agent-trace",
    "RW_TABLE_APPROVALS": "rw-approvals",
}


def _client(name: str) -> Any:
    endpoint = os.environ.get("RW_AWS_ENDPOINT_URL") or None
    return boto3.client(name, endpoint_url=endpoint)


def _log(action: str, **fields: Any) -> None:
    print(json.dumps({"service": "rw-scheduler", "action": action, **fields}, default=str))


def _asg() -> str:
    return os.environ.get("RW_WORKER_ASG", "rw-worker-asg")


def set_desired(capacity: int) -> dict[str, Any]:
    autoscaling = _client("autoscaling")
    autoscaling.set_desired_capacity(
        AutoScalingGroupName=_asg(), DesiredCapacity=capacity, HonorCooldown=False
    )
    return {"asg": _asg(), "desired": capacity}


def status() -> dict[str, Any]:
    groups = _client("autoscaling").describe_auto_scaling_groups(AutoScalingGroupNames=[_asg()])
    if not groups["AutoScalingGroups"]:
        raise LookupError(f"auto scaling group {_asg()} not found")
    group = groups["AutoScalingGroups"][0]
    return {
        "asg": _asg(),
        "desired": group["DesiredCapacity"],
        "instances": [
            {
                "id": i["InstanceId"],
                "lifecycle_state": i["LifecycleState"],
                "health": i.get("HealthStatus"),
            }
            for i in group.get("Instances", [])
        ],
    }


def _plain(value: Any) -> Any:
    """DynamoDB Decimals to int or float so rows serialize as ordinary JSON."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | set | tuple):
        return [_plain(v) for v in value]
    return value


def _scan(table_name: str) -> list[dict[str, Any]]:
    endpoint = os.environ.get("RW_AWS_ENDPOINT_URL") or None
    table = boto3.resource("dynamodb", endpoint_url=endpoint).Table(table_name)
    rows, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        rows.extend(_plain(item) for item in page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return rows
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def export(now: datetime | None = None) -> dict[str, Any]:
    bucket = os.environ["RW_ARTIFACTS_BUCKET"]
    day = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    s3 = _client("s3")
    written = {}
    for env_name in EXPORT_TABLES:
        table = os.environ.get(env_name, DEFAULT_TABLES[env_name])
        rows = _scan(table)
        body = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
        key = f"traces/{day}/{table}.jsonl"
        s3.put_object(
            Bucket=bucket, Key=key, Body=body.encode(), ContentType="application/x-ndjson"
        )
        written[table] = {"key": key, "rows": len(rows)}
    return {"bucket": bucket, "tables": written}


ACTIONS = {
    "wake": lambda: set_desired(1),
    "sleep": lambda: set_desired(0),
    "status": status,
    "export": export,
}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    action = (event or {}).get("action")
    if action not in ACTIONS:
        _log("rejected", error=f"unknown action {action!r}")
        raise ValueError(f"action must be one of {sorted(ACTIONS)}, got {action!r}")
    result = ACTIONS[action]()
    _log(action, **result)
    return {"ok": True, "action": action, **result}
