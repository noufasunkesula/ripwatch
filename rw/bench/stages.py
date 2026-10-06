"""Stage-by-stage timing of the baseline pipeline on the same frames (sprint-1.md N-14).

Decode is timed per frame. The other stages work on a clip at a time (stabilize, flow and
detect need neighbouring frames), so each one is timed per clip of CLIP_FRAMES frames and divided
by the frame count: "per-frame latency" for those stages is the clip time per frame. The first
`warmup` frames run through every stage but are not recorded.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from rw.adapters.factory import source_for
from rw.vision import baseline_flow as bf
from rw.vision.state import CameraState

STAGES = ("decode", "preprocess", "stabilize", "timex", "flow", "detect")
CLIP_FRAMES = 50  # 10 s at the 5 fps the pipeline processes
VIDEO_SUFFIXES = (".mp4", ".mov")


@dataclass
class ClipTiming:
    clip: int
    frames: int
    ms_per_frame: dict[str, float]  # stage -> milliseconds per frame, plus "total"
    regions: int


@dataclass
class Frames:
    images: list[np.ndarray] = field(default_factory=list)
    decode_ms: list[float] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)


def video_files(folder: Path) -> list[Path]:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
    if not files:
        raise FileNotFoundError(f"no .mp4/.mov clips in {folder}")
    return files


def decode(files: list[Path], count: int, target_fps: float, max_width: int) -> Frames:
    """`count` frames from the clips in order, looping over them if they are shorter."""
    out = Frames()
    while len(out.images) < count:
        for path in files:
            source = source_for(path, "cam-00", f"bench/{path.stem}")
            frames: Iterator = source.iter_frames(target_fps, max_width)
            out.inputs.append(path.name)
            while len(out.images) < count:
                started = time.perf_counter()
                frame = next(frames, None)
                elapsed = (time.perf_counter() - started) * 1000
                if frame is None:
                    break
                out.images.append(frame.image)
                out.decode_ms.append(elapsed)
            if len(out.images) >= count:
                break
    return out


def _timed(spent: dict[str, float], stage: str, fn: Callable[..., Any], *args: Any) -> Any:
    started = time.perf_counter()
    value = fn(*args)
    spent[stage] = (time.perf_counter() - started) * 1000
    return value


def _seaward_flow(grays: list[np.ndarray], fps: float, params: bf.FlowParams) -> np.ndarray:
    return bf.seaward(bf.mean_flow(grays, fps, params.flow_scale), params.seaward_vector)


def run_clips(
    frames: Frames, warmup: int, fps: float, params: bf.FlowParams | None = None
) -> list[ClipTiming]:
    """Run every stage per clip; clips that start inside the warmup are not recorded."""
    params = params or bf.FlowParams()
    state = CameraState("cam-00")
    rows: list[ClipTiming] = []
    for number, start in enumerate(range(0, len(frames.images), CLIP_FRAMES)):
        images = frames.images[start : start + CLIP_FRAMES]
        if len(images) < 2:
            break
        spent: dict[str, float] = {}
        grays, _ = _timed(spent, "preprocess", bf.preprocess, images)
        grays, _ = _timed(spent, "stabilize", bf.stabilize, grays)
        timex = _timed(spent, "timex", bf.update_timex, grays, state, fps)
        flow = _timed(spent, "flow", _seaward_flow, grays, fps, params)
        height, width = images[0].shape[:2]
        state.flow_history.append(bf._resize_to(flow, (max(1, height // 4), max(1, width // 4))))
        history = [bf._resize_to(h, flow.shape) for h in state.flow_history]
        combined = np.mean(history, axis=0)
        regions = _timed(spent, "detect", bf.detect_regions, combined, history, timex, params,
                        (height, width))  # fmt: skip
        if start < warmup:
            continue
        n = len(images)
        per_frame = {stage: ms / n for stage, ms in spent.items()}
        per_frame["decode"] = float(np.mean(frames.decode_ms[start : start + n]))
        per_frame["total"] = sum(per_frame[s] for s in STAGES)
        rows.append(ClipTiming(number, n, per_frame, len(regions)))
    return rows
