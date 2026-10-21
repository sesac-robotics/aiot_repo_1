-- HCC PostgreSQL DB v0.1 | 2026-10-19
-- Requires PostgreSQL 13+ and installed pgvector extension. Execute on NEW HCC database.
-- Idempotent CREATE IF NOT EXISTS is for a fresh DB; it is NOT a schema migration mechanism.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS hcc;

-- Shared identity. name can initially be NULL when only face JSON has been delivered;
-- GUI registration must supply the proper name before the record is operational.
CREATE TABLE IF NOT EXISTS hcc.persons (
    person_id       VARCHAR(40) PRIMARY KEY,
    person_type     VARCHAR(20) NOT NULL CHECK (person_type IN ('PATIENT', 'STAFF', 'VISITOR')),
    name            VARCHAR(100),
    gender          VARCHAR(20),
    birth_date      DATE,
    can_open        BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hcc.patient_profiles (
    person_id       VARCHAR(40) PRIMARY KEY REFERENCES hcc.persons(person_id) ON DELETE RESTRICT,
    height_cm       NUMERIC(5,1) CHECK (height_cm > 0),
    weight_kg       NUMERIC(5,1) CHECK (weight_kg > 0),
    blood_type      VARCHAR(3),
    medical_history TEXT,
    medications     TEXT,
    room_number     VARCHAR(20)
);

-- One representative registered template per person / model, updatable on re-enrollment.
CREATE TABLE IF NOT EXISTS hcc.face_templates (
    template_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id             VARCHAR(40) NOT NULL REFERENCES hcc.persons(person_id) ON DELETE RESTRICT,
    model                 VARCHAR(50) NOT NULL,
    embedding             vector(512) NOT NULL,
    embedding_normalized  BOOLEAN NOT NULL DEFAULT TRUE,
    images_used           SMALLINT NOT NULL DEFAULT 1 CHECK (images_used >= 1),
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (person_id, model)
);

CREATE TABLE IF NOT EXISTS hcc.rfid_cards (
    card_uid      VARCHAR(64) PRIMARY KEY,
    person_id     VARCHAR(40) NOT NULL REFERENCES hcc.persons(person_id) ON DELETE RESTRICT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (valid_until IS NULL OR valid_until > valid_from)
);

CREATE TABLE IF NOT EXISTS hcc.cameras (
    camera_id       VARCHAR(32) PRIMARY KEY,
    location        VARCHAR(100),
    camera_type     VARCHAR(20) NOT NULL CHECK (camera_type IN ('CCTV', 'FACE', 'TRACKING', 'OTHER')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_seen_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS hcc.doors (
    door_id          VARCHAR(32) PRIMARY KEY,
    location         VARCHAR(100),
    reported_state   VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN'
                     CHECK (reported_state IN ('OPEN', 'CLOSED', 'UNKNOWN')),
    state_source     VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN'
                     CHECK (state_source IN ('SENSOR', 'MCU_REPORT', 'SIMULATED', 'UNKNOWN')),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hcc.fall_events (
    event_id      VARCHAR(64) PRIMARY KEY,
    person_id     VARCHAR(40) REFERENCES hcc.persons(person_id) ON DELETE SET NULL,
    camera_id     VARCHAR(32) NOT NULL REFERENCES hcc.cameras(camera_id) ON DELETE RESTRICT,
    track_id      VARCHAR(64),
    event_type    VARCHAR(24) NOT NULL CHECK (event_type IN ('COLLAPSE','FALL','SUSPECTED_FALL')),
    occurred_at   TIMESTAMPTZ NOT NULL,
    location      VARCHAR(100),
    confidence    REAL CHECK (confidence >= 0 AND confidence <= 1),
    bbox_xyxy     JSONB CHECK (bbox_xyxy IS NULL OR (jsonb_typeof(bbox_xyxy) = 'array' AND jsonb_array_length(bbox_xyxy) = 4)),
    received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hcc.alerts (
    alert_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fall_event_id     VARCHAR(64) NOT NULL UNIQUE REFERENCES hcc.fall_events(event_id) ON DELETE RESTRICT,
    status            VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('ACTIVE','ACKNOWLEDGED','RESOLVED')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at   TIMESTAMPTZ,
    resolved_at       TIMESTAMPTZ,
    handled_by        VARCHAR(40) REFERENCES hcc.persons(person_id) ON DELETE SET NULL,
    resolution_note   TEXT,
    CHECK (acknowledged_at IS NULL OR acknowledged_at >= started_at),
    CHECK (resolved_at IS NULL OR resolved_at >= started_at)
);

CREATE TABLE IF NOT EXISTS hcc.identification_events (
    event_id        VARCHAR(64) PRIMARY KEY,
    person_id       VARCHAR(40) REFERENCES hcc.persons(person_id) ON DELETE SET NULL,
    method          VARCHAR(12) NOT NULL CHECK (method IN ('FACE','RFID')),
    camera_id       VARCHAR(32) REFERENCES hcc.cameras(camera_id) ON DELETE SET NULL,
    reader_id       VARCHAR(32),
    result          VARCHAR(24) NOT NULL,
    similarity      REAL CHECK (similarity BETWEEN -1 AND 1),
    model           VARCHAR(50),
    occurred_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS hcc.door_commands (
    command_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    door_id            VARCHAR(32) NOT NULL REFERENCES hcc.doors(door_id) ON DELETE RESTRICT,
    requested_by       VARCHAR(40) REFERENCES hcc.persons(person_id) ON DELETE SET NULL,
    request_source     VARCHAR(20) NOT NULL CHECK (request_source IN ('GUI','FACE_AUTH','RFID_AUTH','AUTO')),
    action             VARCHAR(12) NOT NULL CHECK (action IN ('OPEN','CLOSE','STATUS')),
    ack_status         VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                       CHECK (ack_status IN ('PENDING','ACCEPTED','ERROR','TIMEOUT')),
    reported_state     VARCHAR(16) CHECK (reported_state IN ('OPEN','CLOSED','UNKNOWN')),
    physical_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    requested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    responded_at       TIMESTAMPTZ,
    error_code         VARCHAR(64),
    CHECK (responded_at IS NULL OR responded_at >= requested_at)
);

CREATE TABLE IF NOT EXISTS hcc.access_events (
    access_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    identification_event_id  VARCHAR(64) REFERENCES hcc.identification_events(event_id) ON DELETE SET NULL,
    person_id                VARCHAR(40) REFERENCES hcc.persons(person_id) ON DELETE SET NULL,
    door_id                  VARCHAR(32) NOT NULL REFERENCES hcc.doors(door_id) ON DELETE RESTRICT,
    decision                 VARCHAR(12) NOT NULL CHECK (decision IN ('ALLOW','DENY')),
    reason                   VARCHAR(100),
    command_id               UUID REFERENCES hcc.door_commands(command_id) ON DELETE SET NULL,
    occurred_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_face_templates_active ON hcc.face_templates(model, is_active);
CREATE INDEX IF NOT EXISTS idx_rfid_cards_person ON hcc.rfid_cards(person_id);
CREATE INDEX IF NOT EXISTS idx_fall_events_time ON hcc.fall_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_fall_events_person ON hcc.fall_events(person_id);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON hcc.alerts(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_identification_time ON hcc.identification_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_access_time ON hcc.access_events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_door_commands_time ON hcc.door_commands(door_id, requested_at DESC);

-- Deliberately no HNSW index yet: a small number of registered faces benefits from exact search.
-- Add vector_cosine_ops HNSW after enough data and performance measurements warrant it.

CREATE OR REPLACE FUNCTION hcc.touch_person_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_person_updated_at ON hcc.persons;
CREATE TRIGGER trg_person_updated_at
BEFORE UPDATE ON hcc.persons
FOR EACH ROW EXECUTE FUNCTION hcc.touch_person_updated_at();
COMMIT;
