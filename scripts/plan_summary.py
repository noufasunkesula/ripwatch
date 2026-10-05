"""Summarize `terraform show -json <planfile>` read from stdin (sprint-1.md N-05 `make plan`).

Prints add / change / destroy counts and every resource being created that bills money, so a
human can say "yes" to an apply knowing what it costs.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

# Resource types that bill when created. Value: how they bill (north star sections 1 and 21).
COSTLY: dict[str, str] = {
    "aws_instance": "hourly compute",
    "aws_autoscaling_group": "hourly compute while instances run",
    "aws_nat_gateway": "hourly + per GB (north star forbids it)",
    "aws_lb": "hourly (north star forbids it)",
    "aws_eip": "hourly while allocated",
    "aws_vpc_endpoint": "hourly per interface endpoint (north star forbids it)",
    "aws_cloudfront_distribution": "per request and GB (free tier)",
    "aws_cloudtrail": "first management trail free, data events billed",
    "aws_cloudwatch_dashboard": "per dashboard beyond 3",
    "aws_cloudwatch_metric_alarm": "per alarm beyond 10",
    "aws_kms_key": "monthly per key",
    "aws_dynamodb_table": "on-demand per request and GB (free tier)",
    "aws_lambda_function": "per request and GB-s (free tier)",
    "aws_s3_bucket": "per GB stored and requests",
    "aws_sqs_queue": "per request (free tier)",
    "aws_sns_topic": "per publish and email (free tier)",
    "aws_apigatewayv2_api": "per request (free tier)",
    "aws_cognito_user_pool": "per monthly active user (free tier)",
    "aws_budgets_budget": "first 2 budgets free",
    "aws_ce_anomaly_monitor": "free",
}


def summarize(plan: dict[str, Any]) -> tuple[Counter[str], list[tuple[str, str]]]:
    counts: Counter[str] = Counter({"add": 0, "change": 0, "destroy": 0})
    costly: list[tuple[str, str]] = []
    for rc in plan.get("resource_changes", []):
        if rc.get("mode") != "managed":
            continue
        actions = rc.get("change", {}).get("actions", [])
        if "create" in actions:
            counts["add"] += 1
            if rc.get("type") in COSTLY:
                costly.append((rc["address"], COSTLY[rc["type"]]))
        if "update" in actions:
            counts["change"] += 1
        if "delete" in actions:
            counts["destroy"] += 1
    return counts, costly


def render(stack: str, plan: dict[str, Any]) -> str:
    counts, costly = summarize(plan)
    lines = [
        f"Plan summary for {stack}: {counts['add']} to add, {counts['change']} to change, "
        f"{counts['destroy']} to destroy"
    ]
    if costly:
        lines.append("Resources that cost money:")
        lines += [f"  - {address}: {how}" for address, how in costly]
    else:
        lines.append("Resources that cost money: none")
    if counts["destroy"]:
        lines.append("WARNING: this plan destroys resources.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    stack = argv[1] if len(argv) > 1 else "stack"
    print(render(stack, json.load(sys.stdin)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
