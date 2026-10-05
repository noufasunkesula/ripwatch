"""Incident action tools: create, watch, alert, request approval, close (sprint-1.md D-04).

These are the only tools that change anything. None of them takes a public action: the agent can
alert the head lifeguard and ask for approval, but raising a flag, a PA announcement or a dispatch
only happens after a human approves in the dashboard (rw-api). Every status change goes through
`lifecycle_rules.next_status`, so a tool can never make a move the D-06 diagram forbids.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import cv2
import numpy as np
from boto3.dynamodb.conditions import Attr
from pydantic import BaseModel, Field

from rw.agent import lifecycle_rules as rules
from rw.common.ids import new_id
from rw.common.metrics import emit
from rw.contracts import VisionResult
from rw.contracts.base import CameraId, IncidentId, ResultId, TraceId
from rw.contracts.decision import Action, RiskLevel
from rw.mcp_tools.store import DetectionStore
from rw.mcp_tools.tools.zoom import _load

Renderer = Callable[[np.ndarray, VisionResult], np.ndarray]
SNAPSHOT_JPEG_QUALITY = 90
NO_KEYFRAMES = "no_keyframes"


class IncidentNotFound(LookupError):
    pass


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _get(table: Any, incident_id: str) -> dict:
    item = table.get_item(Key={"incident_id": incident_id}).get("Item")
    if item is None:
        raise IncidentNotFound(f"incident {incident_id} not found")
    return item


def _move(table: Any, incident: dict, event: str, now: datetime, **fields: Any) -> str:
    """Apply a lifecycle event plus extra fields; the condition guards against a racing writer."""
    status = rules.next_status(incident["status"], event)
    names = {"#status": "status", "#updated": "updated_at"}
    values = {":status": status, ":updated": _iso(now), ":expected": incident["status"]}
    sets = ["#status = :status", "#updated = :updated"]
    for i, (key, value) in enumerate(fields.items()):
        names[f"#f{i}"] = key
        values[f":f{i}"] = value
        sets.append(f"#f{i} = :f{i}")
    table.update_item(
        Key={"incident_id": incident["incident_id"]},
        UpdateExpression="SET " + ", ".join(sets),
        ConditionExpression="#status = :expected",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    return status


# ---------------------------------------------------------------- snapshot


def default_renderer(image: np.ndarray, result: VisionResult) -> np.ndarray:
    """rw.vision.draw overlay (N-11); plain polygons until baseline vision is installed."""
    try:
        from rw.vision.draw import draw_overlay
    except ImportError:
        out = image.copy()
        for rip in result.rips:
            cv2.polylines(out, [np.array(rip.polygon_px, np.int32)], True, (0, 0, 255), 2)
        for swimmer in result.swimmers:
            x, y, w, h = swimmer.bbox_px
            cv2.rectangle(out, (x, y), (x + w, y + h), (255, 200, 0), 2)
        return out
    return draw_overlay(image, result)


def annotate(image: np.ndarray, result: VisionResult, renderer: Renderer) -> np.ndarray:
    """Overlay scaled to the keyframe, plus a caption bar: RipWatch, camera, label, time."""
    height, width = image.shape[:2]
    frame = cv2.resize(image, (result.input.width, result.input.height))
    drawn = cv2.resize(renderer(frame, result), (width, height))
    top = max(result.rips, key=lambda r: r.confidence, default=None)
    label = f"{top.label.value} {top.confidence:.2f}" if top else result.summary.status.value
    caption = f"RipWatch  {result.camera_id}  {label}  {_iso(result.input.end_ts)}"
    bar = np.zeros((28, width, 3), np.uint8)
    cv2.putText(
        bar, caption, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA
    )
    return np.vstack([drawn, bar])


# ---------------------------------------------------------------- create_incident


class CreateIncidentInput(BaseModel):
    trace_id: TraceId
    camera_id: CameraId
    result_id: ResultId
    risk_level: RiskLevel
    summary: Annotated[str, Field(min_length=1, max_length=500)]


class CreateIncidentOutput(BaseModel):
    incident_id: IncidentId
    status: str
    created: bool  # false when an incident already existed for this result
    evidence_s3_uri: str | None
    note: str | None


def create_incident(
    store: DetectionStore,
    s3: Any,
    incidents: Any,
    artifacts_bucket: str,
    args: CreateIncidentInput,
    now: datetime,
    renderer: Renderer = default_renderer,
) -> CreateIncidentOutput:
    """Open an incident for a result and save an annotated evidence snapshot.

    Use when a rip is real enough to track (risk ELEVATED or above, or a confirmed rip). The
    incident starts as "watching". Calling it again for the same result_id returns the existing
    incident instead of creating a second one. The snapshot is the first keyframe with the rip
    outline, swimmer boxes, label and time; note is "no_keyframes" if the clip had none.
    """
    existing = incidents.scan(
        FilterExpression=Attr("result_id").eq(args.result_id),
        ProjectionExpression="incident_id, #s, evidence_s3_uri",
        ExpressionAttributeNames={"#s": "status"},
    ).get("Items", [])
    if existing:
        item = existing[0]
        return CreateIncidentOutput(
            incident_id=item["incident_id"],
            status=item["status"],
            created=False,
            evidence_s3_uri=item.get("evidence_s3_uri"),
            note=None,
        )

    result = store.get_result(args.camera_id, args.result_id)
    incident_id = new_id("inc")
    evidence, note = None, NO_KEYFRAMES
    image = _load(s3, result.keyframes[0].s3_uri) if result.keyframes else None
    if image is not None:
        ok, jpeg = cv2.imencode(
            ".jpg",
            annotate(image, result, renderer),
            [cv2.IMWRITE_JPEG_QUALITY, SNAPSHOT_JPEG_QUALITY],
        )
        if ok:
            key = f"evidence/{incident_id}/snapshot.jpg"
            s3.put_object(
                Bucket=artifacts_bucket, Key=key, Body=jpeg.tobytes(), ContentType="image/jpeg"
            )
            evidence, note = f"s3://{artifacts_bucket}/{key}", None

    top = max(result.rips, key=lambda r: r.confidence, default=None)
    item = {
        "incident_id": incident_id,
        "camera_id": args.camera_id,
        "result_id": args.result_id,
        "trace_id": args.trace_id,
        "status": rules.WATCHING,
        "risk_level": args.risk_level.value,
        "summary": args.summary,
        "rip_id": top.rip_id if top else None,
        "max_confidence": str(result.summary.max_confidence),
        "evidence_s3_uri": evidence,
        "pending_action": None,
        "watch_until_clips": None,
        "created_at": _iso(now),
        "updated_at": _iso(now),
    }
    incidents.put_item(Item=item, ConditionExpression="attribute_not_exists(incident_id)")
    emit("IncidentsCreated", 1)
    return CreateIncidentOutput(
        incident_id=incident_id,
        status=rules.WATCHING,
        created=True,
        evidence_s3_uri=evidence,
        note=note,
    )


# ---------------------------------------------------------------- set_watch


class SetWatchInput(BaseModel):
    trace_id: TraceId
    incident_id: IncidentId
    clips: Annotated[int, Field(ge=1, le=6)]
    reason: Annotated[str, Field(min_length=1, max_length=300)]


class SetWatchOutput(BaseModel):
    status: str
    watch_until_clips: int


def set_watch(incidents: Any, args: SetWatchInput, now: datetime) -> SetWatchOutput:
    """Keep watching an incident for the next 1 to 6 clips before deciding.

    Use when the evidence is not strong enough to alert yet. The incident stays "watching";
    each follow-up clip counts down, and the rip resolves on its own if it is gone for the rest
    of the watch. Only for incidents that are still "watching".
    """
    incident = _get(incidents, args.incident_id)
    status = _move(
        incidents, incident, "watch", now, watch_until_clips=args.clips, watch_reason=args.reason
    )
    return SetWatchOutput(status=status, watch_until_clips=args.clips)


# ---------------------------------------------------------------- alert_lifeguard


class AlertInput(BaseModel):
    trace_id: TraceId
    incident_id: IncidentId
    message: Annotated[str, Field(min_length=1, max_length=300)]


class AlertOutput(BaseModel):
    alerted: Literal[True] = True
    status: str
    sns_message_id: str


def alert_lifeguard(
    incidents: Any, sns: Any, topic_arn: str, dashboard_url: str, args: AlertInput, now: datetime
) -> AlertOutput:
    """Alert the head lifeguard about an incident (email via rw-lifeguard-alerts).

    The message (max 300 characters) should say what was seen, where, and how sure you are.
    A link to the incident on the dashboard is added. This informs a person; it does not raise
    a flag or start any public action (use request_approval for that). Status becomes "alerted".
    """
    incident = _get(incidents, args.incident_id)
    status = _move(incidents, incident, "alert", now, alerted_at=_iso(now))
    link = f"{dashboard_url.rstrip('/')}/incidents/{args.incident_id}"
    risk = incident.get("risk_level", "")
    response = sns.publish(
        TopicArn=topic_arn,
        Subject=f"RipWatch {risk} {incident['camera_id']}: possible rip current"[:100],
        Message=f"{args.message}\n\nIncident {args.incident_id}: {link}",
    )
    return AlertOutput(status=status, sns_message_id=response["MessageId"])


# ---------------------------------------------------------------- request_approval


class ApprovalRequestInput(BaseModel):
    trace_id: TraceId
    incident_id: IncidentId
    action: Action
    message: Annotated[str, Field(min_length=1, max_length=300)]


class ApprovalRequestOutput(BaseModel):
    approval_requested: Literal[True] = True
    status: str
    pending_action: Action


def request_approval(
    incidents: Any, args: ApprovalRequestInput, now: datetime
) -> ApprovalRequestOutput:
    """Ask the head lifeguard to approve a public action for an incident.

    action is "raise_red_flag", "pa_announcement" or "dispatch_lifeguard". This never performs
    the action: it records it as pending and the incident becomes "alerted" until a human
    approves or rejects it in the dashboard.
    """
    incident = _get(incidents, args.incident_id)
    status = _move(
        incidents,
        incident,
        "alert",
        now,
        pending_action=args.action.value,
        approval_message=args.message,
        approval_requested_at=_iso(now),
    )
    return ApprovalRequestOutput(status=status, pending_action=args.action)


# ---------------------------------------------------------------- close_incident


class CloseInput(BaseModel):
    trace_id: TraceId
    incident_id: IncidentId
    outcome: Literal["false_alarm", "resolved", "confirmed"]
    reason: Annotated[str, Field(min_length=1, max_length=300)]


class CloseOutput(BaseModel):
    status: str
    outcome: str


_OUTCOME_EVENT = {"false_alarm": "false_alarm", "resolved": "resolve", "confirmed": "confirm"}


def close_incident(incidents: Any, args: CloseInput, now: datetime) -> CloseOutput:
    """Finish an incident with an outcome.

    "false_alarm": a watching incident was not a rip (status "closed").
    "resolved": the rip is gone (from "watching" or "approved").
    "confirmed": follow-up clips confirmed the rip after approval (from "approved").
    """
    incident = _get(incidents, args.incident_id)
    status = _move(
        incidents,
        incident,
        _OUTCOME_EVENT[args.outcome],
        now,
        outcome=args.outcome,
        outcome_reason=args.reason,
        closed_at=_iso(now),
    )
    if args.outcome == "false_alarm":
        emit("FalseAlarmsRejected", 1)
    return CloseOutput(status=status, outcome=args.outcome)
