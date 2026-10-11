"""Worker dwell steps call the shipped tracker on every frame."""

from rules.dwell import DwellTracker
from worker.main import step_dwell


def test_long_absence_restarts_the_timer_instead_of_flagging():
    """Seen at t=9, missing through t=40, back at t=41. Grace is 10s.

    Empty frames must be passed to the tracker. Skipping them leaves last_seen
    at 9, so the return at t=41 looks like one continuous dwell and flags.
    """
    tracker = DwellTracker(grace_s=10, lost_tolerance_s=15, move_tol_px=25, zone="KKL-MARKET")
    anchor = [(40.0, 80.0)]
    assert step_dwell(tracker, [1], anchor, [2], [True], 9) == []
    for t in range(10, 41):
        assert step_dwell(tracker, None, None, None, None, t) == []
    assert step_dwell(tracker, [1], anchor, [2], [True], 41) == []
    assert step_dwell(tracker, [1], anchor, [2], [True], 50) == []
    alerts = step_dwell(tracker, [1], anchor, [2], [True], 51)
    assert len(alerts) == 1
    assert alerts[0]["dwell_s"] == 10
    assert alerts[0]["status"] == "PENDING_OFFICER_REVIEW"


def test_out_of_zone_frames_also_let_the_lost_timer_expire():
    tracker = DwellTracker(grace_s=10, lost_tolerance_s=15, move_tol_px=25, zone="KKL-MARKET")
    anchor = [(40.0, 80.0)]
    assert step_dwell(tracker, [1], anchor, [2], [True], 9) == []
    for t in range(10, 41):
        assert step_dwell(tracker, [1], anchor, [2], [False], t) == []
    assert step_dwell(tracker, [1], anchor, [2], [True], 41) == []


def test_short_empty_gap_still_flags_at_the_original_grace_instant():
    tracker = DwellTracker(grace_s=10, lost_tolerance_s=15, move_tol_px=25, zone="KKL-MARKET")
    anchor = [(50.0, 50.0)]
    assert step_dwell(tracker, [4], anchor, [2], [True], 0) == []
    assert step_dwell(tracker, None, None, None, None, 3) == []
    alerts = step_dwell(tracker, [4], anchor, [2], [True], 10)
    assert len(alerts) == 1
    assert alerts[0]["dwell_s"] == 10
