from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from rw.mcp_tools.tools.ocean import (
    OceanConditionsInput,
    TideTrend,
    get_ocean_conditions,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
ARGS = OceanConditionsInput(trace_id="tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F")
PARAM = "/rw/ocean/latest"


@pytest.fixture
def ssm():
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-1")


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _snapshot(low_in: timedelta = timedelta(hours=5), age=timedelta(minutes=10), **extra):
    """Next low at NOW + low_in, highs 6 h either side of it."""
    low = NOW + low_in
    snap = {
        "fetched_at": _iso(NOW - age),
        "station": "8729108",
        "tides": [
            {"ts": _iso(low - timedelta(hours=6)), "type": "H", "height_m": 0.6},
            {"ts": _iso(low), "type": "L", "height_m": 0.1},
            {"ts": _iso(low + timedelta(hours=6)), "type": "H", "height_m": 0.7},
            {"ts": _iso(low + timedelta(hours=12)), "type": "L", "height_m": 0.2},
        ],
        "tide_trend": "falling",
        "alerts": [],
        "errors": [],
    }
    snap.update(extra)
    return snap


def _alert(event: str) -> dict:
    return {
        "event": event,
        "headline": f"{event} issued",
        "expires": _iso(NOW + timedelta(hours=6)),
    }


def _run(ssm, snapshot: dict | None):
    if snapshot is not None:
        ssm.put_parameter(Name=PARAM, Value=json.dumps(snapshot), Type="String")
    return get_ocean_conditions(ssm, ARGS, now=lambda: NOW)


def test_normal_conditions(ssm):
    out = _run(ssm, _snapshot())

    assert out.ocean_factor == 1.0
    assert out.station == "8729108"
    assert out.next_low.ts == NOW + timedelta(hours=5)
    assert out.next_high.ts == NOW + timedelta(hours=11)
    assert out.tide_trend == TideTrend.FALLING
    assert out.age_minutes == 10
    assert out.stale is False
    assert out.note is None


@pytest.mark.parametrize("low_in", [timedelta(hours=1, minutes=30), timedelta(hours=-1)])
def test_near_low_tide_raises_factor(ssm, low_in):
    out = _run(ssm, _snapshot(low_in=low_in))

    assert out.ocean_factor == 1.2


@pytest.mark.parametrize("event", ["Rip Current Statement", "Beach Hazards Statement"])
def test_rip_alert_adds_bonus(ssm, event):
    out = _run(ssm, _snapshot(alerts=[_alert(event)]))

    assert out.ocean_factor == 1.3
    assert out.alerts[0].event == event


def test_low_tide_and_alert_combine(ssm):
    out = _run(ssm, _snapshot(low_in=timedelta(hours=1), alerts=[_alert("Rip Current Statement")]))

    assert out.ocean_factor == 1.5


def test_high_surf_alert_listed_but_no_bonus(ssm):
    out = _run(ssm, _snapshot(alerts=[_alert("High Surf Advisory")]))

    assert out.ocean_factor == 1.0
    assert len(out.alerts) == 1


def test_trend_recomputed_after_low_passed(ssm):
    # Snapshot said falling, but the low was 1 h ago: now rising toward the next high.
    out = _run(ssm, _snapshot(low_in=timedelta(hours=-1)))

    assert out.tide_trend == TideTrend.RISING


def test_old_snapshot_is_stale_but_returned(ssm):
    out = _run(ssm, _snapshot(age=timedelta(minutes=121)))

    assert out.stale is True
    assert out.age_minutes == 121
    assert out.next_low is not None


def test_partial_errors_reported(ssm):
    out = _run(ssm, _snapshot(errors=["nws: timeout"]))

    assert out.note == "partial: nws: timeout"


def test_missing_parameter_is_no_data(ssm):
    out = _run(ssm, None)

    assert out.note == "no_data"
    assert out.ocean_factor == 1.0
    assert out.stale is True


def test_invalid_snapshot(ssm):
    ssm.put_parameter(Name=PARAM, Value="{not json", Type="String")

    out = get_ocean_conditions(ssm, ARGS, now=lambda: NOW)

    assert out.note == "invalid_snapshot"
