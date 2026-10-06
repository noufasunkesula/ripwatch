"""Scripted stand-in for Bedrock (sprint-1.md D-05). Deterministic tests and `make local-demo`.

A script is a YAML file in tests/fixtures/llm_scripts/<scenario>.yaml:

    turns:
      - tool_use:
          - name: zoom_and_recheck
            input: {trace_id: "{trace_id}", result_id: "{result_id}", camera_id: "{camera_id}",
                    rip_id: "{top_rip_id}"}
      - raise: ThrottlingException          # the model call fails
      - repeat: 7                           # the next turn, 7 times
        tool_use: [...]
      - tool_use:
          - name: submit_decision
            input: {decision: alert, risk_level: HIGH, reasons: [...],
                    requested_action: raise_red_flag}

"{name}" placeholders are filled from the JSON the agent put in the first user message (trace_id,
result_id, camera_id, job_id, top_rip_id, incident_id) and from every tool result so far (for
example incident_id from create_incident). A string that is exactly "{name}" keeps the value's type.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from rw.agent.llm import LLMError

SCRIPTS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "llm_scripts"
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
FAKE_USAGE = {"inputTokens": 120, "outputTokens": 30}


def _context(messages: list[dict]) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    for message in messages:
        for block in message.get("content", []):
            if "text" in block and message["role"] == "user" and "{" in block["text"]:
                text = block["text"]
                try:
                    data = json.loads(text[text.index("{") :])
                except ValueError:
                    continue
                ctx.update(data.get("ids", {}))
            if "toolResult" in block:
                for part in block["toolResult"].get("content", []):
                    if isinstance(part.get("json"), dict):
                        ctx.update({k: v for k, v in part["json"].items() if v is not None})
    return ctx


def _fill(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole and whole.group(1) in ctx:
            return ctx[whole.group(1)]
        return _PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: _fill(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ctx) for v in value]
    return value


class FakeLLM:
    model_id = "fake-llm"

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns: list[dict[str, Any]] = []
        for turn in turns:
            self.turns += [turn] * int(turn.get("repeat", 1))
        self.calls = 0

    @classmethod
    def scenario(cls, name: str, folder: Path = SCRIPTS) -> FakeLLM:
        data = yaml.safe_load((folder / f"{name}.yaml").read_text(encoding="utf-8"))
        return cls(data["turns"])

    def converse(
        self, messages: list[dict], system: str, tool_config: dict[str, Any]
    ) -> dict[str, Any]:
        if self.calls >= len(self.turns):
            raise LLMError("fake script exhausted")
        turn = self.turns[self.calls]
        self.calls += 1
        if "raise" in turn:
            raise LLMError(str(turn["raise"]))
        ctx = _context(messages)
        content: list[dict[str, Any]] = []
        if turn.get("text"):
            content.append({"text": _fill(turn["text"], ctx)})
        for i, use in enumerate(turn.get("tool_use", [])):
            content.append(
                {
                    "toolUse": {
                        "toolUseId": f"fake-{self.calls}-{i}",
                        "name": use["name"],
                        "input": _fill(use.get("input", {}), ctx),
                    }
                }
            )
        stop = "tool_use" if any("toolUse" in c for c in content) else "end_turn"
        return {
            "output": {"message": {"role": "assistant", "content": content}},
            "stopReason": stop,
            "usage": dict(FAKE_USAGE),
        }
