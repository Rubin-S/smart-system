"""Busy-hours insight from 15-minute occupancy slots.

A weekday run of slots at or above 90% occupancy lasting at least one hour
becomes a line such as "Usually full 6–9 PM".
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SLOT_MINUTES = 15


def _clock(minutes: int) -> tuple[str, str]:
    minutes = minutes % (24 * 60)
    hour24, minute = divmod(minutes, 60)
    suffix = "AM" if hour24 < 12 else "PM"
    hour12 = hour24 % 12 or 12
    label = f"{hour12}" if minute == 0 else f"{hour12}:{minute:02d}"
    return label, suffix


def _format_run(start_min: int, end_min: int) -> str:
    start_label, start_suffix = _clock(start_min)
    end_label, end_suffix = _clock(end_min)
    if start_suffix == end_suffix:
        return f"Usually full {start_label}–{end_label} {end_suffix}"
    return f"Usually full {start_label} {start_suffix}–{end_label} {end_suffix}"


def busy_insight(
    slots: list[tuple[int, float]],
    threshold: float = 0.9,
    min_minutes: int = 60,
    slot_minutes: int = SLOT_MINUTES,
) -> str | None:
    """``slots`` are ``(minute_of_day, mean_ratio)`` for one weekday."""
    if not slots:
        return None
    ordered = sorted(slots, key=lambda item: item[0])
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    previous: int | None = None
    for minute, ratio in ordered:
        high = ratio >= threshold
        continuous = previous is not None and minute == previous + slot_minutes
        if high and run_start is not None and continuous:
            previous = minute
            continue
        if run_start is not None and previous is not None:
            runs.append((run_start, previous))
        if high:
            run_start = minute
            previous = minute
        else:
            run_start = None
            previous = None
    if run_start is not None and previous is not None:
        runs.append((run_start, previous))

    best: tuple[int, int] | None = None
    best_duration = -1
    for start, last in runs:
        end = last + slot_minutes
        duration = end - start
        if duration >= min_minutes and duration > best_duration:
            best = (start, end)
            best_duration = duration
    if best is None:
        return None
    return _format_run(best[0], best[1])


def slots_from_snapshots(
    rows: list[tuple[datetime, float, float]],
    weekday: int,
    tz: ZoneInfo = IST,
) -> list[tuple[int, float]]:
    """Average occupied/capacity into 15-minute slots for ``weekday`` (Monday=0)."""
    buckets: dict[int, list[float]] = {}
    for ts, occupied, capacity in rows:
        if capacity <= 0:
            continue
        local = ts.astimezone(tz)
        if local.weekday() != weekday:
            continue
        minute = local.hour * 60 + (local.minute // SLOT_MINUTES) * SLOT_MINUTES
        buckets.setdefault(minute, []).append(float(occupied) / float(capacity))
    return [(minute, sum(values) / len(values)) for minute, values in sorted(buckets.items())]
