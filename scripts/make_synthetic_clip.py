"""Synthetic beach clip for tests and local demos (sprint-1.md N-11).

Sea is at the top of the frame (seaward vector [0, -1]). Bright sinusoidal waves roll down toward
the shore; a darker textured strip in the middle flows up, out to sea: the motion signature of a rip
current. `--static` writes the same frame for the whole clip (no motion), which must read as clear.

    uv run python scripts/make_synthetic_clip.py --out work/synthetic_rip.mp4
    uv run python scripts/make_synthetic_clip.py --out work/static.mp4 --static
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

WIDTH, HEIGHT, FPS, SECONDS = 640, 360, 15, 10
WAVE_PERIOD_PX = 40
WAVE_SPEED_PX = 2.0  # per source frame, toward shore (down)
RIP_SPEED_PX = 1.5  # per source frame, seaward (up); 4.5 px per processed frame at 5 fps
RIP_X = (280, 360)  # strip columns


def _rip_texture(height: int, width: int, length: int, rng: np.random.Generator) -> np.ndarray:
    """Tall blurred-noise texture the strip window slides over."""
    # Blobs about 15 px across, stretched back to full contrast after blurring: coarse enough
    # that optical flow is not ambiguous at 4.5 px per processed frame.
    noise = rng.integers(0, 255, size=(height + length, width), dtype=np.uint8)
    blurred = cv2.GaussianBlur(noise, (0, 0), 5.0)
    return cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX)


def make_frames(
    seconds: float = SECONDS,
    fps: int = FPS,
    width: int = WIDTH,
    height: int = HEIGHT,
    static: bool = False,
    seed: int = 0,
) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    count = max(1, round(seconds * fps))
    strip_w = RIP_X[1] - RIP_X[0]
    texture = _rip_texture(height, strip_w, int(count * RIP_SPEED_PX) + 1, rng)
    grain = rng.normal(0, 6, size=(height, width))  # fixed sensor-like grain
    ys = np.arange(height, dtype=np.float32)[:, None]

    frames = []
    for i in range(count):
        t = 0 if static else i
        waves = 120 + 70 * np.sin(2 * np.pi * (ys - WAVE_SPEED_PX * t) / WAVE_PERIOD_PX)
        gray = np.repeat(waves, width, axis=1) + grain
        offset = int(round(RIP_SPEED_PX * t))
        strip = texture[offset : offset + height].astype(np.float32)
        gray[:, RIP_X[0] : RIP_X[1]] = 0.45 * strip + 25  # darker, textured, moving up
        gray = np.clip(gray, 0, 255).astype(np.uint8)
        # Blue-green sea tint so the clip looks like water in a player.
        tint = [gray, (gray * 0.9).astype(np.uint8), (gray * 0.6).astype(np.uint8)]
        frames.append(cv2.merge(tint))
    return frames


def write_clip(path: str | Path, **kwargs: object) -> Path:
    """Write an mp4 (mp4v codec) and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = make_frames(**kwargs)  # type: ignore[arg-type]
    height, width = frames[0].shape[:2]
    fps = int(kwargs.get("fps", FPS))  # type: ignore[call-overload]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"cannot open a video writer for {path}")
    for frame in frames:
        writer.write(frame)
    writer.release()
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="work/synthetic_rip.mp4")
    parser.add_argument("--static", action="store_true", help="no motion (expected: clear)")
    parser.add_argument("--seconds", type=float, default=SECONDS)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    out = write_clip(args.out, seconds=args.seconds, static=args.static, seed=args.seed)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
