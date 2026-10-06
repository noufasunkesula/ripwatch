"""Incident lifecycle (sprint-1.md D-06): transition() for every move, and follow-up rules."""

from __future__ import annotations

import copy
import itertools

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from rw.agent import lifecycle
from rw.agent import lifecycle_rules as rules
from rw.agent.trace import InMemoryTraceSink, TraceWriter
from rw.common.ids import new_id
from rw.contracts import VisionResult
from tests.unit.agent.test_lifecycle_rules import ALLOWED
from tests.unit.mcp_tools.helpers import T0

INCIDENT = "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="rw-incidents",
            KeySchema=[{"AttributeName": "incident_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "incident_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


@pytest.fixture
def sink():
    return InMemoryTraceSink()


def _writer(sink: InMemoryTraceSink) -> TraceWriter:
    return TraceWriter(INCIDENT, "tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F", sink, lambda: T0)


def _incident(table, status: str, **fields) -> dict:
    item = {"incident_id": INCIDENT, "camera_id": "cam-01", "status": status, **fields}
    table.put_item(Item=item)
    return dict(item)


def _row(table) -> dict:
    return table.get_item(Key={"incident_id": INCIDENT})["Item"]


def _result(vision_result: dict, status: str) -> VisionResult:
    payload = copy.deepcopy(vision_result)
    payload["result_id"] = new_id("res")
    if status == "clear":
        payload["rips"], payload["swimmers"] = [], []
        payload["summary"].update(
            status="clear", max_confidence=0.0, rip_count=0, swimmer_count=0, swimmers_at_risk=0
        )
    elif status == "uncertain":
        payload["rips"][0].update(label="uncertain", confidence=0.55)
        payload["summary"].update(
            status="uncertain", max_confidence=0.55, swimmer_count=0, swimmers_at_risk=0
        )
        payload["swimmers"] = []
    return VisionResult.model_validate(payload)


# ---------------------------------------------------------------- transition


@pytest.mark.parametrize(("status", "event"), sorted(ALLOWED))
def test_every_allowed_transition_updates_the_row_and_traces_a_change(table, sink, status, event):
    incident = _incident(table, status, followup_clear_streak=2, followup_rip_hits=1)

    new = lifecycle.transition(table, incident, event, T0, _writer(sink), "why", note="x")

    row = _row(table)
    assert new == ALLOWED[(status, event)] == row["status"] == incident["status"]
    assert row["note"] == "x" and row["updated_at"] == "2026-10-05T10:15:00Z"
    steps = sink.query(INCIDENT)
    if new == status:  # watch while watching, alert while alerted: no status change
        assert steps == [] and row["followup_clear_streak"] == 2
    else:
        assert [(s.type.value, s.name) for s in steps] == [("status_change", "lifecycle")]
        assert steps[0].output_summary == {"from": status, "to": new}
        assert steps[0].input == {"incident_id": INCIDENT, "event": event}
        assert steps[0].reasoning_summary == "why"
        assert row["followup_clear_streak"] == 0 and row["followup_rip_hits"] == 0


DISALLOWED = sorted(set(itertools.product(rules.STATUSES, rules.EVENTS)) - set(ALLOWED))


@pytest.mark.parametrize(("status", "event"), DISALLOWED)
def test_every_disallowed_transition_raises_and_writes_nothing(table, sink, status, event):
    incident = _incident(table, status)

    with pytest.raises(rules.InvalidTransition):
        lifecycle.transition(table, incident, event, T0, _writer(sink))

    assert _row(table) == {"incident_id": INCIDENT, "camera_id": "cam-01", "status": status}
    assert sink.query(INCIDENT) == []


def test_a_stale_read_cannot_overwrite_a_newer_status(table, sink):
    stale = _incident(table, "alerted")
    lifecycle.transition(table, dict(stale), "approve", T0)  # someone else approved first

    with pytest.raises(ClientError, match="ConditionalCheckFailed"):
        lifecycle.transition(table, stale, "reject", T0, _writer(sink))
    assert _row(table)["status"] == "approved" and sink.query(INCIDENT) == []


# ---------------------------------------------------------------- follow-ups


def _follow(table, sink, incident, vision_result, *statuses):
    outcomes = [
        lifecycle.followup(table, incident, _result(vision_result, s), T0, _writer(sink))
        for s in statuses
    ]
    return outcomes[-1]


def test_watching_counts_down_and_asks_the_model_while_clips_remain(table, sink, vision_result):
    incident = _incident(table, "watching", watch_until_clips=3)

    outcome = _follow(table, sink, incident, vision_result, "clear", "clear")

    assert not outcome.handled and outcome.event is None
    assert outcome.reasons[-1] == "1 watch clips left"
    row = _row(table)
    assert row["watch_until_clips"] == 1 and row["followup_clear_streak"] == 2
    assert sink.query(INCIDENT) == []


def test_watching_resolves_when_the_watch_ends_after_3_clear(table, sink, vision_result):
    incident = _incident(table, "watching", watch_until_clips=3)

    outcome = _follow(table, sink, incident, vision_result, "clear", "clear", "clear")

    assert outcome.handled and outcome.event == "resolve"
    row = _row(table)
    assert row["status"] == "resolved" and row["outcome"] == "resolved"
    assert row["watch_until_clips"] == 0
    (step,) = sink.query(INCIDENT)
    assert step.output_summary == {"from": "watching", "to": "resolved"}
    assert step.reasoning_summary == "watch over and the last 3 follow-ups were clear"


def test_watching_with_the_rip_back_goes_to_the_model_at_the_end(table, sink, vision_result):
    incident = _incident(table, "watching", watch_until_clips=3)

    outcome = _follow(table, sink, incident, vision_result, "clear", "clear", "uncertain")

    assert not outcome.handled and _row(table)["status"] == "watching"
    assert _row(table)["followup_clear_streak"] == 0  # uncertain is not clear


def test_approved_is_confirmed_by_2_followups_with_the_rip(table, sink, vision_result):
    incident = _incident(table, "approved")

    first = _follow(table, sink, incident, vision_result, "rip")
    assert first.handled and first.event is None and _row(table)["status"] == "approved"
    second = _follow(table, sink, incident, vision_result, "uncertain", "rip")

    assert second.handled and second.event == "confirm"
    row = _row(table)
    assert row["status"] == "confirmed" and row["outcome"] == "confirmed"
    (step,) = sink.query(INCIDENT)
    assert step.output_summary == {"from": "approved", "to": "confirmed"}


def test_approved_resolves_after_3_clear_in_a_row(table, sink, vision_result):
    incident = _incident(table, "approved")

    outcome = _follow(table, sink, incident, vision_result, "clear", "clear", "uncertain")
    assert outcome.event is None
    outcome = _follow(table, sink, incident, vision_result, "clear", "clear", "clear")

    assert outcome.handled and outcome.event == "resolve"
    assert _row(table)["status"] == "resolved"


def test_alerted_only_counts_while_the_lifeguard_decides(table, sink, vision_result):
    incident = _incident(table, "alerted", pending_action="raise_red_flag")

    outcome = _follow(table, sink, incident, vision_result, "clear", "clear", "clear", "clear")

    assert outcome.handled and outcome.event is None
    assert outcome.reasons[-1] == "waiting for the lifeguard's approval"
    row = _row(table)
    assert row["status"] == "alerted" and row["followup_clear_streak"] == 4
    assert sink.query(INCIDENT) == []


def test_counters_restart_after_approval(table, sink, vision_result):
    incident = _incident(table, "alerted")
    _follow(table, sink, incident, vision_result, "rip", "rip")
    lifecycle.transition(table, incident, "approve", T0)  # rw-api does this

    outcome = _follow(table, sink, incident, vision_result, "rip")

    assert outcome.event is None and _row(table)["followup_rip_hits"] == 1
