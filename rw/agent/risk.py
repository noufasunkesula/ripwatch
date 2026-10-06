"""Deterministic pre-risk (sprint-1.md section 9.5).

Given to the model as context and used by the fallback policy when the model fails. Pure
function of the VisionResult, the ocean alert flag and the thresholds, so it is easy to audit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from rw.contracts import VisionResult
from rw.contracts.decision import RiskLevel
from rw.contracts.vision import Mode, Status

ORDER = [RiskLevel.LOW, RiskLevel.ELEVATED, RiskLevel.HIGH, RiskLevel.CRITICAL]


@dataclass(frozen=True)
class RiskThresholds:
    high_confidence: float = 0.85
    near_rip_px: float = 30.0
    poor_glare: float = 0.3

    @classmethod
    def from_json(cls, text: str | None) -> RiskThresholds:
        """Parse /rw/risk/thresholds; `{}`, empty or missing keys mean the defaults."""
        data = json.loads(text) if text and text.strip() else {}
        known = {k: float(v) for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class Risk:
    level: RiskLevel
    reasons: list[str] = field(default_factory=list)


def _lower(level: RiskLevel) -> RiskLevel:
    return ORDER[max(0, ORDER.index(level) - 1)]


def assess(
    result: VisionResult, rip_statement_active: bool, thresholds: RiskThresholds | None = None
) -> Risk:
    t = thresholds or RiskThresholds()
    status = result.summary.status
    conf = result.summary.max_confidence
    in_rip = [s for s in result.swimmers if s.in_rip_id]
    near = [
        s
        for s in result.swimmers
        if s.in_rip_id
        or (s.distance_to_rip_px is not None and s.distance_to_rip_px <= t.near_rip_px)
    ]
    reasons: list[str] = []

    if status == Status.RIP and in_rip:
        level = RiskLevel.CRITICAL
        reasons.append(f"{len(in_rip)} swimmer(s) inside the rip")
    elif status == Status.RIP and (conf >= t.high_confidence or near or rip_statement_active):
        level = RiskLevel.HIGH
        if conf >= t.high_confidence:
            reasons.append(f"rip confidence {conf:.2f} >= {t.high_confidence}")
        if near:
            reasons.append(f"{len(near)} swimmer(s) within {t.near_rip_px:g} px of the rip")
        if rip_statement_active:
            reasons.append("NWS rip current statement active")
    elif status == Status.RIP or (status == Status.UNCERTAIN and (near or rip_statement_active)):
        level = RiskLevel.ELEVATED
        reasons.append(f"{status.value} at {conf:.2f}")
        if near:
            reasons.append(f"{len(near)} swimmer(s) near")
        if rip_statement_active:
            reasons.append("NWS rip current statement active")
    else:
        level = RiskLevel.LOW
        reasons.append(f"{status.value} at {conf:.2f}, nobody near")

    if result.mode == Mode.IMAGE and level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        level = RiskLevel.HIGH if in_rip else RiskLevel.ELEVATED
        reasons.append("image mode: no motion evidence")

    poor = result.quality.glare > t.poor_glare or result.quality.low_light
    if poor and level != RiskLevel.CRITICAL and level != RiskLevel.LOW:
        level = _lower(level)
        reasons.append("poor image quality (glare or low light)")
    return Risk(level, reasons)
