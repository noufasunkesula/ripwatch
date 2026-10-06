"""python -m rw.bench: per-stage latency, FPS, CPU, memory and cost of the baseline pipeline.

    python -m rw.bench --inputs <dir or s3://bucket/prefix> --runtime <cool|std-arm|std-x86>
                       --frames 300 --warmup 50 --out work/bench/<run_id>/

Without --inputs it generates the synthetic clip (scripts/make_synthetic_clip.py) into the output
folder, which is what `make bench-local` uses. Writes results.csv, summary.json, build_info.txt and
one chart per stage (sprint-1.md N-14). A laptop run is not a benchmark result: the instance type
is "local" and no cost is computed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2

from rw.bench import report, stages
from rw.common.runtime import check_cv2_runtime

IMDS = "http://169.254.169.254/latest"
TARGET_FPS = 5.0
MAX_WIDTH = 640


def instance_type(timeout_s: float = 0.5) -> str:
    """EC2 instance type via IMDSv2, or "local" when not on EC2."""
    try:
        token_req = urllib.request.Request(  # noqa: S310 (fixed IMDS URL)
            f"{IMDS}/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )  # fmt: skip
        with urllib.request.urlopen(token_req, timeout=timeout_s) as r:  # noqa: S310 (IMDS)
            token = r.read().decode()
        type_req = urllib.request.Request(  # noqa: S310 (fixed IMDS URL)
            f"{IMDS}/meta-data/instance-type", headers={"X-aws-ec2-metadata-token": token}
        )
        with urllib.request.urlopen(type_req, timeout=timeout_s) as r:  # noqa: S310 (IMDS)
            return r.read().decode().strip() or "local"
    except OSError:
        return "local"


def cpu_seconds() -> float:
    """utime + stime of this process: /proc/self/stat on Linux, process_time elsewhere."""
    try:
        fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError, AttributeError):
        return time.process_time()


def max_rss_mb() -> float | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(rss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def fetch_inputs(inputs: str | None, out: Path) -> Path:
    folder = out / "inputs"
    if inputs is None:
        from scripts.make_synthetic_clip import write_clip

        write_clip(folder / "synthetic_rip.mp4")
        return folder
    if inputs.startswith("s3://"):
        from rw.common.aws import client

        bucket, _, prefix = inputs.removeprefix("s3://").partition("/")
        s3 = client("s3")
        folder.mkdir(parents=True, exist_ok=True)
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].lower().endswith(stages.VIDEO_SUFFIXES):
                    s3.download_file(bucket, obj["Key"], str(folder / Path(obj["Key"]).name))
        return folder
    return Path(inputs)


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out or f"work/bench/{run_id}")
    out.mkdir(parents=True, exist_ok=True)
    files = stages.video_files(fetch_inputs(args.inputs, out))
    itype = instance_type()
    cv2_info = check_cv2_runtime(itype)
    (out / "build_info.txt").write_text(cv2.getBuildInformation(), encoding="utf-8")

    cpu0, wall0 = cpu_seconds(), time.perf_counter()
    frames = stages.decode(files, args.frames + args.warmup, TARGET_FPS, MAX_WIDTH)
    rows = stages.run_clips(frames, args.warmup, TARGET_FPS)
    wall, cpu = time.perf_counter() - wall0, cpu_seconds() - cpu0
    if not rows:
        raise SystemExit("no clips measured: raise --frames or lower --warmup")

    stats = report.summarize(rows)
    measured = sum(r.frames for r in rows)
    processing_s = sum(r.ms_per_frame["total"] * r.frames for r in rows) / 1000
    per_video_s = processing_s / (measured / TARGET_FPS)
    prices = report.load_prices()
    report.write_csv(out / "results.csv", rows)
    charts = report.write_charts(out, rows, args.runtime)
    summary = {
        "run_id": run_id,
        "runtime": args.runtime,
        "instance_type": itype,
        "machine": platform.machine(),
        "cv2": cv2_info,
        "inputs": sorted(set(frames.inputs)),
        "frames_measured": measured,
        "warmup_frames": args.warmup,
        "target_fps": TARGET_FPS,
        "fps": round(measured / processing_s, 2) if processing_s else None,
        "stages_ms_per_frame": stats,
        "processing_s_per_video_s": round(per_video_s, 6),
        "cost_per_camera_hour": report.cost_per_camera_hour(
            prices, args.runtime, itype, per_video_s
        ),
        "cpu_percent": round(100 * cpu / wall, 1) if wall else None,
        "max_rss_mb": max_rss_mb(),
        "charts": charts or "skipped (matplotlib not installed)",
        "note": "local run, not a benchmark result" if itype == "local" else None,
    }
    report.write_summary(out / "summary.json", summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--inputs", help="folder or s3://bucket/prefix of clips (default: synthetic)"
    )
    parser.add_argument(
        "--runtime",
        choices=("cool", "std-arm", "std-x86"),
        default=os.environ.get("RW_RUNTIME")
        or ("std-arm" if platform.machine().lower() in {"arm64", "aarch64"} else "std-x86"),
    )
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--out", help="output folder (default work/bench/<run_id>/)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    summary = run(parse_args(argv))
    total = summary["stages_ms_per_frame"]["total"]
    print(json.dumps({k: summary[k] for k in ("run_id", "runtime", "instance_type", "fps",
                                               "cost_per_camera_hour")}, default=str))  # fmt: skip
    print(f"total ms/frame: mean {total['mean_ms']}, p95 {total['p95_ms']}")


if __name__ == "__main__":
    main()
