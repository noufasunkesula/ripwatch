"""rw-mcp-tools: the MCP server the agent calls tools through (sprint-1.md D-03).

Built on the official mcp SDK's MCPServer (FastMCP in mcp 1.x; Decision Log
2026-10-03), streamable HTTP on 127.0.0.1:8765.

No auth, on purpose: the server binds to loopback only and runs on the same
worker as rw-agent, the only client. Nothing off the instance can reach it, and
the worker security group has no inbound rules. Do not bind it to 0.0.0.0.

Every tool takes `trace_id` so its log lines join the agent trace. Anticipated
failures (unknown result or rip, bad arguments) are raised as ToolError so the
model reads the reason; anything else is a crash and the model sees only a
generic message.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from rw.contracts.base import CameraId, NonNegInt, ResultId, RipId, TraceId
from rw.mcp_tools.store import DetectionStore
from rw.mcp_tools.tools import flow, ocean, spread, swimmers
from rw.mcp_tools.tools import zoom as zoom_tool

HOST = "127.0.0.1"
PORT = 8765
SERVER_NAME = "rw-mcp-tools"

log = logging.getLogger("rw.mcp_tools")

INSTRUCTIONS = (
    "Tools for checking a possible rip current on a beach camera. Data tools only "
    "read; none of them alerts anyone. Pass the trace_id you were given to every call."
)


class UnavailableRechecker:
    """Placeholder until baseline vision's VisionPipeline.recheck (N-11) is wired in."""

    def recheck(self, crops: list[Any], result: Any) -> float:
        raise ToolError("recheck is not available yet (baseline vision not installed)")


@dataclass
class ToolDeps:
    store: DetectionStore
    s3: Any
    ssm: Any
    rechecker: zoom_tool.Rechecker = field(default_factory=UnavailableRechecker)
    ssm_prefix: str = "/rw"
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


def _doc(fn: Callable[..., Any]) -> str:
    return inspect.cleandoc(fn.__doc__ or "")


@contextmanager
def _call(tool: str, trace_id: str) -> Iterator[None]:
    """Log one tool call with its trace_id and latency; turn expected errors into ToolError."""
    start = time.perf_counter()
    status = "ok"
    try:
        yield
    except (LookupError, ValidationError, ValueError) as exc:
        status = "error"
        raise ToolError(str(exc)) from exc
    except Exception:
        status = "crash"
        raise
    finally:
        log.info(
            "tool_call",
            extra={
                "tool": tool,
                "trace_id": trace_id,
                "status": status,
                "latency_ms": round((time.perf_counter() - start) * 1000),
            },
        )


def build_server(deps: ToolDeps) -> MCPServer:
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)

    @server.tool(description=_doc(zoom_tool.zoom_and_recheck))
    def zoom_and_recheck(
        trace_id: TraceId,
        result_id: ResultId,
        camera_id: CameraId,
        rip_id: RipId | None = None,
        bbox_px: tuple[NonNegInt, NonNegInt, NonNegInt, NonNegInt] | None = None,
        zoom: Annotated[float, Field(ge=1.5, le=4.0)] = 2.0,
    ) -> zoom_tool.ZoomOutput:
        with _call("zoom_and_recheck", trace_id):
            args = zoom_tool.ZoomInput(
                trace_id=trace_id,
                result_id=result_id,
                camera_id=camera_id,
                rip_id=rip_id,
                bbox_px=bbox_px,
                zoom=zoom,
            )
            return zoom_tool.zoom_and_recheck(deps.store, deps.s3, deps.rechecker, args)

    @server.tool(description=_doc(flow.get_flow_stats))
    def get_flow_stats(
        trace_id: TraceId,
        camera_id: CameraId,
        window_s: Annotated[int, Field(ge=30, le=300)] = 120,
    ) -> flow.FlowStatsOutput:
        with _call("get_flow_stats", trace_id):
            args = flow.FlowStatsInput(trace_id=trace_id, camera_id=camera_id, window_s=window_s)
            return flow.get_flow_stats(deps.store, args, now=deps.now)

    @server.tool(description=_doc(swimmers.track_swimmers))
    def track_swimmers(
        trace_id: TraceId, result_id: ResultId, camera_id: CameraId
    ) -> swimmers.TrackSwimmersOutput:
        with _call("track_swimmers", trace_id):
            args = swimmers.TrackSwimmersInput(
                trace_id=trace_id, result_id=result_id, camera_id=camera_id
            )
            return swimmers.track_swimmers(deps.store, args)

    @server.tool(description=_doc(spread.predict_spread))
    def predict_spread(
        trace_id: TraceId,
        result_id: ResultId,
        camera_id: CameraId,
        rip_id: RipId,
        horizons_s: Annotated[
            tuple[Annotated[int, Field(ge=1, le=900)], ...], Field(min_length=1, max_length=5)
        ] = (60, 180, 300),
    ) -> spread.PredictSpreadOutput:
        with _call("predict_spread", trace_id):
            args = spread.PredictSpreadInput(
                trace_id=trace_id,
                result_id=result_id,
                camera_id=camera_id,
                rip_id=rip_id,
                horizons_s=list(horizons_s),
            )
            conditions = ocean.get_ocean_conditions(
                deps.ssm, ocean.OceanConditionsInput(trace_id=trace_id), deps.ssm_prefix, deps.now
            )
            return spread.predict_spread(deps.store, args, ocean_factor=conditions.ocean_factor)

    @server.tool(description=_doc(ocean.get_ocean_conditions))
    def get_ocean_conditions(trace_id: TraceId) -> ocean.OceanConditionsOutput:
        with _call("get_ocean_conditions", trace_id):
            args = ocean.OceanConditionsInput(trace_id=trace_id)
            return ocean.get_ocean_conditions(deps.ssm, args, deps.ssm_prefix, deps.now)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        tools = await server.list_tools()
        return JSONResponse({"ok": True, "service": SERVER_NAME, "tools": len(tools)})

    return server
