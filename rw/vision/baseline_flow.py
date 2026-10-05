"""Baseline rip detector: classic OpenCV optical flow, no learned model (sprint-1.md N-11).

A rip current is water flowing out to sea through the surf. On video it shows up as a patch whose
mean optical flow points seaward while the waves around it travel toward shore. The baseline:

    preprocess -> stabilize -> timex -> flow -> seaward projection -> detect (contours)

Each stage is timed into `timings` and emitted as FrameLatencyMs{stage}. This is our evaluation
baseline and the default until Saif's Detector lands; it must stay explainable, not clever.

Confidence for a region (logistic, all inputs in [0, 2] or [0, 1]):
    z = -3.0 + 2.5 * magnitude + 1.5 * persistence + 1.0 * timex
    magnitude   = min(mean seaward speed / seaward_ref_px_s, 2)
    persistence = share of recent clips (flow history) where the region also flowed seaward
    timex       = how much darker the region is than the scene in the long-exposure average
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from rw.common.metrics import timed
from rw.vision.state import TIMEX_WINDOW_S, CameraState

MAX_POLYGON_POINTS = 32


@dataclass(frozen=True)
class FlowParams:
    flow_scale: float = 0.5  # /rw/vision/flow_scale
    min_rip_area_px: float = 400.0  # /rw/vision/min_rip_area_px, processed-frame pixels
    seaward_vector: tuple[float, float] = (0.0, -1.0)  # camera config; up in the image is sea
    seaward_min_px_s: float = 4.0  # flow slower than this is not a rip
    seaward_ref_px_s: float = 10.0  # speed that counts as a clear rip signal


@dataclass
class Region:
    polygon_px: list[tuple[int, int]]
    bbox_px: tuple[int, int, int, int]
    area_px: float
    confidence: float
    seaward_px_s: float | None
    flow_score: float | None
    timex_score: float | None
    persistence: float | None
    detector_score: float | None = None


@dataclass
class QualityInfo:
    glare: float
    blur: float
    low_light: bool
    camera_shake_px: float | None
    notes: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    regions: list[Region]
    quality: QualityInfo
    width: int
    height: int


def logistic(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def confidence(magnitude: float, persistence: float, timex: float) -> float:
    return logistic(-3.0 + 2.5 * magnitude + 1.5 * persistence + 1.0 * timex)


# ---------------------------------------------------------------- stages


def preprocess(frames: list[np.ndarray]) -> tuple[list[np.ndarray], QualityInfo]:
    """Gray, CLAHE on the L channel (glare), 5x5 blur; quality measured on the raw frames."""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    grays = []
    for bgr in frames:
        lightness = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[:, :, 0]
        grays.append(cv2.GaussianBlur(clahe.apply(lightness), (5, 5), 0))

    raw = cv2.cvtColor(frames[len(frames) // 2], cv2.COLOR_BGR2GRAY)
    glare = float(np.mean(raw >= 240))
    sharpness = float(cv2.Laplacian(raw, cv2.CV_64F).var())
    blur = float(np.clip(1.0 - sharpness / 500.0, 0.0, 1.0))
    low_light = float(raw.mean()) < 50.0
    notes = []
    if glare > 0.3:
        notes.append("heavy glare")
    if low_light:
        notes.append("low light")
    return grays, QualityInfo(glare, blur, low_light, None, notes)


def _affine_to(prev: np.ndarray, curr: np.ndarray) -> np.ndarray | None:
    """Translation+rotation+scale mapping curr onto prev, or None if the scene does not agree."""
    points = cv2.goodFeaturesToTrack(prev, maxCorners=200, qualityLevel=0.01, minDistance=8)
    if points is None or len(points) < 10:
        return None
    moved, status, _ = cv2.calcOpticalFlowPyrLK(prev, curr, points, None)
    ok = status.ravel() == 1
    if ok.sum() < 10:
        return None
    matrix, inliers = cv2.estimateAffinePartial2D(
        moved[ok], points[ok], method=cv2.RANSAC, ransacReprojThreshold=1.0
    )
    # Water moves; only a camera shake moves the whole frame the same way.
    if matrix is None or inliers is None or inliers.mean() < 0.6:
        return None
    return matrix


def stabilize(grays: list[np.ndarray]) -> tuple[list[np.ndarray], float | None]:
    """Align each frame to the previous aligned frame. Returns frames and mean shift in px."""
    if len(grays) < 2:
        return grays, None
    height, width = grays[0].shape
    aligned = [grays[0]]
    shifts = []
    for curr in grays[1:]:
        matrix = _affine_to(aligned[-1], curr)
        if matrix is None:
            aligned.append(curr)
            shifts.append(0.0)
            continue
        shift = float(np.hypot(matrix[0, 2], matrix[1, 2]))
        if shift > 20.0:  # implausible for a mounted camera; trust the raw frame
            aligned.append(curr)
            shifts.append(0.0)
            continue
        aligned.append(cv2.warpAffine(curr, matrix, (width, height), borderMode=cv2.BORDER_REFLECT))
        shifts.append(shift)
    return aligned, float(np.mean(shifts))


def update_timex(grays: list[np.ndarray], state: CameraState, fps: float) -> np.ndarray:
    """Rolling long-exposure mean (about TIMEX_WINDOW_S seconds of frames)."""
    alpha = max(1.0 / (max(fps, 0.1) * TIMEX_WINDOW_S), 0.02)
    if state.timex is None or state.timex.shape != grays[0].shape:
        state.timex = grays[0].astype(np.float32)
    for gray in grays:
        cv2.accumulateWeighted(gray.astype(np.float32), state.timex, alpha)
    return state.timex


def mean_flow(grays: list[np.ndarray], fps: float, scale: float) -> np.ndarray:
    """Mean Farneback flow over the clip, in processed-frame px per second, on the scaled grid."""
    small = [cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) for g in grays]
    total = np.zeros((*small[0].shape, 2), np.float32)
    for prev, curr in zip(small, small[1:], strict=False):
        total += cv2.calcOpticalFlowFarneback(prev, curr, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    pairs = max(len(small) - 1, 1)
    return total / pairs / scale * fps


def seaward(flow: np.ndarray, vector: tuple[float, float]) -> np.ndarray:
    vx, vy = vector
    norm = math.hypot(vx, vy) or 1.0
    return (flow[:, :, 0] * vx + flow[:, :, 1] * vy) / norm


def _resize_to(field_: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    return cv2.resize(field_, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)


def _polygon(contour: np.ndarray, factor: float, width: int, height: int) -> list[tuple[int, int]]:
    """approxPolyDP until at most 32 points, scaled to processed pixels and clipped to the frame."""
    epsilon = 0.01 * cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    while len(approx) > MAX_POLYGON_POINTS:
        epsilon *= 1.5
        approx = cv2.approxPolyDP(contour, epsilon, True)
    points: list[tuple[int, int]] = []
    for x, y in approx.reshape(-1, 2):
        point = (
            int(np.clip(round(x * factor), 0, width)),
            int(np.clip(round(y * factor), 0, height)),
        )
        if point not in points:
            points.append(point)
    if len(points) < 3:
        x, y, w, h = cv2.boundingRect(contour)
        x0, y0 = int(x * factor), int(y * factor)
        x1, y1 = min(int((x + w) * factor), width), min(int((y + h) * factor), height)
        points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return points


def timex_score(timex: np.ndarray | None, mask: np.ndarray) -> float | None:
    """How much darker the region is than the scene median in the long exposure, in [0, 1]."""
    if timex is None or not mask.any():
        return None
    median = float(np.median(timex))
    region = float(timex[mask].mean())
    return float(np.clip(2.0 * (median - region) / (median + 1e-6), 0.0, 1.0))


def detect_regions(
    seaward_map: np.ndarray,
    history: list[np.ndarray],
    timex: np.ndarray | None,
    params: FlowParams,
    frame_shape: tuple[int, int],
) -> list[Region]:
    """Threshold the seaward flow, clean with morphology, keep contours above the minimum area."""
    height, width = frame_shape
    factor = 1.0 / params.flow_scale
    mask = (seaward_map > params.seaward_min_px_s).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions = []
    for contour in contours:
        area_px = float(cv2.contourArea(contour)) * factor * factor
        if area_px < params.min_rip_area_px:
            continue
        small_mask = np.zeros_like(mask)
        cv2.drawContours(small_mask, [contour], -1, 1, thickness=cv2.FILLED)
        inside = small_mask.astype(bool)
        speed = float(seaward_map[inside].mean())
        persistence = (
            float(np.mean([h[inside].mean() > params.seaward_min_px_s for h in history]))
            if history
            else 0.0
        )
        polygon = _polygon(contour, factor, width, height)
        full_mask = np.zeros((height, width), np.uint8)
        cv2.fillPoly(full_mask, [np.array(polygon, np.int32)], 1)
        t_score = timex_score(timex, full_mask.astype(bool))
        magnitude = min(speed / params.seaward_ref_px_s, 2.0)
        x, y, w, h = cv2.boundingRect(np.array(polygon, np.int32))
        regions.append(
            Region(
                polygon_px=polygon,
                bbox_px=(int(x), int(y), int(w), int(h)),
                area_px=round(area_px, 1),
                confidence=round(confidence(magnitude, persistence, t_score or 0.0), 4),
                seaward_px_s=round(speed, 2),
                flow_score=round(magnitude / 2.0, 4),
                timex_score=None if t_score is None else round(t_score, 4),
                persistence=persistence,
            )
        )
    return sorted(regions, key=lambda r: r.confidence, reverse=True)


# ---------------------------------------------------------------- modes


def analyze_video(
    frames: list[np.ndarray],
    state: CameraState,
    fps: float,
    params: FlowParams,
    timings: dict[str, float],
) -> Analysis:
    height, width = frames[0].shape[:2]
    with timed("preprocess", record=timings):
        grays, quality = preprocess(frames)
    with timed("stabilize", record=timings):
        grays, quality.camera_shake_px = stabilize(grays)
    with timed("timex", record=timings):
        timex = update_timex(grays, state, fps)
    if len(grays) < 2:
        quality.notes.append("single frame: no motion")
        return Analysis([], quality, width, height)
    with timed("flow", record=timings):
        current = seaward(mean_flow(grays, fps, params.flow_scale), params.seaward_vector)
        quarter = (max(1, height // 4), max(1, width // 4))
        state.flow_history.append(_resize_to(current, quarter))
        history = [_resize_to(h, current.shape) for h in state.flow_history]
        combined = np.mean(history, axis=0)
        state.prev_gray.extend(grays[-2:])
    with timed("detect", record=timings):
        regions = detect_regions(combined, history, timex, params, (height, width))
    return Analysis(regions, quality, width, height)


def _align_to_first(images: list[np.ndarray]) -> list[np.ndarray]:
    """ORB features + RANSAC homography onto the first image; unmatched images stay as they are."""
    orb = cv2.ORB_create(1000)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    base_kp, base_desc = orb.detectAndCompute(images[0], None)
    height, width = images[0].shape[:2]
    aligned = [images[0]]
    for image in images[1:]:
        kp, desc = orb.detectAndCompute(image, None)
        if base_desc is None or desc is None:
            aligned.append(image)
            continue
        matches = sorted(matcher.match(desc, base_desc), key=lambda m: m.distance)[:200]
        if len(matches) < 10:
            aligned.append(image)
            continue
        src = np.float32([kp[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
        dst = np.float32([base_kp[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
        homography, _ = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if homography is None:
            aligned.append(image)
            continue
        aligned.append(cv2.warpPerspective(image, homography, (width, height)))
    return aligned


def analyze_burst(
    frames: list[np.ndarray], params: FlowParams, timings: dict[str, float]
) -> Analysis:
    """Photos about 1 s apart: align, then coarse flow at 1 fps. Does not touch camera history."""
    height, width = frames[0].shape[:2]
    with timed("preprocess", record=timings):
        grays, quality = preprocess(frames)
    with timed("stabilize", record=timings):
        grays = _align_to_first(grays)
    quality.notes.append("burst: coarse motion")
    with timed("flow", record=timings):
        current = seaward(mean_flow(grays, 1.0, params.flow_scale), params.seaward_vector)
    with timed("detect", record=timings):
        timex = np.mean(np.stack(grays).astype(np.float32), axis=0)
        regions = detect_regions(current, [current], timex, params, (height, width))
    return Analysis(regions, quality, width, height)


def appearance_regions(gray: np.ndarray, params: FlowParams) -> list[tuple[np.ndarray, float]]:
    """Dark, low-texture gaps inside the bright surf band (contour, strength in [0, 1])."""
    texture = cv2.GaussianBlur(np.abs(cv2.Laplacian(gray, cv2.CV_32F)), (15, 15), 0)
    bright = gray > np.percentile(gray, 60)
    band_rows = bright.mean(axis=1) > 0.3
    if band_rows.sum() < 5:
        return []
    band = np.zeros_like(gray, dtype=bool)
    band[band_rows, :] = True
    candidate = (
        band & (gray < 0.75 * np.median(gray[band])) & (texture < 0.5 * np.median(texture[band]))
    ).astype(np.uint8)
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(candidate, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    band_area = float(band.sum())
    found = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area >= params.min_rip_area_px:
            found.append((contour, float(np.clip(area / (0.2 * band_area), 0.0, 1.0))))
    return sorted(found, key=lambda item: item[1], reverse=True)


def analyze_image(
    frames: list[np.ndarray], params: FlowParams, timings: dict[str, float]
) -> Analysis:
    """No motion in one photo: report at most `uncertain` from appearance, so the agent asks for
    a follow-up clip (north star 3)."""
    height, width = frames[0].shape[:2]
    with timed("preprocess", record=timings):
        grays, quality = preprocess(frames[:1])
    quality.notes.append("image: no motion evidence")
    regions = []
    with timed("detect", record=timings):
        for contour, strength in appearance_regions(grays[0], params)[:3]:
            polygon = _polygon(contour, 1.0, width, height)
            x, y, w, h = cv2.boundingRect(np.array(polygon, np.int32))
            regions.append(
                Region(
                    polygon_px=polygon,
                    bbox_px=(int(x), int(y), int(w), int(h)),
                    area_px=float(cv2.contourArea(contour)),
                    confidence=round(0.40 + 0.15 * strength, 4),
                    seaward_px_s=None,
                    flow_score=None,
                    timex_score=None,
                    persistence=None,
                    detector_score=round(strength, 4),
                )
            )
    return Analysis(regions, quality, width, height)
