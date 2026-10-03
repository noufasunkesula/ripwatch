"""get_flow_stats: how strong and how persistent each rip has been on a camera recently."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from statistics import mean
from typing import Annotated

from pydantic import BaseModel, Field

from rw.contracts import VisionResult
from rw.contracts.base import CameraId, RipId, TraceId
from rw.contracts.vision import Rip
from rw.mcp_tools.store import DetectionStore

TREND_TOLERANCE = 0.10
MAX_RIPS = 10


class FlowTrend(StrEnum):
    RISING = "rising"
    STEADY = "steady"
    FALLING = "falling"
    UNKNOWN = "unknown"


class FlowStatsInput(BaseModel):
    trace_id: TraceId
    camera_id: CameraId
    window_s: Annotated[int, Field(ge=30, le=300)] = 120


class RipFlowStats(BaseModel):
    rip_id: RipId
    clips_seen: int
    mean_flow_px_per_s: float | None
    max_flow_px_per_s: float | None
    mean_flow_m_per_s: float | None
    persist_s: float
    trend: FlowTrend
    last_confidence: float


class FlowStatsOutput(BaseModel):
    camera_id: CameraId
    window_s: int
    clips_seen: int
    rips: list[RipFlowStats]
    truncated: bool


def trend(flows: list[float]) -> FlowTrend:
    """Compare the mean of the older half of the window with the newer half."""
    if len(flows) < 2:
        return FlowTrend.UNKNOWN
    half = len(flows) // 2
    older, newer = mean(flows[:half]), mean(flows[half:])
    if newer > older * (1 + TREND_TOLERANCE) and newer > 0:
        return FlowTrend.RISING
    if newer < older * (1 - TREND_TOLERANCE):
        return FlowTrend.FALLING
    return FlowTrend.STEADY


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _stats(sightings: list[tuple[VisionResult, Rip]]) -> RipFlowStats:
    flows = [r.evidence.seaward_flow_px_per_s for _, r in sightings]
    flows = [f for f in flows if f is not None]
    flows_m = [r.evidence.seaward_flow_m_per_s for _, r in sightings]
    flows_m = [f for f in flows_m if f is not None]
    last_result, last_rip = sightings[-1]
    persist = last_rip.persist_s
    if persist is None:
        persist = max(0.0, (last_result.input.end_ts - last_rip.first_seen_ts).total_seconds())
    return RipFlowStats(
        rip_id=last_rip.rip_id,
        clips_seen=len(sightings),
        mean_flow_px_per_s=_round(mean(flows)) if flows else None,
        max_flow_px_per_s=_round(max(flows)) if flows else None,
        mean_flow_m_per_s=_round(mean(flows_m)) if flows_m else None,
        persist_s=round(persist, 1),
        trend=trend(flows),
        last_confidence=last_rip.confidence,
    )


def get_flow_stats(
    store: DetectionStore,
    args: FlowStatsInput,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FlowStatsOutput:
    """Seaward flow and persistence of each rip on a camera over a recent window.

    Use to tell a real, lasting rip from a one-clip blip, and whether it is
    getting stronger. window_s is 30 to 300 seconds (default 120). For each rip
    seen in the window: clips seen, mean and max seaward flow, how long it has
    persisted, trend ("rising", "steady", "falling", or "unknown" with fewer
    than 2 flow readings) and its latest confidence. Strongest rips first, at
    most 10.
    """
    results = store.results_since(args.camera_id, now() - timedelta(seconds=args.window_s))

    by_rip: dict[str, list[tuple[VisionResult, Rip]]] = {}
    for result in results:
        for rip in result.rips:
            by_rip.setdefault(rip.rip_id, []).append((result, rip))

    stats = sorted(
        (_stats(sightings) for sightings in by_rip.values()),
        key=lambda s: (s.max_flow_px_per_s is None, -(s.max_flow_px_per_s or 0.0)),
    )
    return FlowStatsOutput(
        camera_id=args.camera_id,
        window_s=args.window_s,
        clips_seen=len(results),
        rips=stats[:MAX_RIPS],
        truncated=len(stats) > MAX_RIPS,
    )
