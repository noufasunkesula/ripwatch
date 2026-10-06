"""NWS active alerts for one forecast zone, beach-related only (sprint-1.md D-08)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

URL = "https://api.weather.gov/alerts/active"
TIMEOUT_S = 10
MAX_ALERTS = 3
HEADLINE_CHARS = 160
KEEP = ("rip current", "beach hazards", "high surf")


def fetch(zone: str, user_agent: str) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 (fixed https URL)
        f"{URL}?{urllib.parse.urlencode({'zone': zone})}",
        headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        return json.load(response)


def parse(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Up to 3 alerts whose event mentions rip currents, beach hazards or high surf."""
    if "features" not in body:
        raise ValueError(f"NWS error: {body.get('title') or body.get('detail') or 'no features'}")
    alerts = []
    for feature in body["features"]:
        props = feature.get("properties") or {}
        event = str(props.get("event") or "")
        if any(word in event.lower() for word in KEEP):
            alerts.append(
                {
                    "event": event,
                    "headline": str(props.get("headline") or event)[:HEADLINE_CHARS],
                    "expires": props.get("expires") or props.get("ends"),
                }
            )
    return alerts[:MAX_ALERTS]
