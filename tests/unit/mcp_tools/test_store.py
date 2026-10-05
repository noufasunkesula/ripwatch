from __future__ import annotations

from datetime import timedelta

import boto3
import pytest
from moto import mock_aws

from rw.contracts import VisionResult
from rw.mcp_tools.store import (
    DynamoDetectionStore,
    InMemoryDetectionStore,
    ResultNotFound,
    to_item,
    ts_result,
)
from tests.unit.mcp_tools.helpers import T0, result_at


@pytest.fixture
def dynamo_store():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="rw-detections",
            KeySchema=[
                {"AttributeName": "camera_id", "KeyType": "HASH"},
                {"AttributeName": "ts_result", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "camera_id", "AttributeType": "S"},
                {"AttributeName": "ts_result", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoDetectionStore(table)


@pytest.fixture(params=["memory", "dynamo"])
def store(request):
    if request.param == "memory":
        return InMemoryDetectionStore()
    return request.getfixturevalue("dynamo_store")


def test_item_format(vision_result):
    result = VisionResult.model_validate(vision_result)

    item = to_item(result)

    assert item["camera_id"] == "cam-01"
    assert item["ts_result"] == f"2026-10-05T10:15:00.000000Z#{result.result_id}"
    assert item["result_id"] == result.result_id
    assert item["expires_at"] == int((result.created_at + timedelta(hours=24)).timestamp())
    assert VisionResult.model_validate_json(item["result"]) == result


def test_ts_result_is_fixed_width_so_string_order_is_time_order(vision_result):
    whole = result_at(vision_result, 1, 0)
    fraction = result_at(vision_result, 2, 0.5)

    assert len(ts_result(whole)) == len(ts_result(fraction))
    assert ts_result(whole) < ts_result(fraction)


def test_put_and_get(store, vision_result):
    result = result_at(vision_result, 1, 0)
    store.put(result)

    assert store.get_result("cam-01", result.result_id) == result


def test_get_missing_raises(store, vision_result):
    store.put(result_at(vision_result, 1, 0))

    with pytest.raises(ResultNotFound):
        store.get_result("cam-01", "res_01J9ZC4M6Y2N8Q4T7V1B3K5D99")
    with pytest.raises(ResultNotFound):
        store.get_result("cam-02", "res_01J9ZC4M6Y2N8Q4T7V1B3K5D01")


def test_results_since_window_and_order(store, vision_result):
    for n, offset in [(3, 60), (1, 0), (2, 30), (4, 90)]:
        store.put(result_at(vision_result, n, offset))

    got = store.results_since("cam-01", T0 + timedelta(seconds=30))

    assert [r.result_id[-2:] for r in got] == ["02", "03", "04"]
    assert store.results_since("cam-02", T0) == []


def test_dynamo_query_follows_pages(dynamo_store, vision_result, monkeypatch):
    for n in range(1, 4):
        dynamo_store.put(result_at(vision_result, n, n * 10))
    real_query = dynamo_store.table.query
    monkeypatch.setattr(dynamo_store.table, "query", lambda **kw: real_query(Limit=1, **kw))

    got = dynamo_store.results_since("cam-01", T0)

    assert [r.result_id[-2:] for r in got] == ["01", "02", "03"]
