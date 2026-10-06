"""Local end-to-end flows (sprint-1.md D-09) on moto server with rw-mcp-tools over HTTP.

Covered now: candidate -> agent (FakeLLM scenario a) -> incident alerted -> rw-api approval ->
approved -> two follow-ups -> confirmed, and image upload -> uncertain -> follow-up request.
Not yet: the clip upload and ingest step in front of it. Ingest (N-11) is not written; that test
is skipped until it lands. Until then each test writes the VisionResult ingest would write
(rw.mcp_tools.store.to_item) and sends its CandidateMessage itself.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from mcp import Client

from rw.agent.__main__ import build_agent, run_once
from rw.agent.fake_llm import FakeLLM
from rw.agent.trace import DynamoTraceSink
from rw.common import metrics
from rw.common.ids import new_id
from rw.contracts import CandidateMessage, VisionResult
from rw.mcp_tools.store import to_item
from tests.integration.conftest import ARTIFACTS, DATA, REGION, keyframe_jpeg
from tests.unit.conftest import VISION_RESULT
from tests.unit.lambdas.api.conftest import API_MODULES, build_zip_dir

pytestmark = pytest.mark.anyio
CLAIMS = {"sub": "user-123", "email": "head.lifeguard@example.com"}
T0 = datetime(2026, 10, 5, 10, 15, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    """rw-api's handler from its zip layout (see tests/unit/lambdas/api/conftest.py)."""
    build = build_zip_dir(tmp_path_factory.mktemp("rw-api-zip"))
    names = (*API_MODULES, "rw_shared", "rw_shared.enums", "rw_shared.lifecycle_rules")
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    sys.path.insert(0, str(build))
    try:
        spec = importlib.util.spec_from_file_location("handler", build / "handler.py")
        handler = importlib.util.module_from_spec(spec)
        sys.modules["handler"] = handler
        spec.loader.exec_module(handler)
        yield handler, sys.modules["routes"]
    finally:
        sys.path.remove(str(build))
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _aws(world, name):
    return boto3.client(name, region_name=REGION, endpoint_url=world["endpoint"])


def _table(world, name):
    return boto3.resource("dynamodb", region_name=REGION, endpoint_url=world["endpoint"]).Table(
        name
    )


def call_api(api, route, *, path=None, body=None):
    handler, routes = api
    routes.reset_clients()  # this test's moto server
    response = handler.handler(
        {
            "routeKey": route,
            "pathParameters": path,
            "body": None if body is None else json.dumps(body),
            "requestContext": {"authorizer": {"jwt": {"claims": CLAIMS}}},
        }
    )
    return response["statusCode"], json.loads(response["body"])


def ingest_writes(world, kind: str, start: datetime, job_id: str | None = None) -> VisionResult:
    """What ingest (N-11) will do per clip: keyframe to S3, result to rw-detections."""
    payload = copy.deepcopy(VISION_RESULT)
    result_id = new_id("res")
    payload.update(result_id=result_id, trace_id=new_id("tr"), job_id=job_id or new_id("job"))
    shift = start - datetime.fromisoformat(payload["input"]["start_ts"])
    for key in ("start_ts", "end_ts"):
        payload["input"][key] = (datetime.fromisoformat(payload["input"][key]) + shift).isoformat()
    for frame in payload["keyframes"]:
        frame["ts"] = (datetime.fromisoformat(frame["ts"]) + shift).isoformat()
        frame["s3_uri"] = f"s3://{ARTIFACTS}/keyframes/cam-01/{result_id}/000.jpg"
    payload["created_at"] = (datetime.fromisoformat(payload["created_at"]) + shift).isoformat()
    if kind == "image":
        payload["mode"] = "image"
        payload["rips"][0].update(label="uncertain", confidence=0.55)
        payload["rips"][0]["evidence"]["seaward_flow_px_per_s"] = None
        payload["summary"].update(
            status="uncertain", max_confidence=0.55, swimmer_count=0, swimmers_at_risk=0
        )
        payload["swimmers"] = []
    result = VisionResult.model_validate(payload)
    _aws(world, "s3").put_object(
        Bucket=ARTIFACTS, Key=f"keyframes/cam-01/{result_id}/000.jpg", Body=keyframe_jpeg()
    )
    _table(world, "rw-detections").put_item(Item=to_item(result))
    return result


def send_candidate(world, result: VisionResult, incident_id: str | None = None) -> None:
    status = result.summary.status.value
    candidate = CandidateMessage(
        result_id=result.result_id,
        trace_id=result.trace_id,
        camera_id=result.camera_id,
        mode=result.mode,
        status=status,
        max_confidence=result.summary.max_confidence,
        swimmers_at_risk=result.summary.swimmers_at_risk,
        active_incident_id=incident_id,
        reason="active_incident_followup" if incident_id else f"status_{status}",
        created_at=result.created_at,
    )
    _aws(world, "sqs").send_message(
        QueueUrl=world["candidates"], MessageBody=candidate.model_dump_json()
    )


async def agent_takes_one(world, agent) -> None:
    assert await run_once(agent, _aws(world, "sqs"), world["candidates"]) == 1


# ---------------------------------------------------------------- main flow


async def test_rip_clip_to_alert_approval_and_confirmed_incident(world, api, capsys):
    rip = ingest_writes(world, "rip", T0)
    send_candidate(world, rip)
    llm = FakeLLM.scenario("confident_rip_alert")

    async with Client(world["mcp_url"]) as mcp:
        agent = build_agent(mcp, llm)
        await agent_takes_one(world, agent)
        (incident,) = _table(world, "rw-incidents").scan()["Items"]
        incident_id = incident["incident_id"]
        assert incident["status"] == "alerted" and incident["pending_action"] == "raise_red_flag"

        status, body = call_api(
            api,
            "POST /api/incidents/{incident_id}/approval",
            path={"incident_id": incident_id},
            body={"decision": "approve", "action": "raise_red_flag"},
        )
        assert (status, body["status"], body["beach_flag"]) == (200, "approved", "red")

        for n in (1, 2):
            again = ingest_writes(world, "rip", T0 + timedelta(seconds=10 * n))
            send_candidate(world, again, incident_id)
            await agent_takes_one(world, agent)

    incident = _table(world, "rw-incidents").get_item(Key={"incident_id": incident_id})["Item"]
    assert incident["status"] == "confirmed" and incident["outcome"] == "confirmed"
    assert incident["last_decision"]["decision"] == "watch"
    assert llm.calls == 4  # follow-ups after approval never reach the model
    camera = _table(world, "rw-cameras").get_item(Key={"camera_id": "cam-01"})["Item"]
    assert camera["beach_flag"] == "red"

    steps = DynamoTraceSink(_table(world, "rw-agent-trace")).query(incident_id)
    assert [(s.type.value, s.name) for s in steps] == [
        ("candidate_received", "candidate"),
        ("llm_call", "fake-llm"),
        ("tool_call", "zoom_and_recheck"),
        ("llm_call", "fake-llm"),
        ("tool_call", "create_incident"),
        ("llm_call", "fake-llm"),
        ("tool_call", "alert_lifeguard"),
        ("status_change", "lifecycle"),
        ("tool_call", "request_approval"),
        ("llm_call", "fake-llm"),
        ("decision", "decision"),
        ("human_approval", "approve"),
        ("status_change", "lifecycle"),
        ("candidate_received", "candidate"),
        ("decision", "decision"),
        ("candidate_received", "candidate"),
        ("status_change", "lifecycle"),
        ("decision", "decision"),
    ]
    assert [s.step for s in steps] == list(range(1, len(steps) + 1))
    assert [s.output_summary for s in steps if s.type.value == "status_change"] == [
        {"from": "watching", "to": "alerted"},
        {"from": "alerted", "to": "approved"},
        {"from": "approved", "to": "confirmed"},
    ]

    sqs = _aws(world, "sqs")
    emails = sqs.receive_message(QueueUrl=world["inbox"], MaxNumberOfMessages=10)["Messages"]
    bodies = [json.loads(m["Body"])["Message"] for m in emails]
    assert any("Rip current at cam-01" in b for b in bodies)
    assert any("raise_red_flag was approved" in b for b in bodies)
    queue = sqs.get_queue_attributes(
        QueueUrl=world["candidates"], AttributeNames=["ApproximateNumberOfMessages"]
    )
    assert queue["Attributes"]["ApproximateNumberOfMessages"] == "0"

    names = [r.name for r in metrics.recorded()]
    assert {"IncidentsCreated", "AgentToolCalls", "AgentDecisionLatencyMs", "BedrockTokens"} <= set(
        names
    )
    assert "AgentFallbackUsed" not in names and names.count("AgentToolCalls") == 3
    emf = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"_aws"' in line]
    assert [e for e in emf if "ApprovalLatencySec" in e]


# ---------------------------------------------------------------- image upload


async def test_uncertain_image_upload_asks_for_a_followup_capture(world, api):
    status, upload = call_api(
        api,
        "POST /api/uploads",
        body={"filename": "beach.jpg", "content_type": "image/jpeg", "size_bytes": 2048,
              "camera_id": "cam-01"},
    )  # fmt: skip
    assert status == 200 and upload["mode"] == "image"
    _aws(world, "s3").put_object(Bucket=DATA, Key=upload["s3_key"], Body=keyframe_jpeg())

    image = ingest_writes(world, "image", T0, job_id=upload["job_id"])
    send_candidate(world, image)
    async with Client(world["mcp_url"]) as mcp:
        await agent_takes_one(world, build_agent(mcp, FakeLLM.scenario("image_followup")))

    status, job = call_api(api, "GET /api/jobs/{job_id}", path={"job_id": upload["job_id"]})
    assert status == 200
    assert job["followup_request"]["message"].startswith("Please send a 10 second clip")
    (incident,) = _table(world, "rw-incidents").scan()["Items"]
    assert incident["status"] == "watching" and incident["last_decision"]["decision"] == "watch"


# ---------------------------------------------------------------- blocked on N-11


@pytest.mark.skip(reason="needs ingest (N-11): rw.ingest is not written yet")
async def test_clip_upload_through_ingest_to_candidate(world):
    """Upload a synthetic clip to incoming/cam-01/, send the S3-style event to rw-jobs by hand
    (moto server does not deliver S3 notifications reliably), let ingest process it, and expect
    a CandidateMessage on rw-candidates. Then continue as the main flow above."""
