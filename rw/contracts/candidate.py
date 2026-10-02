"""CandidateMessage v1.0 (sprint-1.md section 7.2): body of each rw-candidates SQS message."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import model_validator

from rw.contracts.base import (
    CameraId,
    Confidence,
    Contract,
    IncidentId,
    NonNegInt,
    ResultId,
    Timestamp,
    TraceId,
)
from rw.contracts.vision import Mode, Status


class CandidateReason(StrEnum):
    STATUS_RIP = "status_rip"
    STATUS_UNCERTAIN = "status_uncertain"
    ACTIVE_INCIDENT_FOLLOWUP = "active_incident_followup"


class CandidateMessage(Contract):
    """Small pointer to a VisionResult; the agent loads the full result from rw-detections."""

    schema_version: Literal["1.0"] = "1.0"
    result_id: ResultId
    trace_id: TraceId
    camera_id: CameraId
    mode: Mode
    status: Status
    max_confidence: Confidence
    swimmers_at_risk: NonNegInt
    active_incident_id: IncidentId | None
    reason: CandidateReason
    created_at: Timestamp

    @model_validator(mode="after")
    def _reason_matches(self) -> CandidateMessage:
        if self.reason == CandidateReason.ACTIVE_INCIDENT_FOLLOWUP:
            if self.active_incident_id is None:
                raise ValueError("active_incident_followup requires active_incident_id")
        elif self.reason == CandidateReason.STATUS_RIP and self.status != Status.RIP:
            raise ValueError("reason status_rip requires status rip")
        elif self.reason == CandidateReason.STATUS_UNCERTAIN and self.status != Status.UNCERTAIN:
            raise ValueError("reason status_uncertain requires status uncertain")
        return self
