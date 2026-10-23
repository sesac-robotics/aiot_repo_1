"""GUI 실시간 알림 (WebSocket /ws).

서버 어디서든 `publish("door_state", {...})` 한 줄이면 접속한 모든 GUI에 전송된다.
메시지 형식: {"type": "...", "ts": "ISO8601", "data": {...}}

type 목록 (추가하면 README 표에도 적을 것)
    door_state     문 상태 변경          (SR-04)
    face_result    얼굴 식별 결과        (SR-05)
    face_registered 얼굴 등록 완료       (SR-02)
    fall_alert     낙상 알람 발생        (SR-03)
    alert_update   알람 확인/해제        (SR-03)
    door_command   문 열기 명령 결과     (SR-04)  PENDING → ACCEPTED → (문 상태는 door_state)
    patient_changed 사람 등록·수정·삭제  (SR-02)

WebSocket 말고 다른 통로(GUI 이벤트 수신기 HTTP 8765 등)도 add_listener()로 같은 이벤트를 받는다.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket


class EventHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listeners: list = []

    def add_listener(self, fn) -> None:
        """fn(event_type, data)가 모든 이벤트마다 호출된다. 오래 걸리는 일은 하지 말 것(큐에 넣기만)."""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def _broadcast(self, text: str) -> None:
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                self._clients.discard(ws)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """동기 함수(일반 API 핸들러)에서도 호출 가능."""
        for fn in self._listeners:
            try:
                fn(event_type, data)
            except Exception:  # noqa: BLE001  다른 통로 오류가 WebSocket 전송을 막지 않게
                pass
        if self._loop is None or not self._clients:
            return
        text = json.dumps({"type": event_type, "ts": datetime.now(timezone.utc).isoformat(),
                           "data": data}, ensure_ascii=False, default=str)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._loop.create_task(self._broadcast(text))
        else:
            asyncio.run_coroutine_threadsafe(self._broadcast(text), self._loop)


hub = EventHub()
publish = hub.publish
