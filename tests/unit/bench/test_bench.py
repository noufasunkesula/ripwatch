"""rw.bench (sprint-1.md N-14): a short run on the synthetic clip, the cost formula, prices."""

from __future__ import annotations

import csv
import json

import pytest

from rw.bench import __main__ as bench
from rw.bench import report, stages
from scripts.make_synthetic_clip import write_clip


@pytest.fixture(scope="module")
def clip_dir(tmp_path_factory):
    folder = tmp_path_factory.mktemp("bench-inputs")
    write_clip(folder / "rip.mp4", seconds=4)  # 20 frames at 5 fps: decode loops over it
    return folder


def test_short_run_writes_csv_summary_charts_and_build_info(clip_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "instance_type", lambda: "local")
    out = tmp_path / "run"

    summary = bench.run(bench.parse_args(
        ["--inputs", str(clip_dir), "--runtime", "std-x86", "--frames", "100", "--warmup", "50",
         "--out", str(out)]
    ))  # fmt: skip

    assert summary["frames_measured"] == 100 and summary["warmup_frames"] == 50
    assert set(summary["stages_ms_per_frame"]) == {*stages.STAGES, "total"}
    assert summary["fps"] > 0 and summary["processing_s_per_video_s"] > 0
    assert summary["cost_per_camera_hour"]["usd"] is None  # a laptop has no price
    assert summary["note"] == "local run, not a benchmark result"
    assert summary["cv2"]["opencv_version"]
    with (out / "results.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [int(r["clip"]) for r in rows] == [1, 2]  # clip 0 was the warmup
    assert all(float(r["total_ms_per_frame"]) > 0 for r in rows)
    assert json.loads((out / "summary.json").read_text())["run_id"] == summary["run_id"]
    assert "General configuration" in (out / "build_info.txt").read_text()
    assert sorted(summary["charts"]) == sorted(f"{s}.png" for s in (*stages.STAGES, "total"))
    assert all((out / name).stat().st_size > 0 for name in summary["charts"])


def test_rip_clip_is_seen_by_the_detect_stage(clip_dir):
    frames = stages.decode(stages.video_files(clip_dir), 100, 5.0, 640)
    rows = stages.run_clips(frames, warmup=0, fps=5.0)
    assert len(frames.images) == 100 and len(frames.decode_ms) == 100
    assert any(r.regions for r in rows)


def test_cost_per_camera_hour():
    prices = report.load_prices()
    std = report.cost_per_camera_hour(prices, "std-arm", "c7g.large", 0.2)
    cool = report.cost_per_camera_hour(prices, "cool", "c7g.large", 0.2)
    assert std["usd"] == pytest.approx(0.0725 * 0.2)
    assert cool["usd"] == pytest.approx((0.0725 + 0.01) * 0.2)
    assert report.cost_per_camera_hour(prices, "std-x86", "local", 0.2)["usd"] is None


def test_prices_file_has_sources_and_the_runtime_map():
    prices = report.load_prices()
    assert set(prices["instances"]) == {"c7g.large", "c7i.large"}
    for entry in (*prices["instances"].values(), prices["cool_fee"]):
        assert entry["price_per_h"] > 0 and entry["source"] and "checked_on" in entry
    assert prices["runtime_instance"] == {
        "cool": "c7g.large", "std-arm": "c7g.large", "std-x86": "c7i.large",
    }  # fmt: skip


def test_instance_type_is_local_off_ec2(monkeypatch):
    def unreachable(*args, **kwargs):
        raise OSError("no route to IMDS")

    monkeypatch.setattr(bench.urllib.request, "urlopen", unreachable)
    assert bench.instance_type() == "local"


def test_too_few_frames_is_a_clear_error(clip_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "instance_type", lambda: "local")
    args = ["--inputs", str(clip_dir), "--frames", "1", "--warmup", "50", "--out", str(tmp_path)]
    with pytest.raises(SystemExit, match="no clips measured"):
        bench.run(bench.parse_args(args))
