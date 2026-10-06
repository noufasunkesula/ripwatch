"""rw-ocean-poller: tide and beach alerts into SSM every 30 minutes (sprint-1.md D-08).

Writes compact JSON to `<RW_SSM_PREFIX>/ocean/latest` (standard tier, under 4 KB) in the format
`rw.mcp_tools.tools.ocean.OceanSnapshot` reads: fetched_at, station, the next 4 tides,
tide_trend, up to 3 alerts and errors. If NOAA or NWS fails, the last good section from the
previous snapshot is kept and the failure is listed in `errors`.

Lambda runtime only: stdlib + boto3. One JSON log line per run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import boto3
import noaa
import nws

MAX_BYTES = 4096


def _client(name: str) -> Any:
    return boto3.client(name, endpoint_url=os.environ.get("RW_AWS_ENDPOINT_URL") or None)


def _param() -> str:
    return f"{os.environ.get('RW_SSM_PREFIX', '/rw')}/ocean/latest"


def _log(**fields: Any) -> None:
    print(json.dumps({"service": "rw-ocean-poller", **fields}, default=str))


def _previous(ssm: Any) -> dict[str, Any]:
    try:
        value = json.loads(ssm.get_parameter(Name=_param())["Parameter"]["Value"])
    except Exception:  # noqa: BLE001 (first run, or unreadable: nothing to keep)
        return {}
    return value if isinstance(value, dict) else {}


def _fit(snapshot: dict[str, Any]) -> str:
    """JSON under 4 KB: drop alerts from the end, then error detail, until it fits."""
    text = json.dumps(snapshot, separators=(",", ":"))
    while len(text.encode()) >= MAX_BYTES and snapshot["alerts"]:
        snapshot["alerts"].pop()
        text = json.dumps(snapshot, separators=(",", ":"))
    if len(text.encode()) >= MAX_BYTES:
        snapshot["errors"] = [e[:100] for e in snapshot["errors"]][:3]
        text = json.dumps(snapshot, separators=(",", ":"))
    return text


def poll(
    now: datetime,
    fetch_tides: Callable[[str, datetime], dict[str, Any]] = noaa.fetch,
    fetch_alerts: Callable[[str, str], dict[str, Any]] = nws.fetch,
) -> dict[str, Any]:
    station = os.environ["RW_NOAA_STATION_ID"]
    zone = os.environ["RW_NWS_ZONE_ID"]
    agent = os.environ["RW_NWS_USER_AGENT"]
    ssm = _client("ssm")
    previous = _previous(ssm)
    errors: list[str] = []

    try:
        tides = noaa.parse(fetch_tides(station, now), now)
    except Exception as exc:  # noqa: BLE001 (keep the last good tides)
        errors.append(f"noaa: {type(exc).__name__}: {exc}"[:300])
        tides = previous.get("tides", []) if previous.get("station") == station else []
        tides = [t for t in tides if datetime.fromisoformat(t["ts"]) > now]
    try:
        alerts = nws.parse(fetch_alerts(zone, agent))
    except Exception as exc:  # noqa: BLE001 (keep the last good alerts)
        errors.append(f"nws: {type(exc).__name__}: {exc}"[:300])
        alerts = previous.get("alerts", [])

    snapshot = {
        "fetched_at": now.isoformat().replace("+00:00", "Z"),
        "station": station,
        "tides": tides,
        "tide_trend": noaa.trend(tides, now),
        "alerts": alerts,
        "errors": errors,
    }
    value = _fit(snapshot)
    ssm.put_parameter(Name=_param(), Value=value, Type="String", Tier="Standard", Overwrite=True)
    _log(action="poll", tides=len(snapshot["tides"]), alerts=len(snapshot["alerts"]),
         errors=errors, bytes=len(value.encode()))  # fmt: skip
    return snapshot


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    return poll(datetime.now(UTC))
