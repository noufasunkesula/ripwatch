from __future__ import annotations

import boto3
import cv2
import numpy as np
import pytest
from moto import mock_aws
from pydantic import ValidationError

from rw.contracts import Status, VisionResult
from rw.mcp_tools.store import InMemoryDetectionStore
from rw.mcp_tools.tools.zoom import (
    NO_KEYFRAMES,
    RipNotFound,
    ZoomInput,
    crop_and_zoom,
    zoom_and_recheck,
)

BUCKET = "rw-artifacts-example"
RIP_ID = "cam-01-rip-0007"


class FakeRechecker:
    def __init__(self, confidence: float) -> None:
        self.confidence = confidence
        self.crops: list[np.ndarray] = []

    def recheck(self, crops, result):
        self.crops = crops
        return self.confidence


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _put_jpeg(s3, key: str, width: int = 640, height: int = 360) -> None:
    image = np.full((height, width, 3), 128, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf.tobytes())


def _keyframe(n: int) -> dict:
    return {
        "index": n,
        "ts": "2026-10-05T10:15:00Z",
        "s3_uri": f"s3://{BUCKET}/keyframes/cam-01/r/{n:03d}.jpg",
    }


def _setup(vision_result: dict, keyframes: list[int]):
    vision_result["keyframes"] = [_keyframe(n) for n in keyframes]
    result = VisionResult.model_validate(vision_result)
    return InMemoryDetectionStore([result]), result


def _args(result: VisionResult, **kw) -> ZoomInput:
    kw.setdefault("rip_id", RIP_ID)
    return ZoomInput(
        trace_id=result.trace_id, result_id=result.result_id, camera_id=result.camera_id, **kw
    )


def test_confirms_rip_on_zoomed_crop(s3, vision_result):
    _put_jpeg(s3, "keyframes/cam-01/r/000.jpg")
    store, result = _setup(vision_result, [0])
    rechecker = FakeRechecker(0.88)

    out = zoom_and_recheck(store, s3, rechecker, _args(result))

    assert out.label == Status.RIP
    assert out.confidence == 0.88
    assert out.before_confidence == 0.82
    assert out.keyframes_used == 1
    assert out.note is None
    # bbox [300, 138, 55, 124] grown 25% -> 69 x 156, zoomed 2x
    assert rechecker.crops[0].shape == (312, 138, 3)


def test_confidence_drop_gives_clear(s3, vision_result):
    _put_jpeg(s3, "keyframes/cam-01/r/000.jpg")
    store, result = _setup(vision_result, [0])

    out = zoom_and_recheck(store, s3, FakeRechecker(0.3), _args(result))

    assert out.label == Status.CLEAR
    assert out.before_confidence == 0.82


def test_custom_thresholds(s3, vision_result):
    _put_jpeg(s3, "keyframes/cam-01/r/000.jpg")
    store, result = _setup(vision_result, [0])

    out = zoom_and_recheck(store, s3, FakeRechecker(0.75), _args(result), rip_threshold=0.8)

    assert out.label == Status.UNCERTAIN


def test_bbox_target_has_no_before_confidence(s3, vision_result):
    _put_jpeg(s3, "keyframes/cam-01/r/000.jpg")
    store, result = _setup(vision_result, [0])
    rechecker = FakeRechecker(0.5)

    out = zoom_and_recheck(
        store, s3, rechecker, _args(result, rip_id=None, bbox_px=(100, 100, 40, 40), zoom=3.0)
    )

    assert out.before_confidence is None
    assert out.label == Status.UNCERTAIN
    assert rechecker.crops[0].shape == (150, 150, 3)  # 40 grown 25% = 50, zoomed 3x


def test_skips_missing_keyframes(s3, vision_result):
    _put_jpeg(s3, "keyframes/cam-01/r/001.jpg")
    store, result = _setup(vision_result, [0, 1])

    out = zoom_and_recheck(store, s3, FakeRechecker(0.9), _args(result))

    assert out.keyframes_used == 1


@pytest.mark.parametrize("keyframes", [[], [0]])
def test_no_keyframes_returns_original(s3, vision_result, keyframes):
    store, result = _setup(vision_result, keyframes)  # nothing uploaded
    rechecker = FakeRechecker(0.1)

    out = zoom_and_recheck(store, s3, rechecker, _args(result))

    assert out.note == NO_KEYFRAMES
    assert out.keyframes_used == 0
    assert out.confidence == out.before_confidence == 0.82
    assert out.label == Status.RIP
    assert rechecker.crops == []


def test_unknown_rip_raises(s3, vision_result):
    store, result = _setup(vision_result, [])

    with pytest.raises(RipNotFound):
        zoom_and_recheck(store, s3, FakeRechecker(0.5), _args(result, rip_id="cam-01-rip-9999"))


def test_crop_scales_to_keyframe_size():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)  # keyframe 2x the processed frame

    crop = crop_and_zoom(image, (300, 138, 55, 124), (640, 360), 2.0)

    assert crop.shape == (620, 276, 3)


def test_crop_clipped_at_image_edge():
    image = np.zeros((360, 640, 3), dtype=np.uint8)

    crop = crop_and_zoom(image, (0, 0, 40, 40), (640, 360), 2.0)

    assert crop.shape == (90, 90, 3)  # grown box starts at -5, clipped to 0..45


@pytest.mark.parametrize(
    "kw",
    [
        {"rip_id": None},
        {"bbox_px": (1, 1, 5, 5)},
        {"zoom": 1.4},
        {"zoom": 4.1},
    ],
)
def test_input_validation(vision_result, kw):
    result = VisionResult.model_validate(vision_result)

    with pytest.raises(ValidationError):
        _args(result, **kw)
