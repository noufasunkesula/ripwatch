"""VisionPipeline: frames in, a validated VisionResult out (sprint-1.md N-11).

This is the interface Saif implements against: pass a `Detector` (rips) and/or `SwimmerDetector`
(people) and the pipeline fuses them with the baseline flow evidence. With neither, the baseline
flow detector alone produces the result.

`process()` takes a `ResultContext` in addition to the sprint's (frames, mode, state): a
VisionResult needs the job, trace, source and S3 location, which the frames do not carry.
`recheck()` follows the `Rechecker` protocol of `zoom_and_recheck` (D-03): crops plus the original
result in, a rip confidence out.
"""

from __future__ import annotations

import platform
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import cv2
import numpy as np

from rw.adapters.base import Frame
from rw.common.ids import new_id
from rw.common.metrics import timed
from rw.common.runtime import check_cv2_runtime
from rw.contracts.base import DEFAULT_RIP_THRESHOLD, DEFAULT_UNCERTAIN_THRESHOLD
from rw.contracts.vision import Mode, RuntimeVariant, Status, VisionResult, classify
from rw.vision import baseline_flow as bf
from rw.vision.detector import Detector, SwimmerDetector, Track
from rw.vision.state import CameraState, KnownRip

PIPELINE_NAME = "baseline_flow"
PIPELINE_VERSION = "0.1.0"
MAX_KEYFRAMES = 10
KEYFRAME_JPEG_QUALITY = 85
RIP_MATCH_IOU = 0.3
TRACK_MATCH_IOU = 0.3
AT_RISK_DISTANCE_PX = 30.0

# (s3 key, jpeg bytes) -> s3:// URI. Ingest uploads to rw-artifacts; tests keep bytes in memory.
KeyframeSink = Callable[[str, bytes], str]


@dataclass(frozen=True)
class ResultContext:
    job_id: str
    trace_id: str
    source_id: str
    s3_uri: str
    fps_source: float | None = None
    out_of_order: bool = False
    active_incident: bool = False


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _default_variant() -> str:
    return "std-arm" if platform.machine().lower() in {"arm64", "aarch64"} else "std-x86"


class VisionPipeline:
    def __init__(
        self,
        detector: Detector | None = None,
        swimmer_detector: SwimmerDetector | None = None,
        params: bf.FlowParams | None = None,
        rip_threshold: float = DEFAULT_RIP_THRESHOLD,
        uncertain_threshold: float = DEFAULT_UNCERTAIN_THRESHOLD,
        keyframe_sink: KeyframeSink | None = None,
        instance_type: str = "local",
    ) -> None:
        self.detector = detector
        self.swimmer_detector = swimmer_detector
        self.params = params or bf.FlowParams()
        self.rip_threshold = rip_threshold
        self.uncertain_threshold = uncertain_threshold
        self.keyframe_sink = keyframe_sink
        runtime = check_cv2_runtime(instance_type)
        self.runtime = {
            "variant": runtime["variant"] or _default_variant(),
            "opencv_version": runtime["opencv_version"],
            "cv2_path": runtime["cv2_path"] or "unknown",
            "instance_type": runtime["instance_type"],
            "pipeline": PIPELINE_NAME if detector is None else f"{PIPELINE_NAME}+{detector.name}",
            "pipeline_version": PIPELINE_VERSION
            if detector is None
            else f"{PIPELINE_VERSION}+{detector.version}",
        }
        RuntimeVariant(self.runtime["variant"])  # fail fast on a bad RW_RUNTIME

    # ------------------------------------------------------------ process

    def process(
        self, frames: Iterable[Frame], mode: Mode, state: CameraState, context: ResultContext
    ) -> VisionResult:
        started = time.perf_counter()
        timings: dict[str, float] = {}
        with timed("decode", record=timings):
            frames = list(frames)
        if not frames:
            raise ValueError(f"no frames decoded for {context.source_id}")

        images = [f.image for f in frames]
        start_ts, end_ts = frames[0].ts, frames[-1].ts
        duration = (end_ts - start_ts).total_seconds()
        fps = (len(frames) - 1) / duration if len(frames) > 1 and duration > 0 else None

        if mode == Mode.IMAGE:
            analysis = bf.analyze_image(images, self.params, timings)
        elif mode == Mode.BURST:
            analysis = bf.analyze_burst(images, self.params, timings)
        else:
            analysis = bf.analyze_video(images, state, fps or 5.0, self.params, timings)

        if self.detector is not None:
            with timed("detect", record=timings):
                self._fuse_detector(analysis, images)

        regions = [
            r
            for r in analysis.regions
            if classify(r.confidence, self.rip_threshold, self.uncertain_threshold) != Status.CLEAR
        ]
        rips = self._assign_rips(regions, state, end_ts, mode)
        with timed("track", record=timings):
            swimmers = self._swimmers(frames, rips, state, mode)

        max_conf = max((r["confidence"] for r in rips), default=0.0)
        status = classify(max_conf, self.rip_threshold, self.uncertain_threshold)
        at_risk = sum(
            1
            for s in swimmers
            if s["in_rip_id"] or (s["distance_to_rip_px"] or 1e9) <= AT_RISK_DISTANCE_PX
        )
        result_id = new_id("res")

        keyframes = []
        if self.keyframe_sink is not None and (status != Status.CLEAR or context.active_incident):
            with timed("keyframes", record=timings):
                keyframes = self._keyframes(frames, context, result_id)

        for stage in ("decode", "preprocess", "stabilize", "timex", "flow", "detect", "track"):
            timings.setdefault(stage, 0.0)
        timings.setdefault("keyframes", 0.0)
        timings["total"] = (time.perf_counter() - started) * 1000

        payload = {
            "result_id": result_id,
            "trace_id": context.trace_id,
            "camera_id": state.camera_id,
            "source_id": context.source_id,
            "job_id": context.job_id,
            "mode": mode,
            "out_of_order": context.out_of_order,
            "input": {
                "s3_uri": context.s3_uri,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "fps_source": context.fps_source,
                "fps_processed": round(fps, 3) if fps else None,
                "frames_processed": len(frames),
                "width": analysis.width,
                "height": analysis.height,
            },
            "runtime": self.runtime,
            "summary": {
                "status": status,
                "max_confidence": max_conf,
                "rip_count": len(rips),
                "swimmer_count": len(swimmers),
                "swimmers_at_risk": at_risk,
            },
            "rips": rips,
            "swimmers": swimmers,
            "keyframes": keyframes,
            "quality": {
                "glare": round(analysis.quality.glare, 4),
                "blur": round(analysis.quality.blur, 4),
                "low_light": analysis.quality.low_light,
                "camera_shake_px": None
                if analysis.quality.camera_shake_px is None
                else round(analysis.quality.camera_shake_px, 3),
                "notes": analysis.quality.notes,
            },
            "timings_ms": {k: round(v, 2) for k, v in timings.items()},
            "created_at": datetime.now(UTC),
        }
        return VisionResult.model_validate(
            payload,
            context={
                "rip_threshold": self.rip_threshold,
                "uncertain_threshold": self.uncertain_threshold,
            },
        )

    # ------------------------------------------------------------ parts

    def _fuse_detector(self, analysis: bf.Analysis, images: list[np.ndarray]) -> None:
        """Add the model's detections; where one overlaps a flow region, keep the higher score."""
        for det in self.detector.detect(images):  # type: ignore[union-attr]
            points = np.array(det.polygon_px, np.int32)
            x, y, w, h = cv2.boundingRect(points)
            bbox = (int(x), int(y), int(w), int(h))
            match = next(
                (r for r in analysis.regions if _iou(r.bbox_px, bbox) > RIP_MATCH_IOU), None
            )
            if match is not None:
                match.detector_score = det.score if det.score is not None else det.confidence
                match.confidence = max(match.confidence, det.confidence)
                continue
            analysis.regions.append(
                bf.Region(
                    polygon_px=[(int(px), int(py)) for px, py in det.polygon_px][:32],
                    bbox_px=bbox,
                    area_px=float(cv2.contourArea(points)),
                    confidence=det.confidence,
                    seaward_px_s=None,
                    flow_score=None,
                    timex_score=None,
                    persistence=None,
                    detector_score=det.score if det.score is not None else det.confidence,
                )
            )

    def _assign_rips(
        self, regions: list[bf.Region], state: CameraState, now: datetime, mode: Mode
    ) -> list[dict]:
        """Keep rip IDs stable across clips by bbox overlap with rips seen before."""
        rips = []
        for region in regions:
            known = max(
                state.rips.values(),
                key=lambda k: _iou(k.bbox_px, region.bbox_px),
                default=None,
            )
            if known is not None and _iou(known.bbox_px, region.bbox_px) > RIP_MATCH_IOU:
                known.bbox_px, known.last_seen_ts = region.bbox_px, now
            else:
                known = KnownRip(state.new_rip_id(), region.bbox_px, now, now)
                state.rips[known.rip_id] = known
            label = classify(region.confidence, self.rip_threshold, self.uncertain_threshold)
            rips.append(
                {
                    "rip_id": known.rip_id,
                    "label": label.value,
                    "confidence": region.confidence,
                    "polygon_px": region.polygon_px,
                    "bbox_px": region.bbox_px,
                    "polygon_m": None,
                    "area_px": region.area_px,
                    "area_m2": None,
                    "evidence": {
                        "detector_score": region.detector_score,
                        "flow_score": region.flow_score,
                        "seaward_flow_px_per_s": None
                        if mode == Mode.IMAGE
                        else region.seaward_px_s,
                        "seaward_flow_m_per_s": None,
                        "timex_score": region.timex_score,
                    },
                    "first_seen_ts": known.first_seen_ts,
                    "persist_s": round((now - known.first_seen_ts).total_seconds(), 2),
                }
            )
        return rips

    def _swimmers(
        self, frames: list[Frame], rips: list[dict], state: CameraState, mode: Mode
    ) -> list[dict]:
        """Detect on about one frame per second, IoU-track, and relate each swimmer to the rips."""
        if self.swimmer_detector is None:
            return []
        seen: dict[str, Track] = {}
        last_t = None
        for frame in frames:
            t = frame.ts.timestamp()
            if last_t is not None and t - last_t < 1.0:
                continue
            last_t = t
            for box in self.swimmer_detector.detect(frame.image):
                track = max(
                    state.tracks.values(),
                    key=lambda tr: _iou(tr.bbox_px, box.bbox_px),
                    default=None,
                )
                if track is None or _iou(track.bbox_px, box.bbox_px) <= TRACK_MATCH_IOU:
                    track = Track(state.new_track_id(), box.bbox_px, box.confidence)
                    state.tracks[track.track_id] = track
                track.bbox_px, track.confidence = box.bbox_px, box.confidence
                x, y, w, h = box.bbox_px
                track.history.append((t, (x + w / 2, y + h / 2)))
                seen[track.track_id] = track

        polygons = {r["rip_id"]: np.array(r["polygon_px"], np.int32) for r in rips}
        swimmers = []
        for track in seen.values():
            x, y, w, h = track.bbox_px
            center = (float(x + w / 2), float(y + h / 2))
            in_rip, distance = None, None
            for rip_id, polygon in polygons.items():
                signed = cv2.pointPolygonTest(polygon, center, True)  # >0 inside
                d = max(0.0, -signed)
                if distance is None or d < distance:
                    distance = d
                if signed >= 0 and in_rip is None:
                    in_rip = rip_id
            drift = None
            if mode != Mode.IMAGE and len(track.history) >= 2:
                (t0, (x0, y0)), (t1, (x1, y1)) = track.history[0], track.history[-1]
                if t1 > t0:
                    drift = (round((x1 - x0) / (t1 - t0), 3), round((y1 - y0) / (t1 - t0), 3))
            swimmers.append(
                {
                    "track_id": track.track_id,
                    "bbox_px": track.bbox_px,
                    "confidence": round(track.confidence, 4),
                    "position_m": None,
                    "in_rip_id": in_rip,
                    "distance_to_rip_px": None if distance is None else round(distance, 2),
                    "distance_to_rip_m": None,
                    "drift_px_per_s": drift,
                }
            )
        return swimmers

    def _keyframes(self, frames: list[Frame], context: ResultContext, result_id: str) -> list[dict]:
        """One JPEG per second of video, at most 10 (contract rule)."""
        chosen, last_t = [], None
        for frame in frames:
            t = frame.ts.timestamp()
            if last_t is None or t - last_t >= 1.0:
                chosen.append(frame)
                last_t = t
            if len(chosen) == MAX_KEYFRAMES:
                break
        keyframes = []
        camera_id = frames[0].camera_id
        for index, frame in enumerate(chosen):
            ok, jpeg = cv2.imencode(
                ".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, KEYFRAME_JPEG_QUALITY]
            )
            if not ok:
                continue
            key = f"keyframes/{camera_id}/{result_id}/{index:03d}.jpg"
            uri = self.keyframe_sink(key, jpeg.tobytes())  # type: ignore[misc]
            keyframes.append({"index": index, "ts": frame.ts, "s3_uri": uri})
        return keyframes

    # ------------------------------------------------------------ recheck (zoom_and_recheck)

    def recheck(self, crops: list[np.ndarray], result: VisionResult) -> float:
        """Rip confidence in [0, 1] for zoomed crops of one region (Rechecker protocol, D-03).

        Several crops of a moving clip: flow between consecutive crops. One crop or image mode:
        the appearance heuristic, which never reaches `rip` on its own.
        """
        if not crops:
            return 0.0
        height, width = crops[0].shape[:2]
        crops = [cv2.resize(c, (width, height)) for c in crops]
        scores = []
        if self.detector is not None:
            scores += [d.confidence for d in self.detector.detect(crops)]
        grays, _ = bf.preprocess(crops)
        if len(grays) >= 2 and result.mode != Mode.IMAGE:
            fps = 1.0  # keyframes are one per second
            speed = bf.seaward(bf.mean_flow(grays, fps, 1.0), self.params.seaward_vector)
            magnitude = min(max(float(np.median(speed)), 0.0) / self.params.seaward_ref_px_s, 2.0)
            timex = np.mean(np.stack(grays).astype(np.float32), axis=0)
            centre = np.zeros(timex.shape, bool)
            centre[height // 4 : 3 * height // 4, width // 4 : 3 * width // 4] = True
            darkness = bf.timex_score(timex, centre) or 0.0
            scores.append(bf.confidence(magnitude, 0.0, darkness))
        else:
            found = bf.appearance_regions(grays[0], self.params)
            scores.append(0.40 + 0.15 * found[0][1] if found else 0.0)
        return round(float(np.clip(max(scores), 0.0, 1.0)), 4)
