"""A single uploaded photo (mode image)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from rw.adapters.base import (
    DEFAULT_MAX_WIDTH,
    DEFAULT_TARGET_FPS,
    Frame,
    decode_image,
    fit_width,
)
from rw.contracts.vision import Mode


class ImageSource:
    mode = Mode.IMAGE

    def __init__(
        self, path: str | Path, camera_id: str, source_id: str, start_ts: datetime | None = None
    ) -> None:
        self.path = Path(path)
        self.camera_id = camera_id
        self.source_id = source_id
        self.start_ts = start_ts or datetime.now(UTC)
        self._image = decode_image(self.path.read_bytes(), self.path.name)

    @property
    def fps_source(self) -> None:
        return None

    def iter_frames(
        self, target_fps: float = DEFAULT_TARGET_FPS, max_width: int = DEFAULT_MAX_WIDTH
    ) -> Iterator[Frame]:
        yield Frame(
            image=fit_width(self._image, max_width),
            ts=self.start_ts,
            index=0,
            source_id=self.source_id,
            camera_id=self.camera_id,
        )
