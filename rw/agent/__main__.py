"""python -m rw.agent: the rw-agent service (sprint-1.md D-05).

Long-polls rw-candidates (wait 20 s, max 1, visibility 120 s), runs the Agent through rw-mcp-tools
on 127.0.0.1:8765, and deletes a message only after its decision is written. A message that is not
a valid CandidateMessage, or whose handling crashes, is left for SQS to retry and then the DLQ.

Live settings come from SSM: /rw/bedrock/model_id, /rw/agent/cooldown_s, /rw/agent/max_tool_calls,
/rw/risk/thresholds. RW_LLM=fake replays RW_FAKE_SCENARIO (default confident_rip_alert).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import anyio
from mcp import Client
from pydantic import ValidationError

from rw.agent.cooldown import Cooldown
from rw.agent.fake_llm import FakeLLM
from rw.agent.llm import BedrockLLM, LLMClient
from rw.agent.loop import Agent, AgentDeps, AgentLimits
from rw.agent.risk import RiskThresholds
from rw.agent.tools import McpTools
from rw.agent.trace import DynamoTraceSink
from rw.common.aws import client, resource
from rw.common.config import Settings, get_settings
from rw.common.heartbeat import beat
from rw.common.logging import setup_logging
from rw.common.metrics import configure
from rw.contracts import CandidateMessage
from rw.mcp_tools.server import HOST, PORT
from rw.mcp_tools.store import DynamoDetectionStore
from rw.mcp_tools.tools.ocean import RIP_ALERT_EVENTS, OceanSnapshot

SERVICE = "rw-agent"
log = logging.getLogger(SERVICE)


def ssm_values(ssm: Any, prefix: str) -> dict[str, str]:
    """Every /rw/... parameter as {relative name: value}; missing ones fall back to defaults."""
    values, kwargs = {}, {"Path": prefix, "Recursive": True}
    while True:
        page = ssm.get_parameters_by_path(**kwargs)
        for p in page.get("Parameters", []):
            values[p["Name"][len(prefix) :].lstrip("/")] = p["Value"]
        if "NextToken" not in page:
            return values
        kwargs["NextToken"] = page["NextToken"]


def rip_statement_reader(ssm: Any, prefix: str):
    """True when the ocean snapshot (rw-ocean-poller) holds a rip current or beach hazards alert."""

    def active() -> bool:
        try:
            raw = ssm.get_parameter(Name=f"{prefix}/ocean/latest")["Parameter"]["Value"]
            snapshot = OceanSnapshot.model_validate_json(raw)
        except Exception:  # no snapshot yet, or unreadable: no statement known
            return False
        return any(a.event.strip().lower() in RIP_ALERT_EVENTS for a in snapshot.alerts)

    return active


def make_llm(settings: Settings, model_id: str) -> LLMClient:
    if settings.llm == "fake":
        return FakeLLM.scenario(os.environ.get("RW_FAKE_SCENARIO", "confident_rip_alert"))
    return BedrockLLM(model_id)


async def run_once(agent: Agent, sqs: Any, queue_url: str) -> int:
    """Handle at most one message. Returns how many were deleted (0 or 1)."""
    response = sqs.receive_message(
        QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20, VisibilityTimeout=120
    )
    deleted = 0
    for message in response.get("Messages", []):
        try:
            candidate = CandidateMessage.model_validate_json(message["Body"])
        except ValidationError:
            log.error("invalid_candidate", extra={"message_id": message["MessageId"]})
            continue  # retried, then the DLQ keeps it for a human
        decision = await agent.handle(candidate)
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
        deleted += 1
        log.info(
            "candidate_done",
            extra={
                "trace_id": candidate.trace_id,
                "camera_id": candidate.camera_id,
                "decision": decision.decision.value if decision else "suppressed_duplicate",
                "incident_id": decision.incident_id if decision else None,
            },
        )
    return deleted


async def serve() -> None:
    settings = get_settings()
    ssm = client("ssm")
    params = ssm_values(ssm, settings.ssm_prefix)
    model_id = params.get("bedrock/model_id", settings.bedrock_model_id)
    limits = AgentLimits(
        max_tool_calls=int(params.get("agent/max_tool_calls", 6)),
        cooldown_s=float(params.get("agent/cooldown_s", 60)),
    )
    dynamodb = resource("dynamodb")
    sqs = client("sqs")
    queue_url = settings.require("queue_candidates_url")
    async with Client(f"http://{HOST}:{PORT}/mcp") as mcp_client:
        agent = Agent(
            AgentDeps(
                llm=make_llm(settings, model_id),
                tools=McpTools(mcp_client),
                store=DynamoDetectionStore(dynamodb.Table(settings.table_detections)),
                incidents=dynamodb.Table(settings.table_incidents),
                trace_sink=DynamoTraceSink(dynamodb.Table(settings.table_trace)),
                cooldown=Cooldown(),
                rip_statement_active=rip_statement_reader(ssm, settings.ssm_prefix),
                thresholds=RiskThresholds.from_json(params.get("risk/thresholds")),
                limits=limits,
                clock=lambda: datetime.now(UTC),
            )
        )
        log.info("agent_ready", extra={"model_id": model_id, "llm": settings.llm})
        while True:
            beat(SERVICE)
            await run_once(agent, sqs, queue_url)


def main() -> None:
    setup_logging(SERVICE)
    configure(SERVICE)
    anyio.run(serve)


if __name__ == "__main__":
    main()
