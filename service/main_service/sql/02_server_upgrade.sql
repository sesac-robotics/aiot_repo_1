-- 서버 v1이 쓰는 추가분. 팀 원본 db/schema.sql(v0.1)은 건드리지 않고 이 파일만 덧붙인다.
-- 여러 번 실행해도 안전하다(idempotent). 서버가 켜질 때마다 자동으로 실행된다.
CREATE SEQUENCE IF NOT EXISTS hcc.patient_seq;                          -- 환자 코드 P0001, P0002 ... 발급
ALTER TABLE hcc.patient_profiles ALTER COLUMN blood_type TYPE VARCHAR(10); -- 'AB+' 등 (구 migration 001)

-- SR-04 Door History: Door MCU가 보낸 문 상태(1=OPEN, 0=CLOSED)가 바뀔 때마다 한 줄
CREATE TABLE IF NOT EXISTS hcc.door_state_log (
    log_id       BIGSERIAL PRIMARY KEY,
    door_id      VARCHAR(32) NOT NULL REFERENCES hcc.doors(door_id) ON DELETE RESTRICT,
    state        VARCHAR(16) NOT NULL CHECK (state IN ('OPEN','CLOSED')),
    source_addr  VARCHAR(64),
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_door_state_log_time ON hcc.door_state_log(door_id, received_at DESC);
