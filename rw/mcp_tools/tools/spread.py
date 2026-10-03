"""predict_spread: where a rip and nearby swimmers will be at a few future horizons.

Baseline method `linear_advection_v1` (sprint-1.md D-03), Saif can replace it:
  - the rip polygon moves seaward by its measured seaward flow speed and grows
    around its centroid by GROWTH_PER_MIN, both scaled by the ocean factor;
  - each swimmer's box centre moves with its drift;
  - a swimmer is predicted inside if its moved centre falls in the moved polygon.

Seaward is up the image (toward the horizon, -y) until cameras carry a
calibrated direction. Predicted polygons are clamped to the frame; `leaves_frame`
says when that clamping happened. Without motion data (image mode) the method
is `no_motion` and the rip and swimmers stay where they are.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from rw.contracts import VisionResult
from rw.contracts.base import CameraId, ResultId, RipId, TraceId, TrackId
from rw.mcp_tools.store import DetectionStore

METHOD = "linear_advection_v1"
NO_MOTION = "no_motion"
GROWTH_PER_MIN = 0.10
SEAWARD = (0.0, -1.0)
MAX_SWIMMERS = 10

Horizon = Annotated[int, Field(ge=1, le=900)]


class RipNotFound(LookupError):
    pass


class PredictSpreadInput(BaseModel):
    trace_id: TraceId
    result_id: ResultId
    camera_id: CameraId
    rip_id: RipId
    horizons_s: Annotated[list[Horizon], Field(min_length=1, max_length=5)] = [60, 180, 300]


class HorizonOut(BaseModel):
    horizon_s: int
    polygon_px: list[tuple[int, int]]
    swimmers_inside: list[TrackId]
    leaves_frame: bool


class PredictSpreadOutput(BaseModel):
    result_id: ResultId
    rip_id: RipId
    method: str
    ocean_factor: float
    horizons: list[HorizonOut]


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(points)
    return sum(x for x, _ in points) / n, sum(y for _, y in points) / n


def _inside(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """Ray casting point-in-polygon test."""
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _clamp(polygon: list[tuple[float, float]], width: int, height: int):
    clamped = [(min(max(round(x), 0), width), min(max(round(y), 0), height)) for x, y in polygon]
    leaves = any(not (0 <= x <= width and 0 <= y <= height) for x, y in polygon)
    return clamped, leaves


def predict_spread(
    store: DetectionStore, args: PredictSpreadInput, ocean_factor: float = 1.0
) -> PredictSpreadOutput:
    """Predicted rip outline and swimmers caught in it at future times.

    Use after a rip is confirmed, to judge how urgent it is: whether swimmers
    will be pulled in within the next few minutes. Returns, for each horizon
    in seconds (default 60, 180, 300), the predicted rip polygon in image
    pixels, the track_ids of swimmers predicted inside it, and whether the rip
    reaches the frame edge. `method` is "no_motion" when the result has no
    flow data (single images), in which case nothing moves.
    """
    result: VisionResult = store.get_result(args.camera_id, args.result_id)
    rip = next((r for r in result.rips if r.rip_id == args.rip_id), None)
    if rip is None:
        raise RipNotFound(f"rip {args.rip_id} not in result {args.result_id}")

    speed = rip.evidence.seaward_flow_px_per_s
    method = METHOD if speed is not None else NO_MOTION
    polygon = [(float(x), float(y)) for x, y in rip.polygon_px]
    cx, cy = _centroid(polygon)
    width, height = result.input.width, result.input.height

    horizons = []
    for t in args.horizons_s:
        if speed is None:
            moved = polygon
        else:
            shift = speed * t * ocean_factor
            dx, dy = SEAWARD[0] * shift, SEAWARD[1] * shift
            scale = 1.0 + GROWTH_PER_MIN * (t / 60.0) * ocean_factor
            moved = [(cx + (x - cx) * scale + dx, cy + (y - cy) * scale + dy) for x, y in polygon]
        clamped, leaves = _clamp(moved, width, height)

        inside = []
        for sw in result.swimmers:
            bx, by, bw, bh = sw.bbox_px
            px, py = bx + bw / 2, by + bh / 2
            if sw.drift_px_per_s is not None:
                px += sw.drift_px_per_s[0] * t
                py += sw.drift_px_per_s[1] * t
            if _inside((px, py), moved):
                inside.append(sw.track_id)

        horizons.append(
            HorizonOut(
                horizon_s=t,
                polygon_px=clamped,
                swimmers_inside=inside[:MAX_SWIMMERS],
                leaves_frame=leaves,
            )
        )

    return PredictSpreadOutput(
        result_id=result.result_id,
        rip_id=rip.rip_id,
        method=method,
        ocean_factor=ocean_factor,
        horizons=horizons,
    )
