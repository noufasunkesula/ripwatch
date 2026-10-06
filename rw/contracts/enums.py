"""Contract enums, stdlib only (sprint-1.md D-07 shared code note).

rw-api packages this file into its Lambda zip as rw_shared/enums.py, so it must not import pydantic
or anything from rw. The contract models import their enums from here.
"""

from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    VIDEO = "video"
    BURST = "burst"
    IMAGE = "image"


class RuntimeVariant(StrEnum):
    COOL = "cool"
    STD_ARM = "std-arm"
    STD_X86 = "std-x86"


class Status(StrEnum):
    RIP = "rip"
    UNCERTAIN = "uncertain"
    CLEAR = "clear"


class RipLabel(StrEnum):
    RIP = "rip"
    UNCERTAIN = "uncertain"


class JobSource(StrEnum):
    CAMERA_SIM = "camera_sim"
    UPLOAD = "upload"


class JobStatus(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class CandidateReason(StrEnum):
    STATUS_RIP = "status_rip"
    STATUS_UNCERTAIN = "status_uncertain"
    ACTIVE_INCIDENT_FOLLOWUP = "active_incident_followup"


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


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class StepType(StrEnum):
    CANDIDATE_RECEIVED = "candidate_received"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    DECISION = "decision"
    STATUS_CHANGE = "status_change"
    FALLBACK = "fallback"
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"
    HUMAN_APPROVAL = "human_approval"
