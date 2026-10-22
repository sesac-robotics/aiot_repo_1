"""SR-06 추적 중계: 추적 좌표를 받아 CCTV MCU(Arduino, USB Serial)로 전달.

MCU로 보내는 줄 형식 (기존 팀 규격 유지)
    T,<x>,<y>,<dx>,<dy>\\n     대상 추적 중
    N\\n                       대상 없음 → 모터 정지

안전장치
    - 3초보다 오래된 좌표는 버림 (늦게 도착한 좌표로 모터가 움직이지 않게)
    - 초당 10번까지만 MCU로 전송 (N 정지 명령은 항상 즉시)
    - T가 1.5초 동안 안 오면 자동으로 N 전송 (추적 끊김 시 모터 정지)
TRACK_SERIAL_PORT가 비어 있으면 수신만 하고 전송하지 않는다.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

from ..config import settings

MAX_AGE_SEC = 3.0
MIN_INTERVAL = 0.1
WATCHDOG_SEC = 1.5


class TrackingBridge:
    def __init__(self, serial_port: str, baud: int, camera_id: str) -> None:
        self.camera_id = camera_id
        self._serial = None
        self._port, self._baud = serial_port, baud
        self._lock = threading.Lock()
        self._last_ts: datetime | None = None
        self._last_sent = 0.0
        self._last_t = 0.0
        self.received = 0
        self.last: dict | None = None
        self._last_bbox = False
        self._stop = threading.Event()

    @property
    def mode(self) -> str:
        return "serial" if self._serial else "receive_only"

    def start(self) -> None:
        if self._port:
            import serial
            self._serial = serial.Serial(self._port, self._baud, timeout=0.1, write_timeout=0.2)
            threading.Thread(target=self._watchdog, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        if self._serial:
            try:
                self._serial.write(
                    (b"TRACK,0,0,-1,-1,-1,-1,640,480\n"
                     if self._last_bbox else b"N\n")
                )
            finally:
                self._serial.close()

    def forward(self, obs: dict) -> dict:
        """obs: camera_id, timestamp(datetime), state, x, y, dx, dy"""
        if obs["camera_id"] != self.camera_id:
            return {"accepted": False, "forwarded": False, "reason": "unknown_camera"}
        age = (datetime.now(timezone.utc) - obs["timestamp"]).total_seconds()
        if abs(age) > MAX_AGE_SEC:
            return {"accepted": False, "forwarded": False, "reason": "stale_timestamp"}
        with self._lock:
            if self._last_ts and obs["timestamp"] <= self._last_ts:
                return {"accepted": False, "forwarded": False, "reason": "out_of_order"}
            self._last_ts = obs["timestamp"]
            self.received += 1
            self.last = {k: obs.get(k) for k in (
                "camera_id", "timestamp", "state", "x", "y", "dx", "dy",
                "body_detected", "face_detected", "x1", "y1", "x2", "y2",
                "frame_width", "frame_height")}
            self._last_bbox = obs.get("x1") is not None
            now = time.monotonic()
            if obs["state"] == "T" and now - self._last_sent < MIN_INTERVAL:
                return {"accepted": True, "forwarded": False, "reason": "rate_limited"}
            if not self._serial:
                return {"accepted": True, "forwarded": False, "reason": "serial_not_configured"}
            if obs.get("x1") is not None:
                line = (f"TRACK,{int(obs['body_detected'])},{int(obs['face_detected'])},"
                        f"{obs['x1']},{obs['y1']},{obs['x2']},{obs['y2']},"
                        f"{obs['frame_width']},{obs['frame_height']}\n")
            else:
                line = ("N\n" if obs["state"] == "N"
                        else f"T,{obs['x']},{obs['y']},{obs['dx']},{obs['dy']}\n")
            self._serial.write(line.encode())
            self._last_sent = now
            self._last_t = now if obs["state"] == "T" else 0.0
            return {"accepted": True, "forwarded": True, "reason": "sent"}

    def _watchdog(self) -> None:
        while not self._stop.wait(0.2):
            with self._lock:
                if self._last_t and time.monotonic() - self._last_t > WATCHDOG_SEC:
                    try:
                        self._serial.write(
                            (b"TRACK,0,0,-1,-1,-1,-1,640,480\n"
                             if self._last_bbox else b"N\n")
                        )
                    except Exception:
                        pass
                    self._last_t = 0.0


def parse_pc2_line(raw: str) -> dict:
    """PC2 TCP 한 줄 → 관측값.  'T,T,x,y,dx,dy' (얼굴 있음) / 'T,N,...' (얼굴 없음) / 'N,N'"""
    parts = raw.strip().split(",")
    if len(parts) == 9 and parts[0] == "TRACK":
        try:
            body, face, x1, y1, x2, y2, width, height = (int(p) for p in parts[1:])
        except ValueError as exc:
            raise ValueError("TRACK 숫자 형식 오류") from exc
        if body not in (0, 1) or face not in (0, 1) or face > body:
            raise ValueError("TRACK 상태 오류")
        if not (1 <= width <= 4096 and 1 <= height <= 4096):
            raise ValueError("TRACK 화면 크기 오류")
        if body == 0 and (x1, y1, x2, y2) != (-1, -1, -1, -1):
            raise ValueError("대상 없음 좌표 오류")
        if body and not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError("TRACK Bounding Box 범위 오류")
        return {"state": "T" if body else "N", "face_detected": bool(face),
                "body_detected": body, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "frame_width": width, "frame_height": height}
    if parts == ["N", "N"]:
        return {"state": "N", "face_detected": False}
    if len(parts) != 6 or parts[0] != "T" or parts[1] not in ("T", "N"):
        raise ValueError(f"알 수 없는 추적 줄: {raw!r}")
    x, y, dx, dy = (int(p) for p in parts[2:])
    if not (0 <= x <= 8192 and 0 <= y <= 8192 and -1000 <= dx <= 1000 and -1000 <= dy <= 1000):
        raise ValueError("좌표 범위 초과")
    return {"state": "T", "face_detected": parts[1] == "T", "x": x, "y": y, "dx": dx, "dy": dy}


class PC2TCPReceiver:
    """PC2가 TCP로 보내는 추적 줄을 받아 bridge.forward()로 넘긴다."""

    def __init__(self, bridge: TrackingBridge, port: int) -> None:
        self.bridge, self.port = bridge, port
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "0.0.0.0", self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer) -> None:
        try:
            while line := await reader.readline():
                try:
                    obs = parse_pc2_line(line.decode("ascii", errors="replace"))
                except ValueError:
                    continue                      # 잘못된 줄은 버리고 연결은 유지
                obs.update(camera_id=self.bridge.camera_id, timestamp=datetime.now(timezone.utc))
                self.bridge.forward(obs)
        finally:
            writer.close()


bridge = TrackingBridge(settings.track_serial_port, settings.track_serial_baud, settings.track_camera_id)
