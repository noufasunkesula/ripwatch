"""Agent trace writer (sprint-1.md D-02).

Every agent action becomes one TraceStep row under a trace_key:
  - cand_<result_id> while the candidate has no incident
  - inc_<...> once it has one; rekey() moves the earlier steps over so one
    incident has one complete trace.

Storage goes through a TraceSink: InMemoryTraceSink for tests, DynamoTraceSink for
rw-agent-trace (the full step is kept as JSON in `row`, top-level fields stay queryable).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel

from rw.contracts.trace import StepType, TraceStep

MAX_FIELD_BYTES = 2048
MAX_LIST_ITEMS = 10
_ELLIPSIS = "...[truncated]"


class TraceSink(Protocol):
    def put(self, step: TraceStep) -> None: ...

    def query(self, trace_key: str) -> list[TraceStep]:
        """All steps for a trace_key, ordered by step."""
        ...


class InMemoryTraceSink:
    def __init__(self) -> None:
        self.rows: dict[str, dict[int, TraceStep]] = {}

    def put(self, step: TraceStep) -> None:
        self.rows.setdefault(step.trace_key, {})[step.step] = step

    def query(self, trace_key: str) -> list[TraceStep]:
        steps = self.rows.get(trace_key, {})
        return [steps[n] for n in sorted(steps)]


class DynamoTraceSink:
    """rw-agent-trace rows: trace_key (hash) + step (range); the step stored as JSON."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def put(self, step: TraceStep) -> None:
        item = json.loads(step.model_dump_json(), parse_float=Decimal)  # DynamoDB has no float
        self.table.put_item(Item={**item, "trace_key": step.trace_key, "step": step.step,
                                  "row": step.model_dump_json()})  # fmt: skip

    def query(self, trace_key: str) -> list[TraceStep]:
        from boto3.dynamodb.conditions import Key

        rows, kwargs = [], {"KeyConditionExpression": Key("trace_key").eq(trace_key)}
        while True:
            page = self.table.query(**kwargs)
            rows += [TraceStep.model_validate_json(i["row"]) for i in page.get("Items", [])]
            if "LastEvaluatedKey" not in page:
                return sorted(rows, key=lambda r: r.step)
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _to_json(value: Any) -> Any:
    """Normalize to plain JSON types (models, datetimes, tuples become JSON-safe)."""
    return json.loads(json.dumps(value, default=_json_default))


def _byte_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())


def truncate_text(text: str, max_bytes: int = MAX_FIELD_BYTES) -> str:
    """Cut a string to at most max_bytes of UTF-8, marking that it was cut."""
    raw = text.encode()
    if len(raw) <= max_bytes:
        return text
    keep = max_bytes - len(_ELLIPSIS.encode())
    return raw[:keep].decode(errors="ignore") + _ELLIPSIS


def _cap(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _cap(v) for k, v in value.items()}
    if isinstance(value, list):
        capped = [_cap(v) for v in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            capped.append(f"...[{len(value) - MAX_LIST_ITEMS} more]")
        return capped
    if isinstance(value, str):
        return truncate_text(value)
    return value


def summarize(value: Any) -> dict[str, Any] | None:
    """Make a dict field safe to store: lists capped at 10, whole field at most 2 KB.

    If the capped dict is still over 2 KB, it is replaced by a marker with a
    preview, so full tool outputs never reach DynamoDB.
    """
    if value is None:
        return None
    data = _to_json(value)
    if not isinstance(data, dict):
        data = {"value": data}
    data = _cap(data)
    size = _byte_len(data)
    if size <= MAX_FIELD_BYTES:
        return data
    preview = json.dumps(data, ensure_ascii=False)
    return {
        "_truncated": True,
        "_bytes": size,
        "preview": truncate_text(preview, MAX_FIELD_BYTES - 128),
    }


class TraceWriter:
    """Writes numbered TraceStep rows for one trace."""

    def __init__(
        self,
        trace_key: str,
        trace_id: str,
        sink: TraceSink,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.trace_key = trace_key
        self.trace_id = trace_id
        self._sink = sink
        self._clock = clock
        existing = sink.query(trace_key)
        self._next = existing[-1].step + 1 if existing else 1

    def step(
        self,
        type: StepType | str,
        name: str,
        input: Any = None,
        output_summary: Any = None,
        reasoning_summary: str | None = None,
        latency_ms: float = 0,
        error: str | BaseException | None = None,
    ) -> TraceStep:
        """Record one step and return the stored row."""
        if isinstance(error, BaseException):
            error = _format_error(error)
        row = TraceStep(
            trace_key=self.trace_key,
            step=self._next,
            trace_id=self.trace_id,
            type=StepType(type),
            name=name,
            input=summarize(input) or {},
            output_summary=summarize(output_summary),
            reasoning_summary=truncate_text(reasoning_summary) if reasoning_summary else None,
            latency_ms=max(0, round(latency_ms)),
            error=truncate_text(error) if error else None,
            created_at=self._clock(),
        )
        self._sink.put(row)
        self._next += 1
        return row

    def rekey(self, new_trace_key: str) -> None:
        """Copy this trace's steps to new_trace_key and keep writing there.

        Used when a candidate becomes (or joins) an incident. Steps are appended
        after any steps the incident already has, so the incident trace stays
        one ordered sequence. The old rows are left in place.
        """
        if new_trace_key == self.trace_key:
            return
        old = self._sink.query(self.trace_key)
        target = self._sink.query(new_trace_key)
        offset = target[-1].step if target else 0
        for i, row in enumerate(old, start=1):
            moved = row.model_dump() | {"trace_key": new_trace_key, "step": offset + i}
            self._sink.put(TraceStep.model_validate(moved))
        self.trace_key = new_trace_key
        self._next = offset + len(old) + 1


def _format_error(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"
