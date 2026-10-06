"""track_swimmers: who is in or near a rip in one vision result."""

from __future__ import annotations

from pydantic import BaseModel

from rw.contracts.base import CameraId, ResultId, RipId, TraceId, TrackId
from rw.mcp_tools.store import DetectionStore

MAX_SWIMMERS = 10


class TrackSwimmersInput(BaseModel):
    trace_id: TraceId
    result_id: ResultId
    camera_id: CameraId


class SwimmerOut(BaseModel):
    track_id: TrackId
    in_rip_id: RipId | None
    distance_to_rip_px: float | None
    distance_to_rip_m: float | None
    drift_px_per_s: tuple[float, float] | None
    confidence: float


class TrackSwimmersOutput(BaseModel):
    result_id: ResultId
    camera_id: CameraId
    swimmer_count: int
    swimmers_at_risk: int
    swimmers: list[SwimmerOut]
    truncated: bool


def _risk_order(sw: SwimmerOut) -> tuple[int, float]:
    # Swimmers inside a rip first, then nearest to a rip, unknown distance last.
    in_rip = 0 if sw.in_rip_id is not None else 1
    distance = sw.distance_to_rip_px if sw.distance_to_rip_px is not None else float("inf")
    return in_rip, distance


def track_swimmers(store: DetectionStore, args: TrackSwimmersInput) -> TrackSwimmersOutput:
    """Swimmers in a vision result, most at risk first.

    Use when a rip is detected and you need to know whether people are in or
    near it. Returns each swimmer's track_id, the rip they are inside (if any),
    distance to the nearest rip, drift velocity (null in image mode) and the
    number of swimmers at risk. At most 10 swimmers are listed; `truncated` is
    true when there were more.
    """
    result = store.get_result(args.camera_id, args.result_id)
    swimmers = sorted(
        (
            SwimmerOut(
                track_id=sw.track_id,
                in_rip_id=sw.in_rip_id,
                distance_to_rip_px=sw.distance_to_rip_px,
                distance_to_rip_m=sw.distance_to_rip_m,
                drift_px_per_s=sw.drift_px_per_s,
                confidence=sw.confidence,
            )
            for sw in result.swimmers
        ),
        key=_risk_order,
    )
    return TrackSwimmersOutput(
        result_id=result.result_id,
        camera_id=result.camera_id,
        swimmer_count=len(swimmers),
        swimmers_at_risk=result.summary.swimmers_at_risk,
        swimmers=swimmers[:MAX_SWIMMERS],
        truncated=len(swimmers) > MAX_SWIMMERS,
    )
