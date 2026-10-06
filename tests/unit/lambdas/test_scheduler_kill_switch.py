import json
from datetime import UTC, datetime
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from lambdas.kill_switch import handler as kill_switch
from lambdas.scheduler import handler as scheduler

REGION = "us-east-1"
ASG = "rw-worker-asg"
BUCKET = "rw-artifacts-123456789012"


@pytest.fixture
def aws(monkeypatch):
    for key in ("AWS_PROFILE", "RW_AWS_ENDPOINT_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("RW_WORKER_ASG", ASG)
    monkeypatch.setenv("RW_ARTIFACTS_BUCKET", BUCKET)
    with mock_aws():
        ec2 = boto3.client("ec2", region_name=REGION)
        image = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
        ec2.create_launch_template(
            LaunchTemplateName="rw-worker-lt",
            LaunchTemplateData={"ImageId": image, "InstanceType": "c7g.large"},
        )
        boto3.client("autoscaling", region_name=REGION).create_auto_scaling_group(
            AutoScalingGroupName=ASG,
            LaunchTemplate={"LaunchTemplateName": "rw-worker-lt", "Version": "$Latest"},
            MinSize=0,
            MaxSize=1,
            DesiredCapacity=0,
            AvailabilityZones=[f"{REGION}a"],
        )
        yield


def _desired():
    groups = boto3.client("autoscaling", region_name=REGION).describe_auto_scaling_groups(
        AutoScalingGroupNames=[ASG]
    )
    return groups["AutoScalingGroups"][0]["DesiredCapacity"]


# ---------------------------------------------------------------- rw-scheduler


def test_wake_status_and_sleep(aws, capsys):
    assert scheduler.handler({"action": "wake"}) == {
        "ok": True,
        "action": "wake",
        "asg": ASG,
        "desired": 1,
    }
    assert _desired() == 1

    status = scheduler.handler({"action": "status"})
    assert status["desired"] == 1
    assert {i["lifecycle_state"] for i in status["instances"]} <= {"InService", "Pending"}

    scheduler.handler({"action": "sleep"})
    assert _desired() == 0

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["action"] for line in lines] == ["wake", "status", "sleep"]
    assert all(line["service"] == "rw-scheduler" for line in lines)


@pytest.mark.parametrize("event", [{}, {"action": "reboot"}, None])
def test_unknown_action_is_rejected(aws, event):
    with pytest.raises(ValueError, match="action must be one of"):
        scheduler.handler(event)


def _table(name, hash_key, range_key=None, range_type="S"):
    keys = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    attrs = [{"AttributeName": hash_key, "AttributeType": "S"}]
    if range_key:
        keys.append({"AttributeName": range_key, "KeyType": "RANGE"})
        attrs.append({"AttributeName": range_key, "AttributeType": range_type})
    return boto3.resource("dynamodb", region_name=REGION).create_table(
        TableName=name, KeySchema=keys, AttributeDefinitions=attrs, BillingMode="PAY_PER_REQUEST"
    )


def test_export_writes_one_jsonl_per_table(aws):
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET)
    incidents = _table("rw-incidents", "incident_id")
    trace = _table("rw-agent-trace", "trace_key", "step", "N")
    _table("rw-approvals", "incident_id", "ts")
    incidents.put_item(Item={"incident_id": "inc_1", "status": "alerted", "risk": Decimal("0.82")})
    for step in range(3):
        trace.put_item(Item={"trace_key": "inc_1", "step": step, "name": f"s{step}"})

    result = scheduler.handler({"action": "export"})
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    assert result["tables"] == {
        "rw-incidents": {"key": f"traces/{day}/rw-incidents.jsonl", "rows": 1},
        "rw-agent-trace": {"key": f"traces/{day}/rw-agent-trace.jsonl", "rows": 3},
        "rw-approvals": {"key": f"traces/{day}/rw-approvals.jsonl", "rows": 0},
    }

    s3 = boto3.client("s3", region_name=REGION)
    body = s3.get_object(Bucket=BUCKET, Key=f"traces/{day}/rw-incidents.jsonl")["Body"].read()
    assert json.loads(body) == {"incident_id": "inc_1", "status": "alerted", "risk": 0.82}
    steps = s3.get_object(Bucket=BUCKET, Key=f"traces/{day}/rw-agent-trace.jsonl")["Body"].read()
    assert sorted(json.loads(line)["step"] for line in steps.decode().splitlines()) == [0, 1, 2]
    empty = s3.get_object(Bucket=BUCKET, Key=f"traces/{day}/rw-approvals.jsonl")["Body"].read()
    assert empty == b""


def test_plain_converts_nested_decimals():
    row = {"a": Decimal("2"), "b": [Decimal("1.5"), {"c": Decimal("3")}], "d": "x"}
    assert scheduler._plain(row) == {"a": 2, "b": [1.5, {"c": 3}], "d": "x"}


# ---------------------------------------------------------------- rw-kill-switch


@pytest.fixture
def ops_topic(aws, monkeypatch):
    sns = boto3.client("sns", region_name=REGION)
    arn = sns.create_topic(Name="rw-ops-alerts")["TopicArn"]
    sqs = boto3.client("sqs", region_name=REGION)
    queue = sqs.create_queue(QueueName="ops-inbox")["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    sns.subscribe(TopicArn=arn, Protocol="sqs", Endpoint=queue_arn)
    monkeypatch.setenv("RW_OPS_TOPIC_ARN", arn)

    def messages():
        got = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10).get("Messages", [])
        return [json.loads(m["Body"])["Message"] for m in got]

    return messages


def _budget_event(text):
    return {"Records": [{"EventSource": "aws:sns", "Sns": {"Message": text}}]}


def test_kill_switch_scales_to_zero_and_tells_the_team(ops_topic):
    scheduler.handler({"action": "wake"})
    result = kill_switch.handler(_budget_event("AWS Budget rw-monthly: ACTUAL 80% " + "x" * 900))

    assert result == {"ok": True, "asg": ASG, "desired_before": 1, "desired": 0}
    assert _desired() == 0
    (message,) = ops_topic()
    assert message.startswith("Kill switch fired: worker scaled to 0. Budget message: AWS Budget")
    assert len(message.split("Budget message: ", 1)[1]) == 500


def test_kill_switch_is_idempotent(ops_topic):
    kill_switch.handler(_budget_event("first"))
    second = kill_switch.handler(_budget_event("second"))
    assert second["desired_before"] == 0 and _desired() == 0
    messages = ops_topic()
    assert len(messages) == 2
    assert "(it was already at 0)" in messages[1]


def test_kill_switch_handles_an_empty_event(ops_topic):
    kill_switch.handler({})
    (message,) = ops_topic()
    assert message.endswith("Budget message: (no message)")
