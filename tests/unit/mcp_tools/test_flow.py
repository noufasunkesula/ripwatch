from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from rw.mcp_tools.store import InMemoryDetectionStore
from rw.mcp_tools.tools.flow import FlowStatsInput, FlowTrend, get_flow_stats, trend
from tests.unit.mcp_tools.helpers import T0, result_at

TRACE = "tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
RIP = "cam-01-rip-0007"
NOW = T0 + timedelta(seconds=120)


def _run(store, window_s: int = 120):
    args = FlowStatsInput(trace_id=TRACE, camera_id="cam-01", window_s=window_s)
    return get_flow_stats(store, args, now=lambda: NOW)


def _store(vision_result, flows: list[float | None], **kw) -> InMemoryDetectionStore:
    # One 10 s clip every 20 s, ending just before NOW.
    return InMemoryDetectionStore(
        [result_at(vision_result, n, 20 * n, flow=f, **kw) for n, f in enumerate(flows, start=1)]
    )


@pytest.mark.parametrize(
    ("flows", "expected"),
    [
        ([], FlowTrend.UNKNOWN),
        ([5.0], FlowTrend.UNKNOWN),
        ([4.0, 4.0, 6.0, 6.0], FlowTrend.RISING),
        ([6.0, 6.0, 4.0, 4.0], FlowTrend.FALLING),
        ([5.0, 5.2, 5.1, 5.3], FlowTrend.STEADY),
        ([0.0, 0.0], FlowTrend.STEADY),
        ([0.0, 1.0], FlowTrend.RISING),
    ],
)
def test_trend(flows, expected):
    assert trend(flows) == expected


def test_stats_for_one_rip(vision_result):
    store = _store(vision_result, [4.0, 5.0, 6.0, 7.0])

    out = _run(store)

    assert out.clips_seen == 4
    (rip,) = out.rips
    assert rip.rip_id == RIP
    assert rip.clips_seen == 4
    assert rip.mean_flow_px_per_s == 5.5
    assert rip.max_flow_px_per_s == 7.0
    assert rip.mean_flow_m_per_s is None
    assert rip.persist_s == 30.0  # from the result's persist_s
    assert rip.trend == FlowTrend.RISING
    assert rip.last_confidence == 0.82


def test_window_excludes_older_clips(vision_result):
    store = _store(vision_result, [9.0, 9.0, 1.0, 1.0, 1.0])

    out = _run(store, window_s=60)  # clips starting at 60 s and later

    assert out.clips_seen == 3
    assert out.rips[0].max_flow_px_per_s == 1.0
    assert out.rips[0].trend == FlowTrend.STEADY


def test_rips_sorted_strongest_first(vision_result):
    store = InMemoryDetectionStore(
        [
            result_at(vision_result, 1, 20, flow=2.0, rips=["cam-01-rip-0001"]),
            result_at(vision_result, 2, 40, flow=8.0, rips=["cam-01-rip-0002"]),
            result_at(vision_result, 3, 60, flow=None, rips=["cam-01-rip-0003"]),
        ]
    )

    out = _run(store)

    assert [r.rip_id[-4:] for r in out.rips] == ["0002", "0001", "0003"]
    assert out.rips[2].mean_flow_px_per_s is None
    assert out.rips[2].trend == FlowTrend.UNKNOWN


def test_persist_falls_back_to_first_seen(vision_result):
    result = result_at(vision_result, 1, 20)
    result.rips[0].persist_s = None
    store = InMemoryDetectionStore([result])

    out = _run(store)

    # first_seen 10:14:40, clip ends 10:15:30
    assert out.rips[0].persist_s == 50.0


def test_clear_clips_count_but_add_no_rips(vision_result):
    store = InMemoryDetectionStore([result_at(vision_result, 1, 20, rips=[])])

    out = _run(store)

    assert out.clips_seen == 1
    assert out.rips == []


def test_caps_rips(vision_result):
    rips = [f"cam-01-rip-{n:04d}" for n in range(12)]
    store = InMemoryDetectionStore([result_at(vision_result, 1, 20, rips=rips)])

    out = _run(store)

    assert len(out.rips) == 10
    assert out.truncated is True


@pytest.mark.parametrize("window", [29, 301])
def test_window_limits(window):
    with pytest.raises(ValidationError):
        FlowStatsInput(trace_id=TRACE, camera_id="cam-01", window_s=window)
