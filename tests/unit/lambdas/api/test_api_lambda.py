"""rw-api routes (sprint-1.md D-07) against moto, with fake JWT claims."""

from __future__ import annotations

import ast
import copy
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rw.agent.trace import DynamoTraceSink, TraceWriter
from rw.common.ids import new_id
from rw.contracts import Approval, Job, TraceStep, VisionResult
from rw.contracts.trace import StepType
from rw.mcp_tools.store import to_item
from tests.unit.lambdas.api.conftest import API_MODULES, ARTIFACTS, DATA, ROOT, SHARED

CLAIMS = {"sub": "user-123", "email": "head.lifeguard@example.com"}
INCIDENT = "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
TRACE_ID = "tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"


def call(api, route: str, *, path=None, query=None, body=None, claims=CLAIMS) -> tuple[int, dict]:
    handler, _ = api
    event = {
        "routeKey": route,
        "pathParameters": path,
        "queryStringParameters": query,
        "body": None if body is None else json.dumps(body),
        "requestContext": {"authorizer": {"jwt": {"claims": claims}}} if claims else {},
    }
    response = handler.handler(event, None)
    assert response["headers"]["Content-Type"] == "application/json"
    return response["statusCode"], json.loads(response["body"])


def _camera(aws, flag="green"):
    aws["tables"]["rw-cameras"].put_item(
        Item={"camera_id": "cam-01", "name": "Demo beach camera 1", "beach_flag": flag}
    )


def _detection(aws, vision_result: dict, start: datetime) -> VisionResult:
    payload = copy.deepcopy(vision_result)
    payload["result_id"] = new_id("res")
    shift = start - datetime.fromisoformat(payload["input"]["start_ts"])
    for key in ("start_ts", "end_ts"):
        payload["input"][key] = (datetime.fromisoformat(payload["input"][key]) + shift).isoformat()
    result = VisionResult.model_validate(payload)
    aws["tables"]["rw-detections"].put_item(Item=to_item(result))
    return result


def _incident(aws, status="alerted", **fields):
    item = {
        "incident_id": INCIDENT,
        "camera_id": "cam-01",
        "trace_id": TRACE_ID,
        "status": status,
        "risk_level": "CRITICAL",
        "summary": "rip with a swimmer",
        "pending_action": "raise_red_flag",
        "approval_requested_at": "2026-10-05T10:15:00Z",
        "followup_rip_hits": 1,
        "created_at": "2026-10-05T10:14:30Z",
        "updated_at": "2026-10-05T10:15:00Z",
        **fields,
    }
    aws["tables"]["rw-incidents"].put_item(Item=item)
    return item


def _row(aws, name, key):
    return aws["tables"][name].get_item(Key=key).get("Item")


# ---------------------------------------------------------------- routing and auth


def test_health_needs_no_login_and_reports_the_version(api, aws, monkeypatch):
    monkeypatch.setenv("RW_VERSION", "abc1234")
    status, body = call(api, "GET /api/health", claims=None)
    assert (status, body) == (200, {"ok": True, "version": "abc1234"})


def test_protected_routes_need_claims_and_unknown_routes_are_404(api, aws):
    handler, routes = api
    for route, (_, needs_user) in routes.ROUTES.items():
        if needs_user:
            assert call(api, route, claims=None) == (
                401,
                {"error": "unauthorized", "message": "sign in required"},
            )
    assert call(api, "DELETE /api/everything")[0] == 404


def test_cors_header_and_no_stack_trace_on_errors(api, aws, monkeypatch):
    handler, routes = api

    def boom(req):
        raise RuntimeError("secret detail")

    monkeypatch.setitem(routes.ROUTES, "GET /api/cameras", (boom, True))
    response = handler.handler(
        {"routeKey": "GET /api/cameras",
         "requestContext": {"authorizer": {"jwt": {"claims": CLAIMS}}}}, None
    )  # fmt: skip
    assert response["statusCode"] == 500
    assert response["headers"]["Access-Control-Allow-Origin"] == "https://dashboard.example"
    assert json.loads(response["body"]) == {"error": "internal", "message": "internal error"}


# ---------------------------------------------------------------- cameras and detections


def test_cameras_show_flag_and_latest_status(api, aws, vision_result):
    _camera(aws)
    t0 = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    _detection(aws, vision_result, t0)
    latest = _detection(aws, vision_result, t0 + timedelta(seconds=10))

    status, body = call(api, "GET /api/cameras")

    assert status == 200
    (cam,) = body["cameras"]
    assert cam["camera_id"] == "cam-01" and cam["beach_flag"] == "green"
    assert cam["latest"]["result_id"] == latest.result_id
    assert cam["latest"]["status"] == latest.summary.status.value


def test_detections_newest_first_since_and_limit(api, aws, vision_result):
    t0 = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    results = [_detection(aws, vision_result, t0 + timedelta(seconds=10 * i)) for i in range(4)]

    status, body = call(
        api,
        "GET /api/cameras/{camera_id}/detections",
        path={"camera_id": "cam-01"},
        query={"since": "2026-10-05T10:00:10Z", "limit": "2"},
    )

    assert status == 200
    assert [d["result_id"] for d in body["detections"]] == [
        results[3].result_id,
        results[2].result_id,
    ]
    first = body["detections"][0]
    assert first["keyframe_keys"] == [k.s3_uri.split("/", 3)[3] for k in results[3].keyframes]
    assert set(first["rips"][0]) == {"rip_id", "label", "confidence", "polygon_px", "bbox_px"}


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ({"camera_id": "beach"}, None),
        ({"camera_id": "cam-01"}, {"limit": "51"}),
        ({"camera_id": "cam-01"}, {"limit": "0"}),
        ({"camera_id": "cam-01"}, {"since": "yesterday"}),
    ],
)
def test_detections_reject_bad_parameters(api, aws, path, query):
    status, body = call(api, "GET /api/cameras/{camera_id}/detections", path=path, query=query)
    assert status == 400 and body["error"] == "bad_request"


# ---------------------------------------------------------------- incidents and traces


def test_incidents_by_status_and_active_by_default(api, aws):
    _incident(aws, status="alerted")
    aws["tables"]["rw-incidents"].put_item(
        Item={"incident_id": "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9G", "status": "closed",
              "camera_id": "cam-01", "created_at": "2026-10-05T09:00:00Z"}
    )  # fmt: skip

    assert [i["incident_id"] for i in call(api, "GET /api/incidents")[1]["incidents"]] == [INCIDENT]
    _, closed = call(api, "GET /api/incidents", query={"status": "closed"})
    assert [i["status"] for i in closed["incidents"]] == ["closed"]
    assert call(api, "GET /api/incidents", query={"status": "lost"})[0] == 400


def test_incident_detail_has_last_decision_and_evidence_url(api, aws):
    aws["s3"].put_object(Bucket=ARTIFACTS, Key=f"evidence/{INCIDENT}/snapshot.jpg", Body=b"jpg")
    _incident(
        aws,
        evidence_s3_uri=f"s3://{ARTIFACTS}/evidence/{INCIDENT}/snapshot.jpg",
        last_decision={"decision": "alert"},
    )

    status, body = call(api, "GET /api/incidents/{incident_id}", path={"incident_id": INCIDENT})

    assert status == 200 and body["last_decision"] == {"decision": "alert"}
    assert f"evidence/{INCIDENT}/snapshot.jpg" in body["evidence_url"]
    assert "Expires=" in body["evidence_url"] or "X-Amz-Expires=300" in body["evidence_url"]
    missing = {"incident_id": "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9G"}
    assert call(api, "GET /api/incidents/{incident_id}", path=missing)[0] == 404
    assert call(api, "GET /api/incidents/{incident_id}", path={"incident_id": "x"})[0] == 400


def test_trace_returns_the_agent_steps_in_order(api, aws):
    _incident(aws)
    writer = TraceWriter(INCIDENT, TRACE_ID, DynamoTraceSink(aws["tables"]["rw-agent-trace"]))
    for name in ("candidate", "zoom_and_recheck", "decision"):
        writer.step(StepType.TOOL_CALL, name, input={"n": name})

    status, body = call(
        api, "GET /api/incidents/{incident_id}/trace", path={"incident_id": INCIDENT}
    )

    assert status == 200
    assert [(s["step"], s["name"]) for s in body["steps"]] == [
        (1, "candidate"),
        (2, "zoom_and_recheck"),
        (3, "decision"),
    ]


# ---------------------------------------------------------------- approval


def _approve(api, **body):
    payload = {"decision": "approve", "action": "raise_red_flag", **body}
    return call(
        api,
        "POST /api/incidents/{incident_id}/approval",
        path={"incident_id": INCIDENT},
        body=payload,
    )


def test_approving_a_red_flag_moves_the_incident_and_raises_the_flag(api, aws, capsys):
    _camera(aws)
    _incident(aws)
    writer = TraceWriter(INCIDENT, TRACE_ID, DynamoTraceSink(aws["tables"]["rw-agent-trace"]))
    writer.step(StepType.DECISION, "decision")

    status, body = _approve(api)

    assert status == 200 and body["status"] == "approved" and body["beach_flag"] == "red"
    incident = _row(aws, "rw-incidents", {"incident_id": INCIDENT})
    assert incident["status"] == "approved" and incident["approval_by"] == CLAIMS["email"]
    assert incident["followup_rip_hits"] == 0  # counters restart in the new status
    assert _row(aws, "rw-cameras", {"camera_id": "cam-01"})["beach_flag"] == "red"

    (approval,) = aws["tables"]["rw-approvals"].scan()["Items"]
    record = Approval.model_validate({**approval, "latency_s": float(approval["latency_s"])})
    assert record.decision.value == "approve" and record.user_sub == "user-123"
    assert record.latency_s > 0

    steps = DynamoTraceSink(aws["tables"]["rw-agent-trace"]).query(INCIDENT)
    assert [(s.step, s.type.value, s.name) for s in steps] == [
        (1, "decision", "decision"),
        (2, "human_approval", "approve"),
        (3, "status_change", "lifecycle"),
    ]
    assert steps[2].output_summary == {"from": "alerted", "to": "approved"}
    assert all(isinstance(s, TraceStep) for s in steps)

    message = aws["sqs"].receive_message(QueueUrl=aws["inbox"])["Messages"][0]
    assert "raise_red_flag was approved by head.lifeguard@example.com" in message["Body"]
    emf = [json.loads(line) for line in capsys.readouterr().out.splitlines() if "_aws" in line]
    assert emf and emf[0]["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "RipWatch"
    assert emf[0]["ApprovalLatencySec"] == record.latency_s


def test_rejecting_needs_a_reason_and_keeps_the_flag(api, aws):
    _camera(aws)
    _incident(aws)

    status, body = _approve(api, decision="reject")
    assert (status, body["message"]) == (400, "reason is required to reject")

    status, body = _approve(api, decision="reject", reason="lifeguard on site, no rip")
    assert status == 200 and body["status"] == "rejected" and body["beach_flag"] is None
    assert _row(aws, "rw-cameras", {"camera_id": "cam-01"})["beach_flag"] == "green"


@pytest.mark.parametrize(
    ("incident", "body"),
    [
        ({"status": "watching"}, {}),
        ({"status": "approved"}, {}),
        ({}, {"action": "pa_announcement"}),
    ],
    ids=["watching", "already-approved", "other-action"],
)
def test_approval_on_wrong_status_or_action_is_409(api, aws, incident, body):
    _incident(aws, **incident)
    status, response = _approve(api, **body)
    assert status == 409 and response["error"] == "conflict"
    assert aws["tables"]["rw-approvals"].scan()["Count"] == 0


@pytest.mark.parametrize(
    "body",
    [{"decision": "maybe"}, {"action": "evacuate"}, {"reason": 5}],
)
def test_approval_body_is_validated(api, aws, body):
    _incident(aws)
    assert _approve(api, **body)[0] == 400


def test_approval_for_a_missing_incident_is_404(api, aws):
    assert _approve(api)[0] == 404


# ---------------------------------------------------------------- uploads, jobs, media


def test_upload_creates_a_job_and_a_locked_down_presigned_post(api, aws):
    _camera(aws)
    status, body = call(
        api,
        "POST /api/uploads",
        body={"filename": "beach.mp4", "content_type": "video/mp4", "size_bytes": 5_000_000,
              "camera_id": "cam-01"},
    )  # fmt: skip

    assert status == 200 and body["mode"] == "video" and body["expires_in"] == 600
    assert body["s3_key"] == f"incoming/cam-01/{body['job_id']}.mp4"
    fields = body["upload"]["fields"]
    assert fields["key"] == body["s3_key"] and fields["Content-Type"] == "video/mp4"
    policy = json.loads(__import__("base64").b64decode(fields["policy"]))
    assert ["content-length-range", 1, 5_000_000] in policy["conditions"]
    assert {"Content-Type": "video/mp4"} in policy["conditions"]

    job = _row(aws, "rw-jobs", {"job_id": body["job_id"]})
    Job.model_validate({k: v for k, v in job.items() if k in Job.model_fields})
    assert job["status"] == "awaiting_upload" and job["source"] == "upload"


def test_upload_without_a_camera_uses_the_upload_prefix(api, aws):
    status, body = call(
        api,
        "POST /api/uploads",
        body={"filename": "photo.png", "content_type": "image/png", "size_bytes": 1000},
    )
    assert status == 200 and body["mode"] == "image"
    assert body["s3_key"].startswith("incoming/upload/job_")
    assert _row(aws, "rw-jobs", {"job_id": body["job_id"]})["camera_id"] == "cam-00"


@pytest.mark.parametrize(
    "change",
    [
        {"content_type": "application/pdf"},
        {"size_bytes": 200 * 1024 * 1024 + 1},
        {"size_bytes": 0},
        {"size_bytes": True},
        {"filename": ""},
        {"camera_id": "cam-99"},
        {"camera_id": "../etc"},
    ],
)
def test_upload_rejects_bad_requests(api, aws, change):
    body = {"filename": "a.mp4", "content_type": "video/mp4", "size_bytes": 10, **change}
    status, response = call(api, "POST /api/uploads", body=body)
    assert status == 400 and response["error"] == "bad_request"


def test_job_shows_status_result_summary_and_followup(api, aws, vision_result):
    result = _detection(aws, vision_result, datetime(2026, 10, 5, 10, 0, tzinfo=UTC))
    job_id = new_id("job")
    aws["tables"]["rw-jobs"].put_item(
        Item={"job_id": job_id, "camera_id": "cam-01", "status": "done",
              "result_id": result.result_id, "followup_request": {"message": "send a clip"}}
    )  # fmt: skip

    status, body = call(api, "GET /api/jobs/{job_id}", path={"job_id": job_id})

    assert status == 200 and body["result_summary"]["status"] == result.summary.status.value
    assert body["followup_request"] == {"message": "send a clip"}
    assert call(api, "GET /api/jobs/{job_id}", path={"job_id": new_id("job")})[0] == 404


@pytest.mark.parametrize(
    ("key", "bucket"),
    [
        ("replay/clips/cam-01/a.mp4", DATA),
        ("incoming/cam-01/job_1.mp4", DATA),
        ("evidence/inc_1/snapshot.jpg", ARTIFACTS),
        ("keyframes/cam-01/k.jpg", ARTIFACTS),
    ],
)
def test_media_presigns_allowed_prefixes(api, aws, key, bucket):
    status, body = call(api, "GET /api/media", query={"key": key})
    assert status == 200 and bucket in body["url"] and key in body["url"]
    assert body["expires_in"] == 300


@pytest.mark.parametrize(
    "key", ["raw/ripvis/train/x.jpg", "releases/rw.tar.gz", "replay/../raw/x", "", "/replay/a"]
)
def test_media_outside_allowed_prefixes_is_403(api, aws, key):
    status, body = call(api, "GET /api/media", query={"key": key})
    assert status == 403 and body["error"] == "forbidden"


# ---------------------------------------------------------------- packaging


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("module", API_MODULES)
def test_api_imports_only_stdlib_boto3_its_own_modules_and_rw_shared(module):
    allowed = set(sys.stdlib_module_names) | {"boto3", "botocore", "rw_shared", *API_MODULES}
    used = _imports(ROOT / "lambdas/api" / f"{module}.py")
    assert used <= allowed, used - allowed


@pytest.mark.parametrize("source", sorted(SHARED.values()), ids=lambda p: p.name)
def test_shared_files_are_stdlib_only(source):
    assert _imports(source) <= set(sys.stdlib_module_names)


def test_ids_match_the_contract_format(api):
    _, routes = api
    job_id = routes.new_id("job")
    assert routes.ID["job"].match(job_id) and job_id != routes.new_id("job")
