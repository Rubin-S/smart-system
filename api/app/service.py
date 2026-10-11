"""API operations. Status, dwell, merge, and priority come from ``rules``."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rules.busy import IST, busy_insight, slots_from_snapshots
from rules.dwell import DwellTracker, TrackObs
from rules.merge import Candidate, distinct_report_count, select_merge
from rules.priority import priority_score
from rules.status import StatusInput, closing_soon, derive_status, effective_capacity

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "media"
IST_TZ = IST


def _now() -> datetime:
    return datetime.now(timezone.utc)


def num(value: float):
    value = float(value)
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return round(value, 2)


def maps_url(lat: float, lng: float) -> str:
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={lat:.6f},{lng:.6f}&travelmode=two-wheeler"
    )


def grace_seconds() -> float:
    return float(os.environ.get("DEMO_GRACE_SECONDS", "1"))


def _zone_sql(where: str) -> str:
    return f"""
        SELECT z.*,
               ST_AsGeoJSON(z.boundary::geometry) AS geojson,
               ST_Y(z.centroid::geometry) AS lat,
               ST_X(z.centroid::geometry) AS lng
        FROM zones z
        WHERE {where}
    """


def resolve_zone(conn, ident):
    if ident is None:
        raise LookupError("zone is required")
    text = str(ident)
    if text.isdigit():
        row = conn.execute(_zone_sql("z.id = %s"), (int(text),)).fetchone()
    else:
        row = conn.execute(_zone_sql("z.code = %s"), (text,)).fetchone()
    if row is None:
        raise LookupError(f"unknown zone {ident}")
    return row


def live_readings(conn, zone_id: int) -> list[float]:
    rows = conn.execute(
        """
        SELECT occupied FROM occupancy_snapshots
        WHERE zone_id = %s AND source <> 'history'
        ORDER BY ts DESC
        LIMIT 5
        """,
        (zone_id,),
    ).fetchall()
    return [float(row["occupied"]) for row in reversed(rows)]


def closure_flags(conn, zone_id: int, now: datetime):
    active = conn.execute(
        """
        SELECT 1 FROM zone_closures
        WHERE zone_id = %s AND starts_at <= %s
          AND (ends_at IS NULL OR ends_at > %s)
        LIMIT 1
        """,
        (zone_id, now, now),
    ).fetchone()
    nxt = conn.execute(
        """
        SELECT starts_at FROM zone_closures
        WHERE zone_id = %s AND starts_at > %s
        ORDER BY starts_at ASC
        LIMIT 1
        """,
        (zone_id, now),
    ).fetchone()
    soon, minutes = closing_soon(now, nxt["starts_at"] if nxt else None)
    closes_at = nxt["starts_at"].isoformat() if nxt and soon else None
    return bool(active), soon, minutes, closes_at


def load_history(conn) -> dict[int, list]:
    rows = conn.execute(
        """
        SELECT zone_id, ts, occupied, capacity
        FROM occupancy_snapshots
        WHERE source = 'history'
        """
    ).fetchall()
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row["zone_id"], []).append(
            (row["ts"], float(row["occupied"]), float(row["capacity"]))
        )
    return grouped


def _busy_for(history: dict, zone_id: int, now: datetime, kind: str):
    if kind != "authorised_parking":
        return None
    weekday = now.astimezone(IST_TZ).weekday()
    return busy_insight(slots_from_snapshots(history.get(zone_id, []), weekday))


def _peak(history: dict, zone_id: int | None, now: datetime) -> bool:
    if zone_id is None:
        return False
    local = now.astimezone(IST_TZ)
    minute = local.hour * 60 + (local.minute // 15) * 15
    for slot, ratio in slots_from_snapshots(history.get(zone_id, []), local.weekday()):
        if slot == minute and ratio >= 0.9:
            return True
    return False


def present_zone(conn, zone_id: int, history: dict | None = None, now: datetime | None = None) -> dict:
    import json

    now = now or _now()
    row = conn.execute(_zone_sql("z.id = %s"), (zone_id,)).fetchone()
    if row is None:
        raise LookupError(f"unknown zone {zone_id}")
    readings = live_readings(conn, zone_id)
    schedule_closed, soon, minutes, closes_at = closure_flags(conn, zone_id, now)
    result = derive_status(
        StatusInput(
            readings=readings,
            capacity_car=row["capacity_car"],
            capacity_2w=row["capacity_2w"],
            pcu_weight_2w=float(row["pcu_weight_2w"]),
            previous_status=row["status"] or "unknown",
            occ_updated_at=row["occ_updated_at"],
            now=now,
            manual_override=row["manual_override"],
            override_until=row["override_until"],
            schedule_closed=schedule_closed,
        )
    )
    latest = float(readings[-1]) if readings else 0.0
    if history is None:
        history = load_history(conn)
    lat = round(float(row["lat"]), 6)
    lng = round(float(row["lng"]), 6)
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "city": row["city"],
        "kind": row["kind"],
        "status": result.status,
        "occupied": num(latest),
        "capacity": result.capacity,
        "free": num(max(0.0, result.capacity - latest)),
        "overflow": result.overflow,
        "closing_soon": soon,
        "closes_at": closes_at,
        "minutes_until_close": minutes,
        "busy_insight": _busy_for(history, zone_id, now, row["kind"]),
        "lat": lat,
        "lng": lng,
        "near_emergency": row["near_emergency"],
        "override_reason": row["override_reason"] if row["manual_override"] else None,
        "maps_url": maps_url(lat, lng),
        "geometry": json.loads(row["geojson"]),
        "updated_at": row["occ_updated_at"].isoformat() if row["occ_updated_at"] else None,
    }


def zone_event(payload: dict) -> dict:
    data = {key: value for key, value in payload.items() if key != "geometry"}
    return {"type": "zone.updated", "data": data}


def list_zones(conn) -> list[dict]:
    history = load_history(conn)
    ids = conn.execute("SELECT id FROM zones ORDER BY id").fetchall()
    return [present_zone(conn, row["id"], history) for row in ids]


def feature_collection(zones: list[dict]) -> dict:
    features = []
    for zone in zones:
        properties = {key: value for key, value in zone.items() if key != "geometry"}
        features.append(
            {
                "type": "Feature",
                "id": zone["code"],
                "geometry": zone["geometry"],
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _insert_alert(conn, kind, zone_id, message, level, dedupe_key, incident_id=None):
    conn.execute(
        """
        INSERT INTO alerts (kind, zone_id, incident_id, message, level, dedupe_key)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) DO NOTHING
        """,
        (kind, zone_id, incident_id, message, level, dedupe_key),
    )


def _audit(conn, actor: str, action: str, entity: str, reason: str | None = None, actor_type: str = "officer"):
    conn.execute(
        """
        INSERT INTO audit_log (actor_type, actor_id, action, entity, reason)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (actor_type, actor, action, entity, reason),
    )


def apply_occupancy(
    conn,
    zone_ident,
    occupied: float,
    *,
    source: str = "cctv",
    occupied_2w: float = 0,
    confidence: float | None = None,
    replace: bool = False,
    now: datetime | None = None,
) -> tuple[dict, list[dict]]:
    now = now or _now()
    zone = resolve_zone(conn, zone_ident)
    old_status = zone["status"]
    if replace:
        conn.execute(
            "DELETE FROM occupancy_snapshots WHERE zone_id = %s AND source <> 'history'",
            (zone["id"],),
        )
    capacity = effective_capacity(zone["capacity_car"], zone["capacity_2w"], zone["pcu_weight_2w"])
    conn.execute(
        """
        INSERT INTO occupancy_snapshots
            (zone_id, ts, occupied, occupied_2w, capacity, source, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (zone["id"], now, float(occupied), float(occupied_2w), capacity, source, confidence),
    )
    readings = live_readings(conn, zone["id"])
    schedule_closed, _, _, _ = closure_flags(conn, zone["id"], now)
    result = derive_status(
        StatusInput(
            readings=readings,
            capacity_car=zone["capacity_car"],
            capacity_2w=zone["capacity_2w"],
            pcu_weight_2w=float(zone["pcu_weight_2w"]),
            previous_status=old_status or "unknown",
            occ_updated_at=now,
            now=now,
            manual_override=zone["manual_override"],
            override_until=zone["override_until"],
            schedule_closed=schedule_closed,
        )
    )
    latest = float(readings[-1])
    conn.execute(
        """
        UPDATE zones
        SET status = %s, occupied_now = %s, occ_updated_at = %s
        WHERE id = %s
        """,
        (result.status, latest, now, zone["id"]),
    )
    history = load_history(conn)
    payload = present_zone(conn, zone["id"], history, now)
    hour = now.strftime("%Y-%m-%dT%H")
    label = f"{zone['name']} {payload['occupied']}/{payload['capacity']}"
    if payload["status"] == "almost_full" and old_status != "almost_full":
        _insert_alert(
            conn,
            "almost_full",
            zone["id"],
            f"{label} almost full",
            "warn",
            f"almost_full:{zone['id']}:{hour}",
        )
    if payload["overflow"]:
        _insert_alert(
            conn,
            "overflow",
            zone["id"],
            f"{label} overflow",
            "critical",
            f"overflow:{zone['id']}:{hour}",
        )
    _audit(conn, source, "zone.occupancy", f"zone:{zone['id']}", actor_type="cv_worker" if source == "cctv" else "system")
    return payload, [zone_event(payload)]


def set_zone_status(conn, zone_ident, action: str, reason: str | None, until: str | None = None, actor: str = "PC-1142"):
    if action not in ("open", "close", "restrict"):
        raise ValueError("action must be open, close, or restrict")
    if not reason or not str(reason).strip():
        raise ValueError("a reason is required")
    zone = resolve_zone(conn, zone_ident)
    now = _now()
    if action == "open":
        override = None
        until_dt = None
        stored_reason = reason.strip()
    else:
        override = "closed" if action == "close" else "restricted"
        until_dt = datetime.fromisoformat(until) if until else None
        if until_dt is not None and until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
        stored_reason = reason.strip()
    conn.execute(
        """
        UPDATE zones
        SET manual_override = %s, override_reason = %s, override_until = %s
        WHERE id = %s
        """,
        (override, stored_reason, until_dt, zone["id"]),
    )
    fresh = resolve_zone(conn, zone["id"])
    readings = live_readings(conn, zone["id"])
    schedule_closed, _, _, _ = closure_flags(conn, zone["id"], now)
    result = derive_status(
        StatusInput(
            readings=readings,
            capacity_car=fresh["capacity_car"],
            capacity_2w=fresh["capacity_2w"],
            pcu_weight_2w=float(fresh["pcu_weight_2w"]),
            previous_status=fresh["status"] or "unknown",
            occ_updated_at=fresh["occ_updated_at"],
            now=now,
            manual_override=fresh["manual_override"],
            override_until=fresh["override_until"],
            schedule_closed=schedule_closed,
        )
    )
    conn.execute("UPDATE zones SET status = %s WHERE id = %s", (result.status, zone["id"]))
    payload = present_zone(conn, zone["id"], load_history(conn), now)
    _audit(conn, actor, f"zone.{action}", f"zone:{zone['id']}", stored_reason)
    return payload, [zone_event(payload)]


def add_closure(conn, zone_ident, starts_at: str, reason: str, ends_at: str | None = None, grace_min: int = 15, actor: str = "PC-1142"):
    if not reason or not str(reason).strip():
        raise ValueError("a reason is required")
    zone = resolve_zone(conn, zone_ident)
    start = datetime.fromisoformat(starts_at)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = None
    if ends_at:
        end = datetime.fromisoformat(ends_at)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    conn.execute(
        """
        INSERT INTO zone_closures (zone_id, starts_at, ends_at, reason, created_by, grace_min)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (zone["id"], start, end, reason.strip(), actor, grace_min),
    )
    _audit(conn, actor, "zone.closure", f"zone:{zone['id']}", reason.strip())
    payload = present_zone(conn, zone["id"])
    return payload, [zone_event(payload)]


def nearby(conn, lat: float, lng: float, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id,
               ST_Distance(centroid, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS dist_m
        FROM zones
        WHERE kind = 'authorised_parking'
        ORDER BY dist_m ASC
        LIMIT %s
        """,
        (lng, lat, limit),
    ).fetchall()
    history = load_history(conn)
    results = []
    for row in rows:
        payload = present_zone(conn, row["id"], history)
        payload["distance_m"] = round(float(row["dist_m"]), 1)
        results.append(payload)
    return results


def _write_evidence(label: str) -> str:
    from PIL import Image, ImageDraw

    folder = MEDIA / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"ev-{uuid4().hex}.jpg"
    image = Image.new("RGB", (640, 360), (22, 32, 42))
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 130, 470, 270), outline=(255, 92, 70), width=4)
    draw.text((24, 24), label[:80], fill=(255, 255, 255))
    draw.text((24, 52), _now().strftime("%Y-%m-%d %H:%M:%S UTC"), fill=(190, 205, 214))
    image.save(folder / name, "JPEG", quality=85)
    return f"/media/evidence/{name}"


def _score_incident(
    conn,
    *,
    incident_type: str,
    zone_id: int | None,
    n_reporters: int,
    cctv_status: str,
    first_seen: datetime,
    now: datetime,
    blocks_emergency: bool,
    history: dict | None = None,
) -> float:
    near = False
    full = False
    if zone_id is not None:
        zone = conn.execute("SELECT * FROM zones WHERE id = %s", (zone_id,)).fetchone()
        if zone is not None:
            near = bool(zone["near_emergency"])
            capacity = effective_capacity(zone["capacity_car"], zone["capacity_2w"], zone["pcu_weight_2w"])
            full = zone["status"] == "full" or (capacity > 0 and float(zone["occupied_now"]) > 1.10 * capacity)
            if zone["kind"] == "emergency_access":
                blocks_emergency = True
    if history is None:
        history = load_history(conn)
    minutes = max(0.0, (now - first_seen).total_seconds() / 60.0)
    return round(
        priority_score(
            incident_type=incident_type,
            blocks_emergency=blocks_emergency,
            near_emergency=near,
            n_reporters=n_reporters,
            cctv_status=cctv_status,
            minutes_open=minutes,
            zone_full_or_overflow=full,
            peak_hour=_peak(history, zone_id, now),
        ),
        4,
    )


def present_incident(conn, incident_id: int) -> dict:
    row = conn.execute(
        """
        SELECT i.*,
               ST_Y(i.location::geometry) AS lat,
               ST_X(i.location::geometry) AS lng,
               z.code AS zone_code,
               z.name AS zone_name
        FROM incidents i
        LEFT JOIN zones z ON z.id = i.zone_id
        WHERE i.id = %s
        """,
        (incident_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"unknown incident {incident_id}")
    evidence = conn.execute(
        """
        SELECT kind, url, captured_at
        FROM incident_evidence
        WHERE incident_id = %s
        ORDER BY id
        """,
        (incident_id,),
    ).fetchall()
    return {
        "id": row["id"],
        "type": row["type"],
        "state": row["state"],
        "priority": float(row["priority"]),
        "report_count": row["report_count"],
        "cctv_status": row["cctv_status"],
        "evidence_url": row["evidence_url"],
        "evidence": [
            {"kind": item["kind"], "url": item["url"], "captured_at": item["captured_at"].isoformat()}
            for item in evidence
        ],
        "lat": round(float(row["lat"]), 6),
        "lng": round(float(row["lng"]), 6),
        "zone_id": row["zone_id"],
        "zone_code": row["zone_code"],
        "zone_name": row["zone_name"],
        "vehicle_plate": row["vehicle_plate"],
        "blocks_emergency": row["blocks_emergency"],
        "first_seen": row["first_seen"].isoformat(),
        "last_activity": row["last_activity"].isoformat(),
    }


def create_violation(conn, zone_ident, alert: dict, actor: str = "demo", actor_type: str = "system") -> dict:
    zone = resolve_zone(conn, zone_ident)
    now = _now()
    url = _write_evidence(
        f"{zone['code']} track {alert.get('track_id', 0)} dwell {alert.get('dwell_s', 0)}s"
    )
    blocks = zone["kind"] == "emergency_access"
    history = load_history(conn)
    priority = _score_incident(
        conn,
        incident_type="no_parking_violation",
        zone_id=zone["id"],
        n_reporters=0,
        cctv_status="possible",
        first_seen=now,
        now=now,
        blocks_emergency=blocks,
        history=history,
    )
    created = conn.execute(
        """
        INSERT INTO incidents (
            type, state, location, zone_id, blocks_emergency, report_count,
            cctv_status, priority, first_seen, last_activity, evidence_url
        )
        VALUES (
            'no_parking_violation', 'open',
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s, %s, 0, 'possible', %s, %s, %s, %s
        )
        RETURNING id
        """,
        (float(zone["lng"]), float(zone["lat"]), zone["id"], blocks, priority, now, now, url),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO incident_evidence (incident_id, kind, url, captured_at)
        VALUES (%s, 'cctv_snapshot', %s, %s)
        """,
        (created["id"], url, now),
    )
    _insert_alert(
        conn,
        "possible_illegal",
        zone["id"],
        f"Possible illegal parking at {zone['name']}",
        "critical",
        f"possible_illegal:{created['id']}",
        incident_id=created["id"],
    )
    _audit(conn, actor, "incident.create", f"incident:{created['id']}", actor_type=actor_type)
    return present_incident(conn, created["id"])


def _watched(zone, payload) -> bool:
    return (
        zone["kind"] in ("no_parking", "no_stopping", "emergency_access")
        or payload["status"] in ("closed", "restricted")
        or zone["manual_override"] in ("closed", "restricted")
    )


def simulate_vehicle(conn, zone_ident, action: str, count: float | None = None, actor: str = "PC-1142"):
    if action not in ("park", "leave"):
        raise ValueError("action must be park or leave")
    zone = resolve_zone(conn, zone_ident)
    current = present_zone(conn, zone["id"])
    events: list[dict] = []
    grace = grace_seconds()
    tracker = DwellTracker(
        grace_s=grace,
        lost_tolerance_s=15,
        move_tol_px=25,
        zone=zone["code"],
    )
    if action == "leave":
        step = 1.0 if count is None else float(count)
        payload, occ_events = apply_occupancy(
            conn,
            zone["id"],
            max(0.0, float(current["occupied"]) - step),
            source="simulated",
        )
        events.extend(occ_events)
        tracker.update([TrackObs(1, (0.0, 0.0))], 0.0)
        alerts = tracker.update([TrackObs(1, (300.0, 0.0))], grace)
        return {"action": "leave", "zone": payload, "alerts": alerts, "incident": None, "events": events}

    payload = current
    if zone["kind"] == "authorised_parking":
        step = 1.0 if count is None else float(count)
        payload, occ_events = apply_occupancy(
            conn,
            zone["id"],
            float(current["occupied"]) + step,
            source="simulated",
        )
        events.extend(occ_events)
    incident = None
    alerts: list[dict] = []
    if _watched(zone, payload):
        obs = TrackObs(1, (100.0, 100.0), 2)
        tracker.update([obs], 0.0)
        alerts = tracker.update([obs], grace)
        if alerts:
            incident = create_violation(conn, zone["id"], alerts[0], actor=actor, actor_type="demo")
            events.append(
                {
                    "type": "incident.created",
                    "data": {
                        "id": incident["id"],
                        "type": incident["type"],
                        "state": incident["state"],
                        "priority": incident["priority"],
                        "zone_id": incident["zone_id"],
                    },
                }
            )
    return {
        "action": "park",
        "zone": payload,
        "alerts": alerts,
        "incident": incident,
        "events": events,
    }


def _bind_zone(conn, lng: float, lat: float):
    return conn.execute(
        """
        SELECT id FROM zones
        WHERE ST_DWithin(boundary, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 80)
        ORDER BY ST_Distance(boundary, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
        LIMIT 1
        """,
        (lng, lat, lng, lat),
    ).fetchone()


def file_complaint(
    conn,
    *,
    lat: float,
    lng: float,
    category: str,
    description: str | None = None,
    reporter_hash: str | None = None,
    accuracy: float | None = None,
):
    if not category:
        raise ValueError("category is required")
    now = _now()
    reporter = reporter_hash or f"anon-{uuid4().hex}"
    rows = conn.execute(
        """
        SELECT i.id, i.type AS category, i.report_count, i.zone_id, i.cctv_status,
               i.blocks_emergency, i.first_seen,
               ST_Distance(i.location, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) AS d_m,
               EXTRACT(EPOCH FROM (%s - i.last_activity)) / 60.0 AS dt_min
        FROM incidents i
        WHERE i.state IN ('open', 'assigned', 'verified')
          AND ST_DWithin(i.location, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 60)
          AND i.last_activity > %s - interval '45 minutes'
        ORDER BY d_m ASC
        LIMIT 5
        """,
        (lng, lat, now, lng, lat, now),
    ).fetchall()
    candidates = []
    for row in rows:
        hashes = {
            item["reporter_hash"]
            for item in conn.execute(
                "SELECT reporter_hash FROM complaints WHERE incident_id = %s",
                (row["id"],),
            ).fetchall()
        }
        candidates.append(
            Candidate(
                incident_id=row["id"],
                distance_m=max(0.0, float(row["d_m"])),
                dt_min=max(0.0, float(row["dt_min"])),
                category=row["category"],
                reporter_hashes=hashes,
                report_count=row["report_count"],
            )
        )
    target, score = select_merge(candidates, category, 0.0)
    point_sql = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography"
    if target is not None:
        previous = target.report_count
        report_count = distinct_report_count(target.reporter_hashes, reporter)
        existing = conn.execute("SELECT * FROM incidents WHERE id = %s", (target.incident_id,)).fetchone()
        priority = _score_incident(
            conn,
            incident_type=existing["type"],
            zone_id=existing["zone_id"],
            n_reporters=report_count,
            cctv_status=existing["cctv_status"],
            first_seen=existing["first_seen"],
            now=now,
            blocks_emergency=existing["blocks_emergency"],
        )
        conn.execute(
            """
            UPDATE incidents
            SET report_count = %s, last_activity = %s, priority = %s
            WHERE id = %s
            """,
            (report_count, now, priority, target.incident_id),
        )
        complaint = conn.execute(
            f"""
            INSERT INTO complaints (
                incident_id, category, description, location, gps_accuracy_m,
                reporter_hash, merge_score, created_at
            )
            VALUES (%s, %s, %s, {point_sql}, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                target.incident_id,
                category,
                description,
                lng,
                lat,
                accuracy,
                reporter,
                score,
                now,
            ),
        ).fetchone()
        incident = present_incident(conn, target.incident_id)
        return {
            "complaint_id": complaint["id"],
            "incident_id": target.incident_id,
            "merged": True,
            "merge_score": round(float(score), 4) if score is not None else None,
            "report_count": report_count,
            "previous_report_count": previous,
            "priority": incident["priority"],
            "incident": incident,
        }

    bound = _bind_zone(conn, lng, lat)
    zone_id = bound["id"] if bound else None
    blocks = category in ("blocked_emergency",)
    priority = _score_incident(
        conn,
        incident_type=category,
        zone_id=zone_id,
        n_reporters=1,
        cctv_status="none",
        first_seen=now,
        now=now,
        blocks_emergency=blocks,
    )
    created = conn.execute(
        f"""
        INSERT INTO incidents (
            type, state, location, zone_id, blocks_emergency, report_count,
            cctv_status, priority, first_seen, last_activity
        )
        VALUES (%s, 'open', {point_sql}, %s, %s, 1, 'none', %s, %s, %s)
        RETURNING id
        """,
        (category, lng, lat, zone_id, blocks, priority, now, now),
    ).fetchone()
    complaint = conn.execute(
        f"""
        INSERT INTO complaints (
            incident_id, category, description, location, gps_accuracy_m,
            reporter_hash, merge_score, created_at
        )
        VALUES (%s, %s, %s, {point_sql}, %s, %s, NULL, %s)
        RETURNING id
        """,
        (created["id"], category, description, lng, lat, accuracy, reporter, now),
    ).fetchone()
    incident = present_incident(conn, created["id"])
    return {
        "complaint_id": complaint["id"],
        "incident_id": created["id"],
        "merged": False,
        "merge_score": None,
        "report_count": 1,
        "previous_report_count": 0,
        "priority": incident["priority"],
        "incident": incident,
    }


def list_incidents(conn, state: str = "open") -> list[dict]:
    if state:
        rows = conn.execute(
            """
            SELECT id FROM incidents
            WHERE state = %s
            ORDER BY priority DESC, id ASC
            """,
            (state,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT id FROM incidents ORDER BY priority DESC, id ASC").fetchall()
    return [present_incident(conn, row["id"]) for row in rows]


def set_incident_state(conn, incident_id: int, state: str, action: str, actor: str, note: str | None = None, plate: str | None = None):
    row = conn.execute("SELECT id, state FROM incidents WHERE id = %s", (incident_id,)).fetchone()
    if row is None:
        raise LookupError(f"unknown incident {incident_id}")
    if state == "challan_sent":
        raise ValueError("challan submission is not part of this system")
    if plate:
        conn.execute(
            """
            UPDATE incidents
            SET state = %s, vehicle_plate = %s, last_activity = now()
            WHERE id = %s
            """,
            (state, plate, incident_id),
        )
    else:
        conn.execute(
            "UPDATE incidents SET state = %s, last_activity = now() WHERE id = %s",
            (state, incident_id),
        )
    _audit(conn, actor, action, f"incident:{incident_id}", note)
    return present_incident(conn, incident_id)


def list_alerts(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.id, a.kind, a.zone_id, a.incident_id, a.message, a.level, a.created_at,
               z.code AS zone_code, z.name AS zone_name
        FROM alerts a
        LEFT JOIN zones z ON z.id = a.zone_id
        ORDER BY a.id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "zone_id": row["zone_id"],
            "zone_code": row["zone_code"],
            "zone_name": row["zone_name"],
            "incident_id": row["incident_id"],
            "message": row["message"],
            "level": row["level"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


def list_audit(conn, entity: str | None = None) -> list[dict]:
    if entity:
        rows = conn.execute(
            """
            SELECT actor_type, actor_id, action, entity, reason, at
            FROM audit_log
            WHERE entity = %s
            ORDER BY id ASC
            """,
            (entity,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT actor_type, actor_id, action, entity, reason, at
            FROM audit_log
            ORDER BY id ASC
            """
        ).fetchall()
    return [
        {
            "actor": row["actor_id"],
            "actor_type": row["actor_type"],
            "action": row["action"],
            "entity": row["entity"],
            "reason": row["reason"],
            "at": row["at"].isoformat(),
        }
        for row in rows
    ]


def kpis(conn) -> dict:
    zones = [zone for zone in list_zones(conn) if zone["kind"] == "authorised_parking"]
    capacity = sum(zone["capacity"] for zone in zones)
    occupied = sum(float(zone["occupied"]) for zone in zones)
    open_count = conn.execute("SELECT COUNT(*) AS n FROM incidents WHERE state = 'open'").fetchone()["n"]
    alert_count = conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"]
    return {
        "zones": len(zones),
        "capacity": capacity,
        "occupied": num(occupied),
        "free": num(max(0.0, capacity - occupied)),
        "open_incidents": open_count,
        "alerts": alert_count,
    }


def run_step(conn, step: int, actor: str = "PC-1142"):
    """Scripted demo beats. Each one goes through occupancy, closure, dwell, or merge."""
    if step == 1:
        payload, events = apply_occupancy(conn, "KKL-BEACH-N", 16, source="simulated", replace=True)
        return {"step": 1, "zone": payload, "events": events}
    if step == 2:
        payload, events = apply_occupancy(conn, "KKL-BEACH-N", 17, source="simulated", replace=True)
        return {"step": 2, "zone": payload, "events": events}
    if step == 3:
        payload, events = set_zone_status(conn, "KKL-BEACH-N", "close", "Festival procession", actor=actor)
        return {"step": 3, "zone": payload, "events": events}
    if step == 4:
        result = simulate_vehicle(conn, "KKL-MARKET", "park", actor=actor)
        result["step"] = 4
        return result
    if step == 5:
        existing = conn.execute(
            """
            SELECT id FROM incidents
            WHERE state = 'open' AND type = 'no_parking_violation'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        events: list[dict] = []
        if existing is None:
            parked = simulate_vehicle(conn, "KKL-MARKET", "park", actor=actor)
            events.extend(parked["events"])
            incident_id = parked["incident"]["id"]
        else:
            incident_id = existing["id"]
        incident = present_incident(conn, incident_id)
        complaint = file_complaint(
            conn,
            lat=incident["lat"],
            lng=incident["lng"],
            category="double_parking",
            description="Double parking on the scripted step",
            reporter_hash="citizen-step-5",
        )
        events.append({"type": "complaint.merged" if complaint["merged"] else "complaint.created", "data": complaint})
        return {"step": 5, "complaint": complaint, "events": events}
    if step == 6:
        row = conn.execute(
            "SELECT id FROM incidents WHERE state = 'open' ORDER BY priority DESC, id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            raise LookupError("no open incident to verify")
        incident = set_incident_state(conn, row["id"], "verified", "incident.verify", actor, note="Scripted verify")
        return {"step": 6, "incident": incident, "events": []}
    raise LookupError(f"unknown demo step {step}")


def reset_database():
    from api.seed import reseed

    reseed()
