"""Frames and the source protocol every input adapter implements (sprint-1.md N-11).

An adapter turns one input (video, frame folder, image, burst zip) into `Frame`s with timestamps,
already resized to the processing width. The vision pipeline only ever sees `Frame`s.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import cv2
import numpy as np

from rw.contracts.vision import Mode

DEFAULT_TARGET_FPS = 5.0
DEFAULT_MAX_WIDTH = 640


class UnsupportedInput(ValueError):
    """The file type has no adapter, or the file cannot be decoded."""


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR, uint8, already resized to max_width
    ts: datetime  # timezone-aware UTC
    index: int  # position in the processed sequence, from 0
    source_id: str
    camera_id: str


class FrameSource(Protocol):
    mode: Mode
    camera_id: str
    source_id: str

    @property
    def fps_source(self) -> float | None: ...

    def iter_frames(
        self, target_fps: float = DEFAULT_TARGET_FPS, max_width: int = DEFAULT_MAX_WIDTH
    ) -> Iterator[Frame]: ...


def fit_width(image: np.ndarray, max_width: int) -> np.ndarray:
    """Shrink to max_width keeping aspect (INTER_AREA). Never upscales."""
    height, width = image.shape[:2]
    if width <= max_width:
        return image
    new_height = max(1, round(height * max_width / width))
    return cv2.resize(image, (max_width, new_height), interpolation=cv2.INTER_AREA)


def decode_image(data: bytes, name: str) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise UnsupportedInput(f"cannot decode image {name}")
    return image
