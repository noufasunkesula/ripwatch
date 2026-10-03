"""Where the tools read VisionResults from.

rw-detections is keyed by camera_id + ts_result (`<input.start_ts>#<result_id>`).
Tools get a DetectionStore so they can be tested without AWS; the DynamoDB
store plugs in once rw.common.aws (N-04) is available.
"""

from __future__ import annotations

from typing import Protocol

from rw.contracts import VisionResult


class ResultNotFound(LookupError):
    def __init__(self, camera_id: str, result_id: str) -> None:
        super().__init__(f"result {result_id} not found for camera {camera_id}")
        self.camera_id = camera_id
        self.result_id = result_id


class DetectionStore(Protocol):
    def get_result(self, camera_id: str, result_id: str) -> VisionResult:
        """The VisionResult for result_id, or raise ResultNotFound."""
        ...


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
