"""Video clips (.mp4, .mov): RipVIS replay and dashboard uploads."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2

from rw.adapters.base import (
    DEFAULT_MAX_WIDTH,
    DEFAULT_TARGET_FPS,
    Frame,
    UnsupportedInput,
    fit_width,
)
from rw.contracts.vision import Mode

FALLBACK_FPS = 15.0


class VideoFileSource:
    mode = Mode.VIDEO

    def __init__(
        self, path: str | Path, camera_id: str, source_id: str, start_ts: datetime | None = None
    ) -> None:
        self.path = Path(path)
        self.camera_id = camera_id
        self.source_id = source_id
        self.start_ts = start_ts or datetime.now(UTC)
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            raise UnsupportedInput(f"cannot open video {self.path.name}")
        fps = capture.get(cv2.CAP_PROP_FPS)
        capture.release()
        self._fps = float(fps) if fps and fps > 0 else FALLBACK_FPS

    @property
    def fps_source(self) -> float:
        return self._fps

    def iter_frames(
        self, target_fps: float = DEFAULT_TARGET_FPS, max_width: int = DEFAULT_MAX_WIDTH
    ) -> Iterator[Frame]:
        """Every Nth frame (output at most target_fps), timestamped from the clip start."""
        step = max(1, round(self._fps / target_fps))
        capture = cv2.VideoCapture(str(self.path))
        try:
            read_index = out_index = 0
            while True:
                ok, image = capture.read()
                if not ok:
                    break
                if read_index % step == 0:
                    yield Frame(
                        image=fit_width(image, max_width),
                        ts=self.start_ts + timedelta(seconds=read_index / self._fps),
                        index=out_index,
                        source_id=self.source_id,
                        camera_id=self.camera_id,
                    )
                    out_index += 1
                read_index += 1
        finally:
            capture.release()
