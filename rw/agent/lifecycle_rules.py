"""Incident statuses and allowed transitions (sprint-1.md D-06 diagram). Stdlib only.

Plain data so rw-api can package this file into its Lambda zip (D-07 shared code note).
`rw.agent.lifecycle.transition()` (D-06) wraps these rules and writes the trace step; the MCP
action tools (D-04) use `next_status()` so they can never make a move the diagram forbids.

    watching --alert--> alerted --approve--> approved --confirm--> confirmed --close--> closed
       |                   |                     |
       |                   +--reject--> rejected --close--> closed
       |                                         +--resolve--> resolved --close--> closed
       +--resolve--> resolved
       +--false_alarm--> closed
"""

from __future__ import annotations

WATCHING = "watching"
ALERTED = "alerted"
APPROVED = "approved"
REJECTED = "rejected"
CONFIRMED = "confirmed"
RESOLVED = "resolved"
CLOSED = "closed"

STATUSES = (WATCHING, ALERTED, APPROVED, REJECTED, CONFIRMED, RESOLVED, CLOSED)
# Cameras with an incident in one of these keep sending follow-up candidates (sprint-1.md 7.2).
ACTIVE_STATUSES = frozenset({WATCHING, ALERTED, APPROVED})

# status -> {event: next status}
TRANSITIONS: dict[str, dict[str, str]] = {
    WATCHING: {"watch": WATCHING, "alert": ALERTED, "resolve": RESOLVED, "false_alarm": CLOSED},
    # alert again while alerted: alert_lifeguard and request_approval both raise the alert.
    ALERTED: {"alert": ALERTED, "approve": APPROVED, "reject": REJECTED},
    APPROVED: {"confirm": CONFIRMED, "resolve": RESOLVED},
    REJECTED: {"close": CLOSED},
    CONFIRMED: {"close": CLOSED},
    RESOLVED: {"close": CLOSED},
    CLOSED: {},
}
EVENTS = frozenset(e for moves in TRANSITIONS.values() for e in moves)


class InvalidTransition(ValueError):
    """The event is not allowed from the incident's current status."""


def next_status(status: str, event: str) -> str:
    """Status after `event`, or InvalidTransition naming what is allowed instead."""
    if status not in TRANSITIONS:
        raise InvalidTransition(f"unknown incident status {status!r}")
    moves = TRANSITIONS[status]
    if event not in moves:
        allowed = ", ".join(sorted(moves)) or "nothing (final)"
        raise InvalidTransition(f"cannot {event} an incident that is {status}; allowed: {allowed}")
    return moves[event]


def is_active(status: str) -> bool:
    return status in ACTIVE_STATUSES
