"""JSON line logging for every service (sprint-1.md N-04, north star 14.1).

Each line: `ts`, `level`, `service`, `logger`, `msg`, plus any `extra=` keys such as
`trace_id`, `incident_id`, `camera_id`, `result_id`, `job_id`, `mode`.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from rw.common.config import get_settings

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                line[key] = value
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str, ensure_ascii=False)


def setup_logging(service: str, stream: TextIO | None = None) -> logging.Logger:
    """Send all logging as JSON lines to stdout at RW_LOG_LEVEL. Safe to call again."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(get_settings().log_level)
    # boto and urllib3 are noisy at DEBUG and add nothing at INFO.
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(max(root.level, logging.WARNING))
    return logging.getLogger(service)
