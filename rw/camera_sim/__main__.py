"""python -m rw.camera_sim: the simulated beach cameras (sprint-1.md N-11).

Every RW_CAMERA_SIM_INTERVAL_S (default 10), for each enabled camera in rw-cameras with a
`replay_prefix`, copy the next clip from replay/clips/<camera_id>/ to
incoming/<camera_id>/<UTC yyyymmddThhmmssZ>-<seq:06d>.mp4 (metadata source-clip), looping at the
end. The job row (source=camera_sim, status=queued) is written before the copy so the dashboard
sees it before ingest picks it up. The pointer per camera (next clip index, next seq) is kept in
<RW_STATE_DIR>/camera_sim_state.json so a restart continues where it stopped.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rw.common.aws import client, resource
from rw.common.config import get_settings
from rw.common.heartbeat import beat
from rw.common.logging import setup_logging
from rw.contracts import JobStatus
from rw.ingest.jobs import JobStore, parse_key, sim_key

SERVICE = "rw-camera-sim"
STATE_FILE = "camera_sim_state.json"
CLIP_SUFFIXES = (".mp4", ".mov")
log = logging.getLogger(SERVICE)


def load_state(path: Path) -> dict[str, dict[str, int]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict[str, dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _clips(s3: Any, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(CLIP_SUFFIXES)]
    return sorted(keys)


def _cameras(table: Any) -> list[dict[str, Any]]:
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return sorted(items, key=lambda c: c["camera_id"])
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def tick(
    s3: Any,
    cameras: Any,
    jobs: JobStore,
    bucket: str,
    state_path: Path,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[str]:
    """One round: copy the next clip for every enabled camera. Returns the incoming keys written."""
    state = load_state(state_path)
    written = []
    for camera in _cameras(cameras):
        camera_id, prefix = camera["camera_id"], camera.get("replay_prefix")
        if not camera.get("enabled", True) or not prefix:
            continue
        clips = _clips(s3, bucket, prefix)
        if not clips:
            log.warning("no_replay_clips", extra={"camera_id": camera_id, "prefix": prefix})
            continue
        pointer = state.setdefault(camera_id, {"next_clip": 0, "next_seq": 1})
        source = clips[pointer["next_clip"] % len(clips)]
        key = sim_key(camera_id, now(), pointer["next_seq"], Path(source).suffix.lower())
        jobs.create(parse_key(key), JobStatus.QUEUED, now())
        s3.copy_object(
            Bucket=bucket,
            Key=key,
            CopySource={"Bucket": bucket, "Key": source},
            Metadata={"source-clip": source},
            MetadataDirective="REPLACE",
        )
        pointer["next_clip"] = (pointer["next_clip"] + 1) % len(clips)
        pointer["next_seq"] += 1
        written.append(key)
        log.info("clip_sent", extra={"camera_id": camera_id, "key": key, "source": source})
    save_state(state_path, state)
    return written


def main() -> None:
    setup_logging(SERVICE)
    settings = get_settings()
    s3, dynamodb = client("s3"), resource("dynamodb")
    cameras = dynamodb.Table(settings.table_cameras)
    jobs = JobStore(dynamodb.Table(settings.table_jobs))
    bucket = settings.require("data_bucket")
    state_path = Path(settings.state_dir) / STATE_FILE
    while True:
        beat(SERVICE)
        try:
            tick(s3, cameras, jobs, bucket, state_path)
        except Exception:  # noqa: BLE001 (the next round tries again)
            log.exception("camera_sim_round_failed")
        time.sleep(settings.camera_sim_interval_s)


if __name__ == "__main__":
    main()
