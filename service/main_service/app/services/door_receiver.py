"""SR-04 출입문 상태 수신 (TCP).

문을 열고 닫는 판단은 Door MCU가 직접 한다. 서버는 결과만 받는다.

받는 형식 (한 줄 텍스트, 끝은 \\n)
    1             문 열림   (문 ID 생략 시 DOOR_ID 설정값, 기본 door_01)
    0             문 닫힘
    door_01,1     문이 여러 개일 때 ID를 앞에 붙임
그 외의 줄은 무시하고 연결은 유지한다.

처리
    - 상태가 바뀔 때만 DB에 기록 (같은 값이 반복되면 무시)
    - doors.reported_state 갱신, door_state_log에 이력 추가
    - GUI에 WebSocket door_state 전송
    - 연결이 끊기면 online=false로 GUI에 알림
"""
from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..db import get_conn
from ..events import publish

log = logging.getLogger("tsafe.door")
STATE = {"1": "OPEN", "0": "CLOSED"}


def parse_line(raw: str) -> tuple[str, str]:
    """'1' / '0' / 'door_01,1' → (door_id, 'OPEN'|'CLOSED')"""
    text = raw.strip()
    door_id, _, value = text.rpartition(",")
    door_id = door_id.strip() or settings.default_door_id
    if value.strip() not in STATE or len(door_id) > 32:
        raise ValueError(f"알 수 없는 문 상태 줄: {raw!r}")
    return door_id, STATE[value.strip()]


def record_state(door_id: str, state: str, peer: str | None = None, force: bool = False) -> bool:
    """상태가 바뀌었으면 DB에 기록하고 True. 같은 상태면 False.
    force=True: 문 열기 명령으로 실제 동작한 결과(PC4 EVENT)라서 같은 상태여도 기록·알림."""
    with get_conn() as conn:
        row = conn.execute("SELECT reported_state FROM hcc.doors WHERE door_id = %s FOR UPDATE",
                           (door_id,)).fetchone()
        if row is None:   # 처음 보는 문은 자동 등록
            conn.execute("INSERT INTO hcc.doors (door_id) VALUES (%s)", (door_id,))
        elif row["reported_state"] == state and not force:
            return False
        conn.execute("""UPDATE hcc.doors SET reported_state = %s, state_source = 'MCU_REPORT', updated_at = NOW()
                        WHERE door_id = %s""", (state, door_id))
        conn.execute("INSERT INTO hcc.door_state_log (door_id, state, source_addr) VALUES (%s, %s, %s)",
                     (door_id, state, peer))
    publish("door_state", {"door_id": door_id, "state": state, "online": True})
    return True


class DoorStateReceiver:
    def __init__(self, port: int) -> None:
        self.port = port
        self._server: asyncio.base_events.Server | None = None
        self.connected: dict[str, str] = {}      # door_id -> 접속 주소 (마지막으로 보고한 연결 기준)
        self.clients = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "0.0.0.0", self.port)
        log.info("문 상태 TCP 수신 대기: %s", self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = "%s:%s" % writer.get_extra_info("peername")[:2]
        self.clients += 1
        seen: set[str] = set()
        try:
            while line := await reader.readline():
                try:
                    door_id, state = parse_line(line.decode("ascii", errors="replace"))
                except ValueError as exc:
                    log.warning("%s (%s)", exc, peer)
                    continue
                seen.add(door_id)
                self.connected[door_id] = peer
                await asyncio.to_thread(record_state, door_id, state, peer)
        finally:
            self.clients -= 1
            for door_id in seen:
                if self.connected.get(door_id) == peer:
                    self.connected.pop(door_id, None)
                    publish("door_state", {"door_id": door_id, "state": None, "online": False})
            writer.close()


receiver = DoorStateReceiver(settings.door_tcp_port)
