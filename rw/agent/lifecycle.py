"""Incident lifecycle and follow-up watching (sprint-1.md D-06).

`transition()` is the only way an incident's status changes: the MCP action tools (D-04) call it
through `incidents._move`, and the agent calls it for rule-based follow-ups. The allowed moves
live in `lifecycle_rules` (stdlib only, shared with rw-api). With a TraceWriter it also writes the
status_change step; the MCP server has no writer, so the agent records those changes from the
tool results instead (`loop.Agent._caller`).

Follow-ups (`reason=active_incident_followup`) run without Bedrock in three cases:
  - watching: watch_until_clips counts down; at 0 with the last 3 follow-ups clear -> resolved
  - alerted: waiting for a human, nothing for the model to add; counters only
  - approved: 2 follow-ups with the rip -> confirmed; 3 clear in a row -> resolved
Every other watching follow-up goes to the model with the incident as context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rw.agent import lifecycle_rules as rules
from rw.contracts import VisionResult
from rw.contracts.trace import StepType
from rw.contracts.vision import Status

if TYPE_CHECKING:
    from rw.agent.trace import TraceWriter


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _update(table: Any, incident: dict, expected: str, fields: dict[str, Any]) -> None:
    """SET fields on the incident, only if nobody changed its status since we read it."""
    names = {"#status": "status"}
    values: dict[str, Any] = {":expected": expected}
    sets = []
    for i, (key, value) in enumerate(fields.items()):
        names[f"#f{i}"] = key
        values[f":f{i}"] = value
        sets.append(f"#f{i} = :f{i}")
    table.update_item(
        Key={"incident_id": incident["incident_id"]},
        UpdateExpression="SET " + ", ".join(sets),
        ConditionExpression="#status = :expected",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    incident.update(fields)


def transition(
    table: Any,
    incident: dict,
    event: str,
    now: datetime,
    writer: TraceWriter | None = None,
    reason: str | None = None,
    **fields: Any,
) -> str:
    """Apply a lifecycle event to an incident row (and `incident` in place); returns the status.

    Raises InvalidTransition for a move the diagram forbids, before anything is written.
    """
    before = incident["status"]
    status = rules.next_status(before, event)
    changes = {"status": status, "updated_at": _iso(now), **fields}
    if status != before:
        changes = {**rules.FOLLOWUP_COUNTERS, **changes}
    _update(table, incident, before, changes)
    if writer is not None and status != before:
        writer.step(
            StepType.STATUS_CHANGE,
            "lifecycle",
            input={"incident_id": incident["incident_id"], "event": event},
            output_summary={"from": before, "to": status},
            reasoning_summary=reason,
        )
    return status


@dataclass(frozen=True)
class FollowupOutcome:
    handled: bool  # True: decided by the rules, no model call
    event: str | None  # lifecycle event applied, if any
    reasons: list[str]


def followup(
    table: Any, incident: dict, result: VisionResult, now: datetime, writer: TraceWriter
) -> FollowupOutcome:
    """Count one follow-up result against an active incident and apply the rule-based moves."""
    status = incident["status"]
    clear = result.summary.status == Status.CLEAR
    streak = int(incident.get("followup_clear_streak") or 0) + 1 if clear else 0
    hits = int(incident.get("followup_rip_hits") or 0) + (result.summary.status == Status.RIP)
    counters: dict[str, Any] = {"followup_clear_streak": streak, "followup_rip_hits": hits}
    seen = f"follow-up {result.result_id} is {result.summary.status.value}"

    if status == rules.WATCHING:
        left = max(0, int(incident.get("watch_until_clips") or 0) - 1)
        counters["watch_until_clips"] = left
        if left == 0 and streak >= rules.RESOLVE_AFTER_CLEAR:
            why = f"watch over and the last {streak} follow-ups were clear"
            _resolve(table, incident, now, writer, why, counters)
            return FollowupOutcome(True, "resolve", [seen, why])
        _update(table, incident, status, counters)
        return FollowupOutcome(False, None, [seen, f"{left} watch clips left"])

    if status == rules.APPROVED:
        if hits >= rules.CONFIRM_AFTER_RIP:
            why = f"rip seen in {hits} follow-ups after approval"
            transition(table, incident, "confirm", now, writer, why, outcome="confirmed",
                       outcome_reason=why, closed_at=_iso(now))  # fmt: skip
            return FollowupOutcome(True, "confirm", [seen, why])
        if streak >= rules.RESOLVE_AFTER_CLEAR:
            why = f"{streak} clear follow-ups in a row after approval"
            _resolve(table, incident, now, writer, why, counters)
            return FollowupOutcome(True, "resolve", [seen, why])
        _update(table, incident, status, counters)
        return FollowupOutcome(True, None, [seen, "approved, counting follow-ups"])

    _update(table, incident, status, counters)  # alerted: the human decides next
    return FollowupOutcome(True, None, [seen, "waiting for the lifeguard's approval"])


def _resolve(
    table: Any, incident: dict, now: datetime, writer: TraceWriter, why: str, counters: dict
) -> None:
    transition(table, incident, "resolve", now, writer, why, **counters, outcome="resolved",
               outcome_reason=why, closed_at=_iso(now))  # fmt: skip
