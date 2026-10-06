"""The agent: one candidate in, one AgentDecision and a complete trace out (sprint-1.md D-05).

    candidate_received -> [suppressed_duplicate] | llm_call / tool_call ... -> [status_change]
                       -> [fallback] -> decision

The model works through MCP tools and ends by calling submit_decision. Hard limits: 6 MCP tool
calls, 8 model turns, 20 s per model call, 60 s in total. Any model error, limit hit or invalid
decision switches to the rule-based fallback, so the system never goes silent. After the model
decides, the loop checks the decision against what the tools actually did and makes up any
missing step itself (recorded as a status_change step).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio

from rw.agent import fallback
from rw.agent.cooldown import Cooldown
from rw.agent.llm import LLMClient, LLMError
from rw.agent.prompts import SYSTEM_PROMPT, summary_json, user_message
from rw.agent.risk import Risk, RiskThresholds, assess
from rw.agent.tools import SUBMIT_DECISION, SUBMIT_DECISION_SPEC, McpTools, ToolOutcome
from rw.agent.trace import TraceSink, TraceWriter
from rw.common.ids import new_id
from rw.common.metrics import emit
from rw.contracts import AgentDecision, CandidateMessage, VisionResult
from rw.contracts.decision import Action, DecisionKind, RiskLevel
from rw.contracts.trace import StepType
from rw.mcp_tools.store import DetectionStore


class LimitHit(RuntimeError):
    pass


class InvalidDecision(ValueError):
    pass


@dataclass(frozen=True)
class AgentLimits:
    max_tool_calls: int = 6
    max_turns: int = 8
    call_timeout_s: float = 20.0
    total_timeout_s: float = 60.0
    cooldown_s: float = 60.0


@dataclass
class AgentDeps:
    llm: LLMClient
    tools: McpTools
    store: DetectionStore
    incidents: Any  # rw-incidents Table: active incident in, last_decision out
    trace_sink: TraceSink
    cooldown: Cooldown = field(default_factory=Cooldown)
    rip_statement_active: Callable[[], bool] = lambda: False
    thresholds: RiskThresholds = field(default_factory=RiskThresholds)
    limits: AgentLimits = field(default_factory=AgentLimits)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


@dataclass
class _Run:
    """What happened during one candidate, for the decision and the reconciliation."""

    writer: TraceWriter
    ids: dict[str, str]
    incident_id: str | None
    tool_calls: int = 0
    model_tool_calls: int = 0
    called: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class Agent:
    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps

    # ------------------------------------------------------------ entry point

    async def handle(self, candidate: CandidateMessage) -> AgentDecision | None:
        """Decide on one candidate. None means it was suppressed as a duplicate."""
        d = self.deps
        started = time.perf_counter()
        now = d.clock()
        writer = TraceWriter(
            f"cand_{candidate.result_id}", candidate.trace_id, d.trace_sink, d.clock
        )
        writer.step(StepType.CANDIDATE_RECEIVED, "candidate", input=candidate)

        result = d.store.get_result(candidate.camera_id, candidate.result_id)
        incident = self._active_incident(candidate.active_incident_id)
        top = max(result.rips, key=lambda r: r.confidence, default=None)
        polygon = [tuple(p) for p in top.polygon_px] if top else None

        if incident is None and d.cooldown.is_duplicate(
            candidate.camera_id, polygon, now, d.limits.cooldown_s
        ):
            writer.step(
                StepType.SUPPRESSED_DUPLICATE,
                "cooldown",
                input={"camera_id": candidate.camera_id, "rip_id": top.rip_id if top else None},
                reasoning_summary=f"same rip decided within {d.limits.cooldown_s:g} s, no incident",
            )
            return None

        statement = d.rip_statement_active()
        risk = assess(result, statement, d.thresholds)
        ids = {
            "trace_id": candidate.trace_id,
            "camera_id": result.camera_id,
            "result_id": result.result_id,
        }
        run = _Run(writer, ids, incident["incident_id"] if incident else None)

        used_fallback = False
        try:
            with anyio.fail_after(d.limits.total_timeout_s):
                choice = await self._model_loop(candidate, result, incident, risk, statement, run)
            kind, level, reasons, action = self._parse(choice)
            await self._reconcile(kind, level, action, result, run)
        except (LLMError, LimitHit, InvalidDecision, TimeoutError) as exc:
            used_fallback = True
            writer.step(
                StepType.FALLBACK,
                "fallback",
                input={"reason": f"{type(exc).__name__}: {exc}", "pre_risk": risk.level.value},
                error=exc,
            )
            emit("AgentFallbackUsed", 1)
            outcome = await fallback.apply(
                risk.level, ids, run.incident_id, "; ".join(risk.reasons), self._caller(run)
            )
            kind, level, reasons, action = (
                outcome.decision, risk.level, outcome.reasons, outcome.requested_action
            )  # fmt: skip
            run.incident_id = outcome.incident_id

        if kind != DecisionKind.ALERT:
            action = None
        if kind == DecisionKind.ALERT and run.incident_id is None:
            kind, action = DecisionKind.WATCH, None  # cannot alert without an incident
            reasons = [*reasons, "alert downgraded: no incident could be created"]

        d.cooldown.record(candidate.camera_id, polygon, now)
        if run.incident_id:
            writer.rekey(run.incident_id)

        decision = AgentDecision(
            decision_id=new_id("dec"),
            trace_id=candidate.trace_id,
            result_id=result.result_id,
            camera_id=result.camera_id,
            incident_id=run.incident_id,
            decision=kind,
            risk_level=level,
            reasons=[r[:500] for r in reasons][:10],
            requested_action=action,
            tool_calls=run.tool_calls,
            used_fallback=used_fallback,
            model_id=d.llm.model_id,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000),
            created_at=d.clock(),
        )
        writer.step(StepType.DECISION, "decision", output_summary=decision)
        if run.incident_id:
            d.incidents.update_item(
                Key={"incident_id": run.incident_id},
                UpdateExpression="SET last_decision = :d",
                ExpressionAttributeValues={":d": decision.model_dump(mode="json")},
            )
        emit("AgentToolCalls", run.tool_calls)
        emit("AgentDecisionLatencyMs", decision.latency_ms, "Milliseconds")
        emit("BedrockTokens", run.input_tokens + run.output_tokens)
        return decision

    # ------------------------------------------------------------ parts

    def _active_incident(self, incident_id: str | None) -> dict | None:
        if incident_id is None:
            return None
        item = self.deps.incidents.get_item(Key={"incident_id": incident_id}).get("Item")
        if item is None or item.get("status") not in ("watching", "alerted", "approved"):
            return None
        return item

    def _caller(self, run: _Run) -> Callable[[str, dict[str, Any]], Any]:
        async def call(name: str, arguments: dict[str, Any]) -> ToolOutcome:
            started = time.perf_counter()
            outcome = await self.deps.tools.call(name, arguments)
            run.tool_calls += 1
            run.called.append(name)
            if name == "create_incident" and outcome.ok:
                run.incident_id = outcome.data.get("incident_id", run.incident_id)
            run.writer.step(
                StepType.TOOL_CALL,
                name,
                input=arguments,
                output_summary=outcome.data if outcome.ok else None,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=None if outcome.ok else outcome.text or "tool error",
            )
            return outcome

        return call

    async def _model_loop(
        self,
        candidate: CandidateMessage,
        result: VisionResult,
        incident: dict | None,
        risk: Risk,
        statement: bool,
        run: _Run,
    ) -> dict[str, Any]:
        d = self.deps
        summary = summary_json(candidate, result, incident, risk, statement)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": user_message(summary)}]}
        ]
        tool_config = {"tools": [*(await d.tools.specs()), SUBMIT_DECISION_SPEC]}
        call = self._caller(run)

        for _ in range(d.limits.max_turns):
            started = time.perf_counter()
            with anyio.fail_after(d.limits.call_timeout_s):
                response = await anyio.to_thread.run_sync(
                    d.llm.converse, messages, SYSTEM_PROMPT, tool_config, abandon_on_cancel=True
                )
            usage = response.get("usage", {})
            run.input_tokens += int(usage.get("inputTokens", 0))
            run.output_tokens += int(usage.get("outputTokens", 0))
            message = response["output"]["message"]
            uses = [c["toolUse"] for c in message.get("content", []) if "toolUse" in c]
            text = " ".join(c["text"] for c in message.get("content", []) if "text" in c)
            run.writer.step(
                StepType.LLM_CALL,
                d.llm.model_id,
                output_summary={
                    "stop_reason": response.get("stopReason"),
                    "tools": [u["name"] for u in uses],
                },
                reasoning_summary=text or None,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            messages.append(message)
            if not uses:
                raise InvalidDecision("the model stopped without calling submit_decision")

            results = []
            for use in uses:
                if use["name"] == SUBMIT_DECISION:
                    return use.get("input", {})
                run.model_tool_calls += 1
                if run.model_tool_calls > d.limits.max_tool_calls:
                    raise LimitHit(f"more than {d.limits.max_tool_calls} tool calls")
                outcome = await call(use["name"], use.get("input", {}))
                block = {"json": outcome.data} if outcome.ok else {"text": outcome.text or "error"}
                results.append(
                    {
                        "toolResult": {
                            "toolUseId": use["toolUseId"],
                            "content": [block],
                            "status": "success" if outcome.ok else "error",
                        }
                    }
                )
            messages.append({"role": "user", "content": results})
        raise LimitHit(f"no decision after {d.limits.max_turns} model turns")

    @staticmethod
    def _parse(choice: dict[str, Any]) -> tuple[DecisionKind, RiskLevel, list[str], Action | None]:
        try:
            kind = DecisionKind(choice["decision"])
            level = RiskLevel(choice["risk_level"])
            reasons = [str(r) for r in choice.get("reasons") or []][:3]
            raw_action = choice.get("requested_action")
            action = Action(raw_action) if raw_action else None
        except (KeyError, ValueError) as exc:
            raise InvalidDecision(f"submit_decision input is invalid: {exc}") from exc
        return kind, level, reasons, action

    async def _reconcile(
        self,
        kind: DecisionKind,
        level: RiskLevel,
        action: Action | None,
        result: VisionResult,
        run: _Run,
    ) -> None:
        """Make the tool side effects match the decision; record any step added here."""
        call = self._caller(run)
        trace = {"trace_id": run.ids["trace_id"]}
        added: list[str] = []

        async def ensure_incident() -> None:
            if run.incident_id is None:
                out = await call(
                    "create_incident",
                    {**trace, "camera_id": run.ids["camera_id"], "result_id": run.ids["result_id"],
                     "risk_level": level.value, "summary": f"{kind.value} by agent"},
                )  # fmt: skip
                if out.ok:
                    added.append("create_incident")

        if kind == DecisionKind.ALERT:
            await ensure_incident()
            if run.incident_id and "request_approval" not in run.called:
                await call(
                    "request_approval",
                    {**trace, "incident_id": run.incident_id,
                     "action": (action or Action.RAISE_RED_FLAG).value,
                     "message": "Approve the recommended action?"},
                )  # fmt: skip
                added.append("request_approval")
            if run.incident_id and "alert_lifeguard" not in run.called:
                await call(
                    "alert_lifeguard",
                    {**trace, "incident_id": run.incident_id,
                     "message": f"{level.value}: possible rip current at {run.ids['camera_id']}."},
                )  # fmt: skip
                added.append("alert_lifeguard")
        elif kind == DecisionKind.WATCH:
            await ensure_incident()
            if run.incident_id and "set_watch" not in run.called:
                await call(
                    "set_watch",
                    {**trace, "incident_id": run.incident_id, "clips": fallback.WATCH_CLIPS,
                     "reason": "watch decided by agent"},
                )  # fmt: skip
                added.append("set_watch")
        elif kind == DecisionKind.CLOSE_FALSE_ALARM:
            if run.incident_id and "close_incident" not in run.called:
                await call(
                    "close_incident",
                    {**trace, "incident_id": run.incident_id, "outcome": "false_alarm",
                     "reason": "agent decided false alarm"},
                )  # fmt: skip
                added.append("close_incident")
            elif run.incident_id is None:
                emit("FalseAlarmsRejected", 1)  # no incident to close; still a rejected candidate
        elif (
            kind == DecisionKind.RESOLVE and run.incident_id and "close_incident" not in run.called
        ):
            await call(
                "close_incident",
                {**trace, "incident_id": run.incident_id, "outcome": "resolved",
                 "reason": "agent decided resolved"},
            )  # fmt: skip
            added.append("close_incident")

        if added:
            run.writer.step(
                StepType.STATUS_CHANGE,
                "reconcile",
                input={"decision": kind.value, "added": added},
                reasoning_summary="decision needed steps the model skipped; the loop added them",
            )
