"""SR-06 추적 중계: PC2 좌표를 PC4 CCTV MCU로 전달.

PC2에게 받는 줄 (TCP 8102)                       명세 2-②
    TRACK,body,face,x1,y1,x2,y2,width,height    현재 PC2 코드(detect_yolov3.py)가 보내는 형식
    T,T,fx1,fy1,fx2,fy2,bx1,by1,bx2,by2         명세표 형식 (얼굴 4 + 몸 4, 미검출 -1)
    T,T,x,y,dx,dy / N,N                          구형
PC4로 보내는 줄 (TCP 8765, pc4_link 공용 연결)     명세 ③
    TRACK,body,face,x1,y1,x2,y2,width,height    대상 없음: TRACK,0,0,-1,-1,-1,-1,width,height

안전장치
    - 3초보다 오래된 좌표는 버림 (늦게 도착한 좌표로 모터가 움직이지 않게)
    - 초당 10번까지만 MCU로 전송 (N 정지 명령은 항상 즉시)
    - T가 1.5초 동안 안 오면 자동으로 N 전송 (추적 끊김 시 모터 정지)
PC4는 TCP(`TRACK_PC4_HOST`/`TRACK_PC4_PORT`) 또는 서버 직결 USB Serial로 선택한다.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

from ..config import settings
from .pc4_link import PC4Link

MAX_AGE_SEC = 3.0
MIN_INTERVAL = 0.1
WATCHDOG_SEC = 1.5


class TrackingBridge:
    def __init__(self, serial_port: str, baud: int, camera_id: str,
                 pc4_host: str = "", pc4_port: int = 8765, link: PC4Link | None = None) -> None:
        self.camera_id = camera_id
        self._serial = None
        self._port, self._baud = serial_port, baud
        # 문 열기 명령과 같은 PC4 연결을 쓴다 (Bridge가 접속을 1개만 받기 때문)
        self.link = link or PC4Link(pc4_host, pc4_port)
        self._pc4_host = self.link.host
        self._frame = (640, 480)
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
        if self._serial:
            return "serial"
        if self.link.enabled:
            return "pc4_tcp" if self.link.connected else "pc4_tcp(연결 대기)"
        return "receive_only"

    def start(self) -> None:
        if self._port:
            import serial
            self._serial = serial.Serial(self._port, self._baud, timeout=0.1, write_timeout=0.2)
        self.link.start()
        self._stop.clear()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _write(self, line: str) -> bool:
        try:
            if self._serial:
                self._serial.write(line.encode("ascii"))
                return True
            return self.link.send(line)
        except Exception:  # noqa: BLE001
            return False

    def _stop_line(self) -> str:
        """대상 없음(모터 정지) 줄. PC4 Arduino(face_tracking_servo.ino)는 TRACK/DOOR 줄만 알아듣고
        'N'은 무시하므로, PC4로 보낼 때는 항상 TRACK 형식을 쓴다. 'N'은 서버 직결 구형 시리얼용."""
        w, h = self._frame
        return f"TRACK,0,0,-1,-1,-1,-1,{w},{h}\n" if (self._last_bbox or not self._serial) else "N\n"

    def close(self) -> None:
        self._stop.set()
        if self._serial or self._pc4_host:
            self._write(self._stop_line())
        if self._serial:
            self._serial.close()
        self.link.close()

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
            if self._last_bbox:
                self._frame = (obs["frame_width"], obs["frame_height"])
            now = time.monotonic()
            if obs["state"] == "T" and now - self._last_sent < MIN_INTERVAL:
                return {"accepted": True, "forwarded": False, "reason": "rate_limited"}
            if not self._serial and not self._pc4_host:
                return {"accepted": True, "forwarded": False, "reason": "serial_not_configured"}
            if obs.get("x1") is not None:
                line = (f"TRACK,{int(obs['body_detected'])},{int(obs['face_detected'])},"
                        f"{obs['x1']},{obs['y1']},{obs['x2']},{obs['y2']},"
                        f"{obs['frame_width']},{obs['frame_height']}\n")
            elif obs["state"] == "N":                  # 구형 PC2의 'N,N' → 정지 줄
                line = self._stop_line()
            else:
                line = f"T,{obs['x']},{obs['y']},{obs['dx']},{obs['dy']}\n"
            if not self._write(line):
                return {"accepted": True, "forwarded": False, "reason": "pc4_unavailable"}
            self._last_sent = now
            self._last_t = now if obs["state"] == "T" else 0.0
            return {"accepted": True, "forwarded": True, "reason": "sent"}

    def _watchdog(self) -> None:
        while not self._stop.wait(0.2):
            with self._lock:
                if self._last_t and time.monotonic() - self._last_t > WATCHDOG_SEC:
                    try:
                        self._write(self._stop_line())
                    except Exception:
                        pass
                    self._last_t = 0.0


def parse_pc2_line(raw: str) -> dict:
    """PC2 TCP 한 줄 → 관측값. 형식은 파일 맨 위 설명 참고."""
    parts = raw.strip().split(",")
    if len(parts) == 10 and parts[0] in ("T", "N") and parts[1] in ("T", "N"):
        return _parse_face_body_line(parts)
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


def _parse_face_body_line(parts: list[str]) -> dict:
    """명세 2-②: 'T,T,fx1,fy1,fx2,fy2,bx1,by1,bx2,by2' (몸 T/N, 얼굴 T/N, 얼굴 박스, 몸 박스, 미검출 -1).
    따라갈 박스는 얼굴이 있으면 얼굴, 없으면 몸. 화면 크기는 TRACK_FRAME_WIDTH/HEIGHT."""
    try:
        nums = [int(p) for p in parts[2:]]
    except ValueError as exc:
        raise ValueError("숫자 형식 오류") from exc
    body, face = parts[0] == "T", parts[1] == "T"
    w, h = settings.track_frame_width, settings.track_frame_height
    face_box, body_box = nums[:4], nums[4:]
    box = face_box if face and -1 not in face_box else body_box
    if not body or -1 in box:
        return {"state": "N", "face_detected": False, "body_detected": 0,
                "x1": -1, "y1": -1, "x2": -1, "y2": -1, "frame_width": w, "frame_height": h}
    x1, y1, x2, y2 = box
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if not (x1 < x2 and y1 < y2):
        raise ValueError("Bounding Box 범위 오류")
    return {"state": "T", "face_detected": face, "body_detected": 1,
            "x1": x1, "y1": y1, "x2": x2, "y2": y2, "frame_width": w, "frame_height": h}


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


pc4 = PC4Link(settings.track_pc4_host, settings.track_pc4_port)   # 추적·문 열기 공용 연결
bridge = TrackingBridge(
    settings.track_serial_port,
    settings.track_serial_baud,
    settings.track_camera_id,
    link=pc4,
)
