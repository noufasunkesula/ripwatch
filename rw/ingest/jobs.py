"""rw-jobs: S3 events, object keys and the job row ingest keeps up to date (sprint-1.md N-11).

Two key shapes land under incoming/:
  - camera-sim: incoming/<camera_id>/<UTC yyyymmddThhmmssZ>-<seq:06d>.<ext>; camera-sim wrote a
    queued job row before copying, found here through the camera-index GSI by s3_key.
  - upload:     incoming/<camera_id or "upload">/<job_id>.<ext>; rw-api wrote the job row
    (awaiting_upload) when it issued the presigned POST. "upload" means job camera cam-00.
S3 event keys arrive URL-encoded and are unquoted with unquote_plus.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from rw.common.ids import new_id
from rw.contracts import JobSource, JobStatus, Mode

UPLOAD_CAMERA_ID = "cam-00"  # same as rw-api (lambdas/api/routes.py)
JOB_TTL = timedelta(days=7)  # incoming/ objects expire after 7 days
MODE_BY_EXT = {".mp4": Mode.VIDEO, ".mov": Mode.VIDEO, ".jpg": Mode.IMAGE, ".jpeg": Mode.IMAGE,
               ".png": Mode.IMAGE, ".zip": Mode.BURST}  # fmt: skip

_SIM = re.compile(
    r"^incoming/(?P<camera>cam-[0-9]{2,})/(?P<ts>[0-9]{8}T[0-9]{6}Z)-(?P<seq>[0-9]{6})(?P<ext>\.\w+)$"
)
_UPLOAD = re.compile(
    r"^incoming/(?P<camera>cam-[0-9]{2,}|upload)/(?P<job>job_[0-9A-HJKMNP-TV-Z]{26})(?P<ext>\.\w+)$"
)


class UnsupportedKey(ValueError):
    """An object under incoming/ that matches neither key shape."""


@dataclass(frozen=True)
class ObjectKey:
    key: str
    camera_id: str
    source: JobSource
    ext: str
    ts: datetime | None = None  # camera-sim: clip start
    seq: int | None = None  # camera-sim: per-camera sequence
    job_id: str | None = None  # upload: from the file name

    @property
    def mode(self) -> Mode:
        return MODE_BY_EXT[self.ext]

    @property
    def stem(self) -> str:
        return self.key.rsplit("/", 1)[-1].removesuffix(self.ext)


def sim_key(camera_id: str, ts: datetime, seq: int, ext: str = ".mp4") -> str:
    return f"incoming/{camera_id}/{ts.astimezone(UTC):%Y%m%dT%H%M%SZ}-{seq:06d}{ext}"


def parse_key(key: str) -> ObjectKey:
    if m := _SIM.match(key):
        ext = m["ext"].lower()
        if ext not in MODE_BY_EXT:
            raise UnsupportedKey(f"unsupported file type {ext}: {key}")
        ts = datetime.strptime(m["ts"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        return ObjectKey(key, m["camera"], JobSource.CAMERA_SIM, ext, ts=ts, seq=int(m["seq"]))
    if m := _UPLOAD.match(key):
        ext = m["ext"].lower()
        if ext not in MODE_BY_EXT:
            raise UnsupportedKey(f"unsupported file type {ext}: {key}")
        camera = UPLOAD_CAMERA_ID if m["camera"] == "upload" else m["camera"]
        return ObjectKey(key, camera, JobSource.UPLOAD, ext, job_id=m["job"])
    raise UnsupportedKey(f"not an ingest key: {key}")


def s3_objects(body: str) -> list[tuple[str, str]]:
    """(bucket, key) for each object-created record; S3 test events give an empty list."""
    event = json.loads(body)
    if event.get("Event") == "s3:TestEvent":
        return []
    out = []
    for record in event.get("Records", []):
        if not str(record.get("eventName", "")).startswith("ObjectCreated"):
            continue
        s3 = record["s3"]
        out.append((s3["bucket"]["name"], urllib.parse.unquote_plus(s3["object"]["key"])))
    return out


def s3_event(bucket: str, key: str) -> str:
    """An S3 ObjectCreated notification body, for local runs where moto does not deliver them."""
    obj = {"key": urllib.parse.quote_plus(key)}
    record = {"eventSource": "aws:s3", "eventName": "ObjectCreated:Put",
              "s3": {"bucket": {"name": bucket}, "object": obj}}  # fmt: skip
    return json.dumps({"Records": [record]})


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class JobStore:
    """The rw-jobs table as ingest and camera-sim use it."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def create(self, obj: ObjectKey, status: JobStatus, now: datetime) -> dict[str, Any]:
        job = {
            "schema_version": "1.0",
            "job_id": obj.job_id or new_id("job"),
            "camera_id": obj.camera_id,
            "source": obj.source.value,
            "s3_key": obj.key,
            "mode": obj.mode.value,
            "status": status.value,
            "result_id": None,
            "error": None,
            "followup_request": None,
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "expires_at": int((now + JOB_TTL).timestamp()),
        }
        self.table.put_item(Item=job)
        return job

    def find(self, obj: ObjectKey) -> dict[str, Any] | None:
        if obj.job_id:
            return self.table.get_item(Key={"job_id": obj.job_id}).get("Item")
        kwargs: dict[str, Any] = {
            "IndexName": "camera-index",
            "KeyConditionExpression": Key("camera_id").eq(obj.camera_id),
            "FilterExpression": Attr("s3_key").eq(obj.key),
        }
        while True:
            page = self.table.query(**kwargs)
            if page.get("Items"):
                return page["Items"][0]
            if "LastEvaluatedKey" not in page:
                return None
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def find_or_create(self, obj: ObjectKey, now: datetime) -> dict[str, Any]:
        """The job for this object; created (queued) if camera-sim or rw-api did not write one."""
        return self.find(obj) or self.create(obj, JobStatus.QUEUED, now)

    def update(self, job_id: str, status: JobStatus, now: datetime, **fields: Any) -> None:
        values = {"status": status.value, "updated_at": _iso(now), **fields}
        names = {f"#f{i}": k for i, k in enumerate(values)}
        self.table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET " + ", ".join(f"#f{i} = :f{i}" for i in range(len(values))),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={f":f{i}": v for i, v in enumerate(values.values())},
        )
