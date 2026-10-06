"""Presigned S3 URLs for the dashboard (sprint-1.md D-07): media reads and direct uploads."""

from __future__ import annotations

import os
from typing import Any

GET_EXPIRY_S = 300
UPLOAD_EXPIRY_S = 600
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

# content type -> (file extension, job mode)
UPLOAD_TYPES = {
    "video/mp4": ("mp4", "video"),
    "video/quicktime": ("mov", "video"),
    "image/jpeg": ("jpg", "image"),
    "image/png": ("png", "image"),
    "application/zip": ("zip", "burst"),
}


def data_bucket() -> str:
    return os.environ["RW_DATA_BUCKET"]


def artifacts_bucket() -> str:
    return os.environ["RW_ARTIFACTS_BUCKET"]


def media_bucket(key: str) -> str | None:
    """Bucket a dashboard may read `key` from, or None when the key is not allowed."""
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        return None
    if key.startswith(("replay/", "incoming/")):
        return data_bucket()
    if key.startswith(("evidence/", "keyframes/")):
        return artifacts_bucket()
    return None


def split_s3_uri(uri: str) -> tuple[str, str]:
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def get_url(s3: Any, bucket: str, key: str) -> str:
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=GET_EXPIRY_S
    )


def upload_post(s3: Any, key: str, content_type: str, size_bytes: int) -> dict[str, Any]:
    """Presigned POST that only accepts this key, this exact Content-Type and at most size_bytes."""
    return s3.generate_presigned_post(
        Bucket=data_bucket(),
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, size_bytes]],
        ExpiresIn=UPLOAD_EXPIRY_S,
    )
