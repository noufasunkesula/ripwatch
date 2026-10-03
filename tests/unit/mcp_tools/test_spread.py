from __future__ import annotations

import pytest
from pydantic import ValidationError

from rw.contracts import VisionResult
from rw.mcp_tools.store import InMemoryDetectionStore
from rw.mcp_tools.tools.spread import (
    METHOD,
    NO_MOTION,
    PredictSpreadInput,
    RipNotFound,
    predict_spread,
)

RIP_ID = "cam-01-rip-0007"


def _setup(vision_result: dict, speed: float | None = 0.1, **swimmer):
    vision_result["rips"][0]["evidence"]["seaward_flow_px_per_s"] = speed
    vision_result["swimmers"][0].update(swimmer)
    result = VisionResult.model_validate(vision_result)
    args = PredictSpreadInput(
        trace_id=result.trace_id,
        result_id=result.result_id,
        camera_id=result.camera_id,
        rip_id=RIP_ID,
    )
    return InMemoryDetectionStore([result]), args


def _mean_y(polygon) -> float:
    return sum(y for _, y in polygon) / len(polygon)


def _area(polygon) -> float:
    pts = list(polygon)
    return abs(
        sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1], strict=True))
        / 2
    )


def test_default_horizons_move_rip_seaward(vision_result):
    store, args = _setup(vision_result, speed=0.1)
    start = _mean_y(vision_result["rips"][0]["polygon_px"])

    out = predict_spread(store, args)

    assert out.method == METHOD
    assert [h.horizon_s for h in out.horizons] == [60, 180, 300]
    ys = [_mean_y(h.polygon_px) for h in out.horizons]
    assert ys[0] == pytest.approx(start - 6, abs=1)
    assert ys[0] > ys[1] > ys[2]
    assert not any(h.leaves_frame for h in out.horizons)


def test_rip_grows_over_time(vision_result):
    store, args = _setup(vision_result, speed=0.0)
    start = _area(vision_result["rips"][0]["polygon_px"])

    out = predict_spread(store, args)

    areas = [_area(h.polygon_px) for h in out.horizons]
    assert start < areas[0] < areas[1] < areas[2]
    # 300 s at 10%/min: scale 1.5, area x2.25
    assert areas[2] == pytest.approx(start * 2.25, rel=0.05)


def test_ocean_factor_pushes_further(vision_result):
    store, args = _setup(vision_result, speed=0.1)

    calm = predict_spread(store, args, ocean_factor=1.0)
    rough = predict_spread(store, args, ocean_factor=1.5)

    assert rough.ocean_factor == 1.5
    assert _mean_y(rough.horizons[0].polygon_px) < _mean_y(calm.horizons[0].polygon_px)


def test_swimmer_drifting_into_rip_is_predicted_inside(vision_result):
    # Starts ~30 px left of the rip, drifts right at 0.3 px/s: outside at 60 s, inside by 180 s.
    store, args = _setup(
        vision_result,
        speed=0.0,
        bbox_px=[270, 200, 12, 18],
        in_rip_id=None,
        distance_to_rip_px=30.0,
        drift_px_per_s=[0.3, 0.0],
    )

    out = predict_spread(store, args)

    assert out.horizons[0].swimmers_inside == []
    assert out.horizons[1].swimmers_inside == ["cam-01-sw-0012"]


def test_fast_flow_clamps_to_frame(vision_result):
    store, args = _setup(vision_result, speed=6.4)

    out = predict_spread(store, args)

    assert all(h.leaves_frame for h in out.horizons)
    for h in out.horizons:
        assert all(0 <= x <= 640 and 0 <= y <= 360 for x, y in h.polygon_px)


def test_image_mode_has_no_motion(vision_result):
    vision_result["mode"] = "image"
    store, args = _setup(vision_result, speed=None, drift_px_per_s=None)

    out = predict_spread(store, args)

    assert out.method == NO_MOTION
    original = [tuple(p) for p in vision_result["rips"][0]["polygon_px"]]
    assert all(h.polygon_px == original for h in out.horizons)
    assert all(h.swimmers_inside == ["cam-01-sw-0012"] for h in out.horizons)


def test_unknown_rip_raises(vision_result):
    store, args = _setup(vision_result)

    with pytest.raises(RipNotFound):
        predict_spread(store, args.model_copy(update={"rip_id": "cam-01-rip-9999"}))


@pytest.mark.parametrize("horizons", [[], [0], [901], [60, 120, 180, 240, 300, 360]])
def test_horizon_limits(vision_result, horizons):
    _, args = _setup(vision_result)

    with pytest.raises(ValidationError):
        PredictSpreadInput(**{**args.model_dump(), "horizons_s": horizons})
