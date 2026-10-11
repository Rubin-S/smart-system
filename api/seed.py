"""Drop and recreate the PostGIS database, then load Karaikal and Puducherry."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from rules.busy import IST
from rules.status import StatusInput, derive_status, effective_capacity

from api.app.db import connect

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = Path(__file__).resolve().parent / "app" / "schema.sql"

ZONES = [
    {
        "code": "KKL-BEACH-N",
        "name": "Beach Road – North",
        "city": "Karaikal",
        "kind": "authorised_parking",
        "lng": 79.8530,
        "lat": 10.9180,
        "cars": 20,
        "two_wheelers": 0,
        "occupied": 10,
        "near_emergency": False,
        "fee": "Free",
    },
    {
        "code": "KKL-BEACH-LH",
        "name": "Beach Road – Lighthouse end",
        "city": "Karaikal",
        "kind": "authorised_parking",
        "lng": 79.8530,
        "lat": 10.9215,
        "cars": 20,
        "two_wheelers": 0,
        "occupied": 17,
        "near_emergency": False,
        "fee": "Free",
    },
    {
        "code": "KKL-BHARATHIYAR",
        "name": "Bharathiyar Road lot",
        "city": "Karaikal",
        "kind": "authorised_parking",
        "lng": 79.8492,
        "lat": 10.9170,
        "cars": 12,
        "two_wheelers": 0,
        "occupied": 12,
        "near_emergency": False,
        "fee": "₹10/hr",
    },
    {
        "code": "KKL-MARKET",
        "name": "Market Street",
        "city": "Karaikal",
        "kind": "no_parking",
        "lng": 79.8556,
        "lat": 10.9180,
        "cars": 0,
        "two_wheelers": 0,
        "occupied": 0,
        "near_emergency": False,
        "fee": None,
    },
    {
        "code": "KKL-HOSPITAL",
        "name": "Hospital entrance keep-clear",
        "city": "Karaikal",
        "kind": "emergency_access",
        "lng": 79.8480,
        "lat": 10.9150,
        "cars": 0,
        "two_wheelers": 0,
        "occupied": 0,
        "near_emergency": True,
        "fee": None,
    },
    {
        "code": "PDY-BEACH",
        "name": "Beach promenade",
        "city": "Puducherry",
        "kind": "authorised_parking",
        "lng": 79.8359,
        "lat": 11.9349,
        "cars": 30,
        "two_wheelers": 0,
        "occupied": 12,
        "near_emergency": False,
        "fee": "Free",
    },
    {
        "code": "PDY-GOUBERT",
        "name": "Goubert Avenue",
        "city": "Puducherry",
        "kind": "authorised_parking",
        "lng": 79.8334,
        "lat": 11.9320,
        "cars": 18,
        "two_wheelers": 0,
        "occupied": 16,
        "near_emergency": False,
        "fee": "Free",
    },
]


def _ring(lng: float, lat: float, half_w: float = 0.0011, half_h: float = 0.0007) -> list[tuple[float, float]]:
    west, east = lng - half_w, lng + half_w
    south, north = lat - half_h, lat + half_h
    return [(west, south), (east, south), (east, north), (west, north), (west, south)]


def _wkt(ring: list[tuple[float, float]]) -> str:
    points = ", ".join(f"{lng} {lat}" for lng, lat in ring)
    return f"SRID=4326;POLYGON(({points}))"


def apply_schema() -> None:
    sql = SCHEMA.read_text()
    result = subprocess.run(
        [
            "podman",
            "exec",
            "-i",
            "smartpark-db",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "smartpark",
            "-d",
            "smartpark",
        ],
        input=sql,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def reseed() -> list[dict]:
    apply_schema()
    now = datetime.now().astimezone()
    summaries = []
    with connect() as conn:
        for spec in ZONES:
            ring = _ring(spec["lng"], spec["lat"])
            wkt = _wkt(ring)
            readings = [float(spec["occupied"])] if spec["cars"] > 0 else []
            result = derive_status(
                StatusInput(
                    readings=readings,
                    capacity_car=spec["cars"],
                    capacity_2w=spec["two_wheelers"],
                    previous_status="unknown",
                    occ_updated_at=now if spec["cars"] > 0 else now,
                    now=now,
                )
            )
            row = conn.execute(
                """
                INSERT INTO zones (
                    code, name, city, kind, boundary, centroid,
                    capacity_car, capacity_2w, pcu_weight_2w, status,
                    occupied_now, occ_updated_at, near_emergency, fee_info
                )
                VALUES (
                    %s, %s, %s, %s,
                    ST_GeogFromText(%s),
                    ST_Centroid(ST_GeogFromText(%s)::geometry)::geography,
                    %s, %s, 0.25, %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    spec["code"],
                    spec["name"],
                    spec["city"],
                    spec["kind"],
                    wkt,
                    wkt,
                    spec["cars"],
                    spec["two_wheelers"],
                    result.status,
                    float(spec["occupied"]),
                    now,
                    spec["near_emergency"],
                    spec["fee"],
                ),
            ).fetchone()
            zone_id = row["id"]
            capacity = effective_capacity(spec["cars"], spec["two_wheelers"], 0.25)
            if spec["cars"] > 0:
                conn.execute(
                    """
                    INSERT INTO occupancy_snapshots (zone_id, ts, occupied, capacity, source)
                    VALUES (%s, %s, %s, %s, 'seed')
                    """,
                    (zone_id, now, float(spec["occupied"]), capacity),
                )
                _insert_history(conn, zone_id, capacity, now)
            if result.status == "almost_full":
                conn.execute(
                    """
                    INSERT INTO alerts (kind, zone_id, message, level, dedupe_key)
                    VALUES ('almost_full', %s, %s, 'warn', %s)
                    """,
                    (
                        zone_id,
                        f"{spec['name']} almost full ({spec['occupied']}/{capacity})",
                        f"seed-almost-full:{spec['code']}",
                    ),
                )
            summaries.append({"id": zone_id, "code": spec["code"], "status": result.status})

        beach = conn.execute("SELECT id, boundary FROM zones WHERE code = 'KKL-BEACH-N'").fetchone()
        market = conn.execute("SELECT id FROM zones WHERE code = 'KKL-MARKET'").fetchone()
        camera = conn.execute(
            """
            INSERT INTO cameras (code, rtsp_url, name, location, coverage)
            VALUES (
                'CAM-KKL-01',
                'rtsp://127.0.0.1:8554/cam1',
                'Beach Road north camera',
                (SELECT centroid FROM zones WHERE id = %s),
                (SELECT boundary FROM zones WHERE id = %s)
            )
            RETURNING id
            """,
            (beach["id"], beach["id"]),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO camera_rois (camera_id, zone_id, role, polygon_px)
            VALUES
                (%s, %s, 'count', '[[40,40],[600,40],[600,440],[40,440]]'::jsonb),
                (%s, %s, 'no_parking', '[[40,40],[600,40],[600,440],[40,440]]'::jsonb)
            """,
            (camera["id"], beach["id"], camera["id"], market["id"]),
        )
        conn.execute(
            """
            INSERT INTO officers (badge_no, name, role)
            VALUES ('PC-1142', 'Demo Officer', 'officer')
            """
        )
    return summaries


def _insert_history(conn, zone_id: int, capacity: int, now: datetime) -> None:
    rows = []
    today = now.date()
    for day_offset in range(1, 22):
        day = today - timedelta(days=day_offset)
        for slot in range(96):
            minute = slot * 15
            hour, mins = divmod(minute, 60)
            local = datetime(day.year, day.month, day.day, hour, mins, tzinfo=IST)
            ratio = 0.95 if 18 <= hour <= 20 else 0.30
            rows.append((zone_id, local, int(round(ratio * capacity)), capacity, "history"))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO occupancy_snapshots (zone_id, ts, occupied, capacity, source)
            VALUES (%s, %s, %s, %s, %s)
            """,
            rows,
        )


def main() -> None:
    rows = reseed()
    for row in rows:
        print(f"{row['id']:2} {row['code']:16} {row['status']}")


if __name__ == "__main__":
    main()
