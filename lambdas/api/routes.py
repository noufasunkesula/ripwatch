"""rw-api routes (sprint-1.md D-07). Each takes a Request and returns a JSON-able dict.

Lambda runtime only: stdlib + boto3, plus the shared stdlib-only rules packaged as rw_shared
(enums from rw/contracts/enums.py, lifecycle_rules from rw/agent/lifecycle_rules.py).
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Any

import boto3
import presign
from auth import User, require_user
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from rw_shared import enums
from rw_shared import lifecycle_rules as rules

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
UPLOAD_CAMERA_ID = "cam-00"  # job camera for uploads that name no camera (key incoming/upload/)
UPLOAD_TTL = timedelta(days=7)  # same as the incoming/ expiry
CAMERA_ID = re.compile(r"^cam-[0-9]{2,}$")
ID = {p: re.compile(rf"^{p}_[0-9A-HJKMNP-TV-Z]{{26}}$") for p in ("inc", "job", "res")}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
TABLE_DEFAULTS = {
    "RW_TABLE_CAMERAS": "rw-cameras",
    "RW_TABLE_JOBS": "rw-jobs",
    "RW_TABLE_DETECTIONS": "rw-detections",
    "RW_TABLE_INCIDENTS": "rw-incidents",
    "RW_TABLE_TRACE": "rw-agent-trace",
    "RW_TABLE_APPROVALS": "rw-approvals",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def bad_request(message: str) -> ApiError:
    return ApiError(400, "bad_request", message)


def not_found(message: str) -> ApiError:
    return ApiError(404, "not_found", message)


@dataclass
class Request:
    event: dict[str, Any]
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def path(self) -> dict[str, str]:
        return self.event.get("pathParameters") or {}

    @property
    def query(self) -> dict[str, str]:
        return self.event.get("queryStringParameters") or {}

    @property
    def user(self) -> User:
        return require_user(self.event)

    def body(self) -> dict[str, Any]:
        raw = self.event.get("body") or ""
        if self.event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            raise bad_request("body must be JSON") from None
        if not isinstance(body, dict):
            raise bad_request("body must be a JSON object")
        return body


# ---------------------------------------------------------------- AWS


def _endpoint() -> str | None:
    return os.environ.get("RW_AWS_ENDPOINT_URL") or None


@cache
def client(name: str) -> Any:
    return boto3.client(name, endpoint_url=_endpoint())


def table(env: str) -> Any:
    return _dynamodb().Table(os.environ.get(env) or TABLE_DEFAULTS[env])


@cache
def _dynamodb() -> Any:
    return boto3.resource("dynamodb", endpoint_url=_endpoint())


def reset_clients() -> None:
    """Tests: drop cached clients so a new moto context is used."""
    client.cache_clear()
    _dynamodb.cache_clear()


def _query_all(tbl: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while True:
        page = tbl.query(**kwargs)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _scan_all(tbl: Any) -> list[dict[str, Any]]:
    items, kwargs = [], {}
    while True:
        page = tbl.scan(**kwargs)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


# ---------------------------------------------------------------- helpers


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(value: str, name: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise bad_request(f"{name} must be an ISO 8601 time") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def limit_param(req: Request) -> int:
    raw = req.query.get("limit")
    if raw is None:
        return DEFAULT_LIMIT
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_LIMIT:
        raise bad_request(f"limit must be 1 to {MAX_LIMIT}")
    return int(raw)


def path_id(req: Request, name: str, prefix: str) -> str:
    value = req.path.get(name, "")
    if not ID[prefix].match(value):
        raise bad_request(f"{name} is not a valid {prefix}_ id")
    return value


def new_id(prefix: str) -> str:
    """<prefix>_<ULID>, same format as rw.common.ids (48-bit ms time + 80 random bits)."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return f"{prefix}_" + "".join(alphabet[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def _key(uri: str | None) -> str | None:
    return presign.split_s3_uri(uri)[1] if uri else None


def emit_metric(name: str, value: float, unit: str) -> None:
    """One CloudWatch EMF line (namespace RipWatch), the same format rw.common.metrics prints."""
    namespace = os.environ.get("RW_METRIC_NAMESPACE", "RipWatch")
    metric = {"Name": name, "Unit": unit}
    aws = {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": []}
    aws["CloudWatchMetrics"].append(
        {"Namespace": namespace, "Dimensions": [[]], "Metrics": [metric]}
    )
    print(json.dumps({"_aws": aws, name: value}))


# ---------------------------------------------------------------- health, cameras, detections


def health(req: Request) -> dict[str, Any]:
    return {"ok": True, "version": os.environ.get("RW_VERSION", "dev")}


def _latest_result(camera_id: str) -> dict[str, Any] | None:
    page = table("RW_TABLE_DETECTIONS").query(
        KeyConditionExpression=Key("camera_id").eq(camera_id), ScanIndexForward=False, Limit=1
    )
    items = page.get("Items", [])
    return json.loads(items[0]["result"]) if items else None


def cameras(req: Request) -> dict[str, Any]:
    out = []
    for cam in sorted(_scan_all(table("RW_TABLE_CAMERAS")), key=lambda c: c["camera_id"]):
        latest = _latest_result(cam["camera_id"])
        out.append(
            {
                "camera_id": cam["camera_id"],
                "name": cam.get("name"),
                "enabled": cam.get("enabled", True),
                "beach_flag": cam.get("beach_flag", "green"),
                "lat": cam.get("lat"),
                "lon": cam.get("lon"),
                "latest": None
                if latest is None
                else {
                    "result_id": latest["result_id"],
                    "start_ts": latest["input"]["start_ts"],
                    "status": latest["summary"]["status"],
                    "max_confidence": latest["summary"]["max_confidence"],
                },
            }
        )
    return {"cameras": out}


def _detection(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_id": result["result_id"],
        "trace_id": result["trace_id"],
        "job_id": result.get("job_id"),
        "mode": result["mode"],
        "start_ts": result["input"]["start_ts"],
        "end_ts": result["input"]["end_ts"],
        "summary": result["summary"],
        "rips": [
            {k: rip.get(k) for k in ("rip_id", "label", "confidence", "polygon_px", "bbox_px")}
            for rip in result.get("rips", [])
        ],
        "swimmers": result.get("swimmers", []),
        "keyframe_keys": [_key(k.get("s3_uri")) for k in result.get("keyframes", [])],
    }


def detections(req: Request) -> dict[str, Any]:
    camera_id = req.path.get("camera_id", "")
    if not CAMERA_ID.match(camera_id):
        raise bad_request("camera_id must look like cam-01")
    condition = Key("camera_id").eq(camera_id)
    if since := req.query.get("since"):
        # ts_result is "<start_ts %Y-%m-%dT%H:%M:%S.%fZ>#<result_id>" (rw.mcp_tools.store.to_item)
        stamp = parse_iso(since, "since").astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        condition = condition & Key("ts_result").gt(f"{stamp}#~")
    page = table("RW_TABLE_DETECTIONS").query(
        KeyConditionExpression=condition, ScanIndexForward=False, Limit=limit_param(req)
    )
    results = [_detection(json.loads(i["result"])) for i in page.get("Items", [])]
    return {"camera_id": camera_id, "detections": results}


# ---------------------------------------------------------------- incidents


INCIDENT_FIELDS = (
    "incident_id", "camera_id", "status", "risk_level", "summary", "pending_action",
    "created_at", "updated_at",
)  # fmt: skip


def incidents(req: Request) -> dict[str, Any]:
    status = req.query.get("status")
    if status is not None and status not in rules.STATUSES:
        raise bad_request(f"status must be one of {', '.join(rules.STATUSES)}")
    limit = limit_param(req)
    statuses = [status] if status else sorted(rules.ACTIVE_STATUSES)
    found = []
    for s in statuses:
        page = table("RW_TABLE_INCIDENTS").query(
            IndexName="status-index",
            KeyConditionExpression=Key("status").eq(s),
            ScanIndexForward=False,
            Limit=limit,
        )
        found += page.get("Items", [])
    found.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return {"incidents": [{k: i.get(k) for k in INCIDENT_FIELDS} for i in found[:limit]]}


def _incident(incident_id: str) -> dict[str, Any]:
    item = table("RW_TABLE_INCIDENTS").get_item(Key={"incident_id": incident_id}).get("Item")
    if item is None:
        raise not_found(f"incident {incident_id} not found")
    return item


def incident(req: Request) -> dict[str, Any]:
    item = _incident(path_id(req, "incident_id", "inc"))
    evidence_url = None
    if uri := item.get("evidence_s3_uri"):
        bucket, key = presign.split_s3_uri(uri)
        evidence_url = presign.get_url(client("s3"), bucket, key)
    return {
        "incident": item,
        "last_decision": item.get("last_decision"),
        "evidence_url": evidence_url,
    }


def _trace_rows(trace_key: str) -> list[dict[str, Any]]:
    items = _query_all(
        table("RW_TABLE_TRACE"), KeyConditionExpression=Key("trace_key").eq(trace_key)
    )
    rows = [json.loads(i["row"]) if "row" in i else i for i in items]
    return sorted(rows, key=lambda r: int(r["step"]))


def trace(req: Request) -> dict[str, Any]:
    incident_id = path_id(req, "incident_id", "inc")
    _incident(incident_id)
    return {"incident_id": incident_id, "steps": _trace_rows(incident_id)}


# ---------------------------------------------------------------- approval


def _append_trace(incident: dict[str, Any], steps: list[dict[str, Any]], now: datetime) -> None:
    """Add steps after the incident's last step, in the TraceStep v1.0 row format."""
    tbl = table("RW_TABLE_TRACE")
    key = incident["incident_id"]
    last = tbl.query(
        KeyConditionExpression=Key("trace_key").eq(key), ScanIndexForward=False, Limit=1
    )
    number = int(last["Items"][0]["step"]) if last.get("Items") else 0
    for step in steps:
        number += 1
        row = {
            "schema_version": "1.0",
            "trace_key": key,
            "step": number,
            "trace_id": incident["trace_id"],
            "output_summary": None,
            "reasoning_summary": None,
            "latency_ms": 0,
            "error": None,
            "created_at": iso(now),
            **step,
        }
        tbl.put_item(
            Item={**row, "row": json.dumps(row)}, ConditionExpression="attribute_not_exists(step)"
        )


def approval(req: Request) -> dict[str, Any]:
    incident_id = path_id(req, "incident_id", "inc")
    user = req.user
    body = req.body()
    decision, action = body.get("decision"), body.get("action")
    reason = body.get("reason")
    if decision not in {d.value for d in enums.ApprovalDecision}:
        raise bad_request("decision must be approve or reject")
    if action not in {a.value for a in enums.Action}:
        raise bad_request("action must be raise_red_flag, pa_announcement or dispatch_lifeguard")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 1000):
        raise bad_request("reason must be text up to 1000 characters")
    if decision == enums.ApprovalDecision.REJECT and not (reason and reason.strip()):
        raise bad_request("reason is required to reject")
    if not EMAIL.match(user.email):
        raise ApiError(403, "forbidden", "account has no valid email")

    item = _incident(incident_id)
    if item["status"] != rules.ALERTED or item.get("pending_action") != action:
        raise ApiError(
            409,
            "conflict",
            f"incident is {item['status']} with pending action {item.get('pending_action')}",
        )
    event = "approve" if decision == enums.ApprovalDecision.APPROVE else "reject"
    status = rules.next_status(item["status"], event)
    requested = item.get("approval_requested_at")
    latency = (
        max(0.0, (req.now - parse_iso(requested, "requested")).total_seconds())
        if requested
        else 0.0
    )

    changes = {
        **rules.FOLLOWUP_COUNTERS,
        "status": status,
        "updated_at": iso(req.now),
        "approval_decision": decision,
        "approval_by": user.email,
        "approval_at": iso(req.now),
    }
    names = {f"#f{i}": k for i, k in enumerate(changes)}
    values = {f":f{i}": v for i, v in enumerate(changes.values())}
    try:
        table("RW_TABLE_INCIDENTS").update_item(
            Key={"incident_id": incident_id},
            UpdateExpression="SET " + ", ".join(f"#f{i} = :f{i}" for i in range(len(changes))),
            ConditionExpression="#s = :alerted AND #p = :action",
            ExpressionAttributeNames={**names, "#s": "status", "#p": "pending_action"},
            ExpressionAttributeValues={**values, ":alerted": rules.ALERTED, ":action": action},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ApiError(409, "conflict", "incident changed; reload and try again") from None
        raise

    record = {
        "schema_version": "1.0",
        "incident_id": incident_id,
        "ts": iso(req.now),
        "action": action,
        "decision": decision,
        "user_sub": user.sub,
        "user_email": user.email,
        "reason": reason,
        "latency_s": Decimal(str(round(latency, 3))),
    }
    table("RW_TABLE_APPROVALS").put_item(Item=record)
    _append_trace(
        item,
        [
            {"type": enums.StepType.HUMAN_APPROVAL.value, "name": decision,
             "input": {"action": action, "by": user.email}, "reasoning_summary": reason},
            {"type": enums.StepType.STATUS_CHANGE.value, "name": "lifecycle",
             "input": {"incident_id": incident_id, "event": event},
             "output_summary": {"from": rules.ALERTED, "to": status}},
        ],
        req.now,
    )  # fmt: skip

    flag = None
    if event == "approve" and action == enums.Action.RAISE_RED_FLAG:
        flag = "red"
        table("RW_TABLE_CAMERAS").update_item(
            Key={"camera_id": item["camera_id"]},
            UpdateExpression="SET beach_flag = :red, flag_updated_at = :now",
            ExpressionAttributeValues={":red": flag, ":now": iso(req.now)},
        )
    if topic := os.environ.get("RW_TOPIC_LIFEGUARD_ARN"):
        verb = "approved" if event == "approve" else "rejected"
        client("sns").publish(
            TopicArn=topic,
            Subject=f"RipWatch {item['camera_id']}: {action} {verb}"[:100],
            Message=f"{action} was {verb} by {user.email} for incident {incident_id}."
            + (f"\nReason: {reason}" if reason else ""),
        )
    emit_metric("ApprovalLatencySec", round(latency, 3), "Seconds")
    return {"incident_id": incident_id, "status": status, "beach_flag": flag,
            "approval": {**record, "latency_s": float(record["latency_s"])}}  # fmt: skip


# ---------------------------------------------------------------- uploads and jobs


def uploads(req: Request) -> dict[str, Any]:
    body = req.body()
    filename, content_type = body.get("filename"), body.get("content_type")
    size, camera_id = body.get("size_bytes"), body.get("camera_id")
    if not isinstance(filename, str) or not 1 <= len(filename) <= 255:
        raise bad_request("filename must be 1 to 255 characters")
    if content_type not in presign.UPLOAD_TYPES:
        raise bad_request(f"content_type must be one of {', '.join(presign.UPLOAD_TYPES)}")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 1 <= size <= presign.MAX_UPLOAD_BYTES
    ):
        raise bad_request("size_bytes must be 1 byte to 200 MB")
    if camera_id is not None:
        if not isinstance(camera_id, str) or not CAMERA_ID.match(camera_id):
            raise bad_request("camera_id must look like cam-01")
        if "Item" not in table("RW_TABLE_CAMERAS").get_item(Key={"camera_id": camera_id}):
            raise bad_request(f"unknown camera {camera_id}")

    ext, mode = presign.UPLOAD_TYPES[content_type]
    job_id = new_id("job")
    key = f"incoming/{camera_id or 'upload'}/{job_id}.{ext}"
    job = {
        "schema_version": "1.0",
        "job_id": job_id,
        "camera_id": camera_id or UPLOAD_CAMERA_ID,
        "source": enums.JobSource.UPLOAD.value,
        "s3_key": key,
        "mode": mode,
        "status": enums.JobStatus.AWAITING_UPLOAD.value,
        "result_id": None,
        "error": None,
        "followup_request": None,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size,
        "created_at": iso(req.now),
        "updated_at": iso(req.now),
        "expires_at": int((req.now + UPLOAD_TTL).timestamp()),
    }
    table("RW_TABLE_JOBS").put_item(Item=job)
    post = presign.upload_post(client("s3"), key, content_type, size)
    return {"job_id": job_id, "s3_key": key, "mode": mode, "upload": post,
            "expires_in": presign.UPLOAD_EXPIRY_S}  # fmt: skip


def _result(camera_id: str, result_id: str) -> dict[str, Any] | None:
    items = _query_all(
        table("RW_TABLE_DETECTIONS"),
        KeyConditionExpression=Key("camera_id").eq(camera_id),
        FilterExpression="result_id = :r",
        ExpressionAttributeValues={":r": result_id},
    )
    return json.loads(items[0]["result"]) if items else None


def job(req: Request) -> dict[str, Any]:
    job_id = path_id(req, "job_id", "job")
    item = table("RW_TABLE_JOBS").get_item(Key={"job_id": job_id}).get("Item")
    if item is None:
        raise not_found(f"job {job_id} not found")
    summary = None
    if item.get("result_id"):
        result = _result(item["camera_id"], item["result_id"])
        summary = result["summary"] if result else None
    return {
        "job": item,
        "result_summary": summary,
        "followup_request": item.get("followup_request"),
    }


def media(req: Request) -> dict[str, Any]:
    key = req.query.get("key", "")
    bucket = presign.media_bucket(key)
    if bucket is None:
        raise ApiError(403, "forbidden", "key is outside the allowed media prefixes")
    return {"url": presign.get_url(client("s3"), bucket, key), "expires_in": presign.GET_EXPIRY_S}


# route key -> (function, needs a signed-in user)
ROUTES = {
    "GET /api/health": (health, False),
    "GET /api/cameras": (cameras, True),
    "GET /api/cameras/{camera_id}/detections": (detections, True),
    "GET /api/incidents": (incidents, True),
    "GET /api/incidents/{incident_id}": (incident, True),
    "GET /api/incidents/{incident_id}/trace": (trace, True),
    "POST /api/incidents/{incident_id}/approval": (approval, True),
    "POST /api/uploads": (uploads, True),
    "GET /api/jobs/{job_id}": (job, True),
    "GET /api/media": (media, True),
}
