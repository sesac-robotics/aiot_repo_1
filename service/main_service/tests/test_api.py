"""SR별 시나리오 시험. 실제 PostgreSQL(DATABASE_URL)이 필요하다.

    docker compose up -d db
    pytest -q
주의: 시험 시작 때 환자·이벤트 테이블을 비운다. 실제 데이터가 있는 DB에서 돌리지 말 것.
"""
from __future__ import annotations

import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from app.main import app
from app.services.door_receiver import receiver as door_receiver
from tools.fake_pc2 import fake_vector


def now(offset: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


@pytest.fixture(scope="module")
def client():
    with get_conn() as conn:
        conn.execute("""TRUNCATE hcc.access_events, hcc.door_commands, hcc.identification_events, hcc.alerts,
                        hcc.fall_events, hcc.face_templates, hcc.rfid_cards, hcc.patient_profiles,
                        hcc.persons CASCADE""")
        conn.execute("ALTER SEQUENCE hcc.patient_seq RESTART")
        conn.execute("TRUNCATE hcc.door_state_log")
        conn.execute("DELETE FROM hcc.doors WHERE door_id <> 'door_01'")
        conn.execute("UPDATE hcc.doors SET reported_state='UNKNOWN', state_source='UNKNOWN'")
    door_receiver.port = 0                       # 빈 포트 아무거나
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health").json()
    assert r["server"] == "ok" and r["db"] == "ok"


# ------------------------------------------------------------ SR-02
def test_patient_crud(client):
    r = client.post("/api/v1/patients", json={"name": " 김환자 ", "gender": "F", "birth_date": "1950-03-01",
                                              "blood_type": "AB+", "room_number": "101", "can_open": True})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["person_id"] == "P0001" and p["name"] == "김환자" and p["age"] >= 75 and p["face_registered"] is False

    assert client.post("/api/v1/patients", json={"name": "이환자"}).json()["person_id"] == "P0002"
    assert len(client.get("/api/v1/patients").json()["items"]) == 2

    r = client.patch("/api/v1/patients/P0001", json={"weight_kg": 52.5, "medications": "아스피린"})
    assert r.status_code == 200 and r.json()["weight_kg"] == 52.5 and r.json()["name"] == "김환자"

    assert client.post("/api/v1/patients", json={"name": "   "}).status_code == 422
    assert client.post("/api/v1/patients", json={"name": "a", "birth_date": "2999-01-01"}).status_code == 422
    assert client.post("/api/v1/patients", json={"name": "a", "unknown_field": 1}).status_code == 422
    assert client.get("/api/v1/patients/P9999").status_code == 404


def test_card_assign(client):
    assert client.put("/api/v1/patients/P0001/card", json={"card_uid": "04a1b2c3"}).json()["card_uid"] == "04A1B2C3"
    assert client.get("/api/v1/patients/P0001").json()["card_uid"] == "04A1B2C3"
    assert client.put("/api/v1/patients/P0002/card", json={"card_uid": "04A1B2C3"}).status_code == 409
    assert client.put("/api/v1/patients/P0001/card", json={"card_uid": "zz!!"}).status_code == 422


def test_face_register(client):
    r = client.post("/api/v1/face/templates", json={"patient_id": "P0001", "embedding": fake_vector("P0001")})
    assert r.status_code == 200, r.text
    assert client.post("/api/v1/face/templates",
                       json={"patient_id": "P0002", "embedding": fake_vector("P0002")}).status_code == 200
    assert client.get("/api/v1/patients/P0001").json()["face_registered"] is True
    assert client.post("/api/v1/face/templates",
                       json={"patient_id": "P0001", "embedding": [0.1] * 100}).status_code == 422


def test_face_register_face_team_format(client):
    """얼굴팀 기존 등록 JSON(patient_001, 추가 필드 포함)을 그대로 받는다."""
    from app.config import settings
    body = {"patient_id": "patient_009", "model": "buffalo_l", "embedding_dim": 512,
            "embedding": fake_vector("patient_009"), "images_used": 3,
            "created_at": "2026-10-16T09:15:07+00:00", "images": ["a.jpg"], "failed_images": []}
    r = client.post("/api/v1/face/templates", json=body, headers={"Authorization": "Bearer old-token"})
    assert r.status_code == 200 and r.json()["patient_created"] is True
    assert client.get("/api/v1/patients/patient_009").json()["name"] is None   # 이름은 GUI에서 채움
    assert client.post("/api/v1/face/templates", json=body).json()["patient_created"] is False

    object.__setattr__(settings, "face_auto_create_patient", False)   # 설정값은 frozen이라 시험에서만 직접 변경
    try:
        assert client.post("/api/v1/face/templates",
                           json={**body, "patient_id": "patient_010"}).status_code == 404
    finally:
        object.__setattr__(settings, "face_auto_create_patient", True)

    client.delete("/api/v1/patients/patient_009")
    assert client.post("/api/v1/face/templates", json=body).status_code == 409   # 삭제된 환자


# ------------------------------------------------------------ SR-05
def _identify(client, who, **kw):
    body = {"event_id": uuid.uuid4().hex[:16], "camera_id": "FACE01", "timestamp": now(),
            "status": "OK", "embedding": fake_vector(who, noise=0.3)}
    body.update(kw)
    return client.post("/api/v1/face/identify", json=body)


def test_face_identify(client):
    r = _identify(client, "P0001").json()
    assert r["result"] == "MATCHED" and r["person_id"] == "P0001" and r["similarity"] > 0.8 and r["can_open"]
    assert "door_opened" not in r                # 문 개폐는 Door MCU가 판단
    r = _identify(client, "P0002").json()
    assert r["result"] == "MATCHED" and r["person_id"] == "P0002" and r["can_open"] is False
    r = _identify(client, "stranger").json()
    assert r["result"] == "UNKNOWN" and r["person_id"] is None

    eid = uuid.uuid4().hex[:16]
    assert _identify(client, "P0001", event_id=eid).json()["duplicate"] is False
    assert _identify(client, "P0001", event_id=eid).json()["duplicate"] is True

    r = client.post("/api/v1/face/identify", json={"event_id": "x1", "timestamp": now(), "status": "NoFace"})
    assert r.json()["result"] == "NOFACE"
    assert client.post("/api/v1/face/identify", json={"event_id": "x2", "timestamp": now(),
                                                      "status": "OK"}).status_code == 422
    assert client.post("/api/v1/face/identify", json={"event_id": "x3", "timestamp": "2026-10-21T10:00:00",
                                                      "status": "NoFace"}).status_code == 422


# ------------------------------------------------------------ SR-04
def _door_socket():
    port = door_receiver._server.sockets[0].getsockname()[1]
    return socket.create_connection(("127.0.0.1", port), timeout=2)


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_door_state_from_mcu(client):
    with _door_socket() as s:
        s.sendall(b"1\n")
        assert _wait(lambda: client.get("/api/v1/doors/door_01").json()["reported_state"] == "OPEN")
        door = client.get("/api/v1/doors/door_01").json()
        assert door["state_source"] == "MCU_REPORT" and door["online"] is True
        s.sendall(b"1\n1\n")                       # 같은 값 반복 → 이력 안 늘어남
        s.sendall(b"garbage\n")                     # 잘못된 줄 → 무시, 연결 유지
        s.sendall(b"0\n")
        assert _wait(lambda: client.get("/api/v1/doors/door_01").json()["reported_state"] == "CLOSED")
        hist = client.get("/api/v1/doors/door_01/history").json()["items"]
        assert [h["state"] for h in hist[:2]] == ["CLOSED", "OPEN"] and len(hist) == 2
    assert _wait(lambda: client.get("/api/v1/doors/door_01").json()["online"] is False)


def test_door_with_id_autoregisters(client):
    with _door_socket() as s:
        s.sendall(b"door_02,1\n")
        assert _wait(lambda: client.get("/api/v1/doors/door_02").status_code == 200)
    assert client.get("/api/v1/doors/door_02").json()["reported_state"] == "OPEN"
    assert client.post("/api/v1/doors/door_01/commands", json={"action": "OPEN"}).status_code in (404, 405)


def test_websocket_door_event(client):
    with client.websocket_connect("/ws") as ws, _door_socket() as s:
        s.sendall(b"1\n")
        msg = ws.receive_json()
        assert msg["type"] == "door_state" and msg["data"] == {"door_id": "door_01", "state": "OPEN", "online": True}


# ------------------------------------------------------------ SR-03
def test_fall_and_alert(client):
    eid = uuid.uuid4().hex[:16]
    body = {"event_id": eid, "camera_id": "CAM01", "occurred_at": now(), "confidence": 0.9}
    r = client.post("/api/v1/falls", json=body)
    assert r.status_code == 201 and r.json()["status"] == "ACTIVE" and r.json()["location"] == "room_101"
    assert client.post("/api/v1/falls", json=body).json()["duplicate"] is True
    alert_id = r.json()["alert_id"]
    assert client.post(f"/api/v1/alerts/{alert_id}/acknowledge", json={}).json()["status"] == "ACKNOWLEDGED"
    assert client.post(f"/api/v1/alerts/{alert_id}/resolve", json={"note": "확인"}).json()["status"] == "RESOLVED"
    assert client.post(f"/api/v1/alerts/{alert_id}/resolve", json={}).status_code == 409
    assert client.post("/api/v1/falls", json={**body, "event_id": "z", "camera_id": "NOCAM"}).status_code == 409


# ------------------------------------------------------------ SR-06
def test_tracking(client):
    ok = client.post("/api/v1/tracking/observations",
                     json={"camera_id": "CAM01", "timestamp": now(), "state": "T", "x": 1, "y": 2, "dx": 3, "dy": 4})
    assert ok.status_code == 200 and ok.json()["reason"] == "serial_not_configured"
    stale = client.post("/api/v1/tracking/observations", json={"camera_id": "CAM01", "timestamp": now(-10), "state": "N"})
    assert stale.status_code == 409
    bad = client.post("/api/v1/tracking/observations", json={"camera_id": "CAM01", "timestamp": now(), "state": "T"})
    assert bad.status_code == 422


# ------------------------------------------------------------ SR-02 삭제 → SR-05 반영
def test_deactivate_removes_face(client):
    assert client.delete("/api/v1/patients/P0001").json()["face_deleted"] is True
    assert _identify(client, "P0001").json()["person_id"] != "P0001"
    assert client.delete("/api/v1/patients/P0001").status_code == 404
    with get_conn() as conn:
        assert conn.execute("SELECT count(*) AS n FROM hcc.face_templates WHERE person_id='P0001'").fetchone()["n"] == 0
