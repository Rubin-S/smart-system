"""Duplicate-complaint scoring.

Candidates outside 60 m or 45 minutes are not eligible. A remaining candidate
merges when its score is at least 0.55. Distinct reporters are counted from
the set of reporter hashes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MERGE_THRESHOLD = 0.55
WINDOW_M = 60.0
WINDOW_MIN = 45.0

_GROUPS = (
    frozenset({"double_parking", "illegal_parking", "no_parking_violation"}),
    frozenset({"blocked_entrance", "blocked_footpath"}),
)


def compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    return any(left in group and right in group for group in _GROUPS)


def merge_score(distance_m: float, dt_min: float, is_compatible: bool, img_sim: float = 0.0) -> float:
    """Blueprint score. ``img_sim`` is 0 when neither side has a photo hash."""
    similarity = 0.0 if img_sim is None else float(img_sim)
    return (
        0.40 * (1.0 - float(distance_m) / WINDOW_M)
        + 0.25 * (1.0 - float(dt_min) / WINDOW_MIN)
        + 0.20 * (1.0 if is_compatible else 0.0)
        + 0.15 * similarity
    )


def distinct_report_count(existing: set[str], new_reporter: str) -> int:
    return len(set(existing) | {new_reporter})


@dataclass
class Candidate:
    incident_id: int
    distance_m: float
    dt_min: float
    category: str
    reporter_hashes: set[str] = field(default_factory=set)
    report_count: int = 0


def select_merge(
    candidates: list[Candidate],
    category: str,
    img_sim: float = 0.0,
) -> tuple[Candidate | None, float | None]:
    """Pick the highest-scoring in-window candidate, or nothing under 0.55."""
    best: Candidate | None = None
    best_score: float | None = None
    for candidate in candidates:
        if candidate.distance_m > WINDOW_M or candidate.dt_min > WINDOW_MIN:
            continue
        score = merge_score(
            candidate.distance_m,
            candidate.dt_min,
            compatible(candidate.category, category),
            img_sim,
        )
        if best_score is None or score > best_score:
            best = candidate
            best_score = score
    if best is None or best_score is None or best_score < MERGE_THRESHOLD:
        return None, best_score
    return best, best_score
