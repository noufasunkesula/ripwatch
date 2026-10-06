"""Overlays and crops for evidence snapshots and zoom_and_recheck (sprint-1.md N-11 hand-off)."""

from __future__ import annotations

import cv2
import numpy as np

from rw.contracts.vision import RipLabel, VisionResult

RIP_COLOUR = (0, 0, 255)  # BGR red
UNCERTAIN_COLOUR = (0, 200, 255)  # amber
SWIMMER_COLOUR = (255, 200, 0)  # cyan-blue


def draw_overlay(image: np.ndarray, result: VisionResult) -> np.ndarray:
    """Copy of image with rip polygons, labels, swimmer boxes and the status line."""
    out = image.copy()
    for rip in result.rips:
        colour = RIP_COLOUR if rip.label == RipLabel.RIP else UNCERTAIN_COLOUR
        polygon = np.array(rip.polygon_px, np.int32).reshape(-1, 1, 2)
        overlay = out.copy()
        cv2.fillPoly(overlay, [polygon], colour)
        out = cv2.addWeighted(overlay, 0.25, out, 0.75, 0)
        cv2.polylines(out, [polygon], True, colour, 2)
        x, y, _, _ = rip.bbox_px
        cv2.putText(
            out,
            f"{rip.label.value} {rip.confidence:.2f}",
            (x, max(12, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            colour,
            1,
            cv2.LINE_AA,
        )
    for swimmer in result.swimmers:
        x, y, w, h = swimmer.bbox_px
        colour = RIP_COLOUR if swimmer.in_rip_id else SWIMMER_COLOUR
        cv2.rectangle(out, (x, y), (x + w, y + h), colour, 2)
    status = f"{result.camera_id} {result.summary.status.value} {result.summary.max_confidence:.2f}"
    cv2.putText(
        out, status, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA
    )
    return out


def crop(image: np.ndarray, bbox_px: tuple[int, int, int, int], zoom: float = 2.0) -> np.ndarray:
    """Region around bbox, padded so the crop shows context, upscaled by `zoom` (cubic)."""
    height, width = image.shape[:2]
    x, y, w, h = bbox_px
    pad_x, pad_y = max(8, w // 4), max(8, h // 4)
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(width, x + w + pad_x), min(height, y + h + pad_y)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"bbox {bbox_px} is outside the {width}x{height} image")
    region = image[y0:y1, x0:x1]
    return cv2.resize(region, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
