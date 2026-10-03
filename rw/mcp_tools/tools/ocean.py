"""get_ocean_conditions: tide and NWS alerts from the snapshot rw-ocean-poller keeps in SSM.

rw-ocean-poller (D-08) writes OceanSnapshot as JSON to `<RW_SSM_PREFIX>/ocean/latest`
every 30 minutes. This tool only reads it; it never calls NOAA or NWS itself.

ocean_factor (sprint-1.md D-03): 1.0 normal, 1.2 within 2 h of a low tide,
+0.3 if a Rip Current Statement or Beach Hazards Statement is active.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from rw.contracts.base import TraceId

OCEAN_PARAM = "/ocean/latest"
STALE_AFTER = timedelta(minutes=120)
LOW_TIDE_WINDOW = timedelta(hours=2)
LOW_TIDE_FACTOR = 1.2
ALERT_BONUS = 0.3
RIP_ALERT_EVENTS = ("rip current statement", "beach hazards statement")
MAX_ALERTS = 3


class TideType(StrEnum):
    HIGH = "H"
    LOW = "L"


class TideTrend(StrEnum):
    RISING = "rising"
    FALLING = "falling"
    UNKNOWN = "unknown"


class TideEvent(BaseModel):
    ts: AwareDatetime
    type: TideType
    height_m: float


class OceanAlert(BaseModel):
    event: str
    headline: Annotated[str, Field(max_length=160)]
    expires: AwareDatetime | None


class OceanSnapshot(BaseModel):
    """What rw-ocean-poller writes to SSM (must stay under 4 KB)."""

    fetched_at: AwareDatetime
    station: str
    tides: Annotated[list[TideEvent], Field(max_length=4)]
    tide_trend: TideTrend
    alerts: Annotated[list[OceanAlert], Field(max_length=MAX_ALERTS)]
    errors: list[str] = []


class OceanConditionsInput(BaseModel):
    trace_id: TraceId


class OceanConditionsOutput(BaseModel):
    station: str | None
    next_high: TideEvent | None
    next_low: TideEvent | None
    tide_trend: TideTrend
    alerts: list[OceanAlert]
    ocean_factor: float
    age_minutes: int | None
    stale: bool
    note: str | None


def ocean_factor(snapshot: OceanSnapshot, now: datetime) -> float:
    factor = 1.0
    if any(t.type == TideType.LOW and abs(t.ts - now) <= LOW_TIDE_WINDOW for t in snapshot.tides):
        factor = LOW_TIDE_FACTOR
    if any(a.event.strip().lower() in RIP_ALERT_EVENTS for a in snapshot.alerts):
        factor += ALERT_BONUS
    return round(factor, 2)


def _trend(snapshot: OceanSnapshot, now: datetime) -> TideTrend:
    # Recompute from the next event so an older snapshot still gives the right trend.
    upcoming = sorted((t for t in snapshot.tides if t.ts > now), key=lambda t: t.ts)
    if not upcoming:
        return TideTrend.UNKNOWN
    return TideTrend.RISING if upcoming[0].type == TideType.HIGH else TideTrend.FALLING


def _next(snapshot: OceanSnapshot, now: datetime, kind: TideType) -> TideEvent | None:
    return min(
        (t for t in snapshot.tides if t.type == kind and t.ts > now),
        key=lambda t: t.ts,
        default=None,
    )


def _no_data(note: str) -> OceanConditionsOutput:
    return OceanConditionsOutput(
        station=None,
        next_high=None,
        next_low=None,
        tide_trend=TideTrend.UNKNOWN,
        alerts=[],
        ocean_factor=1.0,
        age_minutes=None,
        stale=True,
        note=note,
    )


def get_ocean_conditions(
    ssm: Any,
    args: OceanConditionsInput,
    ssm_prefix: str = "/rw",
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> OceanConditionsOutput:
    """Current tide and beach hazard alerts for the demo beach.

    Use to judge how dangerous conditions are: rip currents are stronger near
    low tide and when the National Weather Service has a Rip Current or Beach
    Hazards Statement out. Returns the next high and low tide, whether the tide
    is rising or falling, active alerts, and `ocean_factor` (1.0 normal, up to
    1.5) to pass to predict_spread. `stale` is true when the data is over 2
    hours old; `note` is "no_data" when no snapshot exists yet.
    """
    try:
        raw = ssm.get_parameter(Name=ssm_prefix + OCEAN_PARAM)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return _no_data("no_data")
    try:
        snapshot = OceanSnapshot.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        return _no_data("invalid_snapshot")

    current = now()
    age = current - snapshot.fetched_at
    return OceanConditionsOutput(
        station=snapshot.station,
        next_high=_next(snapshot, current, TideType.HIGH),
        next_low=_next(snapshot, current, TideType.LOW),
        tide_trend=_trend(snapshot, current),
        alerts=snapshot.alerts,
        ocean_factor=ocean_factor(snapshot, current),
        age_minutes=max(0, int(age.total_seconds() // 60)),
        stale=age > STALE_AFTER,
        note="partial: " + "; ".join(snapshot.errors) if snapshot.errors else None,
    )
