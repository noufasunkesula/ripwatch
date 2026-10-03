"""AgentDecision v1.0 (sprint-1.md section 7.3) and Approval v1.0 (section 7.5)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from rw.contracts.base import (
    CameraId,
    Contract,
    DecisionId,
    IncidentId,
    NonNegFloat,
    NonNegInt,
    ResultId,
    Timestamp,
    TraceId,
)


class DecisionKind(StrEnum):
    IGNORE = "ignore"
    WATCH = "watch"
    ALERT = "alert"
    RESOLVE = "resolve"
    CLOSE_FALSE_ALARM = "close_false_alarm"


class RiskLevel(StrEnum):
    LOW = "LOW"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Action(StrEnum):
    RAISE_RED_FLAG = "raise_red_flag"
    PA_ANNOUNCEMENT = "pa_announcement"
    DISPATCH_LIFEGUARD = "dispatch_lifeguard"


class AgentDecision(Contract):
    """Written by the agent as the last step of every trace."""

    schema_version: Literal["1.0"] = "1.0"
    decision_id: DecisionId
    trace_id: TraceId
    result_id: ResultId
    camera_id: CameraId
    incident_id: IncidentId | None
    decision: DecisionKind
    risk_level: RiskLevel
    reasons: Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=10)]
    requested_action: Action | None
    tool_calls: NonNegInt
    used_fallback: bool
    model_id: str | None
    input_tokens: NonNegInt
    output_tokens: NonNegInt
    latency_ms: NonNegInt
    created_at: Timestamp

    @model_validator(mode="after")
    def _consistency(self) -> AgentDecision:
        if self.decision == DecisionKind.ALERT and self.incident_id is None:
            raise ValueError("alert requires incident_id")
        if self.requested_action is not None and self.decision != DecisionKind.ALERT:
            raise ValueError("requested_action is only allowed with decision alert")
        return self


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Approval(Contract):
    """Row in rw-approvals, written by rw-api when a lifeguard approves or rejects."""

    schema_version: Literal["1.0"] = "1.0"
    incident_id: IncidentId
    ts: Timestamp
    action: Action
    decision: ApprovalDecision
    user_sub: Annotated[str, Field(min_length=1, max_length=128)]
    user_email: Annotated[str, Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=254)]
    reason: Annotated[str, Field(max_length=1000)] | None
    latency_s: NonNegFloat

    @model_validator(mode="after")
    def _reason_on_reject(self) -> Approval:
        if self.decision == ApprovalDecision.REJECT and not (self.reason and self.reason.strip()):
            raise ValueError("reason is required when decision is reject")
        return self
