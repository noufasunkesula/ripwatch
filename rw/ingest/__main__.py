"""python -m rw.ingest: the rw-ingest service (sprint-1.md N-11).

Long-polls rw-jobs (wait 20 s, max 1, visibility 300 s) for S3 object-created events under
incoming/. For each object: find or create its job row, download it to
<RW_STATE_DIR>/work/<job_id>/, pick the adapter, run the vision pipeline in this process, write
the VisionResult to rw-detections (`store.to_item`), mark the job done (or failed with the
error), send a CandidateMessage when 7.2 says so, then delete the message and the work dir.

Process split (Decision Log 2026-10-01): frames never leave this process, so ingest calls the
vision pipeline itself. The rw-vision unit (`python -m rw.vision`) keeps the set of cameras with
an active incident in <RW_HEARTBEAT_DIR>/active_cameras.json, which ingest reads per job; it is
also where model loading for Saif's detector will live. Candidates are sent from here, right
after the result is stored, so a candidate can never point at a result that is not written yet.

Per-camera state (rw.vision.state) lives in memory and is lost on restart (documented). A
camera-sim clip with a lower seq than one already processed runs on a fresh state with
out_of_order=true and never updates the camera's real state.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rw.adapters.factory import source_for
from rw.common.aws import client, resource
from rw.common.config import get_settings
from rw.common.heartbeat import beat
from rw.common.ids import new_id
from rw.common.logging import setup_logging
from rw.common.metrics import configure, emit
from rw.contracts import CandidateMessage, CandidateReason, JobStatus, Status, VisionResult
from rw.ingest.jobs import JobStore, ObjectKey, UnsupportedKey, parse_key, s3_objects
from rw.mcp_tools.store import to_item
from rw.vision.baseline_flow import FlowParams
from rw.vision.pipeline import ResultContext, VisionPipeline
from rw.vision.state import CameraState

SERVICE = "rw-ingest"
ACTIVE_CAMERAS_FILE = "active_cameras.json"
log = logging.getLogger(SERVICE)


def read_active_cameras(path: Path) -> dict[str, str]:
    """camera_id -> active incident_id, as rw-vision last wrote it; {} if missing or unreadable."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    cameras = value.get("cameras", {}) if isinstance(value, dict) else {}
    return {str(k): str(v) for k, v in cameras.items()}


class Ingest:
    def __init__(
        self,
        *,
        s3: Any,
        sqs: Any,
        jobs: JobStore,
        detections: Any,
        cameras: Any,
        pipeline: VisionPipeline,
        candidates_url: str,
        work_dir: Path,
        active_cameras: Callable[[], dict[str, str]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.s3, self.sqs, self.jobs = s3, sqs, jobs
        self.detections, self.cameras, self.pipeline = detections, cameras, pipeline
        self.candidates_url, self.work_dir = candidates_url, work_dir
        self.active_cameras, self.clock = active_cameras, clock
        self.states: dict[str, CameraState] = {}
        self.base_params = pipeline.params

    # ------------------------------------------------------------ queue

    def run_once(self, queue_url: str, wait_s: int = 20) -> int:
        """Handle at most one rw-jobs message. Returns the number of objects processed."""
        response = self.sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=wait_s,
            VisibilityTimeout=300,
        )  # fmt: skip
        done = 0
        for message in response.get("Messages", []):
            try:
                objects = s3_objects(message["Body"])
            except (ValueError, KeyError, TypeError):
                log.error("bad_job_message", extra={"message_id": message["MessageId"]})
                objects = []
            for bucket, key in objects:
                self.handle(bucket, key)
                done += 1
            self.sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
        return done

    # ------------------------------------------------------------ one object

    def handle(self, bucket: str, key: str) -> VisionResult | None:
        """Process one incoming object. Failures end up on the job row, never as a crash."""
        try:
            obj = parse_key(key)
        except UnsupportedKey as exc:
            log.warning("ignored_key", extra={"key": key, "error": str(exc)})
            return None
        job = self.jobs.find_or_create(obj, self.clock())
        self.jobs.update(job["job_id"], JobStatus.PROCESSING, self.clock())
        work = self.work_dir / job["job_id"]
        try:
            result = self._process(bucket, obj, job, work)
        except Exception as exc:  # noqa: BLE001 (recorded on the job; the queue moves on)
            log.exception("job_failed", extra={"job_id": job["job_id"], "key": key})
            self.jobs.update(job["job_id"], JobStatus.FAILED, self.clock(),
                             error=f"{type(exc).__name__}: {exc}"[:500])  # fmt: skip
            return None
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.jobs.update(job["job_id"], JobStatus.DONE, self.clock(), result_id=result.result_id)
        emit("JobsProcessed", 1, dimensions={"mode": result.mode.value})
        return result

    def _process(self, bucket: str, obj: ObjectKey, job: dict, work: Path) -> VisionResult:
        work.mkdir(parents=True, exist_ok=True)
        path = work / obj.key.rsplit("/", 1)[-1]
        self.s3.download_file(bucket, obj.key, str(path))

        state, out_of_order = self._state(obj)
        camera = self._camera(obj.camera_id)
        if camera.get("seaward_vector"):
            vector = tuple(float(v) for v in camera["seaward_vector"])
            self.pipeline.params = dataclasses.replace(self.base_params, seaward_vector=vector)
        else:
            self.pipeline.params = self.base_params

        source_id = f"{obj.camera_id}/{obj.stem}"
        source = source_for(path, obj.camera_id, source_id, obj.ts or self.clock())
        incident_id = self.active_cameras().get(obj.camera_id)
        context = ResultContext(
            job_id=job["job_id"],
            trace_id=new_id("tr"),
            source_id=source_id,
            s3_uri=f"s3://{bucket}/{obj.key}",
            fps_source=source.fps_source,
            out_of_order=out_of_order,
            active_incident=incident_id is not None,
        )
        result = self.pipeline.process(source.iter_frames(), source.mode, state, context)
        if not out_of_order and obj.seq is not None:
            state.last_seq = obj.seq

        self.detections.put_item(Item=to_item(result))
        seconds = result.timings_ms.total / 1000
        if seconds > 0:
            emit("FramesPerSecond", result.input.frames_processed / seconds, "Count/Second",
                 {"mode": result.mode.value})  # fmt: skip
        self._candidate(result, incident_id)
        return result

    def _state(self, obj: ObjectKey) -> tuple[CameraState, bool]:
        state = self.states.setdefault(obj.camera_id, CameraState(obj.camera_id))
        if obj.seq is not None and state.last_seq is not None and obj.seq < state.last_seq:
            return CameraState(obj.camera_id), True
        return state, False

    def _camera(self, camera_id: str) -> dict[str, Any]:
        return self.cameras.get_item(Key={"camera_id": camera_id}).get("Item") or {}

    def _candidate(self, result: VisionResult, incident_id: str | None) -> None:
        """7.2: send when status is rip or uncertain, or the camera has an active incident."""
        status = result.summary.status
        if incident_id is None and status == Status.CLEAR:
            return
        if incident_id is not None:
            reason = CandidateReason.ACTIVE_INCIDENT_FOLLOWUP
        elif status == Status.RIP:
            reason = CandidateReason.STATUS_RIP
        else:
            reason = CandidateReason.STATUS_UNCERTAIN
        message = CandidateMessage(
            result_id=result.result_id,
            trace_id=result.trace_id,
            camera_id=result.camera_id,
            mode=result.mode,
            status=status,
            max_confidence=result.summary.max_confidence,
            swimmers_at_risk=result.summary.swimmers_at_risk,
            active_incident_id=incident_id,
            reason=reason,
            created_at=self.clock(),
        )
        self.sqs.send_message(QueueUrl=self.candidates_url, MessageBody=message.model_dump_json())
        if status != Status.CLEAR:
            emit("RipCandidates", 1, dimensions={"mode": result.mode.value})


# ---------------------------------------------------------------- service


def _ssm_float(ssm: Any, name: str, default: float) -> float:
    try:
        return float(ssm.get_parameter(Name=name)["Parameter"]["Value"])
    except Exception:  # noqa: BLE001 (missing or not a number: the documented default)
        return default


def build_ingest() -> Ingest:
    settings = get_settings()
    s3, ssm, sqs = client("s3"), client("ssm"), client("sqs")
    dynamodb = resource("dynamodb")
    prefix = settings.ssm_prefix
    artifacts = settings.require("artifacts_bucket")

    def keyframe_sink(key: str, data: bytes) -> str:
        s3.put_object(Bucket=artifacts, Key=key, Body=data, ContentType="image/jpeg")
        return f"s3://{artifacts}/{key}"

    pipeline = VisionPipeline(
        params=FlowParams(
            flow_scale=_ssm_float(ssm, f"{prefix}/vision/flow_scale", 0.5),
            min_rip_area_px=_ssm_float(ssm, f"{prefix}/vision/min_rip_area_px", 400.0),
        ),
        rip_threshold=_ssm_float(ssm, f"{prefix}/vision/rip_threshold", 0.70),
        uncertain_threshold=_ssm_float(ssm, f"{prefix}/vision/uncertain_threshold", 0.40),
        keyframe_sink=keyframe_sink,
    )
    active = Path(settings.heartbeat_dir) / ACTIVE_CAMERAS_FILE
    return Ingest(
        s3=s3,
        sqs=sqs,
        jobs=JobStore(dynamodb.Table(settings.table_jobs)),
        detections=dynamodb.Table(settings.table_detections),
        cameras=dynamodb.Table(settings.table_cameras),
        pipeline=pipeline,
        candidates_url=settings.require("queue_candidates_url"),
        work_dir=Path(settings.state_dir) / "work",
        active_cameras=lambda: read_active_cameras(active),
    )


def main() -> None:
    setup_logging(SERVICE)
    configure(SERVICE)
    ingest = build_ingest()
    queue_url = get_settings().require("queue_jobs_url")
    log.info("ingest_ready")
    while True:
        beat(SERVICE)
        ingest.run_once(queue_url)


if __name__ == "__main__":
    main()
