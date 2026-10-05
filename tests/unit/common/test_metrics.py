import json

import pytest

from rw.common import metrics
from rw.common.metrics import Recorded, UnknownMetricError, configure, emit, recorded, timed


def test_memory_sink_records_metrics(env):
    env()
    assert configure("rw-agent") == "memory"
    emit("IncidentsCreated", 1)
    emit("AgentDecisionLatencyMs", 5400, "Milliseconds")
    assert recorded() == [
        Recorded("IncidentsCreated", 1.0, "Count", {}),
        Recorded("AgentDecisionLatencyMs", 5400.0, "Milliseconds", {}),
    ]


def test_memory_sink_rejects_names_and_units_outside_the_north_star(env):
    env()
    with pytest.raises(UnknownMetricError, match="'MadeUpMetric'"):
        emit("MadeUpMetric", 1)
    with pytest.raises(UnknownMetricError, match="'Bytes'"):
        emit("JobsProcessed", 1, "Bytes")
    assert recorded() == []


def test_timed_as_context_manager_and_decorator(env):
    env(RW_RUNTIME="std-arm")
    configure("rw-ingest", "memory")

    with timed("flow"):
        pass

    @timed("decode")
    def decode() -> str:
        return "frames"

    assert decode() == "frames"
    flow, dec = recorded()
    assert flow.name == "FrameLatencyMs" and flow.unit == "Milliseconds"
    assert flow.dimensions == {"stage": "flow", "runtime": "std-arm"}
    assert dec.dimensions["stage"] == "decode"
    assert flow.value >= 0


def test_timed_emits_even_when_the_block_raises(env):
    env()
    with pytest.raises(ValueError), timed("detect"):
        raise ValueError("bad frame")
    assert recorded()[0].dimensions == {"stage": "detect", "runtime": "local"}


def test_sink_detection(env, monkeypatch):
    env()
    assert configure("x") == "memory"
    env(RW_RUNTIME="cool")
    assert configure("x") == "agent"
    env(AWS_LAMBDA_FUNCTION_NAME="rw-api")
    assert configure("x") == "stdout"


def test_stdout_sink_prints_emf(env, capsys):
    env()
    configure("rw-api", "stdout")
    emit("ApprovalLatencySec", 42, "Seconds")
    emit("JobsProcessed", 1, dimensions={"mode": "image"})
    first, second = (json.loads(line) for line in capsys.readouterr().out.splitlines())

    cw = first["_aws"]["CloudWatchMetrics"][0]
    assert cw["Namespace"] == "RipWatch"
    assert cw["Metrics"] == [{"Name": "ApprovalLatencySec", "Unit": "Seconds"}]
    assert cw["Dimensions"] == [[]]
    assert first["ApprovalLatencySec"] == 42
    assert second["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["mode"]]
    assert second["mode"] == "image"


def test_non_memory_sinks_drop_unknown_metrics_with_a_warning(env, capsys, caplog):
    env()
    configure("rw-api", "stdout")
    emit("MadeUpMetric", 1)
    assert capsys.readouterr().out == ""
    assert "dropped metric" in caplog.text


class _FakeAgent:
    def __init__(self, error: Exception | None = None) -> None:
        self.contexts = []
        self.error = error

    def accept(self, ctx) -> None:
        if self.error:
            raise self.error
        self.contexts.append(ctx)


def test_agent_sink_sends_context(env, monkeypatch):
    env()
    agent = _FakeAgent()
    monkeypatch.setattr(metrics, "_agent_sink", lambda: agent)
    configure("rw-vision", "agent")
    emit("FramesPerSecond", 4.8, "Count/Second")
    (ctx,) = agent.contexts
    assert ctx.namespace == "RipWatch"
    assert ctx.metrics["FramesPerSecond"].values == [4.8]


def test_agent_sink_errors_never_break_the_caller(env, monkeypatch, caplog):
    env()
    monkeypatch.setattr(metrics, "_agent_sink", lambda: _FakeAgent(ConnectionRefusedError()))
    configure("rw-vision", "agent")
    emit("FramesPerSecond", 4.8, "Count/Second")
    assert "not sent" in caplog.text


def test_agent_sink_uses_the_service_log_group(env):
    env()
    configure("rw-ingest", "agent")
    assert metrics._agent_sink().log_group_name == "/rw/worker/rw-ingest"
