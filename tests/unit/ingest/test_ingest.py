"""rw-ingest, rw-camera-sim and rw-vision (sprint-1.md N-11) against moto with synthetic clips."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import cv2
import numpy as np
import pytest
from moto import mock_aws

from rw.common import metrics
from rw.common.config import get_settings
from rw.common.ids import new_id
from rw.contracts import CandidateMessage, Job, VisionResult
from rw.ingest import jobs as jobs_mod
from rw.ingest.__main__ import Ingest, read_active_cameras
from rw.ingest.jobs import JobStore, UnsupportedKey, parse_key, s3_event, s3_objects, sim_key
from rw.mcp_tools.store import DynamoDetectionStore
from rw.vision import __main__ as vision_main
from rw.vision.pipeline import VisionPipeline
from scripts.make_synthetic_clip import write_clip

REGION = "us-east-1"
DATA = "rw-data-123456789012"
ARTIFACTS = "rw-artifacts-123456789012"
T0 = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
TABLES = {
    "rw-cameras": ("camera_id", None, None),
    "rw-jobs": ("job_id", None, ("camera-index", "camera_id", "created_at")),
    "rw-detections": ("camera_id", "ts_result", None),
    "rw-incidents": ("incident_id", None, ("status-index", "status", "created_at")),
}


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clips")
    still = np.full((360, 640, 3), 140, np.uint8)
    ok, jpeg = cv2.imencode(".jpg", still)
    assert ok
    (folder / "photo.jpg").write_bytes(jpeg.tobytes())
    (folder / "broken.mp4").write_bytes(b"not a video")
    return {
        "rip": write_clip(folder / "rip.mp4"),
        "static": write_clip(folder / "static.mp4", static=True),
        "photo": folder / "photo.jpg",
        "broken": folder / "broken.mp4",
    }


@pytest.fixture
def aws(monkeypatch, tmp_path):
    for key in ("AWS_PROFILE", "RW_AWS_ENDPOINT_URL", "RW_RUNTIME", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    get_settings.cache_clear()
    metrics.reset()
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        tables = {}
        for name, (hash_key, range_key, gsi) in TABLES.items():
            keys = [{"AttributeName": hash_key, "KeyType": "HASH"}]
            attrs = {hash_key: "S"}
            if range_key:
                keys.append({"AttributeName": range_key, "KeyType": "RANGE"})
                attrs[range_key] = "S"
            extra = {}
            if gsi:
                index, gh, gr = gsi
                attrs |= {gh: "S", gr: "S"}
                extra["GlobalSecondaryIndexes"] = [{
                    "IndexName": index,
                    "KeySchema": [{"AttributeName": gh, "KeyType": "HASH"},
                                  {"AttributeName": gr, "KeyType": "RANGE"}],
                    "Projection": {"ProjectionType": "ALL"},
                }]  # fmt: skip
            tables[name] = ddb.create_table(
                TableName=name,
                KeySchema=keys,
                AttributeDefinitions=[
                    {"AttributeName": k, "AttributeType": t} for k, t in attrs.items()
                ],
                BillingMode="PAY_PER_REQUEST",
                **extra,
            )
        tables["rw-cameras"].put_item(
            Item={"camera_id": "cam-01", "enabled": True, "replay_prefix": "replay/clips/cam-01/"}
        )
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=DATA)
        s3.create_bucket(Bucket=ARTIFACTS)
        sqs = boto3.client("sqs", region_name=REGION)
        queues = {
            q: sqs.create_queue(QueueName=q)["QueueUrl"] for q in ("rw-jobs", "rw-candidates")
        }
        active: dict[str, str] = {}

        def keyframe_sink(key: str, data: bytes) -> str:
            s3.put_object(Bucket=ARTIFACTS, Key=key, Body=data)
            return f"s3://{ARTIFACTS}/{key}"

        ingest = Ingest(
            s3=s3,
            sqs=sqs,
            jobs=JobStore(tables["rw-jobs"]),
            detections=tables["rw-detections"],
            cameras=tables["rw-cameras"],
            pipeline=VisionPipeline(keyframe_sink=keyframe_sink),
            candidates_url=queues["rw-candidates"],
            work_dir=tmp_path / "work",
            active_cameras=lambda: dict(active),
            clock=lambda: T0,
        )
        yield {"s3": s3, "sqs": sqs, "tables": tables, "queues": queues, "ingest": ingest,
               "active": active, "tmp": tmp_path}  # fmt: skip
    get_settings.cache_clear()
    metrics.reset()


def _put(aws, path: Path, key: str) -> None:
    aws["s3"].upload_file(str(path), DATA, key)


def _candidates(aws) -> list[CandidateMessage]:
    messages = (
        aws["sqs"]
        .receive_message(QueueUrl=aws["queues"]["rw-candidates"], MaxNumberOfMessages=10)
        .get("Messages", [])
    )
    return [CandidateMessage.model_validate_json(m["Body"]) for m in messages]


def _job(aws, job_id: str) -> dict:
    return aws["tables"]["rw-jobs"].get_item(Key={"job_id": job_id})["Item"]


# ---------------------------------------------------------------- keys and events


def test_parse_camera_sim_and_upload_keys():
    sim = parse_key("incoming/cam-01/20261006T100000Z-000042.mp4")
    assert (sim.camera_id, sim.source.value, sim.seq, sim.mode.value) == (
        "cam-01", "camera_sim", 42, "video",
    )  # fmt: skip
    assert sim.ts == T0 and sim.stem == "20261006T100000Z-000042"
    job_id = new_id("job")
    upload = parse_key(f"incoming/upload/{job_id}.jpeg")
    assert (upload.camera_id, upload.job_id, upload.mode.value) == ("cam-00", job_id, "image")
    assert parse_key(f"incoming/cam-02/{job_id}.zip").mode.value == "burst"
    assert sim_key("cam-01", T0, 42) == sim.key


@pytest.mark.parametrize(
    "key",
    ["incoming/cam-01/notes.txt", "replay/clips/cam-01/a.mp4", "incoming/cam-01/x.mp4",
     "incoming/cam-01/20261006T100000Z-000042.txt"],
)  # fmt: skip
def test_other_keys_are_unsupported(key):
    with pytest.raises(UnsupportedKey):
        parse_key(key)


def test_s3_events_are_unquoted_and_test_events_skipped():
    key = "incoming/cam-01/20261006T100000Z-000001.mp4"
    assert s3_objects(s3_event(DATA, key)) == [(DATA, key)]
    raw = {"Records": [{"eventName": "ObjectCreated:Put", "s3": {"bucket": {"name": DATA},
           "object": {"key": "incoming%2Fcam-01%2Fa+b.mp4"}}}]}  # fmt: skip
    assert s3_objects(json.dumps(raw)) == [(DATA, "incoming/cam-01/a b.mp4")]
    assert s3_objects(json.dumps({"Event": "s3:TestEvent", "Bucket": DATA})) == []


# ---------------------------------------------------------------- processing


def test_rip_clip_from_camera_sim_becomes_a_result_and_a_candidate(aws, clips):
    key = sim_key("cam-01", T0, 1)
    job = JobStore(aws["tables"]["rw-jobs"]).create(parse_key(key), jobs_mod.JobStatus.QUEUED, T0)
    _put(aws, clips["rip"], key)

    result = aws["ingest"].handle(DATA, key)

    assert result.summary.status.value == "rip" and result.job_id == job["job_id"]
    assert (
        result.input.s3_uri == f"s3://{DATA}/{key}"
        and result.source_id == f"cam-01/{parse_key(key).stem}"
    )
    stored = DynamoDetectionStore(aws["tables"]["rw-detections"]).get_result(
        "cam-01", result.result_id
    )
    assert stored == result
    row = _job(aws, job["job_id"])
    assert row["status"] == "done" and row["result_id"] == result.result_id
    assert aws["tables"]["rw-jobs"].scan()["Count"] == 1  # the camera-sim row, not a new one
    (candidate,) = _candidates(aws)
    assert candidate.reason.value == "status_rip" and candidate.result_id == result.result_id
    assert result.keyframes and aws["s3"].head_object(
        Bucket=ARTIFACTS, Key=result.keyframes[0].s3_uri.split(f"{ARTIFACTS}/", 1)[1]
    )
    assert not (aws["tmp"] / "work" / job["job_id"]).exists()
    names = {r.name for r in metrics.recorded()}
    assert {"JobsProcessed", "RipCandidates", "FramesPerSecond", "FrameLatencyMs"} <= names


def test_clear_clip_sends_nothing_unless_the_camera_has_an_active_incident(aws, clips):
    first = sim_key("cam-01", T0, 1)
    _put(aws, clips["static"], first)
    assert aws["ingest"].handle(DATA, first).summary.status.value == "clear"
    assert _candidates(aws) == []

    aws["active"]["cam-01"] = "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
    second = sim_key("cam-01", T0 + timedelta(seconds=10), 2)
    _put(aws, clips["static"], second)
    aws["ingest"].handle(DATA, second)
    (candidate,) = _candidates(aws)
    assert candidate.reason.value == "active_incident_followup"
    assert candidate.active_incident_id == "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
    assert candidate.status.value == "clear"


def test_out_of_order_clip_runs_on_a_fresh_state(aws, clips):
    newer, older = sim_key("cam-01", T0, 5), sim_key("cam-01", T0 - timedelta(seconds=10), 4)
    _put(aws, clips["rip"], newer)
    _put(aws, clips["rip"], older)

    first = aws["ingest"].handle(DATA, newer)
    second = aws["ingest"].handle(DATA, older)

    assert not first.out_of_order and second.out_of_order
    assert aws["ingest"].states["cam-01"].last_seq == 5


def test_upload_updates_the_job_rw_api_created(aws, clips):
    job_id = new_id("job")
    key = f"incoming/cam-01/{job_id}.jpg"
    aws["tables"]["rw-jobs"].put_item(Item={
        "job_id": job_id, "camera_id": "cam-01", "source": "upload", "s3_key": key,
        "mode": "image", "status": "awaiting_upload", "result_id": None, "error": None,
        "followup_request": None, "created_at": "2026-10-06T09:59:00Z",
        "updated_at": "2026-10-06T09:59:00Z",
    })  # fmt: skip
    _put(aws, clips["photo"], key)

    result = aws["ingest"].handle(DATA, key)

    assert result.mode.value == "image" and result.job_id == job_id
    row = _job(aws, job_id)
    Job.model_validate({k: v for k, v in row.items() if k in Job.model_fields})
    assert row["status"] == "done" and row["source"] == "upload"
    assert aws["tables"]["rw-jobs"].scan()["Count"] == 1


def test_undecodable_clip_fails_the_job_and_keeps_going(aws, clips):
    key = sim_key("cam-01", T0, 1)
    _put(aws, clips["broken"], key)

    assert aws["ingest"].handle(DATA, key) is None

    (row,) = aws["tables"]["rw-jobs"].scan()["Items"]
    assert row["status"] == "failed" and row["error"]
    assert row["source"] == "camera_sim"  # created by ingest when camera-sim had not
    assert _candidates(aws) == []


def test_run_once_consumes_s3_events_and_skips_test_events(aws, clips):
    key = sim_key("cam-01", T0, 1)
    _put(aws, clips["rip"], key)
    queue = aws["queues"]["rw-jobs"]
    aws["sqs"].send_message(QueueUrl=queue, MessageBody=json.dumps({"Event": "s3:TestEvent"}))
    aws["sqs"].send_message(QueueUrl=queue, MessageBody=s3_event(DATA, key))

    assert aws["ingest"].run_once(queue, wait_s=0) == 0
    assert aws["ingest"].run_once(queue, wait_s=0) == 1
    assert aws["sqs"].receive_message(QueueUrl=queue, WaitTimeSeconds=0).get("Messages") is None
    assert len(_candidates(aws)) == 1


# ---------------------------------------------------------------- camera-sim and rw-vision


def test_camera_sim_copies_clips_in_order_and_loops(aws, clips):
    from rw.camera_sim.__main__ import load_state, tick

    for name in ("a.mp4", "b.mp4"):
        _put(aws, clips["static"], f"replay/clips/cam-01/{name}")
    off = {"camera_id": "cam-02", "enabled": False, "replay_prefix": "replay/clips/cam-02/"}
    aws["tables"]["rw-cameras"].put_item(Item=off)
    state = aws["tmp"] / "state" / "camera_sim_state.json"
    jobs = JobStore(aws["tables"]["rw-jobs"])
    times = iter(T0 + timedelta(seconds=10 * i) for i in range(10))

    written = [tick(aws["s3"], aws["tables"]["rw-cameras"], jobs, DATA, state, lambda: next(times))
               for _ in range(3)]  # fmt: skip

    keys = [k for (k,) in written]
    assert [parse_key(k).seq for k in keys] == [1, 2, 3]
    sources = [aws["s3"].head_object(Bucket=DATA, Key=k)["Metadata"]["source-clip"] for k in keys]
    assert sources == ["replay/clips/cam-01/a.mp4", "replay/clips/cam-01/b.mp4",
                       "replay/clips/cam-01/a.mp4"]  # fmt: skip
    assert load_state(state) == {"cam-01": {"next_clip": 1, "next_seq": 4}}
    rows = aws["tables"]["rw-jobs"].scan()["Items"]
    assert {r["status"] for r in rows} == {"queued"} and len(rows) == 3
    assert {r["s3_key"] for r in rows} == set(keys)


def test_vision_unit_writes_active_cameras_for_ingest(aws):
    incidents = aws["tables"]["rw-incidents"]
    for incident_id, camera, status, created in [
        ("inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9A", "cam-01", "watching", "2026-10-06T09:00:00Z"),
        ("inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9B", "cam-01", "approved", "2026-10-06T09:30:00Z"),
        ("inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9C", "cam-02", "closed", "2026-10-06T09:40:00Z"),
        ("inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9D", "cam-03", "alerted", "2026-10-06T09:10:00Z"),
    ]:
        incidents.put_item(Item={"incident_id": incident_id, "camera_id": camera,
                                 "status": status, "created_at": created})  # fmt: skip
    path = aws["tmp"] / "run" / "active_cameras.json"

    cameras = vision_main.refresh(incidents, path)

    assert cameras == {"cam-01": "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9B",
                       "cam-03": "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9D"}  # fmt: skip
    assert read_active_cameras(path) == cameras
    assert read_active_cameras(aws["tmp"] / "missing.json") == {}


def test_results_validate_against_the_contract(aws, clips):
    key = sim_key("cam-01", T0, 1)
    _put(aws, clips["rip"], key)
    result = aws["ingest"].handle(DATA, key)
    VisionResult.model_validate_json(result.model_dump_json())
