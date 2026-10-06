"""Rule-based fallback when the model fails (sprint-1.md D-05 step 7, north star 11).

The system fails toward warning, never silence:
    CRITICAL / HIGH -> create_incident + alert_lifeguard + request_approval(raise_red_flag)
    ELEVATED        -> create_incident + set_watch(3)
    LOW             -> ignore
Every call goes through the same MCP tools the model uses, so the trace and the lifecycle rules
apply. A failing step is recorded and the rest still run: an alert must not be lost because
one write failed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from rw.agent.tools import ToolOutcome
from rw.contracts.decision import Action, DecisionKind, RiskLevel

CallTool = Callable[[str, dict[str, Any]], Awaitable[ToolOutcome]]
WATCH_CLIPS = 3


@dataclass
class FallbackOutcome:
    decision: DecisionKind
    requested_action: Action | None
    incident_id: str | None
    reasons: list[str] = field(default_factory=list)


async def apply(
    level: RiskLevel,
    ids: dict[str, str],
    incident_id: str | None,
    summary: str,
    call: CallTool,
) -> FallbackOutcome:
    """Act on the pre-risk level. `ids` holds trace_id, camera_id and result_id."""
    trace = {"trace_id": ids["trace_id"]}
    reasons = [f"fallback on pre-risk {level.value}"]
    if level == RiskLevel.LOW:
        return FallbackOutcome(DecisionKind.IGNORE, None, incident_id, reasons)

    if incident_id is None:
        created = await call(
            "create_incident",
            {**trace, "camera_id": ids["camera_id"], "result_id": ids["result_id"],
             "risk_level": level.value, "summary": summary[:500]},
        )  # fmt: skip
        incident_id = created.data.get("incident_id") if created.ok else None
        if incident_id is None:
            reasons.append(f"create_incident failed: {created.text[:200]}")
            return FallbackOutcome(DecisionKind.IGNORE, None, None, reasons)

    if level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        message = f"{level.value}: possible rip current at {ids['camera_id']}. {summary}"[:300]
        alert = await call(
            "alert_lifeguard", {**trace, "incident_id": incident_id, "message": message}
        )
        if not alert.ok:
            reasons.append(f"alert_lifeguard failed: {alert.text[:200]}")
        approval = await call(
            "request_approval",
            {**trace, "incident_id": incident_id, "action": Action.RAISE_RED_FLAG.value,
             "message": "Raise the red flag? (automatic fallback, model unavailable)"},
        )  # fmt: skip
        if not approval.ok:
            reasons.append(f"request_approval failed: {approval.text[:200]}")
        return FallbackOutcome(DecisionKind.ALERT, Action.RAISE_RED_FLAG, incident_id, reasons)

    watch = await call(
        "set_watch",
        {
            **trace,
            "incident_id": incident_id,
            "clips": WATCH_CLIPS,
            "reason": "fallback: elevated risk",
        },
    )
    if not watch.ok:
        reasons.append(f"set_watch failed: {watch.text[:200]}")
    return FallbackOutcome(DecisionKind.WATCH, None, incident_id, reasons)
