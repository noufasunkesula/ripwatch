"""RipWatch message contracts. See docs/contracts/README.md before changing anything here."""

from rw.contracts.candidate import CandidateMessage, CandidateReason
from rw.contracts.decision import (
    Action,
    AgentDecision,
    Approval,
    ApprovalDecision,
    DecisionKind,
    RiskLevel,
)
from rw.contracts.trace import StepType, TraceStep
from rw.contracts.vision import (
    Job,
    JobSource,
    JobStatus,
    Mode,
    RipLabel,
    RuntimeVariant,
    Status,
    VisionResult,
    classify,
)

__all__ = [
    "Action",
    "AgentDecision",
    "Approval",
    "ApprovalDecision",
    "CandidateMessage",
    "CandidateReason",
    "DecisionKind",
    "Job",
    "JobSource",
    "JobStatus",
    "Mode",
    "RipLabel",
    "RiskLevel",
    "RuntimeVariant",
    "Status",
    "StepType",
    "TraceStep",
    "VisionResult",
    "classify",
]
