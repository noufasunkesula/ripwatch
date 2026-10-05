"""python -m rw.mcp_tools: run rw-mcp-tools on 127.0.0.1:8765.

Settings come from rw.common.config (RW_TABLE_DETECTIONS, RW_SSM_PREFIX, RW_AWS_ENDPOINT_URL,
AWS_REGION, RW_HEARTBEAT_DIR, RW_LOG_LEVEL). AWS clients from rw.common.aws, JSON logs from
rw.common.logging, heartbeat from rw.common.heartbeat (rw_deploy.sh checks it is fresh).
"""

from __future__ import annotations

import logging
import threading

from rw.common.aws import client, resource
from rw.common.config import get_settings
from rw.common.heartbeat import beat
from rw.common.logging import setup_logging
from rw.mcp_tools.server import HOST, PORT, SERVER_NAME, ToolDeps, build_server
from rw.mcp_tools.store import DynamoDetectionStore

HEARTBEAT_EVERY_S = 30
log = logging.getLogger("rw.mcp_tools")


def _heartbeat(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            beat(SERVER_NAME)
        except OSError:
            log.warning("heartbeat_write_failed")
        stop.wait(HEARTBEAT_EVERY_S)


def build_deps() -> ToolDeps:
    settings = get_settings()
    dynamodb = resource("dynamodb")
    return ToolDeps(
        store=DynamoDetectionStore(dynamodb.Table(settings.table_detections)),
        s3=client("s3"),
        ssm=client("ssm"),
        ssm_prefix=settings.ssm_prefix,
        incidents=dynamodb.Table(settings.table_incidents),
        jobs=dynamodb.Table(settings.table_jobs),
        sns=client("sns"),
        lifeguard_topic_arn=settings.topic_lifeguard_arn,
        artifacts_bucket=settings.artifacts_bucket,
        dashboard_url=settings.allowed_origin or "https://dashboard.invalid",
    )


def main() -> None:
    setup_logging(SERVER_NAME)
    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(stop,), daemon=True).start()
    try:
        build_server(build_deps()).run("streamable-http", host=HOST, port=PORT)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
