"""boto3 client and resource factories (sprint-1.md N-04).

Every client honors RW_AWS_ENDPOINT_URL (moto server locally), AWS_REGION, adaptive retries
(max 3 attempts) and RW_AWS_READ_TIMEOUT_S. Bedrock gets its own read timeout (20 s).
Credentials come from the usual boto3 chain (AWS_PROFILE locally, instance role on AWS).
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from rw.common.config import Settings, get_settings

BEDROCK_SERVICES = frozenset({"bedrock-runtime", "bedrock"})


def client_config(name: str, settings: Settings | None = None) -> Config:
    s = settings or get_settings()
    timeout = s.bedrock_read_timeout_s if name in BEDROCK_SERVICES else s.aws_read_timeout_s
    return Config(
        region_name=s.aws_region,
        retries={"mode": "adaptive", "max_attempts": 3},
        read_timeout=timeout,
        connect_timeout=5,
    )


def _kwargs(name: str, settings: Settings | None) -> dict[str, Any]:
    s = settings or get_settings()
    kwargs: dict[str, Any] = {"region_name": s.aws_region, "config": client_config(name, s)}
    if s.aws_endpoint_url:
        kwargs["endpoint_url"] = s.aws_endpoint_url
    return kwargs


def client(name: str, settings: Settings | None = None) -> Any:
    """A boto3 client, e.g. `client("sqs")`."""
    return boto3.session.Session().client(name, **_kwargs(name, settings))


def resource(name: str, settings: Settings | None = None) -> Any:
    """A boto3 resource, e.g. `resource("dynamodb").Table(...)`. Not thread-safe; one per thread."""
    return boto3.session.Session().resource(name, **_kwargs(name, settings))
