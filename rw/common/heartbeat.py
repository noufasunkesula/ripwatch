"""Service heartbeats: `<RW_HEARTBEAT_DIR>/<service>.heartbeat` holds the last beat's epoch."""

from __future__ import annotations

import os
import time
from pathlib import Path

from rw.common.config import get_settings


def heartbeat_path(service: str, directory: str | Path | None = None) -> Path:
    base = Path(directory) if directory is not None else Path(get_settings().heartbeat_dir)
    return base / f"{service}.heartbeat"


def beat(service: str, directory: str | Path | None = None) -> Path:
    """Write the current epoch seconds for `service`, atomically. Returns the file path."""
    path = heartbeat_path(service, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(f"{time.time():.3f}\n", encoding="ascii")
    os.replace(tmp, path)
    return path


def last_beat(service: str, directory: str | Path | None = None) -> float | None:
    """Epoch of the last beat, or None if the service never beat."""
    path = heartbeat_path(service, directory)
    try:
        return float(path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        return None
