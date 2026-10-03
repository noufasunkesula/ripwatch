"""Build VisionResults at chosen times from the shared example payload."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

from rw.contracts import VisionResult

T0 = datetime(2026, 10, 5, 10, 15, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def result_at(
    base: dict,
    n: int,
    offset_s: float,
    flow: float | None = 6.4,
    rips: list[str] | None = None,
) -> VisionResult:
    """Result number n starting offset_s after T0, 10 s long, with the given rip ids and flow."""
    payload = copy.deepcopy(base)
    start = T0 + timedelta(seconds=offset_s)
    payload["result_id"] = f"res_01J9ZC4M6Y2N8Q4T7V1B3K5D{n:02d}"
    payload["input"]["start_ts"] = _iso(start)
    payload["input"]["end_ts"] = _iso(start + timedelta(seconds=10))
    payload["created_at"] = _iso(start + timedelta(seconds=12))

    template = payload["rips"][0]
    payload["rips"] = []
    for rip_id in rips if rips is not None else [template["rip_id"]]:
        rip = copy.deepcopy(template)
        rip["rip_id"] = rip_id
        rip["evidence"]["seaward_flow_px_per_s"] = flow
        payload["rips"].append(rip)
    payload["summary"]["rip_count"] = len(payload["rips"])
    payload["swimmers"][0]["in_rip_id"] = payload["rips"][0]["rip_id"] if payload["rips"] else None
    if not payload["rips"]:
        payload["summary"].update(status="clear", max_confidence=0.1)
    return VisionResult.model_validate(payload)
