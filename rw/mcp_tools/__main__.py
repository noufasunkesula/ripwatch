"""python -m rw.mcp_tools: run rw-mcp-tools on 127.0.0.1:8765.

Environment:
  RW_TABLE_DETECTIONS   rw-detections table name (default "rw-detections")
  RW_SSM_PREFIX         SSM parameter prefix (default "/rw")
  RW_AWS_ENDPOINT_URL   moto server for local runs (unset on AWS)
  AWS_REGION            default "us-east-1"
  RW_HEARTBEAT_DIR      heartbeat file directory (default "/var/run/rw")

Interim until rw.common (N-04) lands: boto3 clients, logging and the heartbeat
are set up here. Swap to rw.common.aws, rw.common.logging.setup_logging and
rw.common.heartbeat.beat then.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import boto3

from rw.mcp_tools.server import HOST, PORT, SERVER_NAME, ToolDeps, build_server
from rw.mcp_tools.store import DynamoDetectionStore

HEARTBEAT_EVERY_S = 30


def _heartbeat(directory: Path, stop: threading.Event) -> None:
    path = directory / f"{SERVER_NAME}.heartbeat"
    while not stop.is_set():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(str(int(time.time())))
        except OSError:
            logging.getLogger("rw.mcp_tools").warning("heartbeat_write_failed")
        stop.wait(HEARTBEAT_EVERY_S)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("RW_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    region = os.environ.get("AWS_REGION", "us-east-1")
    endpoint = os.environ.get("RW_AWS_ENDPOINT_URL") or None
    session = boto3.session.Session(region_name=region)
    table = session.resource("dynamodb", endpoint_url=endpoint).Table(
        os.environ.get("RW_TABLE_DETECTIONS", "rw-detections")
    )
    deps = ToolDeps(
        store=DynamoDetectionStore(table),
        s3=session.client("s3", endpoint_url=endpoint),
        ssm=session.client("ssm", endpoint_url=endpoint),
        ssm_prefix=os.environ.get("RW_SSM_PREFIX", "/rw"),
    )

    stop = threading.Event()
    beat_dir = Path(os.environ.get("RW_HEARTBEAT_DIR", "/var/run/rw"))
    threading.Thread(target=_heartbeat, args=(beat_dir, stop), daemon=True).start()
    try:
        build_server(deps).run("streamable-http", host=HOST, port=PORT)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
