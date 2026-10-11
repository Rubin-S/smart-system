"""Rules tests import the shipped functions. They do not reimplement them."""

from datetime import datetime, timedelta, timezone

import pytest

from rules.busy import busy_insight
from rules.dwell import DwellTracker, TrackObs
from rules.merge import Candidate, distinct_report_count, merge_score, select_merge
from rules.priority import (
    age_factor,
    cctv_factor,
    context_factor,
    priority_score,
    reports_factor,
    severity_factor,
)
from rules.status import StatusInput, closing_soon, derive_status, effective_capacity

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _status(readings, capacity=100, previous="available", age_s=0, override=None, until=None, schedule=False, capacity_2w=0):
    updated = None if age_s is None else NOW - timedelta(seconds=age_s)
    return derive_status(
        StatusInput(
            readings=readings,
            capacity_car=capacity,
            capacity_2w=capacity_2w,
            previous_status=previous,
            occ_updated_at=updated,
            now=NOW,
            manual_override=override,
            override_until=until,
            schedule_closed=schedule,
        )
    )


def test_effective_capacity_includes_two_wheelers():
    assert effective_capacity(20, 30, 0.25) == 27


def test_hysteresis_almost_full_and_full():
    assert _status([79]).status == "available"
    assert _status([80]).status == "almost_full"
    held = _status([76], previous="almost_full")
    assert held.status == "almost_full"
    assert _status([74], previous="almost_full").status == "available"
    assert _status([100]).status == "full"
    assert _status([96], previous="full").status == "full"
    assert _status([94], previous="full").status == "almost_full"
    assert _status([74], previous="full").status == "available"


def test_overflow_when_smoothed_exceeds_110_percent():
    assert _status([110]).overflow is False
    over = _status([111])
    assert over.overflow is True
    assert over.status == "full"


def test_stale_occupancy_is_unknown():
    assert _status([10], capacity=20, age_s=90).status == "available"
    assert _status([10], capacity=20, age_s=91).status == "unknown"
    assert _status([10], capacity=20, age_s=None).status == "unknown"


def test_officer_override_beats_derived_status_until_it_expires():
    closed = _status([10], capacity=20, override="closed")
    assert closed.status == "closed"
    restricted = _status([10], capacity=20, override="restricted")
    assert restricted.status == "restricted"
    expired = _status([10], capacity=20, override="closed", until=NOW - timedelta(minutes=1))
    assert expired.status == "available"
    assert _status([10], capacity=20, schedule=True).status == "closed"
    # A closure override still wins when the count itself is stale.
    assert _status([10], capacity=20, age_s=120, override="closed").status == "closed"


def test_payload_numbers_follow_capacity_and_count():
    result = _status([16], capacity=20, previous="unknown")
    assert result.status == "almost_full"
    assert result.capacity == 20
    assert result.occupied == 16
    assert result.free == 4


def test_closing_soon_is_the_next_thirty_minutes():
    flag, minutes = closing_soon(NOW, NOW + timedelta(minutes=20))
    assert flag is True
    assert minutes == 20
    assert closing_soon(NOW, NOW + timedelta(minutes=30))[0] is True
    assert closing_soon(NOW, NOW + timedelta(minutes=31))[0] is False
    assert closing_soon(NOW, NOW - timedelta(seconds=1))[0] is False
    assert closing_soon(NOW, None) == (False, None)


def test_parked_track_flags_at_the_grace_instant_only_once():
    tracker = DwellTracker(grace_s=10, lost_tolerance_s=15, move_tol_px=25, zone="Market Street")
    assert tracker.update([TrackObs(1, (10, 10))], 0) == []
    assert tracker.update([TrackObs(1, (10, 10))], 9.9) == []
    alerts = tracker.update([TrackObs(1, (12, 11))], 10)
    assert len(alerts) == 1
    assert alerts[0]["track_id"] == 1
    assert alerts[0]["status"] == "PENDING_OFFICER_REVIEW"
    assert alerts[0]["dwell_s"] == 10
    assert tracker.update([TrackObs(1, (12, 11))], 12) == []


def test_passing_vehicle_is_not_flagged():
    tracker = DwellTracker(grace_s=10, lost_tolerance_s=15, move_tol_px=25)
    assert tracker.update([TrackObs(7, (0, 0))], 0) == []
    assert tracker.update([TrackObs(7, (80, 0))], 4) == []
    assert tracker.update([TrackObs(7, (160, 0))], 8) == []
    assert tracker.update([TrackObs(7, (240, 0))], 12) == []


def test_short_occlusion_keeps_the_dwell_timer():
    tracker = DwellTracker(grace_s=10, lost_tolerance_s=15, move_tol_px=25)
    tracker.update([TrackObs(3, (50, 50))], 0)
    tracker.update([], 3)
    alerts = tracker.update([TrackObs(3, (50, 50))], 10)
    assert len(alerts) == 1
    assert alerts[0]["dwell_s"] == 10


def test_gap_past_lost_tolerance_restarts_the_timer():
    tracker = DwellTracker(grace_s=10, lost_tolerance_s=15, move_tol_px=25)
    tracker.update([TrackObs(3, (50, 50))], 0)
    tracker.update([], 20)
    assert tracker.update([TrackObs(3, (50, 50))], 21) == []
    alerts = tracker.update([TrackObs(3, (50, 50))], 31)
    assert len(alerts) == 1
    assert alerts[0]["dwell_s"] == 10


def test_merge_score_threshold_and_distinct_reporters():
    assert merge_score(0, 0, True, 0) == pytest.approx(0.85)
    assert merge_score(0, 0, True, 0) >= 0.55
    assert merge_score(60, 45, False, 0) == pytest.approx(0.0)
    assert merge_score(50, 40, False, 0) == pytest.approx(0.094444, abs=1e-4)
    assert merge_score(50, 40, False, 0) < 0.55

    close = Candidate(
        incident_id=5,
        distance_m=10,
        dt_min=5,
        category="no_parking_violation",
        reporter_hashes=set(),
        report_count=0,
    )
    chosen, score = select_merge([close], "double_parking", 0.0)
    assert chosen is not None and chosen.incident_id == 5
    assert score is not None and score >= 0.55
    assert distinct_report_count(set(), "alice") == 1
    assert distinct_report_count({"alice"}, "alice") == 1
    assert distinct_report_count({"alice"}, "bob") == 2

    outside = Candidate(incident_id=6, distance_m=80, dt_min=1, category="double_parking")
    assert select_merge([outside], "double_parking", 0.0)[0] is None

    weak = Candidate(
        incident_id=7,
        distance_m=55,
        dt_min=40,
        category="blocked_footpath",
        report_count=1,
    )
    rejected, weak_score = select_merge([weak], "double_parking", 0.0)
    assert weak_score is not None and weak_score < 0.55
    assert rejected is None


def test_priority_formula_weights():
    assert severity_factor("double_parking") == 0.7
    assert severity_factor("blocked_entrance") == 0.8
    assert severity_factor("blocked_footpath") == 0.6
    assert severity_factor("no_parking_violation") == 0.5
    assert severity_factor("spillover") == 0.4
    assert severity_factor("other") == 0.3
    assert severity_factor("other", blocks_emergency=True) == 1.0
    assert reports_factor(0) == 0.0
    assert reports_factor(5) == pytest.approx(1.0)
    assert reports_factor(1) == pytest.approx(0.3868528072, abs=1e-6)
    assert cctv_factor("confirmed") == 1.0
    assert cctv_factor("possible") == 0.6
    assert cctv_factor("none") == 0.0
    assert age_factor(60) == 1.0
    assert age_factor(30) == pytest.approx(0.5)
    assert context_factor(True, False) == 0.5
    assert context_factor(True, True) == 1.0

    assert priority_score(incident_type="other") == pytest.approx(9.0)
    assert priority_score(
        incident_type="other",
        blocks_emergency=True,
        near_emergency=True,
        n_reporters=5,
        cctv_status="confirmed",
        minutes_open=60,
        zone_full_or_overflow=True,
        peak_hour=True,
    ) == pytest.approx(100.0)
    assert priority_score(
        incident_type="no_parking_violation",
        n_reporters=1,
        cctv_status="possible",
    ) == pytest.approx(29.802792108, abs=1e-6)


def test_busy_hours_line_needs_a_one_hour_run():
    slots = []
    for hour in range(24):
        for minute in (0, 15, 30, 45):
            ratio = 0.95 if 18 <= hour <= 20 else 0.30
            slots.append((hour * 60 + minute, ratio))
    assert busy_insight(slots) == "Usually full 6–9 PM"
    short = [(18 * 60, 0.95), (18 * 60 + 15, 0.95), (18 * 60 + 30, 0.95)]
    assert busy_insight(short) is None
    one_hour = [
        (18 * 60, 0.95),
        (18 * 60 + 15, 0.95),
        (18 * 60 + 30, 0.95),
        (18 * 60 + 45, 0.95),
    ]
    assert busy_insight(one_hour) == "Usually full 6–7 PM"
