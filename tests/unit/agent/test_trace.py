from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from rw.agent.trace import (
    MAX_FIELD_BYTES,
    InMemoryTraceSink,
    TraceWriter,
    summarize,
    truncate_text,
)
from rw.contracts.trace import StepType

ULID = "01J9ZC4M6Y2N8Q4T7V1B3K5D9F"
CAND_KEY = f"cand_res_{ULID}"
INC_KEY = f"inc_{ULID}"
TRACE_ID = f"tr_{ULID}"
NOW = datetime(2026, 10, 5, 10, 15, 14, tzinfo=UTC)


@pytest.fixture
def sink() -> InMemoryTraceSink:
    return InMemoryTraceSink()


def writer(sink, key=CAND_KEY) -> TraceWriter:
    return TraceWriter(key, TRACE_ID, sink, clock=lambda: NOW)


def test_steps_increment_from_one(sink):
    w = writer(sink)
    a = w.step("candidate_received", "rw-candidates", input={"result_id": f"res_{ULID}"})
    b = w.step(StepType.TOOL_CALL, "zoom_and_recheck", output_summary={"label": "rip"})
    assert (a.step, b.step) == (1, 2)
    assert [s.step for s in sink.query(CAND_KEY)] == [1, 2]
    assert b.created_at == NOW
    assert b.trace_id == TRACE_ID


def test_step_fields_are_stored(sink):
    row = writer(sink).step(
        "llm_call",
        "nova-lite",
        input={"prompt_tokens": 2140},
        output_summary={"tool": "zoom_and_recheck"},
        reasoning_summary="Zooming to confirm.",
        latency_ms=640.6,
    )
    assert row.type is StepType.LLM_CALL
    assert row.input == {"prompt_tokens": 2140}
    assert row.output_summary == {"tool": "zoom_and_recheck"}
    assert row.reasoning_summary == "Zooming to confirm."
    assert row.latency_ms == 641
    assert row.error is None


def test_missing_input_becomes_empty_dict(sink):
    row = writer(sink).step("decision", "final")
    assert row.input == {}
    assert row.output_summary is None


def test_exception_is_formatted(sink):
    row = writer(sink).step("fallback", "bedrock", error=TimeoutError("read timed out"))
    assert row.error == "TimeoutError: read timed out"


def test_unknown_step_type_rejected(sink):
    with pytest.raises(ValueError):
        writer(sink).step("thinking", "x")


def test_writer_continues_after_existing_steps(sink):
    writer(sink).step("candidate_received", "a")
    writer(sink).step("tool_call", "b")
    assert writer(sink).step("decision", "c").step == 3


def test_rekey_copies_steps_to_new_incident(sink):
    w = writer(sink)
    w.step("candidate_received", "a")
    w.step("tool_call", "b")
    w.rekey(INC_KEY)
    w.step("decision", "c")

    moved = sink.query(INC_KEY)
    assert [(s.step, s.name) for s in moved] == [(1, "a"), (2, "b"), (3, "c")]
    assert all(s.trace_key == INC_KEY for s in moved)
    assert len(sink.query(CAND_KEY)) == 2  # old rows are kept
    assert w.trace_key == INC_KEY


def test_rekey_appends_to_existing_incident_trace(sink):
    inc = writer(sink, INC_KEY)
    for name in ("first", "second", "third"):
        inc.step("tool_call", name)

    follow = writer(sink, f"cand_res_{'01J9ZC4K9B1C2D3E4F5G6H7J8K'}")
    follow.step("candidate_received", "followup")
    follow.rekey(INC_KEY)
    follow.step("decision", "confirm")

    assert [(s.step, s.name) for s in sink.query(INC_KEY)] == [
        (1, "first"),
        (2, "second"),
        (3, "third"),
        (4, "followup"),
        (5, "confirm"),
    ]


def test_rekey_to_same_key_is_noop(sink):
    w = writer(sink)
    w.step("candidate_received", "a")
    w.rekey(CAND_KEY)
    assert len(sink.query(CAND_KEY)) == 1


def test_rekey_to_invalid_key_rejected(sink):
    w = writer(sink)
    w.step("candidate_received", "a")
    with pytest.raises(ValidationError):
        w.rekey("not-a-key")


# truncation


def test_truncate_text_respects_byte_limit():
    text = "é" * 5000  # 2 bytes each
    out = truncate_text(text)
    assert len(out.encode()) <= MAX_FIELD_BYTES
    assert out.endswith("...[truncated]")
    assert truncate_text("short") == "short"


def test_lists_capped_at_ten():
    out = summarize({"swimmers": list(range(25))})
    assert out["swimmers"][:10] == list(range(10))
    assert out["swimmers"][10] == "...[15 more]"
    assert len(out["swimmers"]) == 11


def test_nested_lists_capped():
    out = summarize({"a": {"b": [[1] * 20]}})
    assert len(out["a"]["b"][0]) == 11


def test_large_field_replaced_with_preview():
    big = {f"k{i}": "x" * 300 for i in range(20)}
    out = summarize(big)
    assert out["_truncated"] is True
    assert out["_bytes"] > MAX_FIELD_BYTES
    assert len(json.dumps(out).encode()) <= MAX_FIELD_BYTES


def test_every_stored_field_under_2kb(sink):
    huge = {"frames": [{"pixels": "x" * 5000}] * 50}
    row = writer(sink).step(
        "tool_call",
        "zoom_and_recheck",
        input=huge,
        output_summary=huge,
        reasoning_summary="r" * 10_000,
        error="e" * 10_000,
    )
    for field in (row.input, row.output_summary, row.reasoning_summary, row.error):
        assert len(json.dumps(field).encode()) <= MAX_FIELD_BYTES + 2  # +2 for JSON quotes


def test_summarize_accepts_models_and_non_dicts():
    class Out(BaseModel):
        label: str
        ts: datetime

    assert summarize(Out(label="rip", ts=NOW)) == {"label": "rip", "ts": "2026-10-05T10:15:14Z"}
    assert summarize([1, 2]) == {"value": [1, 2]}
    assert summarize({"t": (1, 2)}) == {"t": [1, 2]}
    assert summarize(None) is None
