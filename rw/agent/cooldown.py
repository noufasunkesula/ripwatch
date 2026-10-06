"""Duplicate suppression (sprint-1.md D-05 step 2).

If this camera had a decision in the last cooldown_s for a rip whose polygon overlaps the current
top rip (IoU > 0.3) and there is no active incident, the candidate is a duplicate: no model call.
In memory, per rw-agent process; a restart only costs one extra decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import cv2
import numpy as np

IOU_THRESHOLD = 0.3


def polygon_iou(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> float:
    """IoU of two pixel polygons, rasterized on the smallest canvas that holds both."""
    points = np.array(a + b, np.int32)
    width, height = int(points[:, 0].max()) + 2, int(points[:, 1].max()) + 2
    mask_a = np.zeros((height, width), np.uint8)
    mask_b = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask_a, [np.array(a, np.int32)], 1)
    cv2.fillPoly(mask_b, [np.array(b, np.int32)], 1)
    union = int(np.count_nonzero(mask_a | mask_b))
    return int(np.count_nonzero(mask_a & mask_b)) / union if union else 0.0


@dataclass
class _Seen:
    polygon: list[tuple[int, int]]
    at: datetime


class Cooldown:
    def __init__(self) -> None:
        self._seen: dict[str, list[_Seen]] = {}

    def record(self, camera_id: str, polygon: list[tuple[int, int]] | None, at: datetime) -> None:
        if polygon:
            self._seen.setdefault(camera_id, []).append(_Seen(list(polygon), at))

    def is_duplicate(
        self,
        camera_id: str,
        polygon: list[tuple[int, int]] | None,
        now: datetime,
        cooldown_s: float,
    ) -> bool:
        if not polygon:
            return False
        recent = [
            s for s in self._seen.get(camera_id, []) if now - s.at <= timedelta(seconds=cooldown_s)
        ]
        self._seen[camera_id] = recent
        return any(polygon_iou(s.polygon, list(polygon)) > IOU_THRESHOLD for s in recent)
