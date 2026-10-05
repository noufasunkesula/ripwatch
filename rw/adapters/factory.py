"""Pick the adapter for an input file by extension (sprint-1.md N-11)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rw.adapters.base import FrameSource, UnsupportedInput
from rw.adapters.burst import BurstSource
from rw.adapters.image import ImageSource
from rw.adapters.video import VideoFileSource

_BY_SUFFIX = {
    ".mp4": VideoFileSource,
    ".mov": VideoFileSource,
    ".jpg": ImageSource,
    ".jpeg": ImageSource,
    ".png": ImageSource,
    ".zip": BurstSource,
}


def source_for(
    path: str | Path, camera_id: str, source_id: str, start_ts: datetime | None = None
) -> FrameSource:
    """Video, image or burst source for path; any other type raises UnsupportedInput."""
    suffix = Path(path).suffix.lower()
    adapter = _BY_SUFFIX.get(suffix)
    if adapter is None:
        raise UnsupportedInput(f"unsupported input type {suffix or '(none)'}: {Path(path).name}")
    return adapter(path, camera_id, source_id, start_ts)
