from __future__ import annotations

import pytest
from pydantic import ValidationError

from rw.contracts import VisionResult
from rw.mcp_tools.store import InMemoryDetectionStore, ResultNotFound
from rw.mcp_tools.tools.swimmers import MAX_SWIMMERS, TrackSwimmersInput, track_swimmers


def _swimmer(n: int, in_rip: bool, distance: float | None) -> dict:
    return {
        "track_id": f"cam-01-sw-{n:04d}",
        "bbox_px": [10 * n, 200, 12, 18],
        "confidence": 0.7,
        "position_m": None,
        "in_rip_id": "cam-01-rip-0007" if in_rip else None,
        "distance_to_rip_px": distance,
        "distance_to_rip_m": None,
        "drift_px_per_s": [0.4, -1.8],
    }


def _store_with(vision_result: dict, swimmers: list[dict], at_risk: int):
    vision_result["swimmers"] = swimmers
    vision_result["summary"]["swimmer_count"] = len(swimmers)
    vision_result["summary"]["swimmers_at_risk"] = at_risk
    result = VisionResult.model_validate(vision_result)
    return InMemoryDetectionStore([result]), result


def _args(result: VisionResult) -> TrackSwimmersInput:
    return TrackSwimmersInput(
        trace_id=result.trace_id, result_id=result.result_id, camera_id=result.camera_id
    )


def test_returns_swimmers_and_at_risk_count(vision_result):
    store, result = _store_with(vision_result, vision_result["swimmers"], at_risk=1)

    out = track_swimmers(store, _args(result))

    assert out.swimmer_count == 1
    assert out.swimmers_at_risk == 1
    assert out.truncated is False
    sw = out.swimmers[0]
    assert sw.track_id == "cam-01-sw-0012"
    assert sw.in_rip_id == "cam-01-rip-0007"
    assert sw.drift_px_per_s == (0.4, -1.8)


def test_in_rip_first_then_nearest_then_unknown(vision_result):
    swimmers = [
        _swimmer(1, in_rip=False, distance=None),
        _swimmer(2, in_rip=False, distance=40.0),
        _swimmer(3, in_rip=True, distance=0.0),
        _swimmer(4, in_rip=False, distance=5.0),
    ]
    store, result = _store_with(vision_result, swimmers, at_risk=2)

    out = track_swimmers(store, _args(result))

    assert [s.track_id[-4:] for s in out.swimmers] == ["0003", "0004", "0002", "0001"]
    assert out.swimmers_at_risk == 2


def test_caps_list_and_flags_truncation(vision_result):
    swimmers = [_swimmer(n, in_rip=False, distance=float(n)) for n in range(1, 14)]
    store, result = _store_with(vision_result, swimmers, at_risk=0)

    out = track_swimmers(store, _args(result))

    assert out.swimmer_count == 13
    assert len(out.swimmers) == MAX_SWIMMERS
    assert out.truncated is True
    assert out.swimmers[0].track_id == "cam-01-sw-0001"


def test_no_swimmers(vision_result):
    store, result = _store_with(vision_result, [], at_risk=0)

    out = track_swimmers(store, _args(result))

    assert out.swimmer_count == 0
    assert out.swimmers == []


def test_unknown_result_raises(vision_result):
    store, result = _store_with(vision_result, [], at_risk=0)
    args = _args(result).model_copy(update={"result_id": "res_01J9ZC4K9B1C2D3E4F5G6H7J8K"})

    with pytest.raises(ResultNotFound):
        track_swimmers(store, args)


def test_input_rejects_bad_ids():
    with pytest.raises(ValidationError):
        TrackSwimmersInput(trace_id="nope", result_id="res_x", camera_id="beach-1")
