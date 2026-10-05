"""Photo bursts: one .zip of 2 to 10 images taken seconds apart (Decision Log 2026-10-01)."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from rw.adapters.base import (
    DEFAULT_MAX_WIDTH,
    DEFAULT_TARGET_FPS,
    Frame,
    UnsupportedInput,
    decode_image,
    fit_width,
)
from rw.adapters.frames import IMAGE_SUFFIXES
from rw.contracts.vision import Mode

MIN_IMAGES = 2
MAX_IMAGES = 10
# EXIF DateTimeOriginal is stored as ASCII "YYYY:MM:DD HH:MM:SS". Searching the APP1 segment for
# it avoids an image library (Lambdas and the worker stay on OpenCV only).
_EXIF_TIME = re.compile(rb"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")
_EXIF_SCAN_BYTES = 65536


def exif_time(data: bytes) -> datetime | None:
    """First EXIF timestamp in a JPEG's APP1 segment, read as UTC; None if absent or invalid."""
    head = data[:_EXIF_SCAN_BYTES]
    start = head.find(b"Exif\x00\x00")
    if start < 0:
        return None
    match = _EXIF_TIME.search(head, start)
    if not match:
        return None
    try:
        return datetime(*(int(g) for g in match.groups()), tzinfo=UTC)
    except ValueError:
        return None


class BurstSource:
    mode = Mode.BURST

    def __init__(
        self, path: str | Path, camera_id: str, source_id: str, start_ts: datetime | None = None
    ) -> None:
        self.path = Path(path)
        self.camera_id = camera_id
        self.source_id = source_id
        self.start_ts = start_ts or datetime.now(UTC)
        try:
            archive = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as exc:
            raise UnsupportedInput(f"{self.path.name} is not a zip") from exc
        with archive:
            names = sorted(
                n
                for n in archive.namelist()
                if not n.endswith("/")
                and not PurePosixPath(n).name.startswith(".")
                and PurePosixPath(n).suffix.lower() in IMAGE_SUFFIXES
            )
            if not MIN_IMAGES <= len(names) <= MAX_IMAGES:
                raise UnsupportedInput(
                    f"burst needs {MIN_IMAGES} to {MAX_IMAGES} images, "
                    f"{self.path.name} has {len(names)}"
                )
            self._raw = [(n, archive.read(n)) for n in names]

    @property
    def fps_source(self) -> None:
        return None

    def _timestamps(self) -> list[datetime]:
        times = [exif_time(data) for _, data in self._raw]
        if all(t is not None for t in times) and times == sorted(times):
            return times  # type: ignore[return-value]
        return [self.start_ts + timedelta(seconds=i) for i in range(len(self._raw))]

    def iter_frames(
        self, target_fps: float = DEFAULT_TARGET_FPS, max_width: int = DEFAULT_MAX_WIDTH
    ) -> Iterator[Frame]:
        for index, ((name, data), ts) in enumerate(zip(self._raw, self._timestamps(), strict=True)):
            yield Frame(
                image=fit_width(decode_image(data, name), max_width),
                ts=ts,
                index=index,
                source_id=self.source_id,
                camera_id=self.camera_id,
            )
