"""MCP tools as Bedrock tool specs, called through an MCP client (sprint-1.md D-05).

`McpTools` wraps `mcp.Client` connected to rw-mcp-tools: over streamable HTTP on the worker,
in-process (Client(server)) in tests and the local demo. `submit_decision` is the one local tool:
the model ends every run by calling it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from rw.contracts.decision import Action, DecisionKind, RiskLevel

SUBMIT_DECISION = "submit_decision"

SUBMIT_DECISION_SPEC = {
    "toolSpec": {
        "name": SUBMIT_DECISION,
        "description": "Finish: record your decision. Call exactly once, as your last step.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "enum": [d.value for d in DecisionKind]},
                    "risk_level": {"type": "string", "enum": [r.value for r in RiskLevel]},
                    "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                    "requested_action": {
                        "type": "string",
                        "enum": [a.value for a in Action],
                        "description": "Only with decision alert.",
                    },
                },
                "required": ["decision", "risk_level", "reasons"],
            }
        },
    }
}


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    data: dict[str, Any]
    text: str


def to_tool_spec(tool: Any) -> dict[str, Any]:
    """An MCP Tool as a Bedrock Converse toolSpec."""
    return {
        "toolSpec": {
            "name": tool.name,
            "description": (tool.description or tool.name)[:4000],
            "inputSchema": {"json": tool.input_schema},
        }
    }


class McpTools:
    def __init__(self, client: Any) -> None:
        self._client = client
        self._specs: list[dict[str, Any]] | None = None

    async def specs(self) -> list[dict[str, Any]]:
        """Fetched once, then cached for the life of the process."""
        if self._specs is None:
            tools = (await self._client.list_tools()).tools
            self._specs = [to_tool_spec(t) for t in tools]
        return self._specs

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        result = await self._client.call_tool(name, arguments)
        text = "\n".join(getattr(c, "text", "") for c in result.content or [])
        data = result.structured_content
        if not isinstance(data, dict):
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                data = {"text": text}
        return ToolOutcome(
            ok=not result.is_error, data=data if not result.is_error else {}, text=text
        )
