"""v2 시험: 시퀀스 다이어그램·명세표 기준 (역할별 출입, PC4 문 열기, 실시간 프레임·낙상, GUI 이벤트).

가짜 PC4 Bridge(TCP)와 가짜 GUI 이벤트 수신기(HTTP)를 띄워 서버가 실제로 무엇을 보내는지 확인한다.
주의: test_api.py처럼 시험 시작 때 테이블을 비운다.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_conn
from app.main import app
from app.services.door_control import controller
from app.services.door_receiver import receiver as door_receiver
from app.services.gui_notify import notifier
from app.services.tracking import bridge, parse_pc2_line, pc4
from tools.fake_pc2 import fake_vector

JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body" * 10 + b"\xff\xd9"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class FakePC4:
    """pc4_bridge.py 흉내: 받은 줄을 기록하고, DOOR,OPEN이면 Arduino처럼 ACK → EVENT JSON을 돌려준다."""

    def __init__(self, reply=True):
        self.lines: list[str] = []
        self.reply = reply
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.conn = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self.conn, _ = self.srv.accept()
        buf = b""
        while True:
            try:
                chunk = self.conn.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode()
                self.lines.append(line)
                if self.reply and line.startswith("DOOR,OPEN,"):
                    rid = int(line.split(",")[2])
                    self.send({"type": "ACK", "request_id": rid, "result": "OPEN_RECEIVED"})
                    self.send({"type": "EVENT", "request_id": rid, "result": "OPEN_COMPLETED"})

    def send(self, obj):
        self.conn.sendall((json.dumps(obj) + "\n").encode())

    def close(self):
        try:
            self.conn and self.conn.close()
        finally:
            self.srv.close()


class FakeGUI:
    """ui/gui/Current/event_bridge.py 흉내: POST /events (Bearer 토큰 확인)."""

    def __init__(self, token):
        self.events: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                ok = self.headers.get("Authorization") == f"Bearer {token}" and self.path == "/events"
                if ok:
                    outer.events.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(202 if ok else 401)
                self.end_headers()

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def of(self, typ):
        return [e for e in self.events if e["type"] == typ]


@pytest.fixture(scope="module")
def env():
    with get_conn() as conn:
        conn.execute("""TRUNCATE hcc.access_events, hcc.door_commands, hcc.identification_events, hcc.alerts,
                        hcc.fall_events, hcc.face_templates, hcc.rfid_cards, hcc.patient_profiles,
                        hcc.persons CASCADE""")
        conn.execute("TRUNCATE hcc.door_state_log")
        conn.execute("UPDATE hcc.doors SET reported_state='UNKNOWN', state_source='UNKNOWN'")
    token = "t" * 24
    gui = FakeGUI(token)
    object.__setattr__(settings, "gui_event_token", token)
    notifier.url = gui.url + "/events"
    door_receiver.port = 0
    with TestClient(app) as c:
        yield c, gui
    pc4.close()


# ------------------------------------------------------------ 1. 사람 관리 (역할)
def test_roles(env):
    c, _ = env
    p = c.post("/api/v1/patients", json={"name": "pikachu", "gender": "M", "birth_date": "1950-01-01"}).json()
    s = c.post("/api/v1/persons", json={"role": "Staff", "name": "nurse kim", "gender": "F"}).json()
    a = c.post("/api/v1/persons", json={"role": "Admin", "name": "admin lee"}).json()
    assert (p["person_id"][0], p["role"], p["can_open"]) == ("P", "Patient", False)
    assert (s["person_id"][0], s["role"], s["can_open"]) == ("S", "Staff", True)
    assert (a["person_id"][0], a["role"], a["can_open"]) == ("A", "Admin", True)
    assert [x["role"] for x in c.get("/api/v1/patients?role=Staff").json()["items"]] == ["Staff"]
    assert len(c.get("/api/v1/persons").json()["items"]) == 3
    assert c.get("/api/v1/persons?role=Doctor").status_code == 422
    # 역할 바꾸면 출입 권한도 따라감
    r = c.patch(f"/api/v1/persons/{a['person_id']}", json={"role": "Patient"}).json()
    assert r["role"] == "Patient" and r["can_open"] is False
    c.patch(f"/api/v1/persons/{a['person_id']}", json={"role": "Admin"})
    env_ids = {"patient": p["person_id"], "staff": s["person_id"], "admin": a["person_id"]}
    for k, v in env_ids.items():
        c.post("/api/v1/face/templates", json={"patient_id": v, "embedding": fake_vector(v)})
    test_roles.ids = env_ids


def _identify(c, who):
    return c.post("/api/v1/face/identify", json={"event_id": uuid.uuid4().hex, "camera_id": "FACE01",
                                                  "timestamp": now(), "status": "OK",
                                                  "embedding": fake_vector(who, noise=0.3)}).json()


# ------------------------------------------------------------ 2. 문 출입 (PC4 없음)
def test_access_without_pc4(env):
    c, gui = env
    ids = test_roles.ids
    r = _identify(c, ids["patient"])                              # case 1 환자: 거부
    assert r["result"] == "MATCHED" and r["role"] == "Patient" and r["allowed"] is False and r["door_command"] is None
    r = _identify(c, ids["staff"])                                # case 2 직원: 허용, 그러나 PC4 미연결
    assert r["allowed"] is True and r["door_command"]["status"] == "ERROR"
    assert r["door_command"]["error"] == "PC4_UNAVAILABLE"
    r = _identify(c, "stranger")
    assert r["result"] == "UNKNOWN" and r["role"] == "Unknown" and r["allowed"] is False
    log = c.get("/api/v1/doors/door_01/access").json()["items"]
    assert [(x["decision"], x["reason"]) for x in log] == [      # 최신순
        ("DENY", "unknown_face"), ("ALLOW", "authorized"), ("DENY", "no_permission")]
    assert wait(lambda: len(gui.of("recognition")) >= 3)
    rec = gui.of("recognition")
    assert {(e["role"], e["allowed"]) for e in rec} >= {("Patient", False), ("Staff", True), ("Unknown", False)}


# ------------------------------------------------------------ 2. 문 출입 (PC4 연결)
def test_access_with_pc4(env):
    c, gui = env
    ids = test_roles.ids
    fake = FakePC4()
    pc4.host, pc4.port = "127.0.0.1", fake.port
    bridge._pc4_host = pc4.host
    pc4.start()
    assert wait(lambda: pc4.connected)

    controller._last_open.clear()
    r = _identify(c, ids["staff"])                                # case 2: 직원 → 문 열기
    cmd = r["door_command"]
    assert cmd["status"] == "PENDING" and cmd["source"] == "FACE_AUTH"
    assert wait(lambda: f"DOOR,OPEN,{cmd['request_id']}" in fake.lines)
    assert wait(lambda: c.get("/api/v1/doors/door_01").json()["reported_state"] == "OPEN")
    rows = c.get("/api/v1/doors/door_01/commands").json()["items"]
    assert rows[0]["ack_status"] == "ACCEPTED" and rows[0]["reported_state"] == "OPEN"
    assert rows[0]["requested_by"] == ids["staff"]
    assert wait(lambda: any(e["state"] == "OPEN" for e in gui.of("door")))

    assert _identify(c, ids["staff"])["door_command"]["status"] == "SKIPPED"   # 같은 사람 연달아 → 1번만

    fake.send({"type": "EVENT", "request_id": cmd["request_id"], "result": "CLOSE_COMPLETED"})   # Arduino가 닫음
    assert wait(lambda: c.get("/api/v1/doors/door_01").json()["reported_state"] == "CLOSED")

    n_open = len([e for e in gui.of("door") if e["state"] == "OPEN"])
    r = c.post("/api/v1/doors/door_01/open", json={"staff_id": ids["admin"]}).json()   # case 3: GUI OPEN
    assert r["status"] == "PENDING" and r["source"] == "GUI"
    assert wait(lambda: c.get("/api/v1/doors/door_01").json()["reported_state"] == "OPEN")
    r = c.post("/api/v1/doors/door_01/open", json={}).json()      # 이미 열린 상태에서 또 열어도 GUI에 OPEN 알림
    assert wait(lambda: len([e for e in gui.of("door") if e["state"] == "OPEN"]) >= n_open + 2)

    # 추적: PC2 줄 → PC4로 TRACK 전달 (명세 ③)
    obs = parse_pc2_line("T,T,254,241,434,421,179,202,516,479")
    obs.update(camera_id=bridge.camera_id, timestamp=datetime.now(timezone.utc))
    assert bridge.forward(obs)["forwarded"] is True
    assert wait(lambda: "TRACK,1,1,254,241,434,421,640,480" in fake.lines)
    stop = parse_pc2_line("N,N")                                  # 구형 '대상 없음' → Arduino가 아는 TRACK 정지 줄
    stop.update(camera_id=bridge.camera_id, timestamp=datetime.now(timezone.utc))
    bridge.forward(stop)
    assert wait(lambda: fake.lines[-1] == "TRACK,0,0,-1,-1,-1,-1,640,480")

    # 응답 없는 PC4 → TIMEOUT
    fake.reply = False
    object.__setattr__(settings, "door_cmd_timeout_sec", 0.5)
    r = c.post("/api/v1/doors/door_01/open", json={}).json()
    assert wait(lambda: c.get("/api/v1/doors/door_01/commands").json()["items"][0]["ack_status"] == "TIMEOUT")
    object.__setattr__(settings, "door_cmd_timeout_sec", 5.0)
    assert c.get("/health").json()["pc4"]["connected"] is True


# ------------------------------------------------------------ 3. 실시간 프레임 + 낙상 (명세 3-①②③)
# 낙상 담당 브랜치(feature/fall_detection)의 run_fall_detection.frame_analysis_for_main_service가 만드는 형식 그대로
def _meta(frame_id, new_events=(), cam="camera_1"):
    return {"message_type": "frame_analysis", "frame_id": frame_id,
            "observed_at": "2026-10-22T14:30:00.123+0900",
            "camera": {"camera_id": cam, "location": "ROOM_1"},
            "source_size": [1280, 720], "coordinate_system": "original_frame_pixels",
            "image": {"format": "jpeg", "source_size": [1280, 720], "display_size": [640, 360],
                      "coordinate_system": "original_frame_pixels"},
            "persons": [{"track_id": 3, "bbox_xyxy": [200, 20, 600, 340]}],
            "fall_judgments": [{"track_id": 3, "state": "fall_confirmed" if new_events else "normal"}],
            "event": {"current_states": [], "new_events": list(new_events)}}


def _fall(track, frame_id, cam="camera_1"):
    return {"event_type": "fall_confirmed", "camera_id": cam, "track_id": track,
            "confirmed_frame_id": frame_id, "confirmed_at": "2026-10-22T14:30:00.123+0900",
            "fall_probability": 0.93}


def _post(c, meta, image=JPEG):
    return c.post("/api/v1/live/frame", data={"metadata": json.dumps(meta)},
                  files={"image": ("frame.jpg", image, "image/jpeg")})


def test_live_frame_and_fall(env):
    c, gui = env
    assert c.get("/api/v1/live/frame/camera_1/image").status_code == 404
    r = _post(c, _meta(1))                                          # 평소: 영상+좌표만, 저장 안 함
    assert r.status_code == 201 and r.json()["new_events"] == []
    img = c.get("/api/v1/live/frame/camera_1/image")
    assert img.status_code == 200 and img.content == JPEG and img.headers["content-type"] == "image/jpeg"
    meta = c.get("/api/v1/live/frame/camera_1").json()             # GUI live_frame_client가 읽는 형식
    assert meta["persons"][0]["track_id"] == 3 and meta["source_size"] == [1280, 720] and "received_at" in meta
    snapshot = c.get("/api/v1/live/frame/camera_1/snapshot").json()
    assert snapshot["metadata"]["frame_id"] == 1 and snapshot["jpeg_base64"]
    assert c.get("/api/v1/alerts?status=ACTIVE").json()["items"] == []

    r = _post(c, _meta(2, [_fall(3, 2)])).json()                    # 낙상 확정 → DB + 알람 (카메라 자동 등록)
    assert len(r["new_events"]) == 1 and r["new_events"][0]["duplicate"] is False
    assert _post(c, _meta(3, [_fall(3, 2)])).json()["new_events"][0]["duplicate"] is True   # 같은 확정 재전송
    alerts = c.get("/api/v1/alerts?status=ACTIVE").json()["items"]
    assert len(alerts) == 1 and alerts[0]["camera_id"] == "camera_1" and alerts[0]["location"] == "ROOM_1"
    assert alerts[0]["confidence"] == pytest.approx(0.93)
    assert wait(lambda: any(e["detected"] for e in gui.of("fall")))
    fall = next(e for e in gui.of("fall") if e["detected"])
    assert fall["alert_id"] == alerts[0]["alert_id"] and fall["status"] == "ACTIVE"

    res = c.post("/api/v1/alerts/resolve_active", json={"note": "현장 확인"}).json()   # Emergency release
    assert res["count"] == 1
    assert wait(lambda: any(e["detected"] is False for e in gui.of("fall")))

    assert _post(c, _meta(4), image=b"notjpeg").status_code == 422
    assert c.post("/api/v1/live/frame", data={"metadata": "{bad"},
                  files={"image": ("f.jpg", JPEG, "image/jpeg")}).status_code == 422
    bad = _meta(5, [_fall(3, 5, cam="other")])                      # 이벤트 카메라가 프레임과 다름
    assert _post(c, bad).status_code == 422


def test_gui_token_required(env):
    _, gui = env
    before = len(gui.events)
    object.__setattr__(settings, "gui_event_token", "wrong-token-xxxxxxxx")
    notifier.on_event("door_state", {"door_id": "door_01", "state": "OPEN", "online": True})
    time.sleep(0.5)
    assert len(gui.events) == before and notifier.last_error   # 토큰이 틀리면 GUI가 거부
    object.__setattr__(settings, "gui_event_token", "t" * 24)
