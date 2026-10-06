"""Bench outputs (sprint-1.md N-14): results.csv, summary.json, one PNG chart per stage.

Charts need matplotlib (dev extra). Without it, the CSV and JSON are still written and the
summary says the charts were skipped.
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

import yaml

from rw.bench.stages import STAGES, ClipTiming

PRICES_FILE = Path(__file__).with_name("prices.yaml")


def load_prices(path: Path = PRICES_FILE) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def cost_per_camera_hour(
    prices: dict[str, Any], runtime: str, instance_type: str, processing_s_per_video_s: float
) -> dict[str, Any]:
    """(instance $/h + COOL fee $/h if runtime is cool) x processing seconds per video second.

    Only for instances priced in prices.yaml; a laptop ("local") gets no cost, not a guess.
    """
    instance = prices["instances"].get(instance_type)
    if instance is None:
        return {"usd": None, "note": f"no price for instance type {instance_type!r}"}
    hourly = float(instance["price_per_h"])
    if runtime == "cool":
        hourly += float(prices["cool_fee"]["price_per_h"])
    return {
        "usd": round(hourly * processing_s_per_video_s, 6),
        "hourly_usd": hourly,
        "instance_type": instance_type,
        "prices_checked_on": instance.get("checked_on"),
    }


def _p(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarize(rows: list[ClipTiming]) -> dict[str, dict[str, float]]:
    out = {}
    for stage in (*STAGES, "total"):
        values = [r.ms_per_frame[stage] for r in rows]
        out[stage] = {
            "mean_ms": round(statistics.fmean(values), 4),
            "p50_ms": round(_p(values, 0.5), 4),
            "p95_ms": round(_p(values, 0.95), 4),
        }
    return out


def write_csv(path: Path, rows: list[ClipTiming]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["clip", "frames", "regions", *[f"{s}_ms_per_frame" for s in STAGES],
                         "total_ms_per_frame"])  # fmt: skip
        for r in rows:
            times = [round(r.ms_per_frame[s], 4) for s in (*STAGES, "total")]
            writer.writerow([r.clip, r.frames, r.regions, *times])


def write_charts(folder: Path, rows: list[ClipTiming], runtime: str) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    written = []
    for stage in (*STAGES, "total"):
        fig, ax = plt.subplots(figsize=(6, 3.2))
        ax.plot([r.clip for r in rows], [r.ms_per_frame[stage] for r in rows], marker="o")
        ax.set_title(f"{stage} ({runtime})")
        ax.set_xlabel("clip")
        ax.set_ylabel("ms per frame")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        name = f"{stage}.png"
        fig.savefig(folder / name, dpi=110)
        plt.close(fig)
        written.append(name)
    return written


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
