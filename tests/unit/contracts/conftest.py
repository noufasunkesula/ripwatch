"""Valid example payloads for every contract, based on sprint-1.md section 7."""

from __future__ import annotations

import copy

import pytest

ULID = "01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
ULID2 = "01J9ZC4K9B1C2D3E4F5G6H7J8K"

VISION_RESULT = {
    "schema_version": "1.0",
    "result_id": f"res_{ULID}",
    "trace_id": f"tr_{ULID}",
    "camera_id": "cam-01",
    "source_id": "cam-01/20261005T101500Z-000123",
    "job_id": f"job_{ULID2}",
    "mode": "video",
    "out_of_order": False,
    "input": {
        "s3_uri": "s3://rw-data-example/incoming/cam-01/20261005T101500Z-000123.mp4",
        "start_ts": "2026-10-05T10:15:00Z",
        "end_ts": "2026-10-05T10:15:10Z",
        "fps_source": 15.0,
        "fps_processed": 5.0,
        "frames_processed": 50,
        "width": 640,
        "height": 360,
    },
    "runtime": {
        "variant": "cool",
        "opencv_version": "5.0.0",
        "cv2_path": "/opt/cool/venvs/python_3.12/lib/python3.12/site-packages/cv2/__init__.py",
        "instance_type": "c7g.large",
        "pipeline": "baseline_flow",
        "pipeline_version": "0.1.0",
    },
    "summary": {
        "status": "rip",
        "max_confidence": 0.82,
        "rip_count": 1,
        "swimmer_count": 1,
        "swimmers_at_risk": 1,
    },
    "rips": [
        {
            "rip_id": "cam-01-rip-0007",
            "label": "rip",
            "confidence": 0.82,
            "polygon_px": [[312, 140], [340, 138], [355, 260], [300, 262]],
            "bbox_px": [300, 138, 55, 124],
            "polygon_m": None,
            "area_px": 5300,
            "area_m2": None,
            "evidence": {
                "detector_score": None,
                "flow_score": 0.79,
                "seaward_flow_px_per_s": 6.4,
                "seaward_flow_m_per_s": None,
                "timex_score": 0.61,
            },
            "first_seen_ts": "2026-10-05T10:14:40Z",
            "persist_s": 30.0,
        }
    ],
    "swimmers": [
        {
            "track_id": "cam-01-sw-0012",
            "bbox_px": [330, 200, 12, 18],
            "confidence": 0.71,
            "position_m": None,
            "in_rip_id": "cam-01-rip-0007",
            "distance_to_rip_px": 0.0,
            "distance_to_rip_m": None,
            "drift_px_per_s": [0.4, -1.8],
        }
    ],
    "keyframes": [
        {
            "index": 0,
            "ts": "2026-10-05T10:15:00Z",
            "s3_uri": f"s3://rw-artifacts-example/keyframes/cam-01/res_{ULID}/000.jpg",
        }
    ],
    "quality": {
        "glare": 0.12,
        "blur": 0.05,
        "low_light": False,
        "camera_shake_px": 1.3,
        "notes": [],
    },
    "timings_ms": {
        "decode": 210.0,
        "preprocess": 95.0,
        "stabilize": 120.0,
        "timex": 30.0,
        "flow": 640.0,
        "detect": 0.0,
        "track": 45.0,
        "keyframes": 80.0,
        "total": 1220.0,
    },
    "created_at": "2026-10-05T10:15:12Z",
}

CANDIDATE = {
    "schema_version": "1.0",
    "result_id": f"res_{ULID}",
    "trace_id": f"tr_{ULID}",
    "camera_id": "cam-01",
    "mode": "video",
    "status": "rip",
    "max_confidence": 0.82,
    "swimmers_at_risk": 1,
    "active_incident_id": None,
    "reason": "status_rip",
    "created_at": "2026-10-05T10:15:12Z",
}

DECISION = {
    "schema_version": "1.0",
    "decision_id": f"dec_{ULID}",
    "trace_id": f"tr_{ULID}",
    "result_id": f"res_{ULID}",
    "camera_id": "cam-01",
    "incident_id": f"inc_{ULID}",
    "decision": "alert",
    "risk_level": "HIGH",
    "reasons": ["rip confirmed after zoom (0.82 -> 0.88)", "1 swimmer drifting toward rip"],
    "requested_action": "raise_red_flag",
    "tool_calls": 4,
    "used_fallback": False,
    "model_id": "amazon.nova-lite-v1:0",
    "input_tokens": 2140,
    "output_tokens": 310,
    "latency_ms": 5400,
    "created_at": "2026-10-05T10:15:18Z",
}

TRACE_STEP = {
    "schema_version": "1.0",
    "trace_key": f"inc_{ULID}",
    "step": 3,
    "trace_id": f"tr_{ULID}",
    "type": "tool_call",
    "name": "zoom_and_recheck",
    "input": {"result_id": f"res_{ULID}", "rip_id": "cam-01-rip-0007", "zoom": 2.0},
    "output_summary": {"confidence": 0.88, "label": "rip"},
    "reasoning_summary": "Confidence was 0.82 with glare 0.12, zooming to confirm before alerting.",
    "latency_ms": 640,
    "error": None,
    "created_at": "2026-10-05T10:15:14Z",
}

APPROVAL = {
    "schema_version": "1.0",
    "incident_id": f"inc_{ULID}",
    "ts": "2026-10-05T10:16:02Z",
    "action": "raise_red_flag",
    "decision": "approve",
    "user_sub": "3f1c2a9e-0000-4000-8000-000000000001",
    "user_email": "lifeguard-demo@example.com",
    "reason": None,
    "latency_s": 44.0,
}

JOB = {
    "job_id": f"job_{ULID2}",
    "camera_id": "cam-01",
    "source": "camera_sim",
    "s3_key": "incoming/cam-01/20261005T101500Z-000123.mp4",
    "mode": "video",
    "status": "done",
    "result_id": f"res_{ULID}",
    "error": None,
    "followup_request": None,
    "created_at": "2026-10-05T10:15:00Z",
    "updated_at": "2026-10-05T10:15:12Z",
}


@pytest.fixture
def vision_result() -> dict:
    return copy.deepcopy(VISION_RESULT)


@pytest.fixture
def candidate() -> dict:
    return copy.deepcopy(CANDIDATE)


@pytest.fixture
def decision() -> dict:
    return copy.deepcopy(DECISION)


@pytest.fixture
def trace_step() -> dict:
    return copy.deepcopy(TRACE_STEP)


@pytest.fixture
def approval() -> dict:
    return copy.deepcopy(APPROVAL)


@pytest.fixture
def job() -> dict:
    return copy.deepcopy(JOB)
