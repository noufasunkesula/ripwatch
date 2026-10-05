"""The slots Saif's model plugs into (sprint-1.md N-11, section 12 risk 1).

`Detector` finds rips in frames; `SwimmerDetector` finds people in one frame. Both return plain
dataclasses in processed-frame pixel coordinates; the pipeline turns them into contract models,
assigns IDs and fuses them with the baseline flow evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class RipDetection:
    polygon_px: list[tuple[int, int]]  # at least 3 points, processed-frame pixels
    confidence: float  # [0, 1]
    score: float | None = None  # raw model score, kept as evidence.detector_score


@dataclass(frozen=True)
class SwimmerBox:
    bbox_px: tuple[int, int, int, int]  # x, y, w, h
    confidence: float


class Detector(Protocol):
    name: str
    version: str

    def detect(self, frames: list[np.ndarray]) -> list[RipDetection]: ...


class SwimmerDetector(Protocol):
    def detect(self, frame: np.ndarray) -> list[SwimmerBox]: ...


@dataclass
class Track:
    """One swimmer across frames and clips (IoU association)."""

    track_id: str
    bbox_px: tuple[int, int, int, int]
    confidence: float
    history: list[tuple[float, tuple[float, float]]] = field(default_factory=list)  # (t, center)
