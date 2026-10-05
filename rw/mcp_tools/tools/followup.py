"""request_followup_capture: ask the uploader for a clip or burst of the same spot (D-04).

Only for image and burst jobs: one photo cannot show water motion, so when the agent is unsure
it asks for more instead of guessing (north star 3). The request shows on the dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from rw.contracts.base import CameraId, JobId, TraceId
from rw.contracts.vision import Mode

FOLLOWUP_MODES = frozenset({Mode.IMAGE.value, Mode.BURST.value})


class JobNotFound(LookupError):
    pass


class FollowupInput(BaseModel):
    trace_id: TraceId
    camera_id: CameraId
    job_id: JobId
    message: Annotated[str, Field(min_length=1, max_length=300)]


class FollowupOutput(BaseModel):
    requested: Literal[True] = True
    job_id: str


def request_followup_capture(jobs: Any, args: FollowupInput, now: datetime) -> FollowupOutput:
    """Ask whoever uploaded a photo or burst for a short video of the same spot.

    Use in image or burst mode when the result is uncertain: say what to capture (for example
    "a 10 second clip of the gap between the breaking waves left of the pier"). Not allowed for
    video jobs, which already show motion.
    """
    job = jobs.get_item(Key={"job_id": args.job_id}).get("Item")
    if job is None or job.get("camera_id") != args.camera_id:
        raise JobNotFound(f"job {args.job_id} not found for camera {args.camera_id}")
    if job.get("mode") not in FOLLOWUP_MODES:
        raise ValueError(
            f"follow-up capture is only for image or burst jobs, not {job.get('mode')}"
        )
    stamp = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    jobs.update_item(
        Key={"job_id": args.job_id},
        UpdateExpression="SET followup_request = :f, updated_at = :u",
        ExpressionAttributeValues={
            ":f": {"message": args.message, "trace_id": args.trace_id, "requested_at": stamp},
            ":u": stamp,
        },
    )
    return FollowupOutput(job_id=args.job_id)
