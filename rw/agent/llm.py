"""Model clients for the agent loop (sprint-1.md D-05). RW_LLM=bedrock|fake selects one.

Both speak the Bedrock Converse shapes: `converse(messages, system, tool_config)` returns the
Converse response dict (`output.message.content`, `stopReason`, `usage`).
"""

from __future__ import annotations

from typing import Any, Protocol

from rw.common.aws import client

MAX_TOKENS = 800
TEMPERATURE = 0.2


class LLMError(RuntimeError):
    """The model call failed (error, throttle, timeout, exhausted script)."""


class LLMClient(Protocol):
    model_id: str

    def converse(
        self, messages: list[dict], system: str, tool_config: dict[str, Any]
    ) -> dict[str, Any]: ...


class BedrockLLM:
    """Amazon Bedrock Converse with tool use. The client's read timeout is 20 s (rw.common.aws)."""

    def __init__(self, model_id: str, bedrock: Any = None) -> None:
        self.model_id = model_id
        self._bedrock = bedrock or client("bedrock-runtime")

    def converse(
        self, messages: list[dict], system: str, tool_config: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return self._bedrock.converse(
                modelId=self.model_id,
                messages=messages,
                system=[{"text": system}],
                toolConfig=tool_config,
                inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
            )
        except Exception as exc:  # botocore ClientError, ReadTimeout, connection errors
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc
