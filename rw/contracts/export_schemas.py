"""Write the JSON Schemas for every contract into docs/contracts/.

Usage:
    python -m rw.contracts.export_schemas            # write files
    python -m rw.contracts.export_schemas --check    # exit 1 if committed files differ
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from rw.contracts import (
    AgentDecision,
    Approval,
    CandidateMessage,
    Job,
    TraceStep,
    VisionResult,
)

SCHEMAS: dict[str, type[BaseModel]] = {
    "vision-result.schema.json": VisionResult,
    "candidate-message.schema.json": CandidateMessage,
    "agent-decision.schema.json": AgentDecision,
    "trace-step.schema.json": TraceStep,
    "approval.schema.json": Approval,
    "job.schema.json": Job,
}

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "docs" / "contracts"


def render(model: type[BaseModel]) -> str:
    schema = model.model_json_schema(mode="validation")
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="fail if files are out of date")
    args = parser.parse_args(argv)

    stale = []
    for filename, model in SCHEMAS.items():
        path = args.out / filename
        text = render(model)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(filename)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path}")

    if stale:
        print("schemas out of date, run python -m rw.contracts.export_schemas:", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
