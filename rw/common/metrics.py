"""CloudWatch metrics in Embedded Metric Format (sprint-1.md N-04, north star 14.2).

Sinks, picked once per process (or set with `configure`):
- `agent`:  worker (RW_RUNTIME set). aws-embedded-metrics agent sink, CloudWatch agent on
            tcp://127.0.0.1:25888 (override with AWS_EMF_AGENT_ENDPOINT),
            log group /rw/worker/<service>.
- `stdout`: Lambda (AWS_LAMBDA_FUNCTION_NAME set). EMF JSON printed to stdout.
- `memory`: tests and local runs. Kept in `recorded()`; unknown metric names raise.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

from aws_embedded_metrics.logger.metrics_context import MetricsContext
from aws_embedded_metrics.serializers.log_serializer import LogSerializer

from rw.common.config import get_settings

log = logging.getLogger(__name__)

Sink = Literal["agent", "stdout", "memory"]

# North star 14.2: the only metric names we publish (keeps CloudWatch inside the free tier).
METRICS: frozenset[str] = frozenset(
    {
        "FrameLatencyMs",
        "FramesPerSecond",
        "RipCandidates",
        "FalseAlarmsRejected",
        "IncidentsCreated",
        "AgentToolCalls",
        "AgentDecisionLatencyMs",
        "AgentFallbackUsed",
        "BedrockTokens",
        "JobsProcessed",
        "ApprovalLatencySec",
        "CostPerCameraHour",
    }
)
UNITS: frozenset[str] = frozenset({"Milliseconds", "Seconds", "Count", "Count/Second", "None"})


class UnknownMetricError(ValueError):
    """A metric name or unit outside north star 14.2."""


@dataclass(frozen=True)
class Recorded:
    name: str
    value: float
    unit: str
    dimensions: dict[str, str] = field(default_factory=dict)


_state: dict[str, object] = {"sink": None, "service": "rw"}
_recorded: list[Recorded] = []


def _detect_sink() -> Sink:
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "stdout"
    if get_settings().runtime is not None:
        return "agent"
    return "memory"


def configure(service: str, sink: Sink | None = None) -> Sink:
    """Set the service name (worker log group) and sink. Auto-detects the sink when None."""
    _state["service"] = service
    _state["sink"] = sink or _detect_sink()
    return _state["sink"]  # type: ignore[return-value]


def recorded() -> list[Recorded]:
    """Metrics collected by the memory sink, oldest first."""
    return list(_recorded)


def reset() -> None:
    """Clear recorded metrics and forget the sink (tests)."""
    _recorded.clear()
    _state["sink"] = None
    _state["service"] = "rw"


def _context(name: str, value: float, unit: str, dims: dict[str, str]) -> MetricsContext:
    ctx = MetricsContext(
        namespace=get_settings().metric_namespace,
        properties={},
        # [{}] serializes to [[]]: the EMF form for a metric with no dimensions.
        dimensions=[dims],
        default_dimensions={},
    )
    ctx.should_use_default_dimensions = False
    ctx.put_metric(name, value, unit)
    return ctx


def emit(
    name: str, value: float, unit: str = "Count", dimensions: Mapping[str, str] | None = None
) -> None:
    """Publish one metric value with optional dimensions."""
    sink: Sink = _state["sink"] or configure(str(_state["service"]))  # type: ignore[assignment]
    dims = {str(k): str(v) for k, v in (dimensions or {}).items()}
    if name not in METRICS or unit not in UNITS:
        problem = f"metric {name!r} unit {unit!r} not allowed (north star 14.2)"
        if sink == "memory":
            raise UnknownMetricError(problem)
        log.warning("dropped metric: %s", problem)
        return

    if sink == "memory":
        _recorded.append(Recorded(name, float(value), unit, dims))
        return
    ctx = _context(name, value, unit, dims)
    try:
        if sink == "stdout":
            for line in LogSerializer.serialize(ctx):
                sys.stdout.write(line + "\n")
            sys.stdout.flush()
        else:
            _agent_sink().accept(ctx)
    except OSError as exc:  # Metrics must never break the pipeline.
        log.warning("metric %s not sent: %s", name, exc)


_agent: dict[str, object] = {}


def _agent_sink():  # noqa: ANN202
    from aws_embedded_metrics.sinks.agent_sink import AgentSink

    group = f"/rw/worker/{_state['service']}"
    if _agent.get("group") != group:
        _agent["group"] = group
        _agent["sink"] = AgentSink(log_group_name=group)
    return _agent["sink"]


@contextmanager
def timed(stage: str, metric: str = "FrameLatencyMs") -> Iterator[None]:
    """Time a block (or decorated function) and emit milliseconds with `stage` and `runtime`.

    Usable as `with timed("flow"):` or `@timed("flow")`.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        runtime = get_settings().runtime or "local"
        emit(metric, elapsed_ms, "Milliseconds", {"stage": stage, "runtime": runtime})
