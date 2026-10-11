"""Zone occupancy status, hysteresis, overflow, and closing-soon.

No database or network access. Camera ingest and the demo controls both call
``derive_status``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

STALE_AFTER = timedelta(seconds=90)
CLOSING_SOON_WINDOW = timedelta(minutes=30)


def effective_capacity(capacity_car: int, capacity_2w: int, pcu_weight_2w: float) -> int:
    """Car slots plus two-wheelers converted to passenger-car units."""
    return int(capacity_car) + math.floor(float(capacity_2w) * float(pcu_weight_2w))


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def _occupancy_status(ratio: float, free: float, previous: str) -> str:
    """Hysteresis around the blueprint thresholds.

    Enter almost_full at ratio >= 0.80 and leave it only below 0.75.
    Enter full at ratio >= 1.00 (or free <= 0) and leave it only below 0.95.
    """

    def enter(r: float, slots_free: float) -> str:
        if r >= 1.0 or slots_free <= 0:
            return "full"
        if r >= 0.80:
            return "almost_full"
        return "available"

    if previous == "full":
        if ratio >= 0.95:
            return "full"
        if ratio >= 0.75:
            return "almost_full"
        return "available"
    if previous == "almost_full":
        if ratio >= 1.0 or free <= 0:
            return "full"
        if ratio < 0.75:
            return "available"
        return "almost_full"
    return enter(ratio, free)


@dataclass
class StatusInput:
    readings: list[float]
    capacity_car: int
    capacity_2w: int = 0
    pcu_weight_2w: float = 0.25
    previous_status: str = "unknown"
    occ_updated_at: datetime | None = None
    now: datetime | None = None
    manual_override: str | None = None
    override_until: datetime | None = None
    schedule_closed: bool = False


@dataclass
class StatusResult:
    status: str
    occupied: float
    latest: float
    capacity: int
    free: float
    overflow: bool
    ratio: float
    smoothed: float


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def derive_status(inp: StatusInput) -> StatusResult:
    now = _aware(inp.now) or datetime.now(timezone.utc)
    updated = _aware(inp.occ_updated_at)
    capacity = effective_capacity(inp.capacity_car, inp.capacity_2w, inp.pcu_weight_2w)
    readings = [float(v) for v in inp.readings]
    smoothed = _median(readings[-5:]) if readings else 0.0
    latest = readings[-1] if readings else 0.0
    ratio = (smoothed / capacity) if capacity > 0 else 0.0
    free = max(0.0, capacity - smoothed) if capacity > 0 else 0.0
    overflow = bool(capacity > 0 and smoothed > (1.10 * capacity))
    stale = updated is None or (now - updated) > STALE_AFTER

    override = inp.manual_override
    until = _aware(inp.override_until)
    override_active = override in ("closed", "restricted") and (until is None or now < until)

    if override_active:
        status = override or "unknown"
    elif inp.schedule_closed:
        status = "closed"
    elif stale:
        status = "unknown"
    elif capacity <= 0:
        status = "restricted"
    else:
        status = _occupancy_status(ratio, free, inp.previous_status)

    return StatusResult(
        status=status,
        occupied=smoothed,
        latest=latest,
        capacity=capacity,
        free=free,
        overflow=overflow,
        ratio=ratio,
        smoothed=smoothed,
    )


def closing_soon(now: datetime, starts_at: datetime | None) -> tuple[bool, int | None]:
    """True when a closure starts in the next 30 minutes (not already started)."""
    if starts_at is None:
        return False, None
    now_a = _aware(now) or datetime.now(timezone.utc)
    start_a = _aware(starts_at)
    assert start_a is not None
    delta = (start_a - now_a).total_seconds()
    if 0 < delta <= CLOSING_SOON_WINDOW.total_seconds():
        return True, int(math.ceil(delta / 60.0))
    return False, None
