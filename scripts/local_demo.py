"""The full RipWatch loop on a laptop (sprint-1.md J-01): make local-up && make local-demo.

Against moto server (RW_AWS_ENDPOINT_URL, local only) with the fake model:
  1. resets moto, seeds it (scripts/local_seed.py) and writes the synthetic clips and a photo
  2. starts rw-mcp-tools over HTTP in a thread (rechecker = the real VisionPipeline.recheck)
  3. feeds 6 clips through camera-sim (3 with a rip, then 3 clear) and one uploaded photo, each
     through ingest (vision in-process), the rw-vision active-camera refresh and the agent
  4. approves the incident through the rw-api handler after the first alert
  5. prints each clip's status, each decision with its tool calls, the incident lifecycle and the
     full trace of the incident, then exits 0 if every expected state was reached, else 1

The services run as sequential steps in one process (only rw-mcp-tools has its own thread) so the
run is deterministic and the exit code means something. moto does not reliably deliver S3 event
notifications, so when none arrives the demo sends the S3-style event to rw-jobs itself.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import logging
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RW_AWS_ENDPOINT_URL", "http://127.0.0.1:5000")
os.environ.setdefault("RW_LLM", "fake")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCOUNT_ID", "123456789012")  # moto's account
WORK = ROOT / "work" / "demo"
os.environ.setdefault("RW_HEARTBEAT_DIR", str(WORK / "run"))
os.environ.setdefault("RW_STATE_DIR", str(WORK / "state"))

import anyio  # noqa: E402
import cv2  # noqa: E402
import numpy as np  # noqa: E402
from mcp import Client  # noqa: E402

from rw.agent.__main__ import build_agent  # noqa: E402
from rw.agent.fake_llm import FakeLLM  # noqa: E402
from rw.agent.trace import DynamoTraceSink  # noqa: E402
from rw.camera_sim.__main__ import STATE_FILE, tick  # noqa: E402
from rw.common import metrics  # noqa: E402
from rw.common.aws import client, resource  # noqa: E402
from rw.common.config import get_settings  # noqa: E402
from rw.contracts import CandidateMessage  # noqa: E402
from rw.ingest.__main__ import ACTIVE_CAMERAS_FILE, build_ingest  # noqa: E402
from rw.ingest.jobs import JobStore, parse_key, s3_event  # noqa: E402
from rw.mcp_tools.__main__ import build_deps  # noqa: E402
from rw.mcp_tools.server import build_server  # noqa: E402
from rw.vision import __main__ as vision_unit  # noqa: E402
from scripts import local_seed  # noqa: E402
from scripts.make_synthetic_clip import write_clip  # noqa: E402

CAMERA = "cam-01"
REPLAY = f"replay/clips/{CAMERA}/"
CLIPS = ("1-rip", "2-rip", "3-rip", "4-clear", "5-clear", "6-clear")
CLAIMS = {"sub": "local-demo", "email": "head.lifeguard@example.com"}


@dataclass
class Step:
    label: str
    status: str | None = None
    confidence: float | None = None
    candidate: str | None = None
    decision: str | None = None
    tools: list[str] = field(default_factory=list)
    incident: str | None = None


# ---------------------------------------------------------------- setup


def reset_and_seed() -> dict[str, Any]:
    endpoint = local_seed.require_local_endpoint()
    reset = urllib.request.Request(f"{endpoint}/moto-api/reset", method="POST")  # noqa: S310
    urllib.request.urlopen(reset, timeout=10).close()  # noqa: S310 (local moto only)
    get_settings.cache_clear()
    summary = local_seed.seed()
    os.environ["RW_QUEUE_JOBS_URL"] = summary["queues"]["jobs"]
    os.environ["RW_QUEUE_CANDIDATES_URL"] = summary["queues"]["candidates"]
    os.environ["RW_TOPIC_LIFEGUARD_ARN"] = summary["topic"]
    # rw-api reads these from its Lambda environment (infra/stacks/serverless), not from Settings.
    os.environ["RW_DATA_BUCKET"] = summary["buckets"]["data"]
    os.environ["RW_ARTIFACTS_BUCKET"] = summary["buckets"]["artifacts"]
    get_settings.cache_clear()
    return summary


def make_inputs() -> dict[str, Path]:
    """Synthetic rip and calm clips, and a photo with a dark smooth gap in the surf band."""
    folder = WORK / "inputs"
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    photo = np.clip(90 + rng.normal(0, 25, (360, 640)), 0, 255)
    photo[120:260, :] = np.clip(200 + rng.normal(0, 30, (140, 640)), 0, 255)
    photo[120:260, 270:370] = 120  # the gap: darker and smooth, like a rip through the surf
    gray = photo.astype(np.uint8)
    cv2.imwrite(str(folder / "photo.jpg"), cv2.merge([gray, gray, gray]))
    return {
        "rip": write_clip(folder / "rip.mp4"),
        "clear": write_clip(folder / "clear.mp4", static=True),
        "photo": folder / "photo.jpg",
    }


def start_mcp(rechecker: Any) -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):  # one line per HTTP call
        logging.getLogger(name).setLevel(logging.WARNING)
    server = build_server(replace(build_deps(), rechecker=rechecker))
    threading.Thread(
        target=server.run, args=("streamable-http",),
        kwargs={"host": "127.0.0.1", "port": port}, daemon=True,
    ).start()  # fmt: skip
    for _ in range(100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return f"http://127.0.0.1:{port}/mcp"
        time.sleep(0.05)
    raise RuntimeError("rw-mcp-tools did not start")


def load_api() -> Any:
    """rw-api's handler from a folder laid out like its Lambda zip."""
    build = WORK / "rw-api-zip"
    shutil.rmtree(build, ignore_errors=True)
    (build / "rw_shared").mkdir(parents=True)
    for source in (ROOT / "lambdas" / "api").glob("*.py"):
        shutil.copy(source, build / source.name)
    shutil.copy(ROOT / "rw/contracts/enums.py", build / "rw_shared" / "enums.py")
    shutil.copy(ROOT / "rw/agent/lifecycle_rules.py", build / "rw_shared" / "lifecycle_rules.py")
    sys.path.insert(0, str(build))
    spec = importlib.util.spec_from_file_location("handler", build / "handler.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["handler"] = module
    spec.loader.exec_module(module)
    return module


def api(handler: Any, route: str, path: dict | None = None, body: dict | None = None) -> dict:
    event = {
        "routeKey": route,
        "pathParameters": path,
        "body": None if body is None else json.dumps(body),
        "requestContext": {"authorizer": {"jwt": {"claims": CLAIMS}}},
    }
    with contextlib.redirect_stdout(io.StringIO()):  # rw-api's JSON log lines
        response = handler.handler(event)
    payload = json.loads(response["body"])
    if response["statusCode"] != 200:
        raise RuntimeError(f"{route} -> {response['statusCode']}: {payload}")
    return payload


# ---------------------------------------------------------------- one input through the loop


def ingest_key(ingest: Any, bucket: str, key: str) -> Any:
    """Take the S3 event moto delivered, or send it ourselves when moto did not."""
    jobs_url = get_settings().require("queue_jobs_url")
    if ingest.run_once(jobs_url, wait_s=1) == 0:
        client("sqs").send_message(QueueUrl=jobs_url, MessageBody=s3_event(bucket, key))
        ingest.run_once(jobs_url, wait_s=1)
    found = client("sqs").receive_message(QueueUrl=jobs_url, WaitTimeSeconds=0)
    for message in found.get("Messages", []):  # a late duplicate event: drop it
        client("sqs").delete_message(QueueUrl=jobs_url, ReceiptHandle=message["ReceiptHandle"])
    return JobStore(resource("dynamodb").Table(get_settings().table_jobs)).find(parse_key(key))


async def run_agent(agent: Any, step: Step) -> None:
    sqs, url = client("sqs"), get_settings().require("queue_candidates_url")
    while True:
        found = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=1, WaitTimeSeconds=0)
        messages = found.get("Messages", [])
        if not messages:
            return
        message = messages[0]
        candidate = CandidateMessage.model_validate_json(message["Body"])
        step.candidate = candidate.reason.value
        scenario = "image_followup" if candidate.mode.value == "image" else "confident_rip_alert"
        agent.deps.llm = FakeLLM.scenario(scenario)
        sink = agent.deps.trace_sink
        # Follow-ups append to the incident's trace; a new candidate starts its own trace.
        incident = candidate.active_incident_id
        before = len(sink.query(incident)) if incident else 0
        decision = await agent.handle(candidate)
        sqs.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
        if decision is None:
            step.decision = "suppressed_duplicate (cooldown)"
            continue
        step.decision, step.incident = decision.decision.value, decision.incident_id
        key = decision.incident_id or f"cand_{candidate.result_id}"
        steps = sink.query(key)[before:]
        step.tools = [s.name for s in steps if s.type.value == "tool_call"]


# ---------------------------------------------------------------- report and checks


def incident_row(incident_id: str) -> dict:
    table = resource("dynamodb").Table(get_settings().table_incidents)
    return table.get_item(Key={"incident_id": incident_id})["Item"]


def print_report(steps: list[Step], incident_id: str | None, trace_sink: Any) -> None:
    print("\n== Clips and decisions")
    print("  (clear clips right after rips still read as rip: the baseline averages flow over the")
    print("   last 6 clips. With no active incident left, the cooldown suppresses repeat alerts.)")
    for s in steps:
        vision = "-" if s.status is None else f"{s.status} {s.confidence:.2f}"
        tools = f"  tools: {', '.join(s.tools)}" if s.tools else ""
        print(f"  {s.label:<14} vision {vision:<15} candidate {s.candidate or '-':<25}"
              f" decision {s.decision or '-'}{tools}")  # fmt: skip
    if incident_id is None:
        return
    trace = trace_sink.query(incident_id)
    print(f"\n== Incident {incident_id} lifecycle")
    for t in trace:
        out = t.output_summary or {}
        if t.type.value == "status_change" and "from" in out:
            print(f"  {t.created_at:%H:%M:%S}  {out['from']} -> {out['to']}")
    print(f"\n== Full trace of {incident_id} ({len(trace)} steps)")
    for t in trace:
        print(f"  {t.step:>2}. {t.type.value:<19} {t.name:<17} {_detail(t)[:90]}")


def _detail(t: Any) -> str:
    out = t.output_summary or {}
    if t.type.value == "status_change" and "from" in out:
        return f"{out['from']} -> {out['to']}"
    if t.type.value == "decision":
        text = f"{out.get('decision')}, risk {out.get('risk_level')}"
        if out.get("requested_action"):
            text += f", asks {out['requested_action']}"
        if out.get("used_fallback"):
            text += ", fallback"
        return text + (f" ({'; '.join(out['reasons'])})" if out.get("reasons") else "")
    if t.type.value == "tool_call":
        keep = ("rip_id", "action", "clips", "outcome", "risk_level")
        args = ", ".join(f"{k}={v}" for k, v in (t.input or {}).items() if k in keep)
        return args or (t.error or "")
    if t.type.value == "human_approval":
        return f"{(t.input or {}).get('action')} by {(t.input or {}).get('by')}"
    return t.reasoning_summary or ""


async def main_async() -> int:
    inputs = make_inputs()
    seeded = reset_and_seed()
    settings = get_settings()
    data = seeded["buckets"]["data"]
    s3 = client("s3")
    for name in CLIPS:
        s3.upload_file(str(inputs[name.split("-")[1]]), data, f"{REPLAY}{name}.mp4")

    shutil.rmtree(WORK / "state", ignore_errors=True)
    shutil.rmtree(WORK / "run", ignore_errors=True)
    metrics.reset()
    ingest = build_ingest()
    mcp_url = start_mcp(ingest.pipeline.recheck)
    handler = load_api()
    incidents = resource("dynamodb").Table(settings.table_incidents)
    cameras = resource("dynamodb").Table(settings.table_cameras)
    jobs = JobStore(resource("dynamodb").Table(settings.table_jobs))
    active_path = Path(settings.heartbeat_dir) / ACTIVE_CAMERAS_FILE
    state_path = Path(settings.state_dir) / STATE_FILE
    checks: dict[str, bool] = {}
    steps: list[Step] = []
    incident_id = None

    async with Client(mcp_url) as mcp:
        agent = build_agent(mcp)
        for number, name in enumerate(CLIPS, start=1):
            vision_unit.refresh(incidents, active_path)
            (key,) = tick(s3, cameras, jobs, data, state_path)
            step = Step(f"clip {number} ({name.split('-')[1]})")
            job = ingest_key(ingest, data, key)
            if job and job.get("result_id"):
                result = ingest.detections.query(
                    KeyConditionExpression="camera_id = :c",
                    FilterExpression="result_id = :r",
                    ExpressionAttributeValues={":c": CAMERA, ":r": job["result_id"]},
                )["Items"][0]
                summary = json.loads(result["result"])["summary"]
                step.status, step.confidence = summary["status"], summary["max_confidence"]
            await run_agent(agent, step)
            steps.append(step)
            if number == 1:
                incident_id = step.incident
                checks["clip 1 opens an alerted incident"] = bool(
                    incident_id and incident_row(incident_id)["status"] == "alerted"
                )
                if incident_id:
                    approved = api(handler, "POST /api/incidents/{incident_id}/approval",
                                   {"incident_id": incident_id},
                                   {"decision": "approve", "action": "raise_red_flag"})  # fmt: skip
                    steps.append(Step("approval", decision=f"rw-api: {approved['status']}",
                                      incident=incident_id))  # fmt: skip
                    checks["rw-api approval moves it to approved"] = (
                        approved["status"] == "approved"
                    )

        upload = api(handler, "POST /api/uploads", body={
            "filename": "photo.jpg", "content_type": "image/jpeg",
            "size_bytes": inputs["photo"].stat().st_size,
        })  # fmt: skip
        s3.upload_file(str(inputs["photo"]), data, upload["s3_key"])
        step = Step("photo upload")
        job = ingest_key(ingest, data, upload["s3_key"])
        if job and job.get("result_id"):
            items = ingest.detections.query(
                KeyConditionExpression="camera_id = :c",
                FilterExpression="result_id = :r",
                ExpressionAttributeValues={":c": job["camera_id"], ":r": job["result_id"]},
            )["Items"]
            summary = json.loads(items[0]["result"])["summary"]
            step.status, step.confidence = summary["status"], summary["max_confidence"]
        await run_agent(agent, step)
        steps.append(step)
        job_view = api(handler, "GET /api/jobs/{job_id}", {"job_id": upload["job_id"]})

    final = incident_row(incident_id) if incident_id else {}
    camera = cameras.get_item(Key={"camera_id": CAMERA})["Item"]
    cam_incidents = [i for i in incidents.scan()["Items"] if i["camera_id"] == CAMERA]
    checks["two rip follow-ups confirm it"] = final.get("status") == "confirmed"
    checks["approval raised the red flag on cam-01"] = camera.get("beach_flag") == "red"
    checks["later clips open no second cam-01 incident"] = len(cam_incidents) == 1
    checks["uncertain photo asks for a follow-up capture"] = bool(job_view.get("followup_request"))
    print_report(
        steps, incident_id, DynamoTraceSink(resource("dynamodb").Table(settings.table_trace))
    )
    if job_view.get("followup_request"):
        print(f"\n== Photo job {upload['job_id']}: follow-up request")
        print(f"  {job_view['followup_request'].get('message')}")
    counts: dict[str, int] = {}
    for r in metrics.recorded():
        counts[r.name] = counts.get(r.name, 0) + 1
    print("\n== Metrics emitted (count)")
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print("\n== Checks")
    for name, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    passed = all(checks.values())
    print(f"\nlocal-demo: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def main() -> int:
    return anyio.run(main_async)


if __name__ == "__main__":
    sys.exit(main())
