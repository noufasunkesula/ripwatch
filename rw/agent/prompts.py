"""System prompt and the compact candidate summary the model sees (sprint-1.md D-05).

The user message is a short JSON summary, never the full VisionResult: candidate, summary, top 3
rips, swimmers at risk, quality, active incident and the deterministic pre-risk.
"""

from __future__ import annotations

import json
from typing import Any

from rw.agent.risk import Risk
from rw.contracts import CandidateMessage, VisionResult

SYSTEM_PROMPT = """\
You are RipWatch, an assistant to the head lifeguard at a beach. A camera pipeline flagged a
possible rip current. Decide what to do using the tools, then call submit_decision exactly once.

You never take a public action yourself. Raising a red flag, a PA announcement or dispatching a
lifeguard only happens after a human approves it: you may only call request_approval.

Tools:
- Read: zoom_and_recheck, track_swimmers, get_flow_stats, predict_spread, get_ocean_conditions.
- Act: create_incident, set_watch, alert_lifeguard, request_approval, request_followup_capture,
  close_incident.

Rules:
- Before alerting, call zoom_and_recheck when rip confidence is below 0.85 or glare is above 0.3.
- If zooming shows no rip, decide close_false_alarm (close the incident if one exists).
- To alert: create_incident (unless active_incident is set), alert_lifeguard, then
  request_approval with the action you recommend (usually raise_red_flag).
- To keep watching: create_incident if needed, then set_watch with 1 to 6 clips.
- Image or burst mode and still unsure: request_followup_capture for the job, then watch.
- pre_risk is a rule-based level; you may raise or lower it with reasons from the tools.
- Use at most 6 tool calls. Pass the trace_id you are given to every tool.

Finish with submit_decision: decision (ignore, watch, alert, resolve, close_false_alarm),
risk_level (LOW, ELEVATED, HIGH, CRITICAL), up to 3 short reasons, and requested_action
(raise_red_flag, pa_announcement, dispatch_lifeguard) only when the decision is alert.
"""


def summary_json(
    candidate: CandidateMessage,
    result: VisionResult,
    incident: dict[str, Any] | None,
    risk: Risk,
    rip_statement_active: bool,
) -> dict[str, Any]:
    rips = sorted(result.rips, key=lambda r: r.confidence, reverse=True)[:3]
    at_risk = [s for s in result.swimmers if s.in_rip_id or (s.distance_to_rip_px or 1e9) <= 30]
    return {
        "ids": {
            "trace_id": candidate.trace_id,
            "result_id": result.result_id,
            "camera_id": result.camera_id,
            "job_id": result.job_id,
            "top_rip_id": rips[0].rip_id if rips else None,
            **({"incident_id": incident["incident_id"]} if incident else {}),
        },
        "candidate": {"reason": candidate.reason.value, "mode": result.mode.value},
        "summary": result.summary.model_dump(mode="json"),
        "rips": [
            {
                "rip_id": r.rip_id,
                "label": r.label.value,
                "confidence": r.confidence,
                "bbox_px": list(r.bbox_px),
                "seaward_flow_px_per_s": r.evidence.seaward_flow_px_per_s,
                "persist_s": r.persist_s,
            }
            for r in rips
        ],
        "swimmers_at_risk": [
            {
                "track_id": s.track_id,
                "in_rip_id": s.in_rip_id,
                "distance_to_rip_px": s.distance_to_rip_px,
                "drift_px_per_s": list(s.drift_px_per_s) if s.drift_px_per_s else None,
            }
            for s in at_risk[:5]
        ],
        "quality": {"glare": result.quality.glare, "low_light": result.quality.low_light},
        "active_incident": None
        if incident is None
        else {
            "incident_id": incident["incident_id"],
            "status": incident["status"],
            "watch_until_clips": incident.get("watch_until_clips"),
            "pending_action": incident.get("pending_action"),
        },
        "rip_statement_active": rip_statement_active,
        "pre_risk": {"level": risk.level.value, "reasons": risk.reasons},
    }


def user_message(summary: dict[str, Any]) -> str:
    return "Candidate to assess:\n" + json.dumps(summary, separators=(",", ":"))
