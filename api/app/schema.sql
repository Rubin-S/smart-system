SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid();

DROP EXTENSION IF EXISTS postgis CASCADE;
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO public;
CREATE EXTENSION postgis;

CREATE TABLE zones (
    id               SERIAL PRIMARY KEY,
    code             TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    city             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    boundary         GEOGRAPHY(POLYGON, 4326) NOT NULL,
    centroid         GEOGRAPHY(POINT, 4326) NOT NULL,
    capacity_car     INT NOT NULL DEFAULT 0,
    capacity_2w      INT NOT NULL DEFAULT 0,
    pcu_weight_2w    REAL NOT NULL DEFAULT 0.25,
    status           TEXT NOT NULL DEFAULT 'unknown',
    manual_override  TEXT,
    override_reason  TEXT,
    override_until   TIMESTAMPTZ,
    occupied_now     DOUBLE PRECISION NOT NULL DEFAULT 0,
    occ_updated_at   TIMESTAMPTZ,
    near_emergency   BOOLEAN NOT NULL DEFAULT FALSE,
    fee_info         TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX zones_boundary_gix ON zones USING GIST (boundary);
CREATE INDEX zones_centroid_gix ON zones USING GIST (centroid);

CREATE TABLE zone_closures (
    id         SERIAL PRIMARY KEY,
    zone_id    INT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    starts_at  TIMESTAMPTZ NOT NULL,
    ends_at    TIMESTAMPTZ,
    kind       TEXT NOT NULL DEFAULT 'closed',
    reason     TEXT,
    created_by TEXT,
    grace_min  INT NOT NULL DEFAULT 15
);

CREATE TABLE cameras (
    id           SERIAL PRIMARY KEY,
    code         TEXT UNIQUE NOT NULL,
    rtsp_url     TEXT NOT NULL,
    name         TEXT NOT NULL,
    location     GEOGRAPHY(POINT, 4326),
    coverage     GEOGRAPHY(POLYGON, 4326),
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE camera_rois (
    id          SERIAL PRIMARY KEY,
    camera_id   INT NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    zone_id     INT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    polygon_px  JSONB NOT NULL
);

CREATE TABLE occupancy_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    zone_id     INT NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    ts          TIMESTAMPTZ NOT NULL,
    occupied    DOUBLE PRECISION NOT NULL,
    occupied_2w DOUBLE PRECISION NOT NULL DEFAULT 0,
    capacity    INT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'cctv',
    confidence  REAL
);
CREATE INDEX occ_zone_ts_idx ON occupancy_snapshots (zone_id, ts DESC);

CREATE TABLE incidents (
    id               BIGSERIAL PRIMARY KEY,
    type             TEXT NOT NULL,
    state            TEXT NOT NULL DEFAULT 'open',
    location         GEOGRAPHY(POINT, 4326) NOT NULL,
    zone_id          INT REFERENCES zones(id),
    severity         SMALLINT NOT NULL DEFAULT 2,
    blocks_emergency BOOLEAN NOT NULL DEFAULT FALSE,
    report_count     INT NOT NULL DEFAULT 0,
    cctv_status      TEXT NOT NULL DEFAULT 'none',
    priority         DOUBLE PRECISION NOT NULL DEFAULT 0,
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity    TIMESTAMPTZ NOT NULL DEFAULT now(),
    vehicle_plate    TEXT,
    evidence_url     TEXT,
    echallan_ref     TEXT
);
CREATE INDEX incidents_loc_gix ON incidents USING GIST (location);
CREATE INDEX incidents_open_idx ON incidents (state, priority DESC);

CREATE TABLE complaints (
    id            BIGSERIAL PRIMARY KEY,
    incident_id   BIGINT REFERENCES incidents(id),
    category      TEXT NOT NULL,
    description   TEXT,
    location      GEOGRAPHY(POINT, 4326) NOT NULL,
    gps_accuracy_m REAL,
    photo_url     TEXT,
    reporter_hash TEXT NOT NULL,
    merge_score   DOUBLE PRECISION,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX complaints_loc_gix ON complaints USING GIST (location);

CREATE TABLE incident_evidence (
    id          BIGSERIAL PRIMARY KEY,
    incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    url         TEXT NOT NULL,
    camera_id   INT REFERENCES cameras(id),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE alerts (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,
    zone_id     INT REFERENCES zones(id),
    incident_id BIGINT REFERENCES incidents(id),
    message     TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'info',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    dedupe_key  TEXT UNIQUE
);

CREATE TABLE officers (
    id        SERIAL PRIMARY KEY,
    badge_no  TEXT UNIQUE NOT NULL,
    name      TEXT NOT NULL,
    role      TEXT NOT NULL DEFAULT 'officer'
);

CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    actor_type TEXT NOT NULL,
    actor_id   TEXT,
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL,
    reason     TEXT,
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
