"""Where the tools read VisionResults from.

rw-detections item format (Decision Log 2026-10-03), shared with ingest (N-11):
  camera_id   PK
  ts_result   SK, `<input.start_ts as %Y-%m-%dT%H:%M:%S.%fZ>#<result_id>` (fixed width,
              so string order is time order)
  result_id   for lookups by id (key attributes cannot be filtered in a Query)
  expires_at  epoch seconds, created_at + 24 h (table TTL)
  result      the VisionResult as one JSON string

Tools get a DetectionStore so they can be tested without AWS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from boto3.dynamodb.conditions import Attr, Key

from rw.contracts import VisionResult

TTL = timedelta(hours=24)
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class ResultNotFound(LookupError):
    def __init__(self, camera_id: str, result_id: str) -> None:
        super().__init__(f"result {result_id} not found for camera {camera_id}")
        self.camera_id = camera_id
        self.result_id = result_id


class DetectionStore(Protocol):
    def get_result(self, camera_id: str, result_id: str) -> VisionResult:
        """The VisionResult for result_id, or raise ResultNotFound."""
        ...

    def results_since(self, camera_id: str, since: datetime) -> list[VisionResult]:
        """Results whose input.start_ts is at or after since, oldest first."""
        ...


def ts_key(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime(_TS_FORMAT)


def ts_result(result: VisionResult) -> str:
    return f"{ts_key(result.input.start_ts)}#{result.result_id}"


def to_item(result: VisionResult) -> dict[str, Any]:
    """The rw-detections item for a result. Ingest writes exactly this."""
    return {
        "camera_id": result.camera_id,
        "ts_result": ts_result(result),
        "result_id": result.result_id,
        "expires_at": int((result.created_at + TTL).timestamp()),
        "result": result.model_dump_json(),
    }


class InMemoryDetectionStore:
    def __init__(self, results: list[VisionResult] | None = None) -> None:
        self.rows: dict[str, dict[str, VisionResult]] = {}
        for result in results or []:
            self.put(result)

    def put(self, result: VisionResult) -> None:
        self.rows.setdefault(result.camera_id, {})[result.result_id] = result

    def get_result(self, camera_id: str, result_id: str) -> VisionResult:
        try:
            return self.rows[camera_id][result_id]
        except KeyError:
            raise ResultNotFound(camera_id, result_id) from None

    def results_since(self, camera_id: str, since: datetime) -> list[VisionResult]:
        results = self.rows.get(camera_id, {}).values()
        return sorted((r for r in results if r.input.start_ts >= since), key=ts_result)


class DynamoDetectionStore:
    """rw-detections through a boto3 DynamoDB Table resource."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def put(self, result: VisionResult) -> None:
        self.table.put_item(Item=to_item(result))

    def _query(self, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            page = self.table.query(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def get_result(self, camera_id: str, result_id: str) -> VisionResult:
        items = self._query(
            KeyConditionExpression=Key("camera_id").eq(camera_id),
            FilterExpression=Attr("result_id").eq(result_id),
            ScanIndexForward=False,
        )
        if not items:
            raise ResultNotFound(camera_id, result_id)
        return VisionResult.model_validate_json(items[0]["result"])

    def results_since(self, camera_id: str, since: datetime) -> list[VisionResult]:
        items = self._query(
            KeyConditionExpression=Key("camera_id").eq(camera_id)
            & Key("ts_result").gte(ts_key(since)),
        )
        return [VisionResult.model_validate_json(i["result"]) for i in items]
