"""rw-kill-switch: the 80% budget notice scales the worker to 0 (sprint-1.md N-12, north star 13).

Subscribed only to the rw-kill-switch SNS topic, which only the rw-monthly 80% notification
feeds (Decision Log 2026-10-01). Idempotent: firing twice leaves the worker at 0 and tells the
team each time.

Lambda runtime only: stdlib + boto3.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3

MESSAGE_CHARS = 500


def _client(name: str) -> Any:
    endpoint = os.environ.get("RW_AWS_ENDPOINT_URL") or None
    return boto3.client(name, endpoint_url=endpoint)


def _budget_message(event: dict[str, Any]) -> str:
    records = (event or {}).get("Records") or []
    messages = [r.get("Sns", {}).get("Message", "") for r in records]
    return " | ".join(m for m in messages if m) or "(no message)"


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    asg = os.environ.get("RW_WORKER_ASG", "rw-worker-asg")
    topic = os.environ["RW_OPS_TOPIC_ARN"]
    autoscaling = _client("autoscaling")

    groups = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[asg])
    before = (
        groups["AutoScalingGroups"][0]["DesiredCapacity"] if groups["AutoScalingGroups"] else None
    )
    autoscaling.set_desired_capacity(
        AutoScalingGroupName=asg, DesiredCapacity=0, HonorCooldown=False
    )

    budget = _budget_message(event)[:MESSAGE_CHARS]
    note = "" if before else " (it was already at 0)"
    text = f"Kill switch fired: worker scaled to 0{note}. Budget message: {budget}"
    _client("sns").publish(TopicArn=topic, Subject="RipWatch kill switch fired", Message=text)

    print(
        json.dumps(
            {"service": "rw-kill-switch", "asg": asg, "desired_before": before, "desired": 0}
        )
    )
    return {"ok": True, "asg": asg, "desired_before": before, "desired": 0}
