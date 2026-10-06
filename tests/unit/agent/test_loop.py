"""Agent loop scenarios (sprint-1.md D-05 tests a to f), each asserting the trace steps in order.

The agent runs against the real MCP server in-process (mcp Client(server)) with moto behind it,
and a FakeLLM replaying tests/fixtures/llm_scripts/<scenario>.yaml.
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from datetime import timedelta

import boto3
import cv2
import numpy as np
import pytest
from mcp import Client
from moto import mock_aws

from rw.agent.cooldown import Cooldown
from rw.agent.fake_llm import FakeLLM
from rw.agent.loop import Agent, AgentDeps, AgentLimits
from rw.agent.tools import McpTools
from rw.agent.trace import InMemoryTraceSink
from rw.common import metrics
from rw.common.config import get_settings
from rw.common.ids import new_id
from rw.contracts import CandidateMessage, VisionResult
from rw.mcp_tools.server import ToolDeps, build_server
from rw.mcp_tools.store import InMemoryDetectionStore
from tests.unit.mcp_tools.helpers import T0

pytestmark = pytest.mark.anyio
REGION = "us-east-1"
ARTIFACTS = "rw-artifacts-123456789012"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FixedRechecker:
    def __init__(self, confidence: float) -> None:
        self.confidence = confidence

    def recheck(self, crops, result):
        return self.confidence


def _result(vision_result: dict, kind: str) -> VisionResult:
    payload = copy.deepcopy(vision_result)
    if kind in ("uncertain", "image"):
        rip = payload["rips"][0]
        rip.update(label="uncertain", confidence=0.55)
        payload["summary"].update(
            status="uncertain", max_confidence=0.55, swimmer_count=0, swimmers_at_risk=0
        )
        payload["swimmers"] = []
    if kind == "image":
        payload["mode"] = "image"
        payload["rips"][0]["evidence"]["seaward_flow_px_per_s"] = None
    return VisionResult.model_validate(payload)


def _candidate(result: VisionResult, active_incident_id=None, reason=None) -> CandidateMessage:
    status = result.summary.status.value
    return CandidateMessage(
        result_id=result.result_id,
        trace_id=result.trace_id,
        camera_id=result.camera_id,
        mode=result.mode,
        status=status,
        max_confidence=result.summary.max_confidence,
        swimmers_at_risk=result.summary.swimmers_at_risk,
        active_incident_id=active_incident_id,
        reason=reason or f"status_{status}",
        created_at=T0 + timedelta(seconds=12),
    )


@asynccontextmanager
async def _world(result: VisionResult, script: str, recheck: float = 0.9):
    """Moto AWS + in-process MCP server + agent wired like the worker."""
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket="rw-artifacts-example")
        s3.create_bucket(Bucket=ARTIFACTS)
        ok, jpeg = cv2.imencode(".jpg", np.full((360, 640, 3), 120, np.uint8))
        key = result.keyframes[0].s3_uri.split("rw-artifacts-example/", 1)[1]
        s3.put_object(Bucket="rw-artifacts-example", Key=key, Body=jpeg.tobytes())

        ddb = boto3.resource("dynamodb", region_name=REGION)
        tables = {}
        for name, key_name in (("rw-incidents", "incident_id"), ("rw-jobs", "job_id")):
            tables[name] = ddb.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": key_name, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key_name, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
        tables["rw-jobs"].put_item(
            Item={"job_id": result.job_id, "camera_id": result.camera_id, "mode": result.mode.value}
        )
        sns = boto3.client("sns", region_name=REGION)
        topic = sns.create_topic(Name="rw-lifeguard-alerts")["TopicArn"]

        store = InMemoryDetectionStore([result])
        tool_deps = ToolDeps(
            store=store,
            s3=s3,
            ssm=boto3.client("ssm", region_name=REGION),
            rechecker=FixedRechecker(recheck),
            incidents=tables["rw-incidents"],
            jobs=tables["rw-jobs"],
            sns=sns,
            lifeguard_topic_arn=topic,
            artifacts_bucket=ARTIFACTS,
            now=lambda: T0 + timedelta(seconds=30),
        )
        sink = InMemoryTraceSink()
        llm = FakeLLM.scenario(script)
        async with Client(build_server(tool_deps)) as client:
            agent = Agent(
                AgentDeps(
                    llm=llm,
                    tools=McpTools(client),
                    store=store,
                    incidents=tables["rw-incidents"],
                    trace_sink=sink,
                    cooldown=Cooldown(),
                    limits=AgentLimits(),
                    clock=lambda: T0 + timedelta(seconds=30),
                )
            )
            yield agent, sink, tables, llm


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for key in ("AWS_PROFILE", "RW_AWS_ENDPOINT_URL", "RW_RUNTIME", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    get_settings.cache_clear()
    metrics.reset()
    yield
    metrics.reset()
    get_settings.cache_clear()


def _steps(sink: InMemoryTraceSink, key: str) -> list[tuple[str, str]]:
    return [(s.type.value, s.name) for s in sink.query(key)]


def _metric_names() -> list[str]:
    return [r.name for r in metrics.recorded()]


# ---------------------------------------------------------------- (a)


async def test_a_confident_rip_with_swimmer_is_zoomed_then_alerted(vision_result):
    result = _result(vision_result, "rip")
    async with _world(result, "confident_rip_alert", recheck=0.91) as (agent, sink, tables, _):
        decision = await agent.handle(_candidate(result))
        incident = tables["rw-incidents"].get_item(Key={"incident_id": decision.incident_id})[
            "Item"
        ]

    assert (
        decision.decision.value == "alert" and decision.requested_action.value == "raise_red_flag"
    )
    assert decision.risk_level.value == "CRITICAL" and not decision.used_fallback
    assert decision.tool_calls == 4 and decision.model_id == "fake-llm"
    assert _steps(sink, decision.incident_id) == [
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
    ]
    zoom = sink.query(decision.incident_id)[2].output_summary
    change = sink.query(decision.incident_id)[7]
    assert change.output_summary == {"from": "watching", "to": "alerted"}
    assert zoom["confidence"] == 0.91 and zoom["before_confidence"] == 0.82
    assert incident["status"] == "alerted" and incident["pending_action"] == "raise_red_flag"
    assert incident["last_decision"]["decision_id"] == decision.decision_id
    names = _metric_names()
    assert "IncidentsCreated" in names and "AgentFallbackUsed" not in names
    assert {"AgentToolCalls", "AgentDecisionLatencyMs", "BedrockTokens"} <= set(names)


# ---------------------------------------------------------------- (b)


async def test_b_uncertain_rip_drops_on_zoom_and_closes_as_false_alarm(vision_result):
    result = _result(vision_result, "uncertain")
    async with _world(result, "uncertain_false_alarm", recheck=0.12) as (agent, sink, tables, _):
        decision = await agent.handle(_candidate(result))
        incidents_count = tables["rw-incidents"].scan()["Count"]

    assert decision.decision.value == "close_false_alarm" and decision.incident_id is None
    assert _steps(sink, f"cand_{result.result_id}") == [
        ("candidate_received", "candidate"),
        ("llm_call", "fake-llm"),
        ("tool_call", "zoom_and_recheck"),
        ("llm_call", "fake-llm"),
        ("decision", "decision"),
    ]
    assert sink.query(f"cand_{result.result_id}")[2].output_summary["label"] == "clear"
    assert "FalseAlarmsRejected" in _metric_names()
    assert incidents_count == 0


# ---------------------------------------------------------------- (c)


async def test_c_image_mode_requests_a_followup_and_watches(vision_result):
    result = _result(vision_result, "image")
    async with _world(result, "image_followup") as (agent, sink, tables, _):
        decision = await agent.handle(_candidate(result))
        job = tables["rw-jobs"].get_item(Key={"job_id": result.job_id})["Item"]
        incident = tables["rw-incidents"].get_item(Key={"incident_id": decision.incident_id})[
            "Item"
        ]

    assert decision.decision.value == "watch" and decision.incident_id
    assert _steps(sink, decision.incident_id) == [
        ("candidate_received", "candidate"),
        ("llm_call", "fake-llm"),
        ("tool_call", "request_followup_capture"),
        ("llm_call", "fake-llm"),
        ("tool_call", "create_incident"),
        ("llm_call", "fake-llm"),
        ("tool_call", "set_watch"),
        ("llm_call", "fake-llm"),
        ("decision", "decision"),
    ]
    assert job["followup_request"]["message"].startswith("Please send a 10 second clip")
    assert incident["status"] == "watching" and incident["watch_until_clips"] == 2


# ---------------------------------------------------------------- (d)


async def test_d_bedrock_error_falls_back_to_the_rules(vision_result):
    result = _result(vision_result, "rip")  # swimmer inside the rip: pre-risk CRITICAL
    async with _world(result, "bedrock_error") as (agent, sink, tables, _):
        decision = await agent.handle(_candidate(result))
        incident = tables["rw-incidents"].get_item(Key={"incident_id": decision.incident_id})[
            "Item"
        ]

    assert decision.used_fallback and decision.decision.value == "alert"
    assert (
        decision.risk_level.value == "CRITICAL"
        and decision.requested_action.value == "raise_red_flag"
    )
    steps = _steps(sink, decision.incident_id)
    assert steps == [
        ("candidate_received", "candidate"),
        ("fallback", "fallback"),
        ("tool_call", "create_incident"),
        ("tool_call", "alert_lifeguard"),
        ("status_change", "lifecycle"),
        ("tool_call", "request_approval"),
        ("decision", "decision"),
    ]
    assert "ThrottlingException" in sink.query(decision.incident_id)[1].error
    assert "AgentFallbackUsed" in _metric_names()
    assert incident["status"] == "alerted"


# ---------------------------------------------------------------- (e)


async def test_e_tool_loop_hits_the_limit_and_falls_back(vision_result):
    result = _result(vision_result, "rip")
    async with _world(result, "tool_loop") as (agent, sink, _, llm):
        decision = await agent.handle(_candidate(result))

    assert decision.used_fallback and decision.decision.value == "alert"
    steps = _steps(sink, decision.incident_id)
    assert steps[:1] == [("candidate_received", "candidate")]
    assert steps[1:13] == [("llm_call", "fake-llm"), ("tool_call", "get_flow_stats")] * 6
    assert steps[13:15] == [("llm_call", "fake-llm"), ("fallback", "fallback")]
    assert steps[-1] == ("decision", "decision")
    assert "more than 6 tool calls" in sink.query(decision.incident_id)[14].error
    assert llm.calls == 7


# ---------------------------------------------------------------- (f)


async def test_f_duplicate_within_cooldown_is_suppressed_without_a_model_call(vision_result):
    result = _result(vision_result, "uncertain")
    async with _world(result, "uncertain_false_alarm", recheck=0.12) as (agent, sink, _, llm):
        first = await agent.handle(_candidate(result))
        calls_after_first = llm.calls
        second = await agent.handle(_candidate(result))

    assert first is not None and second is None
    assert llm.calls == calls_after_first
    assert _steps(sink, f"cand_{result.result_id}")[-1] == ("suppressed_duplicate", "cooldown")


# ---------------------------------------------------------------- extras


async def test_alert_without_approval_is_completed_by_the_loop(vision_result, tmp_path):
    result = _result(vision_result, "rip")
    (tmp_path / "alert_only.yaml").write_text(
        "turns:\n"
        "  - tool_use:\n"
        "      - name: submit_decision\n"
        "        input: {decision: alert, risk_level: HIGH, reasons: [obvious],\n"
        "                requested_action: pa_announcement}\n"
    )
    async with _world(result, "confident_rip_alert") as (agent, sink, tables, _):
        agent.deps.llm = FakeLLM.scenario("alert_only", tmp_path)
        decision = await agent.handle(_candidate(result))
        incident = tables["rw-incidents"].get_item(Key={"incident_id": decision.incident_id})[
            "Item"
        ]

    assert decision.decision.value == "alert" and not decision.used_fallback
    steps = _steps(sink, decision.incident_id)
    assert steps[-6:] == [
        ("tool_call", "create_incident"),
        ("tool_call", "request_approval"),
        ("status_change", "lifecycle"),
        ("tool_call", "alert_lifeguard"),
        ("status_change", "reconcile"),
        ("decision", "decision"),
    ]
    assert incident["pending_action"] == "pa_announcement"


# ---------------------------------------------------------------- follow-ups (D-06)

INCIDENT = "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"


def _followup(vision_result: dict, kind: str) -> VisionResult:
    payload = copy.deepcopy(vision_result)
    payload["result_id"] = new_id("res")
    if kind == "clear":
        payload["rips"], payload["swimmers"] = [], []
        payload["summary"].update(
            status="clear", max_confidence=0.0, rip_count=0, swimmer_count=0, swimmers_at_risk=0
        )
    return VisionResult.model_validate(payload)


def _followup_candidate(result: VisionResult) -> CandidateMessage:
    return _candidate(result, INCIDENT, "active_incident_followup")


def _open(tables, result: VisionResult, status: str, **fields) -> None:
    tables["rw-incidents"].put_item(
        Item={"incident_id": INCIDENT, "camera_id": result.camera_id, "status": status,
              "result_id": result.result_id, "trace_id": result.trace_id, **fields}
    )  # fmt: skip


async def test_watching_followup_resolves_by_rule_without_a_model_call(vision_result):
    first = _result(vision_result, "rip")
    async with _world(first, "bedrock_error") as (agent, sink, tables, llm):
        _open(tables, first, "watching", watch_until_clips=1, followup_clear_streak=2)
        clear = _followup(vision_result, "clear")
        agent.deps.store.put(clear)
        decision = await agent.handle(_followup_candidate(clear))
        row = tables["rw-incidents"].get_item(Key={"incident_id": INCIDENT})["Item"]

    assert llm.calls == 0
    assert decision.decision.value == "resolve" and decision.incident_id == INCIDENT
    assert decision.model_id is None and decision.tool_calls == 0 and not decision.used_fallback
    assert _steps(sink, INCIDENT) == [
        ("candidate_received", "candidate"),
        ("status_change", "lifecycle"),
        ("decision", "decision"),
    ]
    assert row["status"] == "resolved" and row["last_decision"]["decision"] == "resolve"


async def test_approved_incident_is_confirmed_by_two_followups(vision_result):
    first = _result(vision_result, "rip")
    async with _world(first, "bedrock_error") as (agent, sink, tables, llm):
        _open(tables, first, "approved", pending_action="raise_red_flag")
        decisions = []
        for _ in range(2):
            again = _followup(vision_result, "rip")
            agent.deps.store.put(again)
            decisions.append(await agent.handle(_followup_candidate(again)))
        row = tables["rw-incidents"].get_item(Key={"incident_id": INCIDENT})["Item"]

    assert llm.calls == 0 and [d.decision.value for d in decisions] == ["watch", "watch"]
    assert row["status"] == "confirmed"
    assert _steps(sink, INCIDENT) == [
        ("candidate_received", "candidate"),
        ("decision", "decision"),
        ("candidate_received", "candidate"),
        ("status_change", "lifecycle"),
        ("decision", "decision"),
    ]
    assert sink.query(INCIDENT)[3].output_summary == {"from": "approved", "to": "confirmed"}


async def test_watching_followup_with_clips_left_goes_to_the_model(vision_result):
    first = _result(vision_result, "rip")
    async with _world(first, "bedrock_error") as (agent, sink, tables, llm):
        _open(tables, first, "watching", watch_until_clips=3)
        again = _followup(vision_result, "rip")
        agent.deps.store.put(again)
        decision = await agent.handle(_followup_candidate(again))
        row = tables["rw-incidents"].get_item(Key={"incident_id": INCIDENT})["Item"]

    assert llm.calls == 1 and decision.used_fallback  # the model ran (and failed here)
    assert decision.incident_id == INCIDENT and row["status"] == "alerted"
    assert row["watch_until_clips"] == 2
    assert ("status_change", "lifecycle") in _steps(sink, INCIDENT)
