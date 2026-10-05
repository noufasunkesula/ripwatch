import zipfile
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest

from rw.adapters.burst import BurstSource
from rw.adapters.image import ImageSource
from rw.adapters.video import VideoFileSource
from rw.common.ids import new_id
from rw.contracts.vision import Mode, RipLabel, Status, VisionResult
from rw.vision.detector import RipDetection, SwimmerBox
from rw.vision.draw import crop, draw_overlay
from rw.vision.pipeline import ResultContext, VisionPipeline
from rw.vision.state import CameraState
from scripts.make_synthetic_clip import RIP_X, make_frames, write_clip

START = datetime(2026, 10, 5, 10, 15, tzinfo=UTC)
CAM = "cam-01"


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clips")
    return {
        "rip": write_clip(folder / "rip.mp4"),
        "static": write_clip(folder / "static.mp4", static=True),
    }


def _frames(path, start=START):
    return list(VideoFileSource(path, CAM, f"{CAM}/clip", start).iter_frames(5, 640))


def _context(**overrides):
    values = {
        "job_id": new_id("job"),
        "trace_id": new_id("tr"),
        "source_id": f"{CAM}/20261005T101500Z-000001",
        "s3_uri": "s3://rw-data-123456789012/incoming/cam-01/20261005T101500Z-000001.mp4",
        "fps_source": 15.0,
    }
    return ResultContext(**{**values, **overrides})


class MemorySink:
    def __init__(self):
        self.objects = {}

    def __call__(self, key, data):
        self.objects[key] = data
        return f"s3://rw-artifacts-123456789012/{key}"


@pytest.fixture(scope="module")
def pipeline():
    return VisionPipeline(keyframe_sink=MemorySink())


def test_synthetic_rip_clip_is_a_rip(clips, pipeline):
    state = CameraState(CAM)
    result = pipeline.process(_frames(clips["rip"]), Mode.VIDEO, state, _context())

    assert isinstance(result, VisionResult)
    assert result.summary.status == Status.RIP
    assert result.summary.rip_count == 1
    (rip,) = result.rips
    assert rip.rip_id == "cam-01-rip-0001"
    assert rip.label == RipLabel.RIP and rip.confidence >= 0.7
    x, _, w, _ = rip.bbox_px
    assert RIP_X[0] - 10 <= x and x + w <= RIP_X[1] + 10  # found the strip, not the waves
    assert rip.evidence.seaward_flow_px_per_s > 10
    assert rip.evidence.seaward_flow_m_per_s is None and rip.polygon_m is None  # never fake metres
    assert 3 <= len(rip.polygon_px) <= 32
    assert result.input.frames_processed == 50
    assert result.input.fps_processed == pytest.approx(5.0, abs=0.1)
    assert result.runtime.pipeline == "baseline_flow"
    assert result.timings_ms.flow > 0 and result.timings_ms.total >= result.timings_ms.flow
    # 10 s clip, one keyframe per second, at most 10
    assert 1 <= len(result.keyframes) <= 10
    assert result.keyframes[0].s3_uri.endswith(f"keyframes/cam-01/{result.result_id}/000.jpg")
    assert len(state.flow_history) == 1


def test_static_clip_is_clear_without_keyframes(clips):
    sink = MemorySink()
    result = VisionPipeline(keyframe_sink=sink).process(
        _frames(clips["static"]), Mode.VIDEO, CameraState(CAM), _context()
    )
    assert result.summary.status == Status.CLEAR
    assert result.rips == [] and result.summary.max_confidence == 0.0
    assert result.keyframes == [] and sink.objects == {}


def test_active_incident_gets_keyframes_even_when_clear(clips):
    sink = MemorySink()
    result = VisionPipeline(keyframe_sink=sink).process(
        _frames(clips["static"]), Mode.VIDEO, CameraState(CAM), _context(active_incident=True)
    )
    assert result.summary.status == Status.CLEAR
    assert result.keyframes and len(sink.objects) == len(result.keyframes)


def test_rip_id_and_first_seen_carry_over_to_the_next_clip(clips, pipeline):
    state = CameraState(CAM)
    first = pipeline.process(_frames(clips["rip"]), Mode.VIDEO, state, _context())
    later = START + timedelta(seconds=10)
    second = pipeline.process(_frames(clips["rip"], later), Mode.VIDEO, state, _context())
    assert second.rips[0].rip_id == first.rips[0].rip_id
    assert second.rips[0].first_seen_ts == first.rips[0].first_seen_ts
    assert second.rips[0].persist_s >= 10
    assert len(state.flow_history) == 2


def test_fresh_state_leaves_the_camera_state_untouched(clips, pipeline):
    real = CameraState(CAM)
    pipeline.process(
        _frames(clips["rip"]), Mode.VIDEO, CameraState(CAM), _context(out_of_order=True)
    )
    assert len(real.flow_history) == 0 and real.rips == {}


def test_image_mode_has_no_motion_fields(tmp_path, pipeline):
    path = tmp_path / "photo.jpg"
    cv2.imwrite(str(path), make_frames(seconds=0.1)[0])
    source = ImageSource(path, CAM, "upload/photo", START)
    result = pipeline.process(source.iter_frames(), Mode.IMAGE, CameraState(CAM), _context())
    assert result.mode == Mode.IMAGE
    assert result.summary.status in {Status.CLEAR, Status.UNCERTAIN}  # never `rip` from one photo
    assert all(r.evidence.seaward_flow_px_per_s is None for r in result.rips)
    assert "image: no motion evidence" in result.quality.notes


def test_burst_mode_produces_a_valid_result(tmp_path, pipeline):
    zpath = tmp_path / "burst.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for i, frame in enumerate(make_frames(seconds=4, fps=1)):
            ok, data = cv2.imencode(".jpg", frame)
            z.writestr(f"{i}.jpg", data.tobytes())
    source = BurstSource(zpath, CAM, "upload/burst", START)
    result = pipeline.process(source.iter_frames(), Mode.BURST, CameraState(CAM), _context())
    assert result.mode == Mode.BURST
    assert result.input.frames_processed == 4
    assert "burst: coarse motion" in result.quality.notes


class FakeDetector:
    name, version = "fake", "9.9"

    def detect(self, frames):
        return [RipDetection([(20, 20), (80, 20), (80, 120), (20, 120)], 0.55, score=0.61)]


class FakeSwimmers:
    def __init__(self):
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        # A swimmer inside the rip strip, drifting up (seaward) 6 px per call.
        y = 200 - 6 * self.calls
        return [SwimmerBox((310, y, 12, 18), 0.8)]


def test_detector_and_swimmers_are_fused(clips):
    swimmers = FakeSwimmers()
    pipe = VisionPipeline(detector=FakeDetector(), swimmer_detector=swimmers)
    result = pipe.process(_frames(clips["rip"]), Mode.VIDEO, CameraState(CAM), _context())

    assert result.runtime.pipeline == "baseline_flow+fake"
    assert result.runtime.pipeline_version == "0.1.0+9.9"
    labels = sorted((r.label.value, r.evidence.detector_score) for r in result.rips)
    assert ("uncertain", 0.61) in labels  # the model-only region
    (swimmer,) = result.swimmers
    assert swimmer.track_id == "cam-01-sw-0001"
    flow_rip = next(r for r in result.rips if r.evidence.flow_score is not None)
    assert swimmer.in_rip_id == flow_rip.rip_id
    assert result.summary.swimmers_at_risk == 1
    assert swimmer.drift_px_per_s[1] < 0  # moving up the image, out to sea
    assert 5 <= swimmers.calls <= 11  # about one detection per second of a 10 s clip


def test_recheck_scores_moving_rip_crops_above_static_ones(clips, pipeline):
    rip_result = pipeline.process(_frames(clips["rip"]), Mode.VIDEO, CameraState(CAM), _context())
    bbox = rip_result.rips[0].bbox_px
    moving = [crop(f.image, bbox, 2.0) for f in _frames(clips["rip"])[::5]]
    still = [crop(f.image, bbox, 2.0) for f in _frames(clips["static"])[::5]]
    assert pipeline.recheck(moving, rip_result) > pipeline.recheck(still, rip_result)
    assert pipeline.recheck([], rip_result) == 0.0


def test_draw_overlay_and_crop(clips, pipeline):
    frames = _frames(clips["rip"])
    result = pipeline.process(frames, Mode.VIDEO, CameraState(CAM), _context())
    drawn = draw_overlay(frames[0].image, result)
    assert drawn.shape == frames[0].image.shape
    assert not np.array_equal(drawn, frames[0].image)
    zoomed = crop(frames[0].image, (300, 100, 40, 60), zoom=2.0)
    assert zoomed.shape[0] > 60 * 2 and zoomed.shape[1] > 40 * 2
    with pytest.raises(ValueError, match="outside"):
        crop(frames[0].image, (900, 900, 10, 10))
