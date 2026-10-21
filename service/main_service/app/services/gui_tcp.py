"""GUI 모니터링 화면(ui/hcc_gui/patient_monitoring.py)용 TCP 텍스트 통로 (기본 포트 8080).

GUI가 원래 쓰던 형식 그대로, 서버 이벤트를 한 줄 텍스트로 보낸다.
    FALL                          낙상 알람 발생
    NORMAL                        알람 해제
    DOOR_IS_OPEN / DOOR_IS_CLOSE  Door MCU가 보낸 문 상태
    RFID_IN:<역할>:<번호>:<이름>   문 앞 얼굴 식별 성공 (예: RFID_IN:Patient:2:pikachu)

GUI가 보내는 줄(DOOR_OPEN / DOOR_CLOSE 버튼)은 기록만 하고 문을 움직이지 않는다.
문 개폐는 Door MCU가 판단하기로 정했기 때문 (README 참고).
"""
from __future__ import annotations

import asyncio
import logging

from ..events import hub

log = logging.getLogger("tsafe.gui_tcp")


def to_line(event_type: str, data: dict) -> str | None:
    """서버 이벤트 → GUI 텍스트 한 줄. 보낼 필요 없는 이벤트면 None."""
    if event_type == "fall_alert":
        return "FALL"
    if event_type == "alert_update" and data.get("status") == "RESOLVED":
        return "NORMAL"
    if event_type == "door_state" and data.get("online", True):
        return {"OPEN": "DOOR_IS_OPEN", "CLOSED": "DOOR_IS_CLOSE"}.get(data.get("state"))
    if event_type == "face_result" and data.get("result") == "MATCHED":
        name = (data.get("name") or "").replace(":", " ")
        return f"RFID_IN:{data.get('role', 'Patient')}:{data.get('p_num')}:{name}"
    return None


class GuiTCPServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self.clients: set[asyncio.StreamWriter] = set()
        self._server: asyncio.base_events.Server | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.received: list[str] = []          # GUI가 보낸 명령 (최근 20개, /health 확인용)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(self._handle, "0.0.0.0", self.port)
        hub.add_listener(self._on_event)

    async def stop(self) -> None:
        for w in list(self.clients):
            w.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def _on_event(self, event_type: str, data: dict) -> None:
        line = to_line(event_type, data)
        if line and self._loop and self.clients:
            payload = (line + "\n").encode("utf-8")
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                self._loop.create_task(self._send(payload))
            else:
                asyncio.run_coroutine_threadsafe(self._send(payload), self._loop)

    async def _send(self, payload: bytes) -> None:
        for w in list(self.clients):
            try:
                w.write(payload)
                await w.drain()
            except Exception:  # noqa: BLE001  끊긴 GUI는 목록에서 뺀다
                self.clients.discard(w)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
        try:
            while line := await reader.readline():
                cmd = line.decode("utf-8", errors="replace").strip()
                if cmd:
                    log.info("GUI 명령 수신(문 제어는 MCU 담당이라 기록만): %s", cmd)
                    self.received = (self.received + [cmd])[-20:]
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()
