"""Per-camera memory between clips (sprint-1.md N-11).

Held in memory by rw-ingest and lost on restart. That is acceptable: the first clip after a
restart simply has no history (persistence starts again), which is documented in the runbook.
An out-of-order clip is processed with a fresh `CameraState` and never updates the real one.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from rw.vision.detector import Track

FLOW_HISTORY_CLIPS = 6
TIMEX_WINDOW_S = 60.0


@dataclass
class KnownRip:
    rip_id: str
    bbox_px: tuple[int, int, int, int]
    first_seen_ts: datetime
    last_seen_ts: datetime


@dataclass
class CameraState:
    camera_id: str
    prev_gray: deque[np.ndarray] = field(default_factory=lambda: deque(maxlen=2))
    timex: np.ndarray | None = None  # float32 running mean, processed resolution
    # Seaward flow (px/s) per clip at 1/4 processed resolution, newest last.
    flow_history: deque[np.ndarray] = field(
        default_factory=lambda: deque(maxlen=FLOW_HISTORY_CLIPS)
    )
    tracks: dict[str, Track] = field(default_factory=dict)
    rips: dict[str, KnownRip] = field(default_factory=dict)
    next_rip_number: int = 1
    next_track_number: int = 1
    last_seq: int | None = None

    def new_rip_id(self) -> str:
        rip_id = f"{self.camera_id}-rip-{self.next_rip_number:04d}"
        self.next_rip_number += 1
        return rip_id

    def new_track_id(self) -> str:
        track_id = f"{self.camera_id}-sw-{self.next_track_number:04d}"
        self.next_track_number += 1
        return track_id
