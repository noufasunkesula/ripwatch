from __future__ import annotations

import pytest
from pydantic import ValidationError

from rw.contracts import (
    AgentDecision,
    Approval,
    CandidateMessage,
    Job,
    TraceStep,
    VisionResult,
)

ALL = [
    (VisionResult, "vision_result"),
    (CandidateMessage, "candidate"),
    (AgentDecision, "decision"),
    (TraceStep, "trace_step"),
    (Approval, "approval"),
    (Job, "job"),
]


@pytest.mark.parametrize(("model", "fixture"), ALL)
def test_valid_example_round_trips(model, fixture, request):
    data = request.getfixturevalue(fixture)
    obj = model.model_validate(data)
    again = model.model_validate_json(obj.model_dump_json())
    assert again == obj


@pytest.mark.parametrize(("model", "fixture"), ALL)
def test_extra_field_rejected(model, fixture, request):
    data = request.getfixturevalue(fixture)
    data["unexpected"] = 1
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(data)


def test_utc_timestamps_serialize_with_z(vision_result):
    dumped = VisionResult.model_validate(vision_result).model_dump(mode="json")
    assert dumped["created_at"] == "2026-10-05T10:15:12Z"


def test_naive_timestamp_rejected(candidate):
    candidate["created_at"] = "2026-10-05T10:15:12"
    with pytest.raises(ValidationError):
        CandidateMessage.model_validate(candidate)


# VisionResult


def test_polygon_33_points_rejected(vision_result):
    vision_result["rips"][0]["polygon_px"] = [[i, i] for i in range(33)]
    with pytest.raises(ValidationError, match="at most 32"):
        VisionResult.model_validate(vision_result)


def test_polygon_32_points_accepted(vision_result):
    vision_result["rips"][0]["polygon_px"] = [[i, i] for i in range(32)]
    VisionResult.model_validate(vision_result)


@pytest.mark.parametrize("path", ["rip", "swimmer", "summary"])
def test_confidence_above_one_rejected(vision_result, path):
    target = {
        "rip": vision_result["rips"][0],
        "swimmer": vision_result["swimmers"][0],
        "summary": vision_result["summary"],
    }[path]
    key = "max_confidence" if path == "summary" else "confidence"
    target[key] = 1.2
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        VisionResult.model_validate(vision_result)


def test_status_inconsistent_with_threshold_rejected(vision_result):
    vision_result["summary"]["status"] = "uncertain"
    with pytest.raises(ValidationError, match="does not match max_confidence"):
        VisionResult.model_validate(vision_result)


def test_rip_label_inconsistent_with_threshold_rejected(vision_result):
    vision_result["rips"][0]["label"] = "uncertain"
    with pytest.raises(ValidationError, match="label 'uncertain' does not match"):
        VisionResult.model_validate(vision_result)


def test_thresholds_from_context(vision_result):
    # With a stricter rip threshold, 0.82 becomes uncertain.
    vision_result["summary"]["status"] = "uncertain"
    vision_result["rips"][0]["label"] = "uncertain"
    ctx = {"rip_threshold": 0.9, "uncertain_threshold": 0.4}
    VisionResult.model_validate(vision_result, context=ctx)


def test_clear_result_with_no_rips(vision_result):
    vision_result["summary"].update(
        status="clear", max_confidence=0.1, rip_count=0, swimmer_count=0, swimmers_at_risk=0
    )
    vision_result["rips"] = []
    vision_result["swimmers"] = []
    vision_result["keyframes"] = []
    VisionResult.model_validate(vision_result)


def test_rips_null_rejected(vision_result):
    vision_result["rips"] = None
    with pytest.raises(ValidationError):
        VisionResult.model_validate(vision_result)


def test_rip_count_mismatch_rejected(vision_result):
    vision_result["summary"]["rip_count"] = 2
    with pytest.raises(ValidationError, match="rip_count"):
        VisionResult.model_validate(vision_result)


def test_swimmer_in_unknown_rip_rejected(vision_result):
    vision_result["swimmers"][0]["in_rip_id"] = "cam-01-rip-9999"
    with pytest.raises(ValidationError, match="is not in rips"):
        VisionResult.model_validate(vision_result)


def test_polygon_outside_frame_rejected(vision_result):
    vision_result["rips"][0]["polygon_px"][0] = [700, 140]
    with pytest.raises(ValidationError, match="outside the processed frame"):
        VisionResult.model_validate(vision_result)


def test_motion_fields_must_be_null_in_image_mode(vision_result):
    vision_result["mode"] = "image"
    with pytest.raises(ValidationError, match="image mode"):
        VisionResult.model_validate(vision_result)
    vision_result["rips"][0]["evidence"]["seaward_flow_px_per_s"] = None
    vision_result["swimmers"][0]["drift_px_per_s"] = None
    VisionResult.model_validate(vision_result)


def test_more_than_10_keyframes_rejected(vision_result):
    kf = vision_result["keyframes"][0]
    vision_result["keyframes"] = [dict(kf, index=i) for i in range(11)]
    with pytest.raises(ValidationError, match="at most 10"):
        VisionResult.model_validate(vision_result)


def test_size_over_64kb_rejected(vision_result):
    vision_result["quality"]["notes"] = ["x" * 1000] * 70
    with pytest.raises(ValidationError, match="not under 65536"):
        VisionResult.model_validate(vision_result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("result_id", "res_123"),
        ("trace_id", "res_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"),
        ("job_id", "job_01J9ZC4M6Y2N8Q4T7V1B3K5D9I"),  # I is not Crockford base32
        ("camera_id", "camera-1"),
    ],
)
def test_bad_ids_rejected(vision_result, field, value):
    vision_result[field] = value
    with pytest.raises(ValidationError):
        VisionResult.model_validate(vision_result)


# CandidateMessage


def test_followup_requires_incident(candidate):
    candidate.update(reason="active_incident_followup", status="clear", max_confidence=0.1)
    with pytest.raises(ValidationError, match="requires active_incident_id"):
        CandidateMessage.model_validate(candidate)
    candidate["active_incident_id"] = "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
    CandidateMessage.model_validate(candidate)


def test_reason_status_mismatch_rejected(candidate):
    candidate["reason"] = "status_uncertain"
    with pytest.raises(ValidationError, match="requires status uncertain"):
        CandidateMessage.model_validate(candidate)


# AgentDecision


@pytest.mark.parametrize("field", ["decision", "risk_level", "requested_action"])
def test_decision_enums_enforced(decision, field):
    decision[field] = "bogus"
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(decision)


def test_requested_action_only_on_alert(decision):
    decision["decision"] = "watch"
    with pytest.raises(ValidationError, match="only allowed with decision alert"):
        AgentDecision.model_validate(decision)
    decision["requested_action"] = None
    AgentDecision.model_validate(decision)


def test_ignore_without_incident(decision):
    decision.update(decision="ignore", risk_level="LOW", requested_action=None, incident_id=None)
    AgentDecision.model_validate(decision)


# TraceStep


def test_trace_key_candidate_form(trace_step):
    trace_step["trace_key"] = "cand_res_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
    TraceStep.model_validate(trace_step)


@pytest.mark.parametrize(
    "key", ["cand_01J9ZC4M6Y2N8Q4T7V1B3K5D9F", "tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"]
)
def test_bad_trace_key_rejected(trace_step, key):
    trace_step["trace_key"] = key
    with pytest.raises(ValidationError):
        TraceStep.model_validate(trace_step)


def test_step_type_enforced(trace_step):
    trace_step["type"] = "thinking"
    with pytest.raises(ValidationError):
        TraceStep.model_validate(trace_step)


# Approval


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reject_without_reason_rejected(approval, reason):
    approval.update(decision="reject", reason=reason)
    with pytest.raises(ValidationError, match="reason is required"):
        Approval.model_validate(approval)


def test_reject_with_reason_accepted(approval):
    approval.update(decision="reject", reason="Flags already up, crowd cleared")
    Approval.model_validate(approval)


# Job


def test_job_without_schema_version_gets_default(job):
    assert Job.model_validate(job).schema_version == "1.0"


def test_failed_job_requires_error(job):
    job.update(status="failed", result_id=None)
    with pytest.raises(ValidationError, match="error is required"):
        Job.model_validate(job)


def test_done_job_requires_result(job):
    job["result_id"] = None
    with pytest.raises(ValidationError, match="result_id is required"):
        Job.model_validate(job)
