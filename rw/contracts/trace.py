"""TraceStep v1.0 (sprint-1.md section 7.4): one row per step in rw-agent-trace."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from rw.contracts.base import (
    Contract,
    NonNegInt,
    Timestamp,
    TraceId,
    prefixed_id,
)
from rw.contracts.enums import StepType

# trace_key is the incident_id once an incident exists, else cand_<result_id>.
_TRACE_KEY = rf"(?:{prefixed_id('inc')[1:-1]}|cand_{prefixed_id('res')[1:-1]})"
TraceKey = Annotated[str, Field(pattern=rf"^{_TRACE_KEY}$")]


class TraceStep(Contract):
    schema_version: Literal["1.0"] = "1.0"
    trace_key: TraceKey
    step: Annotated[int, Field(ge=1)]
    trace_id: TraceId
    type: StepType
    name: Annotated[str, Field(min_length=1, max_length=128)]
    input: dict[str, Any]
    output_summary: dict[str, Any] | None
    reasoning_summary: Annotated[str, Field(max_length=2048)] | None
    latency_ms: NonNegInt
    error: Annotated[str, Field(max_length=2048)] | None
    created_at: Timestamp
