"""VisionResult v1.0 (sprint-1.md section 7.1) and the ingest Job row (section 7.6)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, ValidationInfo, model_validator

from rw.contracts.base import (
    DEFAULT_RIP_THRESHOLD,
    DEFAULT_UNCERTAIN_THRESHOLD,
    MAX_KEYFRAMES,
    MAX_MESSAGE_BYTES,
    MAX_POLYGON_POINTS,
    CameraId,
    Confidence,
    Contract,
    JobId,
    NonNegFloat,
    NonNegInt,
    ResultId,
    RipId,
    S3Uri,
    Timestamp,
    TraceId,
    TrackId,
    UnitScore,
)
from rw.contracts.enums import JobSource, JobStatus, Mode, RipLabel, RuntimeVariant, Status


def classify(confidence: float, rip_threshold: float, uncertain_threshold: float) -> Status:
    """Map a confidence to a status using the vision thresholds."""
    if confidence >= rip_threshold:
        return Status.RIP
    if confidence >= uncertain_threshold:
        return Status.UNCERTAIN
    return Status.CLEAR


def _thresholds(info: ValidationInfo) -> tuple[float, float]:
    ctx = info.context or {}
    return (
        ctx.get("rip_threshold", DEFAULT_RIP_THRESHOLD),
        ctx.get("uncertain_threshold", DEFAULT_UNCERTAIN_THRESHOLD),
    )


Point = tuple[int, int]
BBox = tuple[NonNegInt, NonNegInt, NonNegInt, NonNegInt]  # x, y, w, h in pixels
PointM = tuple[float, float]


class InputInfo(Contract):
    s3_uri: S3Uri
    start_ts: Timestamp
    end_ts: Timestamp
    fps_source: NonNegFloat | None
    fps_processed: NonNegFloat | None
    frames_processed: Annotated[int, Field(ge=1)]
    width: Annotated[int, Field(ge=1)]
    height: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _ts_order(self) -> InputInfo:
        if self.end_ts < self.start_ts:
            raise ValueError("end_ts must not be before start_ts")
        return self


class RuntimeInfo(Contract):
    variant: RuntimeVariant
    opencv_version: str
    cv2_path: str
    instance_type: str
    pipeline: str
    pipeline_version: str


class Summary(Contract):
    status: Status
    max_confidence: Confidence
    rip_count: NonNegInt
    swimmer_count: NonNegInt
    swimmers_at_risk: NonNegInt


class Evidence(Contract):
    detector_score: UnitScore | None
    flow_score: UnitScore | None
    seaward_flow_px_per_s: float | None
    seaward_flow_m_per_s: float | None
    timex_score: UnitScore | None


class Rip(Contract):
    rip_id: RipId
    label: RipLabel
    confidence: Confidence
    polygon_px: Annotated[list[Point], Field(min_length=3, max_length=MAX_POLYGON_POINTS)]
    bbox_px: BBox
    polygon_m: Annotated[list[PointM], Field(min_length=3, max_length=MAX_POLYGON_POINTS)] | None
    area_px: NonNegFloat
    area_m2: NonNegFloat | None
    evidence: Evidence
    first_seen_ts: Timestamp
    persist_s: NonNegFloat | None


class Swimmer(Contract):
    track_id: TrackId
    bbox_px: BBox
    confidence: Confidence
    position_m: PointM | None
    in_rip_id: RipId | None
    distance_to_rip_px: NonNegFloat | None
    distance_to_rip_m: NonNegFloat | None
    drift_px_per_s: tuple[float, float] | None


class Keyframe(Contract):
    index: NonNegInt
    ts: Timestamp
    s3_uri: S3Uri


class Quality(Contract):
    glare: UnitScore
    blur: UnitScore
    low_light: bool
    camera_shake_px: NonNegFloat | None
    notes: list[str]


class Timings(Contract):
    decode: NonNegFloat
    preprocess: NonNegFloat
    stabilize: NonNegFloat
    timex: NonNegFloat
    flow: NonNegFloat
    detect: NonNegFloat
    track: NonNegFloat
    keyframes: NonNegFloat
    total: NonNegFloat


class VisionResult(Contract):
    """One processed clip or image, produced by rw-vision."""

    schema_version: Literal["1.0"] = "1.0"
    result_id: ResultId
    trace_id: TraceId
    camera_id: CameraId
    source_id: Annotated[str, Field(min_length=1, max_length=256)]
    job_id: JobId
    mode: Mode
    out_of_order: bool
    input: InputInfo
    runtime: RuntimeInfo
    summary: Summary
    rips: list[Rip]
    swimmers: list[Swimmer]
    keyframes: Annotated[list[Keyframe], Field(max_length=MAX_KEYFRAMES)]
    quality: Quality
    timings_ms: Timings
    created_at: Timestamp

    @model_validator(mode="after")
    def _consistency(self, info: ValidationInfo) -> VisionResult:
        rip_t, unc_t = _thresholds(info)

        expected = classify(self.summary.max_confidence, rip_t, unc_t)
        if self.summary.status != expected:
            raise ValueError(
                f"summary.status {self.summary.status.value!r} does not match "
                f"max_confidence {self.summary.max_confidence} (expected {expected.value!r})"
            )
        if self.summary.rip_count != len(self.rips):
            raise ValueError("summary.rip_count must equal len(rips)")
        if self.summary.swimmer_count != len(self.swimmers):
            raise ValueError("summary.swimmer_count must equal len(swimmers)")

        rip_ids = {r.rip_id for r in self.rips}
        for rip in self.rips:
            if classify(rip.confidence, rip_t, unc_t).value != rip.label.value:
                raise ValueError(
                    f"rip {rip.rip_id} label {rip.label.value!r} does not match "
                    f"confidence {rip.confidence}"
                )
            if rip.confidence > self.summary.max_confidence:
                raise ValueError(f"rip {rip.rip_id} confidence exceeds summary.max_confidence")
            if not rip.rip_id.startswith(self.camera_id + "-"):
                raise ValueError(f"rip {rip.rip_id} does not belong to camera {self.camera_id}")
            for x, y in rip.polygon_px:
                if not (0 <= x <= self.input.width and 0 <= y <= self.input.height):
                    raise ValueError(f"rip {rip.rip_id} polygon point outside the processed frame")

        for sw in self.swimmers:
            if sw.in_rip_id is not None and sw.in_rip_id not in rip_ids:
                raise ValueError(f"swimmer {sw.track_id} in_rip_id {sw.in_rip_id} is not in rips")

        if self.summary.swimmers_at_risk > len(self.swimmers):
            raise ValueError("summary.swimmers_at_risk cannot exceed len(swimmers)")

        if self.mode == Mode.IMAGE:
            moving = [r.rip_id for r in self.rips if r.evidence.seaward_flow_px_per_s is not None]
            moving += [s.track_id for s in self.swimmers if s.drift_px_per_s is not None]
            if moving:
                raise ValueError(f"motion fields must be null in image mode: {moving}")

        size = len(self.model_dump_json().encode())
        if size >= MAX_MESSAGE_BYTES:
            raise ValueError(f"serialized size {size} bytes is not under {MAX_MESSAGE_BYTES}")
        return self


class Job(Contract):
    """Ingest job row in rw-jobs."""

    schema_version: Literal["1.0"] = "1.0"
    job_id: JobId
    camera_id: CameraId
    source: JobSource
    s3_key: Annotated[str, Field(min_length=1, max_length=1024)]
    mode: Mode
    status: JobStatus
    result_id: ResultId | None
    error: str | None
    followup_request: dict[str, object] | None
    created_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="after")
    def _consistency(self) -> Job:
        if self.status == JobStatus.DONE and self.result_id is None:
            raise ValueError("result_id is required when status is done")
        if self.status == JobStatus.FAILED and not self.error:
            raise ValueError("error is required when status is failed")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")
        return self
