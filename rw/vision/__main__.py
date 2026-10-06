"""python -m rw.vision: the rw-vision unit (sprint-1.md N-11).

Ingest runs the vision pipeline in its own process (frames never cross processes). This unit
keeps the set of cameras with an active incident (watching, alerted, approved) in
<RW_HEARTBEAT_DIR>/active_cameras.json, refreshed every 30 s from the rw-incidents status-index,
so ingest knows which clear clips still need a follow-up candidate (sprint-1.md 7.2).

Saif's detector loading and the model download (rw-artifacts/models/<name>/<version>/model.onnx
to /var/lib/rw/models/) will live here as well.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from boto3.dynamodb.conditions import Key

from rw.agent.lifecycle_rules import ACTIVE_STATUSES
from rw.common.aws import resource
from rw.common.config import get_settings
from rw.common.heartbeat import beat
from rw.common.logging import setup_logging
from rw.ingest.__main__ import ACTIVE_CAMERAS_FILE

SERVICE = "rw-vision"
REFRESH_S = 30
log = logging.getLogger(SERVICE)


def active_cameras(incidents: Any) -> dict[str, str]:
    """camera_id -> its newest active incident_id."""
    newest: dict[str, tuple[str, str]] = {}
    for status in sorted(ACTIVE_STATUSES):
        kwargs: dict[str, Any] = {
            "IndexName": "status-index",
            "KeyConditionExpression": Key("status").eq(status),
        }
        while True:
            page = incidents.query(**kwargs)
            for item in page.get("Items", []):
                camera, created = item["camera_id"], item.get("created_at", "")
                if camera not in newest or created > newest[camera][0]:
                    newest[camera] = (created, item["incident_id"])
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return {camera: incident for camera, (_, incident) in sorted(newest.items())}


def write_active_cameras(path: Path, cameras: dict[str, str]) -> None:
    """Atomic write, so ingest never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    body = {"updated_at": datetime.now(UTC).isoformat(), "cameras": cameras}
    tmp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(tmp, path)


def refresh(incidents: Any, path: Path) -> dict[str, str]:
    cameras = active_cameras(incidents)
    write_active_cameras(path, cameras)
    return cameras


def main() -> None:
    setup_logging(SERVICE)
    settings = get_settings()
    incidents = resource("dynamodb").Table(settings.table_incidents)
    path = Path(settings.heartbeat_dir) / ACTIVE_CAMERAS_FILE
    while True:
        try:
            cameras = refresh(incidents, path)
            log.info("active_cameras", extra={"count": len(cameras)})
        except Exception:  # noqa: BLE001 (keep the last file; try again next round)
            log.exception("active_cameras_refresh_failed")
        beat(SERVICE)
        time.sleep(REFRESH_S)


if __name__ == "__main__":
    main()
