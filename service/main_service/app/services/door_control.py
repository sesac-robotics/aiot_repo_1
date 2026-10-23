"""SR-04 문 열기 명령: 서버 → PC4 Bridge → Arduino.        명세 ⑥

언제 보내나
    - 얼굴 인식 결과 출입 권한이 있는 사람(직원·관리자)일 때   source=FACE_AUTH   (case 2)
    - 관리자 GUI의 OPEN 버튼                                  source=GUI         (case 3)
    환자(case 1)·모르는 얼굴은 보내지 않는다.

흐름
    1. hcc.door_commands에 PENDING으로 기록, request_id(정수) 발급
    2. PC4로 "DOOR,OPEN,<request_id>\\n" 전송 (전송 실패 → ERROR / PC4_UNAVAILABLE)
    3. PC4 응답
         ACK  OPEN_RECEIVED   → ACCEPTED
         EVENT OPEN_COMPLETED → 문 상태 OPEN 기록 + GUI에 door OPEN
         EVENT CLOSE_COMPLETED→ 문 상태 CLOSED 기록 + GUI에 door CLOSED (문은 Arduino가 스스로 닫음)
         ERROR <이유>         → ERROR (예: DOOR_BUSY, SERIAL_UNAVAILABLE)
    4. DOOR_CMD_TIMEOUT_SEC(기본 5초) 안에 ACK/EVENT가 없으면 TIMEOUT. 재전송은 하지 않는다
       (늦게 도착한 OPEN이 엉뚱한 때 문을 여는 것을 막기 위해. Arduino도 같은 request_id는 무시함).
"""
from __future__ import annotations

import logging
import threading
import time

from ..config import settings
from ..db import get_conn
from ..events import publish
from .door_receiver import record_state
from .pc4_link import PC4Link

log = logging.getLogger("tsafe.door_control")


class DoorController:
    def __init__(self, link: PC4Link) -> None:
        self.link = link
        self._pending: dict[int, dict] = {}       # request_id → {command_id, door_id, sent_at}
        self._last_open: dict[str, float] = {}    # person_id → 마지막으로 연 시각 (중복 열기 방지)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        link.add_listener(self.on_feedback)

    def start(self) -> None:
        self._stop.clear()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._timeout_watch, name="door-timeout", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ 명령
    def open(self, door_id: str, source: str, person_id: str | None = None,
             identification_event_id: str | None = None) -> dict:
        """문 열기 요청. 결과는 비동기로 오므로 여기서는 요청 기록만 돌려준다."""
        if person_id and source == "FACE_AUTH":
            with self._lock:
                last = self._last_open.get(person_id, 0.0)
                if time.monotonic() - last < settings.door_open_cooldown_sec:
                    return {"status": "SKIPPED", "reason": "cooldown"}
                self._last_open[person_id] = time.monotonic()

        with get_conn() as conn:
            if not conn.execute("SELECT 1 FROM hcc.doors WHERE door_id = %s", (door_id,)).fetchone():
                conn.execute("INSERT INTO hcc.doors (door_id) VALUES (%s)", (door_id,))
            row = conn.execute("""
                INSERT INTO hcc.door_commands (door_id, requested_by, request_source, action, request_id)
                VALUES (%s, %s, %s, 'OPEN', nextval('hcc.door_request_seq'))
                RETURNING command_id, request_id, requested_at""",
                (door_id, person_id, source)).fetchone()
        rid = row["request_id"]
        sent = self.link.send(f"DOOR,OPEN,{rid}\n")
        if not sent:
            self._finish(rid, "ERROR", error="PC4_UNAVAILABLE", command_id=row["command_id"])
            status = "ERROR"
        else:
            with self._lock:
                self._pending[rid] = {"command_id": row["command_id"], "door_id": door_id,
                                      "sent_at": time.monotonic(), "accepted": False}
            status = "PENDING"
        result = {"command_id": str(row["command_id"]), "request_id": rid, "door_id": door_id,
                  "source": source, "status": status}
        if status == "ERROR":
            result["error"] = "PC4_UNAVAILABLE"
        publish("door_command", result)
        return result

    # ------------------------------------------------------------ PC4 응답
    def on_feedback(self, item: dict) -> None:
        if item["type"] == "DOOR":              # Bridge가 문 상태를 그대로 넘겨줄 때
            record_state(settings.default_door_id, item["state"], "pc4")
            return
        rid, result = item["request_id"], item["result"]
        with self._lock:
            info = self._pending.get(rid)
        if item["type"] == "ACK" and result == "OPEN_RECEIVED":
            self._finish(rid, "ACCEPTED", keep=True)
        elif item["type"] == "EVENT" and result == "OPEN_COMPLETED":
            self._finish(rid, "ACCEPTED", state="OPEN")
            record_state(info["door_id"] if info else settings.default_door_id, "OPEN", "pc4", force=True)
        elif item["type"] == "EVENT" and result == "CLOSE_COMPLETED":
            record_state(info["door_id"] if info else settings.default_door_id, "CLOSED", "pc4", force=True)
        elif item["type"] == "ERROR":
            self._finish(rid, "ERROR", error=result)

    def _finish(self, rid: int, ack: str, *, state: str | None = None, error: str | None = None,
                keep: bool = False, command_id=None) -> None:
        with self._lock:
            info = self._pending.get(rid) if keep else self._pending.pop(rid, None)
            if info and keep:
                info["accepted"] = True
        with get_conn() as conn:
            row = conn.execute("""
                UPDATE hcc.door_commands
                   SET ack_status = %s, responded_at = COALESCE(responded_at, NOW()),
                       reported_state = COALESCE(%s, reported_state), error_code = COALESCE(%s, error_code)
                 WHERE request_id = %s RETURNING command_id, door_id""",
                (ack, state, error, rid)).fetchone()
        if row and (ack != "ACCEPTED" or state):
            publish("door_command", {"command_id": str(row["command_id"]), "request_id": rid,
                                     "door_id": row["door_id"], "status": ack,
                                     "door_state": state, "error": error})

    def _timeout_watch(self) -> None:
        while not self._stop.wait(0.5):
            now = time.monotonic()
            with self._lock:
                expired = [rid for rid, i in self._pending.items()
                           if now - i["sent_at"] > settings.door_cmd_timeout_sec]
            for rid in expired:
                with self._lock:
                    info = self._pending.pop(rid, None)
                if info is None:
                    continue
                try:
                    if info["accepted"]:    # ACK는 왔는데 OPEN_COMPLETED가 안 옴
                        self._finish(rid, "ACCEPTED", error="NO_COMPLETED_EVENT")
                    else:
                        self._finish(rid, "TIMEOUT", error="NO_RESPONSE")
                except Exception:  # noqa: BLE001
                    log.exception("문 명령 시간 초과 처리 오류")

    def status(self) -> dict:
        with self._lock:
            return {"pending": len(self._pending)}


def _make() -> DoorController:
    from .tracking import pc4           # 추적과 같은 PC4 연결
    return DoorController(pc4)


controller = _make()
