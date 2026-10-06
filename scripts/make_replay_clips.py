"""Cut RipVIS test videos into 10 s replay clips per simulated camera (sprint-1.md N-13).

    uv run python scripts/make_replay_clips.py                 # uses scripts/replay_cameras.yaml
    uv run python scripts/make_replay_clips.py --dry-run       # print the ffmpeg commands only

Writes data/replay/clips/<camera_id>/<video>_<nnnn>.mp4 (960 px wide, 15 fps, no audio, H.264).
rw-camera-sim later copies them, in name order, into incoming/ as if a camera uploaded them.
Needs ffmpeg on PATH (local only).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "scripts" / "replay_cameras.yaml"


def load_cameras(path: Path) -> dict[str, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        cam: list((spec or {}).get("videos") or [])
        for cam, spec in (data.get("cameras") or {}).items()
    }


def ffmpeg_command(source: Path, out_dir: Path) -> list[str]:
    pattern = out_dir / f"{source.stem}_%04d.mp4"
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-vf", "scale=960:-2", "-r", "15", "-an",
        "-c:v", "libx264", "-crf", "23",
        "-f", "segment", "-segment_time", "10", "-reset_timestamps", "1",
        str(pattern),
    ]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cut replay clips with ffmpeg")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "ripvis")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "replay" / "clips")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cameras = load_cameras(args.config)
    if not any(cameras.values()):
        print(f"No videos listed in {args.config}; Saif picks them (sprint-1.md N-13).")
        return 1
    if not args.dry_run and shutil.which("ffmpeg") is None:
        print(
            "ffmpeg not found on PATH. Install it (WSL: sudo apt install ffmpeg).", file=sys.stderr
        )
        return 1

    for camera_id, videos in cameras.items():
        out_dir = args.out / camera_id
        for video in videos:
            source = args.data / video
            command = ffmpeg_command(source, out_dir)
            print(" ".join(command))
            if args.dry_run:
                continue
            if not source.exists():
                print(
                    f"missing {source}; run scripts/download_ripvis.py --parts test",
                    file=sys.stderr,
                )
                return 1
            out_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(command, check=True)  # noqa: S603
        if not args.dry_run:
            count = len(list(out_dir.glob("*.mp4")))
            print(f"{camera_id}: {count} clips in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
