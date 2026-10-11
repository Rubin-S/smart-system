"""Incident priority, 0–100.

P = 100 · (0.30·S + 0.20·E + 0.15·R + 0.15·C + 0.10·A + 0.10·Z)
"""

from __future__ import annotations

import math

_SEVERITY = {
    "blocked_emergency": 1.0,
    "blocked_entrance": 0.8,
    "double_parking": 0.7,
    "blocked_footpath": 0.6,
    "no_parking_violation": 0.5,
    "illegal_parking": 0.5,
    "spillover": 0.4,
    "overflow": 0.4,
    "other": 0.3,
}


def severity_factor(incident_type: str, blocks_emergency: bool = False) -> float:
    if blocks_emergency:
        return 1.0
    return _SEVERITY.get(incident_type, 0.3)


def reports_factor(n_reporters: int) -> float:
    if n_reporters <= 0:
        return 0.0
    return min(1.0, math.log2(1 + n_reporters) / math.log2(6))


def cctv_factor(cctv_status: str) -> float:
    return {"confirmed": 1.0, "possible": 0.6}.get(cctv_status, 0.0)


def age_factor(minutes_open: float) -> float:
    return min(1.0, max(0.0, float(minutes_open)) / 60.0)


def context_factor(zone_full_or_overflow: bool, peak_hour: bool) -> float:
    return 0.5 * (1.0 if zone_full_or_overflow else 0.0) + 0.5 * (1.0 if peak_hour else 0.0)


def priority_score(
    *,
    incident_type: str,
    blocks_emergency: bool = False,
    near_emergency: bool = False,
    n_reporters: int = 0,
    cctv_status: str = "none",
    minutes_open: float = 0.0,
    zone_full_or_overflow: bool = False,
    peak_hour: bool = False,
) -> float:
    severity = severity_factor(incident_type, blocks_emergency)
    emergency = 1.0 if near_emergency else 0.0
    reports = reports_factor(n_reporters)
    cctv = cctv_factor(cctv_status)
    age = age_factor(minutes_open)
    context = context_factor(zone_full_or_overflow, peak_hour)
    return 100.0 * (
        0.30 * severity
        + 0.20 * emergency
        + 0.15 * reports
        + 0.15 * cctv
        + 0.10 * age
        + 0.10 * context
    )
