"""python -m rw.eval --split val (eval.yml). Stub until Saif's evaluation lands.

TODO(Saif): run the configured Detector over RipVIS val, write metrics (AP, temporal confidence
aggregation as in the RipVIS paper) to rw-artifacts/eval/<run_id>/.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rw.eval")
    parser.add_argument("--split", choices=["val"], default="val")
    args = parser.parse_args(argv)
    print(f"rw.eval: evaluation on {args.split} is not implemented yet (Saif, Sprint 2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
