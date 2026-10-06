import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from rw.agent.cooldown import Cooldown, polygon_iou
from rw.agent.fake_llm import FakeLLM, _context, _fill
from rw.agent.llm import BedrockLLM, LLMError
from rw.agent.prompts import SYSTEM_PROMPT, summary_json, user_message
from rw.agent.risk import RiskThresholds, assess
from rw.agent.tools import SUBMIT_DECISION_SPEC, to_tool_spec
from rw.contracts import CandidateMessage, VisionResult
from rw.contracts.decision import RiskLevel


def _result(base, *, status="rip", conf=0.82, in_rip=True, distance=0.0, mode="video", glare=0.12):
    payload = copy.deepcopy(base)
    payload["mode"] = mode
    rip = payload["rips"][0]
    rip["confidence"] = conf
    rip["label"] = "rip" if conf >= 0.7 else "uncertain"
    payload["summary"].update(status=status, max_confidence=conf)
    swimmer = payload["swimmers"][0]
    swimmer["in_rip_id"] = rip["rip_id"] if in_rip else None
    swimmer["distance_to_rip_px"] = distance
    payload["summary"]["swimmers_at_risk"] = 1 if (in_rip or distance <= 30) else 0
    payload["quality"]["glare"] = glare
    if mode == "image":
        rip["evidence"]["seaward_flow_px_per_s"] = None
        swimmer["drift_px_per_s"] = None
    return VisionResult.model_validate(payload)


# ---------------------------------------------------------------- risk (section 9.5)


@pytest.mark.parametrize(
    ("kw", "statement", "level"),
    [
        ({}, False, "CRITICAL"),  # swimmer in the rip
        ({"in_rip": False, "distance": 200, "conf": 0.9}, False, "HIGH"),  # confident
        ({"in_rip": False, "distance": 20}, False, "HIGH"),  # swimmer near
        ({"in_rip": False, "distance": 200}, True, "HIGH"),  # rip statement
        ({"in_rip": False, "distance": 200}, False, "ELEVATED"),  # rip otherwise
        ({"status": "uncertain", "conf": 0.5, "in_rip": False, "distance": 20}, False, "ELEVATED"),
        ({"status": "uncertain", "conf": 0.5, "in_rip": False, "distance": 200}, True, "ELEVATED"),
        ({"status": "uncertain", "conf": 0.5, "in_rip": False, "distance": 200}, False, "LOW"),
        ({"mode": "image", "in_rip": False, "distance": 200, "conf": 0.9}, False, "ELEVATED"),
        ({"mode": "image"}, False, "HIGH"),  # image with a swimmer in the rip
        ({"in_rip": False, "distance": 200, "conf": 0.9, "glare": 0.5}, False, "ELEVATED"),
        ({"glare": 0.5}, False, "CRITICAL"),  # poor quality never lowers CRITICAL
    ],
)
def test_risk_rules(vision_result, kw, statement, level):
    risk = assess(_result(vision_result, **kw), statement)
    assert risk.level == RiskLevel(level), risk.reasons
    assert risk.reasons


def test_thresholds_from_ssm_json():
    assert RiskThresholds.from_json("{}") == RiskThresholds()
    assert RiskThresholds.from_json("") == RiskThresholds()
    custom = RiskThresholds.from_json('{"high_confidence": 0.9, "ignored": 1}')
    assert custom.high_confidence == 0.9 and custom.near_rip_px == 30


# ---------------------------------------------------------------- cooldown


def test_polygon_iou():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert polygon_iou(square, square) == pytest.approx(1.0)
    assert polygon_iou(square, [(20, 20), (30, 20), (30, 30), (20, 30)]) == 0.0
    half = [(5, 0), (15, 0), (15, 10), (5, 10)]
    assert 0.3 < polygon_iou(square, half) < 0.5


def test_cooldown_window_and_overlap():
    now = datetime(2026, 10, 5, 10, 15, tzinfo=UTC)
    poly = [(300, 140), (355, 140), (355, 260), (300, 260)]
    cooldown = Cooldown()
    assert not cooldown.is_duplicate("cam-01", poly, now, 60)
    cooldown.record("cam-01", poly, now)
    assert cooldown.is_duplicate("cam-01", poly, now + timedelta(seconds=30), 60)
    assert not cooldown.is_duplicate("cam-02", poly, now + timedelta(seconds=30), 60)
    assert not cooldown.is_duplicate("cam-01", [(0, 0), (20, 0), (20, 20)], now, 60)
    assert not cooldown.is_duplicate("cam-01", poly, now + timedelta(seconds=61), 60)
    assert not cooldown.is_duplicate("cam-01", None, now, 60)


# ---------------------------------------------------------------- fake LLM


def test_fake_llm_fills_placeholders_from_ids_and_tool_results():
    candidate = 'Candidate:\n{"ids": {"trace_id": "tr_1", "result_id": "res_1"}}'
    result = {"json": {"incident_id": "inc_9", "n": 3}}
    tool_use = {"toolUseId": "a", "name": "x", "input": {}}
    messages = [
        {"role": "user", "content": [{"text": candidate}]},
        {"role": "assistant", "content": [{"toolUse": tool_use}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "a", "content": [result]}}]},
    ]
    ctx = _context(messages)
    assert ctx == {"trace_id": "tr_1", "result_id": "res_1", "incident_id": "inc_9", "n": 3}
    assert _fill(
        {"a": "{incident_id}", "b": "id {trace_id}", "c": "{n}", "d": "{missing}"}, ctx
    ) == {
        "a": "inc_9",
        "b": "id tr_1",
        "c": 3,
        "d": "{missing}",
    }


def test_fake_llm_turns_repeat_raise_and_exhaust():
    llm = FakeLLM([{"repeat": 2, "tool_use": [{"name": "t"}]}, {"raise": "Boom"}])
    first = llm.converse([], "", {})
    assert first["stopReason"] == "tool_use" and first["usage"]["inputTokens"] == 120
    llm.converse([], "", {})
    with pytest.raises(LLMError, match="Boom"):
        llm.converse([], "", {})
    with pytest.raises(LLMError, match="exhausted"):
        llm.converse([], "", {})


@pytest.mark.parametrize(
    "name",
    [
        "confident_rip_alert",
        "uncertain_false_alarm",
        "image_followup",
        "bedrock_error",
        "tool_loop",
    ],
)
def test_every_scenario_script_loads(name):
    assert FakeLLM.scenario(name).turns


# ---------------------------------------------------------------- Bedrock client


class StubBedrock:
    def __init__(self, fail=False):
        self.fail, self.kwargs = fail, None

    def converse(self, **kwargs):
        if self.fail:
            raise TimeoutError("read timed out")
        self.kwargs = kwargs
        return {
            "output": {"message": {"role": "assistant", "content": []}},
            "stopReason": "end_turn",
        }


def test_bedrock_llm_sends_converse_with_limits():
    stub = StubBedrock()
    BedrockLLM("amazon.nova-lite-v1:0", stub).converse([{"role": "user"}], "sys", {"tools": []})
    assert stub.kwargs["modelId"] == "amazon.nova-lite-v1:0"
    assert stub.kwargs["system"] == [{"text": "sys"}]
    assert stub.kwargs["inferenceConfig"] == {"maxTokens": 800, "temperature": 0.2}


def test_bedrock_llm_wraps_errors():
    with pytest.raises(LLMError, match="TimeoutError: read timed out"):
        BedrockLLM("m", StubBedrock(fail=True)).converse([], "s", {})


# ---------------------------------------------------------------- prompts and tool specs


def test_system_prompt_stays_short():
    assert len(SYSTEM_PROMPT.split()) < 450  # about 600 tokens at most


def test_user_message_is_a_compact_summary(vision_result, candidate):
    result = VisionResult.model_validate(vision_result)
    from rw.agent.risk import Risk

    summary = summary_json(
        CandidateMessage.model_validate(candidate), result, None, Risk(RiskLevel.HIGH, ["r"]), False
    )
    text = user_message(summary)
    data = json.loads(text[text.index("{") :])
    assert (
        data["ids"]["top_rip_id"] == result.rips[0].rip_id and data["pre_risk"]["level"] == "HIGH"
    )
    assert "keyframes" not in text and "timings_ms" not in text
    assert len(text) < 2000


def test_tool_specs_are_bedrock_shaped():
    class Tool:
        name, description = "zoom_and_recheck", "Look closer."
        input_schema = {"type": "object", "properties": {}}

    spec = to_tool_spec(Tool())
    assert spec["toolSpec"]["inputSchema"]["json"]["type"] == "object"
    assert SUBMIT_DECISION_SPEC["toolSpec"]["inputSchema"]["json"]["required"] == [
        "decision",
        "risk_level",
        "reasons",
    ]
