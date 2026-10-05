"""OpenCV runtime check (sprint-1.md N-04, north star 0 and 17).

On the COOL worker (`RW_RUNTIME=cool`) the process must use COOL's cv2 under /opt/cool and no
pip OpenCV may be installed. Anything else exits with code 78 (EX_CONFIG) so systemd and the
deploy script stop instead of silently benchmarking the wrong OpenCV.
"""

from __future__ import annotations

import importlib
import logging
import sys
from importlib import metadata
from pathlib import PurePosixPath
from typing import Any

from rw.common.config import get_settings

log = logging.getLogger(__name__)

EX_CONFIG = 78
COOL_ROOT = PurePosixPath("/opt/cool")


def _opencv_distributions() -> list[str]:
    names = {(d.metadata["Name"] or "").lower() for d in metadata.distributions()}
    return sorted(n for n in names if n.startswith("opencv-python"))


def _fail(reason: str) -> None:
    log.error("cv2 runtime check failed: %s", reason, extra={"exit_code": EX_CONFIG})
    sys.exit(EX_CONFIG)


def check_cv2_runtime(instance_type: str = "local") -> dict[str, Any]:
    """Log and verify the cv2 in use. Returns the fields for `VisionResult.runtime`.

    `variant` is RW_RUNTIME (None when unset, e.g. on a laptop). Exits 78 on COOL misconfig.
    """
    variant = get_settings().runtime
    try:
        cv2 = importlib.import_module("cv2")
    except ImportError as exc:
        if variant == "cool":
            _fail(f"cv2 not importable: {exc}")
        raise

    path = str(getattr(cv2, "__file__", "") or "")
    info = {
        "variant": variant,
        "opencv_version": str(cv2.__version__),
        "cv2_path": path,
        "instance_type": instance_type,
    }
    log.info("cv2 runtime", extra={"runtime": info})

    if variant == "cool":
        posix = PurePosixPath(path.replace("\\", "/"))
        if not posix.is_relative_to(COOL_ROOT):
            _fail(f"cv2 loaded from {path}, expected under {COOL_ROOT}")
        pip_opencv = _opencv_distributions()
        if pip_opencv:
            _fail(f"pip OpenCV installed on COOL: {', '.join(pip_opencv)}")
    return info
