"""Grace-period dwell for no-parking and just-closed zones.

A stationary track is flagged once, at the grace instant. A track that moves
more than ``move_tol_px`` from where it entered is treated as passing traffic
and the timer restarts. A gap that does not exceed ``lost_tolerance_s`` keeps
the original timer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrackObs:
    track_id: int
    anchor: tuple[float, float]
    class_id: int = 2


@dataclass
class _Dwell:
    first_seen: float
    last_seen: float
    anchor: tuple[float, float]
    class_id: int
    flagged: bool = False


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


@dataclass
class DwellTracker:
    grace_s: float = 120.0
    lost_tolerance_s: float = 15.0
    move_tol_px: float = 25.0
    zone: str = ""
    tracks: dict[int, _Dwell] = field(default_factory=dict)

    def update(self, observations: list[TrackObs], t: float) -> list[dict]:
        alerts: list[dict] = []
        for obs in observations:
            current = self.tracks.get(obs.track_id)
            if current is None or _dist(obs.anchor, current.anchor) > self.move_tol_px:
                self.tracks[obs.track_id] = _Dwell(t, t, obs.anchor, obs.class_id, False)
                continue
            current.last_seen = t
            current.class_id = obs.class_id
            if not current.flagged and (t - current.first_seen) >= self.grace_s:
                current.flagged = True
                alerts.append(
                    {
                        "zone": self.zone,
                        "track_id": int(obs.track_id),
                        "class_id": int(obs.class_id),
                        "dwell_s": round(t - current.first_seen, 1),
                        "status": "PENDING_OFFICER_REVIEW",
                    }
                )
        self.tracks = {
            key: value
            for key, value in self.tracks.items()
            if (t - value.last_seen) <= self.lost_tolerance_s
        }
        return alerts
