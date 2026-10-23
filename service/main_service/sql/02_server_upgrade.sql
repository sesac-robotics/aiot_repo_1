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

-- v2: 역할 (환자 Patient / 직원 Staff / 관리자 Admin). 관리자 GUI(ui/gui/Current)가 쓰는 역할을 그대로 저장
ALTER TABLE hcc.persons DROP CONSTRAINT IF EXISTS persons_person_type_check;
ALTER TABLE hcc.persons ADD CONSTRAINT persons_person_type_check
    CHECK (person_type IN ('PATIENT', 'STAFF', 'VISITOR', 'ADMIN'));
CREATE SEQUENCE IF NOT EXISTS hcc.staff_seq;     -- 직원 S0001, S0002 ...
CREATE SEQUENCE IF NOT EXISTS hcc.admin_seq;     -- 관리자 A0001 ...

-- v2: 문 열기 명령 번호. PC4로 "DOOR,OPEN,<request_id>"를 보내고 ACK/EVENT를 이 번호로 맞춘다 (명세 ⑥)
CREATE SEQUENCE IF NOT EXISTS hcc.door_request_seq;
ALTER TABLE hcc.door_commands ADD COLUMN IF NOT EXISTS request_id BIGINT UNIQUE;
