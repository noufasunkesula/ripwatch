from __future__ import annotations

import json
import logging
from datetime import timedelta

import boto3
import pytest
from mcp import Client
from moto import mock_aws
from starlette.testclient import TestClient

from rw.contracts import VisionResult
from rw.mcp_tools.server import ToolDeps, build_server
from rw.mcp_tools.store import InMemoryDetectionStore
from tests.unit.mcp_tools.helpers import T0

pytestmark = pytest.mark.anyio

DATA_TOOLS = {
    "zoom_and_recheck",
    "get_flow_stats",
    "track_swimmers",
    "predict_spread",
    "get_ocean_conditions",
}
ACTION_TOOLS = {
    "create_incident",
    "set_watch",
    "alert_lifeguard",
    "request_approval",
    "request_followup_capture",
    "close_incident",
}
TOOLS = DATA_TOOLS | ACTION_TOOLS
TRACE = "tr_01J9ZC4M6Y2N8Q4T7V1B3K5D9F"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def result(vision_result) -> VisionResult:
    vision_result["rips"][0]["evidence"]["seaward_flow_px_per_s"] = 0.1
    return VisionResult.model_validate(vision_result)


@pytest.fixture
def deps(result):
    with mock_aws():
        yield ToolDeps(
            store=InMemoryDetectionStore([result]),
            s3=boto3.client("s3", region_name="us-east-1"),
            ssm=boto3.client("ssm", region_name="us-east-1"),
            now=lambda: T0 + timedelta(seconds=60),
        )


def _ids(result: VisionResult) -> dict:
    return {"trace_id": TRACE, "result_id": result.result_id, "camera_id": result.camera_id}


async def test_lists_the_eleven_tools(deps):
    async with Client(build_server(deps)) as client:
        tools = (await client.list_tools()).tools

    assert {t.name for t in tools} == TOOLS
    for tool in tools:
        assert len(tool.description) > 100, tool.name
        assert "trace_id" in tool.input_schema["required"], tool.name


async def test_track_swimmers_through_server(deps, result):
    async with Client(build_server(deps)) as client:
        out = await client.call_tool("track_swimmers", _ids(result))

    assert out.is_error is False
    assert out.structured_content["swimmers_at_risk"] == 1


async def test_unknown_result_is_a_readable_tool_error(deps, result):
    args = {**_ids(result), "result_id": "res_01J9ZC4K9B1C2D3E4F5G6H7J8K"}
    async with Client(build_server(deps)) as client:
        out = await client.call_tool("track_swimmers", args)

    assert out.is_error is True
    assert "not found" in out.content[0].text


async def test_action_tool_without_its_table_says_what_is_missing(deps):
    args = {
        "trace_id": TRACE,
        "incident_id": "inc_01J9ZC4M6Y2N8Q4T7V1B3K5D9F",
        "clips": 2,
        "reason": "glare",
    }
    async with Client(build_server(deps)) as client:
        out = await client.call_tool("set_watch", args)

    assert out.is_error is True
    assert "rw-incidents is not configured" in out.content[0].text


async def test_bad_trace_id_rejected(deps, result):
    async with Client(build_server(deps)) as client:
        out = await client.call_tool("track_swimmers", {**_ids(result), "trace_id": "nope"})

    assert out.is_error is True


async def test_predict_spread_uses_ocean_factor_from_ssm(deps, result):
    snapshot = {
        "fetched_at": "2026-10-05T10:00:00Z",
        "station": "8729108",
        "tides": [],
        "tide_trend": "falling",
        "alerts": [{"event": "Rip Current Statement", "headline": "x", "expires": None}],
        "errors": [],
    }
    deps.ssm.put_parameter(Name="/rw/ocean/latest", Value=json.dumps(snapshot), Type="String")

    async with Client(build_server(deps)) as client:
        out = await client.call_tool(
            "predict_spread", {**_ids(result), "rip_id": "cam-01-rip-0007"}
        )

    assert out.is_error is False
    assert out.structured_content["ocean_factor"] == 1.3


async def test_get_ocean_conditions_without_data(deps):
    async with Client(build_server(deps)) as client:
        out = await client.call_tool("get_ocean_conditions", {"trace_id": TRACE})

    assert out.structured_content["note"] == "no_data"


async def test_get_flow_stats_through_server(deps, result):
    async with Client(build_server(deps)) as client:
        out = await client.call_tool(
            "get_flow_stats", {"trace_id": TRACE, "camera_id": "cam-01", "window_s": 120}
        )

    assert out.structured_content["clips_seen"] == 1


async def test_zoom_without_keyframes(deps, result):
    async with Client(build_server(deps)) as client:
        out = await client.call_tool(
            "zoom_and_recheck", {**_ids(result), "rip_id": "cam-01-rip-0007", "zoom": 2.0}
        )

    assert out.structured_content["note"] == "no_keyframes"


async def test_zoom_needs_exactly_one_target(deps, result):
    async with Client(build_server(deps)) as client:
        out = await client.call_tool("zoom_and_recheck", _ids(result))

    assert out.is_error is True
    assert "exactly one" in out.content[0].text


async def test_tool_calls_are_logged_with_trace_id(deps, result, caplog):
    caplog.set_level(logging.INFO, logger="rw.mcp_tools")
    async with Client(build_server(deps)) as client:
        await client.call_tool("track_swimmers", _ids(result))

    (record,) = [r for r in caplog.records if r.getMessage() == "tool_call"]
    assert record.trace_id == TRACE
    assert record.tool == "track_swimmers"
    assert record.status == "ok"


def test_health_route(deps):
    app = build_server(deps).streamable_http_app()

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "rw-mcp-tools", "tools": len(TOOLS)}
