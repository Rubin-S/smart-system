"""FastAPI entry point for the parking, enforcement, and reporting API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.app.db import connect
from api.app import service

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "media"
MEDIA.mkdir(parents=True, exist_ok=True)
(MEDIA / "evidence").mkdir(parents=True, exist_ok=True)
WORKER_KEY = os.environ.get("WORKER_KEY", "dev-worker-key")

app = FastAPI(title="Smart Parking, Enforcement & Citizen Reporting")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)

    async def broadcast(self, event: dict) -> None:
        dead: list[WebSocket] = []
        for client in list(self.clients):
            try:
                await client.send_json(event)
            except Exception:
                dead.append(client)
        for client in dead:
            self.clients.discard(client)


hub = Hub()


def _actor(header: str | None, explicit: str | None = None) -> str:
    return (explicit or header or "PC-1142").strip()


def _guard_worker(key: str) -> None:
    if key != WORKER_KEY:
        raise HTTPException(status_code=401, detail="worker key required")


def _run(fn):
    try:
        with connect() as conn:
            return fn(conn)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _emit(events: list[dict]) -> None:
    for event in events:
        await hub.broadcast(event)


class OccupancyIn(BaseModel):
    zone_id: int | None = None
    zone_code: str | None = None
    occupied: float
    occupied_2w: float = 0
    confidence: float | None = None
    source: str = "cctv"


class ViolationIn(BaseModel):
    zone_code: str | None = None
    zone_id: int | None = None
    track_id: int = 0
    dwell_s: float = 0
    class_id: int = 2


class StatusIn(BaseModel):
    action: str
    reason: str | None = None
    until: str | None = None
    actor: str | None = None


class ClosureIn(BaseModel):
    starts_at: str
    ends_at: str | None = None
    reason: str
    grace_min: int = 15
    actor: str | None = None


class ComplaintIn(BaseModel):
    lat: float
    lng: float
    category: str
    description: str | None = None
    reporter_hash: str | None = None
    accuracy: float | None = None


class VerifyIn(BaseModel):
    plate: str | None = None
    note: str | None = None
    actor: str | None = None


class SimulateIn(BaseModel):
    zone_id: int | str | None = None
    roi_id: int | None = None
    action: str
    count: float | None = None
    actor: str | None = None


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT PostGIS_Version()")
        count = conn.execute("SELECT COUNT(*) AS n FROM zones").fetchone()["n"]
    return {"ok": True, "zones": count}


@app.get("/zones")
def zones():
    with connect() as conn:
        return service.feature_collection(service.list_zones(conn))


@app.get("/zones/nearby")
def zones_nearby(lat: float, lng: float, limit: int = 10):
    with connect() as conn:
        return service.nearby(conn, lat, lng, limit)


@app.get("/zones/{ident}")
def zone_detail(ident: str):
    def run(conn):
        zone = service.resolve_zone(conn, ident)
        return service.present_zone(conn, zone["id"])

    return _run(run)


@app.post("/zones/{ident}/status")
async def zone_status(ident: str, body: StatusIn, x_actor: str | None = Header(default=None)):
    payload, events = _run(
        lambda conn: service.set_zone_status(
            conn, ident, body.action, body.reason, body.until, _actor(x_actor, body.actor)
        )
    )
    await _emit(events)
    return payload


@app.post("/zones/{ident}/closures")
async def zone_closure(ident: str, body: ClosureIn, x_actor: str | None = Header(default=None)):
    payload, events = _run(
        lambda conn: service.add_closure(
            conn,
            ident,
            body.starts_at,
            body.reason,
            body.ends_at,
            body.grace_min,
            _actor(x_actor, body.actor),
        )
    )
    await _emit(events)
    return payload


@app.post("/ingest/occupancy")
async def ingest_occupancy(items: list[OccupancyIn], x_worker_key: str = Header(default="")):
    _guard_worker(x_worker_key)
    stored = []

    def run(conn):
        events: list[dict] = []
        payloads = []
        for item in items:
            ident = item.zone_code or item.zone_id
            payload, occ_events = service.apply_occupancy(
                conn,
                ident,
                item.occupied,
                source=item.source or "cctv",
                occupied_2w=item.occupied_2w,
                confidence=item.confidence,
            )
            payloads.append(payload)
            events.extend(occ_events)
        return payloads, events

    stored, events = _run(run)
    await _emit(events)
    return stored


@app.post("/ingest/violation")
async def ingest_violation(body: ViolationIn, x_worker_key: str = Header(default="")):
    _guard_worker(x_worker_key)

    def run(conn):
        ident = body.zone_code or body.zone_id
        incident = service.create_violation(
            conn,
            ident,
            {"track_id": body.track_id, "dwell_s": body.dwell_s, "class_id": body.class_id, "status": "PENDING_OFFICER_REVIEW"},
            actor="cv-worker",
            actor_type="cv_worker",
        )
        return incident

    incident = _run(run)
    await hub.broadcast({"type": "incident.created", "data": {"id": incident["id"], "state": incident["state"]}})
    return incident


@app.post("/complaints")
async def complaints(body: ComplaintIn):
    result = _run(
        lambda conn: service.file_complaint(
            conn,
            lat=body.lat,
            lng=body.lng,
            category=body.category,
            description=body.description,
            reporter_hash=body.reporter_hash,
            accuracy=body.accuracy,
        )
    )
    await hub.broadcast(
        {
            "type": "complaint.merged" if result["merged"] else "complaint.created",
            "data": {
                "complaint_id": result["complaint_id"],
                "incident_id": result["incident_id"],
                "merged": result["merged"],
                "report_count": result["report_count"],
                "priority": result["priority"],
            },
        }
    )
    return result


@app.get("/incidents")
def incidents(state: str = "open", sort: str = "priority"):
    del sort  # the list is always priority descending; the parameter is accepted for clients
    with connect() as conn:
        return service.list_incidents(conn, state)


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: int):
    return _run(lambda conn: service.present_incident(conn, incident_id))


@app.post("/incidents/{incident_id}/verify")
async def verify(incident_id: int, body: VerifyIn | None = None, x_actor: str | None = Header(default=None)):
    payload = body or VerifyIn()
    incident = _run(
        lambda conn: service.set_incident_state(
            conn,
            incident_id,
            "verified",
            "incident.verify",
            _actor(x_actor, payload.actor),
            payload.note,
            payload.plate,
        )
    )
    await hub.broadcast({"type": "incident.updated", "data": {"id": incident["id"], "state": incident["state"]}})
    return incident


@app.post("/incidents/{incident_id}/dismiss")
async def dismiss(incident_id: int, body: VerifyIn | None = None, x_actor: str | None = Header(default=None)):
    payload = body or VerifyIn()
    incident = _run(
        lambda conn: service.set_incident_state(
            conn,
            incident_id,
            "dismissed",
            "incident.dismiss",
            _actor(x_actor, payload.actor),
            payload.note,
            payload.plate,
        )
    )
    await hub.broadcast({"type": "incident.updated", "data": {"id": incident["id"], "state": incident["state"]}})
    return incident


@app.get("/alerts")
def alerts():
    with connect() as conn:
        return service.list_alerts(conn)


@app.get("/audit")
def audit(entity: str | None = None):
    with connect() as conn:
        return service.list_audit(conn, entity)


@app.get("/analytics/kpis")
def analytics_kpis():
    with connect() as conn:
        return service.kpis(conn)


@app.post("/demo/simulate-vehicle")
async def simulate(body: SimulateIn, x_actor: str | None = Header(default=None)):
    ident = body.zone_id if body.zone_id is not None else body.roi_id
    result = _run(lambda conn: service.simulate_vehicle(conn, ident, body.action, body.count, _actor(x_actor, body.actor)))
    await _emit(result.get("events") or [])
    return result


@app.post("/demo/reset")
async def demo_reset():
    service.reset_database()
    await hub.broadcast({"type": "demo.reset", "data": {}})
    return {"ok": True}


@app.post("/demo/step/{step}")
async def demo_step(step: int, x_actor: str | None = Header(default=None)):
    result = _run(lambda conn: service.run_step(conn, step, _actor(x_actor, None)))
    await _emit(result.get("events") or [])
    return result


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, role: str = "citizen"):
    del role
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)


app.mount("/media", StaticFiles(directory=str(MEDIA)), name="media")
