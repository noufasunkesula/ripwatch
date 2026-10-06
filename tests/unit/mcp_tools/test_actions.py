"""D-04 action tools against moto: S3 (keyframes, evidence), DynamoDB, SNS (sprint-1.md D-04)."""

from __future__ import annotations

import json
from datetime import timedelta

import boto3
import cv2
import numpy as np
import pytest
from moto import mock_aws

from rw.common import metrics
from rw.common.config import get_settings
from rw.contracts import VisionResult
from rw.contracts.decision import Action, RiskLevel
from rw.mcp_tools.store import InMemoryDetectionStore
from rw.mcp_tools.tools import followup, incidents
from rw.mcp_tools.tools.incidents import (
    AlertInput,
    ApprovalRequestInput,
    CloseInput,
    CreateIncidentInput,
    SetWatchInput,
)
from tests.unit.mcp_tools.helpers import T0

REGION = "us-east-1"
KEYFRAMES = "rw-artifacts-example"  # bucket named in the shared example result
ARTIFACTS = "rw-artifacts-123456789012"
TRACE = "tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
NOW = T0 + timedelta(seconds=30)


def _table(ddb, name, key):
    return ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def aws(monkeypatch):
    for key in ("AWS_PROFILE", "RW_AWS_ENDPOINT_URL", "RW_RUNTIME", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    get_settings.cache_clear()
    metrics.reset()
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        for bucket in (KEYFRAMES, ARTIFACTS):
            s3.create_bucket(Bucket=bucket)
        ddb = boto3.resource("dynamodb", region_name=REGION)
        sns = boto3.client("sns", region_name=REGION)
        sqs = boto3.client("sqs", region_name=REGION)
        topic = sns.create_topic(Name="rw-lifeguard-alerts")["TopicArn"]
        inbox = sqs.create_queue(QueueName="lifeguard-inbox")["QueueUrl"]
        arn = sqs.get_queue_attributes(QueueUrl=inbox, AttributeNames=["QueueArn"])
        sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=arn["Attributes"]["QueueArn"])

        def inbox_messages():
            got = sqs.receive_message(QueueUrl=inbox, MaxNumberOfMessages=10).get("Messages", [])
            return [json.loads(m["Body"]) for m in got]

        yield {
            "s3": s3,
            "incidents": _table(ddb, "rw-incidents", "incident_id"),
            "jobs": _table(ddb, "rw-jobs", "job_id"),
            "sns": sns,
            "topic": topic,
            "inbox": inbox_messages,
        }
    metrics.reset()
    get_settings.cache_clear()


@pytest.fixture
def result(vision_result) -> VisionResult:
    return VisionResult.model_validate(vision_result)


@pytest.fixture
def store(result) -> InMemoryDetectionStore:
    return InMemoryDetectionStore([result])


def _put_keyframe(s3, result: VisionResult) -> None:
    image = np.full((360, 640, 3), 120, np.uint8)
    ok, jpeg = cv2.imencode(".jpg", image)
    key = result.keyframes[0].s3_uri.split(f"{KEYFRAMES}/", 1)[1]
    s3.put_object(Bucket=KEYFRAMES, Key=key, Body=jpeg.tobytes())


def _create(aws, store, result, renderer=incidents.default_renderer):
    args = CreateIncidentInput(
        trace_id=TRACE,
        camera_id=result.camera_id,
        result_id=result.result_id,
        risk_level=RiskLevel.HIGH,
        summary="Rip 0.82 with one swimmer drifting toward it",
    )
    return incidents.create_incident(
        store, aws["s3"], aws["incidents"], ARTIFACTS, args, NOW, renderer
    )


def _row(aws, incident_id):
    return aws["incidents"].get_item(Key={"incident_id": incident_id})["Item"]


# ---------------------------------------------------------------- create_incident


def test_create_incident_writes_row_and_annotated_snapshot(aws, store, result):
    _put_keyframe(aws["s3"], result)
    calls = []

    def renderer(image, res):
        calls.append(image.shape)
        out = image.copy()
        out[:10, :10] = (0, 0, 255)
        return out

    out = _create(aws, store, result, renderer)
    assert out.created and out.status == "watching" and out.note is None
    assert out.evidence_s3_uri == f"s3://{ARTIFACTS}/evidence/{out.incident_id}/snapshot.jpg"
    assert calls == [(360, 640, 3)]  # rendered at the processed-frame size

    body = aws["s3"].get_object(Bucket=ARTIFACTS, Key=f"evidence/{out.incident_id}/snapshot.jpg")
    snapshot = cv2.imdecode(np.frombuffer(body["Body"].read(), np.uint8), cv2.IMREAD_COLOR)
    assert snapshot.shape == (360 + 28, 640, 3)  # caption bar added under the frame

    row = _row(aws, out.incident_id)
    assert row["status"] == "watching" and row["risk_level"] == "HIGH"
    assert row["result_id"] == result.result_id and row["rip_id"] == result.rips[0].rip_id
    assert row["trace_id"] == TRACE and row["created_at"].endswith("Z")
    assert [r.name for r in metrics.recorded()] == ["IncidentsCreated"]


def test_create_incident_is_idempotent_per_result(aws, store, result):
    first = _create(aws, store, result)
    second = _create(aws, store, result)
    assert second.incident_id == first.incident_id and not second.created
    assert aws["incidents"].scan()["Count"] == 1
    assert len(metrics.recorded()) == 1


def test_create_incident_without_keyframes_has_no_snapshot(aws, store, result):
    out = _create(aws, store, result)  # keyframe object never uploaded
    assert out.evidence_s3_uri is None and out.note == "no_keyframes"


def test_default_renderer_draws_on_a_copy(result):
    image = np.full((360, 640, 3), 120, np.uint8)
    drawn = incidents.default_renderer(image, result)
    assert drawn.shape == image.shape and not np.array_equal(drawn, image)
    assert (image == 120).all()


# ---------------------------------------------------------------- set_watch, alert, approval, close


def test_set_watch_keeps_watching_with_a_countdown(aws, store, result):
    incident_id = _create(aws, store, result).incident_id
    out = incidents.set_watch(
        aws["incidents"],
        SetWatchInput(trace_id=TRACE, incident_id=incident_id, clips=3, reason="glare"),
        NOW,
    )
    assert (out.status, out.watch_until_clips) == ("watching", 3)
    assert _row(aws, incident_id)["watch_until_clips"] == 3


def test_alert_publishes_with_a_dashboard_link(aws, store, result):
    incident_id = _create(aws, store, result).incident_id
    out = incidents.alert_lifeguard(
        aws["incidents"],
        aws["sns"],
        aws["topic"],
        "https://d123.cloudfront.net/",
        AlertInput(
            trace_id=TRACE, incident_id=incident_id, message="Rip at cam-01, 1 swimmer near"
        ),
        NOW,
    )
    assert out.alerted and out.status == "alerted" and out.sns_message_id
    (note,) = aws["inbox"]()
    assert note["Subject"] == "RipWatch HIGH cam-01: possible rip current"
    assert note["Message"].endswith(f"https://d123.cloudfront.net/incidents/{incident_id}")
    assert _row(aws, incident_id)["status"] == "alerted"


def test_request_approval_records_the_action_and_never_performs_it(aws, store, result):
    incident_id = _create(aws, store, result).incident_id
    out = incidents.request_approval(
        aws["incidents"],
        ApprovalRequestInput(
            trace_id=TRACE,
            incident_id=incident_id,
            action=Action.RAISE_RED_FLAG,
            message="Raise red flag?",
        ),
        NOW,
    )
    assert out.approval_requested and out.status == "alerted"
    row = _row(aws, incident_id)
    assert (
        row["pending_action"] == "raise_red_flag" and row["approval_message"] == "Raise red flag?"
    )
    assert aws["inbox"]() == []  # nothing public happened


def test_close_false_alarm_counts_a_rejected_false_alarm(aws, store, result):
    incident_id = _create(aws, store, result).incident_id
    out = incidents.close_incident(
        aws["incidents"],
        CloseInput(
            trace_id=TRACE, incident_id=incident_id, outcome="false_alarm", reason="foam line"
        ),
        NOW,
    )
    assert (out.status, out.outcome) == ("closed", "false_alarm")
    assert _row(aws, incident_id)["outcome_reason"] == "foam line"
    assert [r.name for r in metrics.recorded()] == ["IncidentsCreated", "FalseAlarmsRejected"]


def test_tools_refuse_moves_the_lifecycle_forbids(aws, store, result):
    incident_id = _create(aws, store, result).incident_id
    incidents.request_approval(
        aws["incidents"],
        ApprovalRequestInput(
            trace_id=TRACE, incident_id=incident_id, action=Action.PA_ANNOUNCEMENT, message="PA?"
        ),
        NOW,
    )
    with pytest.raises(ValueError, match="cannot watch an incident that is alerted"):
        incidents.set_watch(
            aws["incidents"],
            SetWatchInput(trace_id=TRACE, incident_id=incident_id, clips=2, reason="x"),
            NOW,
        )
    with pytest.raises(ValueError, match="cannot confirm an incident that is alerted"):
        incidents.close_incident(
            aws["incidents"],
            CloseInput(trace_id=TRACE, incident_id=incident_id, outcome="confirmed", reason="x"),
            NOW,
        )


def test_unknown_incident_raises(aws):
    with pytest.raises(LookupError, match="incident inc_01J9ZC4M6Y2N8Q4T7V1B3K5D00 not found"):
        incidents.set_watch(
            aws["incidents"],
            SetWatchInput(
                trace_id=TRACE, incident_id="inc_01J9ZC4M6Y2N8Q4T7V1B3K5D00", clips=1, reason="x"
            ),
            NOW,
        )


@pytest.mark.parametrize(
    ("model", "kw"),
    [
        (SetWatchInput, {"clips": 7}),
        (SetWatchInput, {"clips": 0}),
        (AlertInput, {"message": "x" * 301}),
        (ApprovalRequestInput, {"action": "close_beach"}),
        (CloseInput, {"outcome": "maybe"}),
    ],
)
def test_input_limits(model, kw):
    base = {
        "trace_id": TRACE,
        "incident_id": "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D00",
        "clips": 1,
        "reason": "r",
        "message": "m",
        "action": "raise_red_flag",
        "outcome": "resolved",
    }
    fields = {k: v for k, v in {**base, **kw}.items() if k in model.model_fields}
    with pytest.raises(ValueError):
        model(**fields)


# ---------------------------------------------------------------- request_followup_capture


def _job(aws, mode, camera="cam-01"):
    job_id = "job_01J9ZC4K9B1C2D3E4F5G6H7J8K"
    aws["jobs"].put_item(
        Item={"job_id": job_id, "camera_id": camera, "mode": mode, "status": "done"}
    )
    return job_id


@pytest.mark.parametrize("mode", ["image", "burst"])
def test_followup_capture_is_written_on_image_and_burst_jobs(aws, mode):
    job_id = _job(aws, mode)
    args = followup.FollowupInput(
        trace_id=TRACE,
        camera_id="cam-01",
        job_id=job_id,
        message="10 s clip of the gap left of the pier",
    )
    assert followup.request_followup_capture(aws["jobs"], args, NOW).requested
    request = aws["jobs"].get_item(Key={"job_id": job_id})["Item"]["followup_request"]
    assert request["message"].startswith("10 s clip") and request["trace_id"] == TRACE


def test_followup_capture_is_refused_for_video_and_unknown_jobs(aws):
    job_id = _job(aws, "video")
    args = followup.FollowupInput(trace_id=TRACE, camera_id="cam-01", job_id=job_id, message="m")
    with pytest.raises(ValueError, match="only for image or burst jobs, not video"):
        followup.request_followup_capture(aws["jobs"], args, NOW)
    wrong_camera = followup.FollowupInput(
        trace_id=TRACE, camera_id="cam-02", job_id=job_id, message="m"
    )
    with pytest.raises(LookupError, match="not found for camera cam-02"):
        followup.request_followup_capture(aws["jobs"], wrong_camera, NOW)
