"""zoom_and_recheck: re-score a rip on zoomed keyframe crops.

Crops come from up to MAX_KEYFRAMES keyframes in S3: the box (from rip_id or
bbox_px, in processed-frame pixels, rescaled to each keyframe's size) is grown
by EXPAND on each axis and upsampled by `zoom` with INTER_CUBIC. Scoring is
done by a Rechecker; the real one is VisionPipeline.recheck from baseline
vision (N-11), tests use a fake.
"""

from __future__ import annotations

from typing import Annotated, Any, Protocol

import cv2
import numpy as np
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field, model_validator

from rw.contracts import Status, VisionResult, classify
from rw.contracts.base import (
    DEFAULT_RIP_THRESHOLD,
    DEFAULT_UNCERTAIN_THRESHOLD,
    MAX_KEYFRAMES,
    CameraId,
    NonNegInt,
    ResultId,
    RipId,
    TraceId,
)
from rw.mcp_tools.store import DetectionStore

EXPAND = 0.25
NO_KEYFRAMES = "no_keyframes"
_MISSING = {"NoSuchKey", "NoSuchBucket", "404"}


class Rechecker(Protocol):
    def recheck(self, crops: list[np.ndarray], result: VisionResult) -> float:
        """Rip confidence in [0, 1] for zoomed crops of one region."""
        ...


class RipNotFound(LookupError):
    pass


class ZoomInput(BaseModel):
    trace_id: TraceId
    result_id: ResultId
    camera_id: CameraId
    rip_id: RipId | None = None
    bbox_px: tuple[NonNegInt, NonNegInt, NonNegInt, NonNegInt] | None = None
    zoom: Annotated[float, Field(ge=1.5, le=4.0)] = 2.0

    @model_validator(mode="after")
    def _one_target(self) -> ZoomInput:
        if (self.rip_id is None) == (self.bbox_px is None):
            raise ValueError("give exactly one of rip_id or bbox_px")
        return self


class ZoomOutput(BaseModel):
    label: Status | None
    confidence: float | None
    before_confidence: float | None
    keyframes_used: int
    note: str | None


def _split_s3_uri(uri: str) -> tuple[str, str]:
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def _load(s3: Any, uri: str) -> np.ndarray | None:
    bucket, key = _split_s3_uri(uri)
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in _MISSING:
            return None
        raise
    image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    return image


def crop_and_zoom(
    image: np.ndarray, bbox: tuple[int, int, int, int], frame: tuple[int, int], zoom: float
) -> np.ndarray:
    """Crop bbox (processed-frame pixels) grown by EXPAND from image, upsampled by zoom."""
    frame_w, frame_h = frame
    img_h, img_w = image.shape[:2]
    sx, sy = img_w / frame_w, img_h / frame_h
    x, y, w, h = bbox
    pad_w, pad_h = w * EXPAND / 2, h * EXPAND / 2
    x0 = max(0, int((x - pad_w) * sx))
    y0 = max(0, int((y - pad_h) * sy))
    x1 = min(img_w, int(np.ceil((x + w + pad_w) * sx)))
    y1 = min(img_h, int(np.ceil((y + h + pad_h) * sy)))
    crop = image[y0 : max(y1, y0 + 1), x0 : max(x1, x0 + 1)]
    return cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)


def zoom_and_recheck(
    store: DetectionStore,
    s3: Any,
    rechecker: Rechecker,
    args: ZoomInput,
    rip_threshold: float = DEFAULT_RIP_THRESHOLD,
    uncertain_threshold: float = DEFAULT_UNCERTAIN_THRESHOLD,
) -> ZoomOutput:
    """Look closer at a rip: re-score it on zoomed crops of the clip's keyframes.

    Use before alerting when confidence is below 0.85 or glare is above 0.3,
    or whenever a detection looks doubtful. Give rip_id (preferred) or a
    bbox_px [x, y, w, h]; zoom is 1.5 to 4.0 (default 2.0). Returns the new
    label ("rip", "uncertain", "clear") and confidence next to the confidence
    before zooming, and how many keyframes were used. If the clip has no
    keyframes, note is "no_keyframes" and the original confidence is returned.
    """
    result = store.get_result(args.camera_id, args.result_id)
    if args.rip_id is not None:
        rip = next((r for r in result.rips if r.rip_id == args.rip_id), None)
        if rip is None:
            raise RipNotFound(f"rip {args.rip_id} not in result {args.result_id}")
        bbox, before = rip.bbox_px, rip.confidence
    else:
        bbox, before = args.bbox_px, None

    frame = (result.input.width, result.input.height)
    crops = []
    for keyframe in result.keyframes[:MAX_KEYFRAMES]:
        image = _load(s3, keyframe.s3_uri)
        if image is not None:
            crops.append(crop_and_zoom(image, bbox, frame, args.zoom))

    if not crops:
        return ZoomOutput(
            label=None if before is None else classify(before, rip_threshold, uncertain_threshold),
            confidence=before,
            before_confidence=before,
            keyframes_used=0,
            note=NO_KEYFRAMES,
        )

    confidence = round(float(rechecker.recheck(crops, result)), 3)
    return ZoomOutput(
        label=classify(confidence, rip_threshold, uncertain_threshold),
        confidence=confidence,
        before_confidence=before,
        keyframes_used=len(crops),
        note=None,
    )
