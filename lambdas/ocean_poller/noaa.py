"""NOAA CO-OPS tide predictions (high/low) for one station (sprint-1.md D-08)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
TIMEOUT_S = 10
MAX_TIDES = 4


def url(station: str, today: datetime) -> str:
    query = {
        "product": "predictions",
        "station": station,
        "datum": "MLLW",
        "units": "metric",
        "time_zone": "gmt",
        "interval": "hilo",
        "format": "json",
        "begin_date": today.strftime("%Y%m%d"),
        "range": "48",
        "application": "RipWatch",
    }
    return f"{URL}?{urllib.parse.urlencode(query)}"


def fetch(station: str, now: datetime) -> dict[str, Any]:
    with urllib.request.urlopen(url(station, now), timeout=TIMEOUT_S) as response:  # noqa: S310
        return json.load(response)


def parse(body: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """The next 4 high/low tides after `now`. NOAA reports errors as {"error": {...}}."""
    if "error" in body:
        raise ValueError(f"NOAA error: {body['error'].get('message', body['error'])}")
    events = []
    for p in body.get("predictions", []):
        ts = datetime.strptime(p["t"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        if ts > now and p.get("type") in ("H", "L"):
            events.append({"ts": ts.isoformat().replace("+00:00", "Z"), "type": p["type"],
                           "height_m": round(float(p["v"]), 3)})  # fmt: skip
    if not events:
        raise ValueError("NOAA returned no upcoming tide predictions")
    return sorted(events, key=lambda e: e["ts"])[:MAX_TIDES]


def trend(tides: list[dict[str, Any]], now: datetime) -> str:
    """Rising while the next tide is high, falling while it is low."""
    upcoming = [t for t in tides if datetime.fromisoformat(t["ts"]) > now]
    if not upcoming:
        return "unknown"
    return "rising" if upcoming[0]["type"] == "H" else "falling"
