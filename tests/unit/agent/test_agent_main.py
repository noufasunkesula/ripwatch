import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from rw.agent import __main__ as entry
from rw.agent.trace import DynamoTraceSink, TraceWriter
from rw.contracts.trace import StepType

pytestmark = pytest.mark.anyio
REGION = "us-east-1"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def aws(monkeypatch):
    for key in ("AWS_PROFILE", "RW_AWS_ENDPOINT_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        yield


class StubAgent:
    def __init__(self, decision=None, fail=False):
        self.seen, self.decision, self.fail = [], decision, fail

    async def handle(self, candidate):
        if self.fail:
            raise RuntimeError("crash")
        self.seen.append(candidate)
        return self.decision


def _queue():
    sqs = boto3.client("sqs", region_name=REGION)
    return sqs, sqs.create_queue(QueueName="rw-candidates")["QueueUrl"]


async def test_run_once_deletes_after_the_decision(aws, candidate):
    sqs, url = _queue()
    sqs.send_message(QueueUrl=url, MessageBody=json.dumps(candidate))
    agent = StubAgent()
    assert await entry.run_once(agent, sqs, url) == 1
    assert agent.seen[0].result_id == candidate["result_id"]
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    assert attrs["ApproximateNumberOfMessages"] == "0"
    assert attrs["ApproximateNumberOfMessagesNotVisible"] == "0"


async def test_invalid_messages_are_left_for_retry_and_the_dlq(aws):
    sqs, url = _queue()
    sqs.send_message(QueueUrl=url, MessageBody='{"not": "a candidate"}')
    assert await entry.run_once(StubAgent(), sqs, url) == 0
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    assert (
        attrs["ApproximateNumberOfMessagesNotVisible"] == "1"
    )  # back after the visibility timeout


async def test_a_crash_keeps_the_message(aws, candidate):
    sqs, url = _queue()
    sqs.send_message(QueueUrl=url, MessageBody=json.dumps(candidate))
    with pytest.raises(RuntimeError):
        await entry.run_once(StubAgent(fail=True), sqs, url)
    attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["All"])["Attributes"]
    assert attrs["ApproximateNumberOfMessagesNotVisible"] == "1"


def test_ssm_values_and_rip_statement(aws):
    ssm = boto3.client("ssm", region_name=REGION)
    ssm.put_parameter(Name="/rw/agent/cooldown_s", Value="45", Type="String")
    ssm.put_parameter(Name="/rw/risk/thresholds", Value="{}", Type="String")
    assert entry.ssm_values(ssm, "/rw") == {"agent/cooldown_s": "45", "risk/thresholds": "{}"}

    active = entry.rip_statement_reader(ssm, "/rw")
    assert active() is False  # no snapshot yet
    snapshot = {
        "fetched_at": "2026-10-05T10:00:00Z",
        "station": "8729108",
        "tides": [],
        "tide_trend": "unknown",
        "alerts": [
            {
                "event": "Rip Current Statement",
                "headline": "High rip risk",
                "expires": "2026-10-05T22:00:00Z",
            }
        ],
        "errors": [],
    }
    ssm.put_parameter(Name="/rw/ocean/latest", Value=json.dumps(snapshot), Type="String")
    assert active() is True


def test_dynamo_trace_sink_round_trip_and_rekey(aws):
    table = boto3.resource("dynamodb", region_name=REGION).create_table(
        TableName="rw-agent-trace",
        KeySchema=[
            {"AttributeName": "trace_key", "KeyType": "HASH"},
            {"AttributeName": "step", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "trace_key", "AttributeType": "S"},
            {"AttributeName": "step", "AttributeType": "N"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    sink = DynamoTraceSink(table)
    clock = lambda: datetime(2026, 10, 5, 10, 15, tzinfo=UTC)  # noqa: E731
    writer = TraceWriter(
        "cand_res_01J9ZC4M6Y2N8Q4T7V1B3K5D9F", "tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F", sink, clock
    )
    writer.step(StepType.CANDIDATE_RECEIVED, "candidate", input={"confidence": 0.82})
    writer.step(StepType.TOOL_CALL, "zoom_and_recheck", output_summary={"confidence": 0.91})
    writer.rekey("inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F")
    writer.step(StepType.DECISION, "decision")

    steps = sink.query("inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F")
    assert [(s.step, s.name) for s in steps] == [
        (1, "candidate"),
        (2, "zoom_and_recheck"),
        (3, "decision"),
    ]
    assert steps[1].output_summary == {"confidence": 0.91}
    assert len(sink.query("cand_res_01J9ZC4M6Y2N8Q4T7V1B3K5D9F")) == 2  # old rows stay
