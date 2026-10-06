"""Folders of ordered frames (.jpg/.png), e.g. RipVIS sampled_images."""

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
from rw.common.config import get_settings
from rw.contracts.vision import Mode

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})


class FrameFolderSource:
    mode = Mode.VIDEO

    def __init__(
        self,
        folder: str | Path,
        camera_id: str,
        source_id: str,
        start_ts: datetime | None = None,
        fps: float | None = None,
    ) -> None:
        self.folder = Path(folder)
        self.camera_id = camera_id
        self.source_id = source_id
        self.start_ts = start_ts or datetime.now(UTC)
        self._fps = fps or get_settings().frame_folder_fps
        self.files = sorted(p for p in self.folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        if not self.files:
            raise UnsupportedInput(f"no .jpg/.png frames in {self.folder}")

    @property
    def fps_source(self) -> float:
        return self._fps

    def iter_frames(
        self, target_fps: float = DEFAULT_TARGET_FPS, max_width: int = DEFAULT_MAX_WIDTH
    ) -> Iterator[Frame]:
        step = max(1, round(self._fps / target_fps))
        for out_index, read_index in enumerate(range(0, len(self.files), step)):
            path = self.files[read_index]
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise UnsupportedInput(f"cannot decode frame {path.name}")
            yield Frame(
                image=fit_width(image, max_width),
                ts=self.start_ts + timedelta(seconds=read_index / self._fps),
                index=out_index,
                source_id=self.source_id,
                camera_id=self.camera_id,
            )
