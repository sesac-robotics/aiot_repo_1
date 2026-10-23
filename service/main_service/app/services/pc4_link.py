"""PC4 Bridge(device/cctv_tracking_servo/pc4_bridge.py)와의 TCP 연결 하나.

pc4_bridge.py는 접속을 1개만 받는다. 그래서 추적(TRACK)과 문 열기(DOOR,OPEN)를
이 연결 하나로 같이 보낸다.

보내는 줄 (서버 → PC4, 한 줄 텍스트, 끝은 \\n)         명세 ③, ⑥
    TRACK,body,face,x1,y1,x2,y2,width,height
    DOOR,OPEN,<request_id>

받는 줄 (PC4 → 서버)                                    명세 ⑥ (Bridge가 JSON으로 바꿔서 보냄)
    {"type":"ACK","request_id":102,"result":"OPEN_RECEIVED"}
    {"type":"EVENT","request_id":102,"result":"OPEN_COMPLETED"}   / CLOSE_COMPLETED
    {"type":"ERROR","request_id":102,"result":"DOOR_BUSY"}        / SERIAL_UNAVAILABLE
    텍스트 "ACK,102,OPEN_RECEIVED" 와 "DOOR,1" / "DOOR,0"(문 상태)도 받는다 (Bridge가 나중에 그대로 넘길 경우 대비)

연결이 끊기면 2초마다 다시 접속한다. 보내기 실패는 False로 알려 줄 뿐 쌓아 두지 않는다
(끊긴 동안의 OPEN을 나중에 실행하면 위험하기 때문. Bridge도 같은 원칙).
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Callable

log = logging.getLogger("tsafe.pc4")
RECONNECT_SEC = 2.0
MAX_LINE = 512


def parse_feedback(raw: str) -> dict | None:
    """PC4가 보낸 한 줄 → {"type": ACK|EVENT|ERROR|DOOR, ...}. 모르는 줄은 None."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            item = json.loads(text)
        except ValueError:
            return None
        if not isinstance(item, dict):
            return None
        if item.get("type") in ("ACK", "EVENT", "ERROR") and isinstance(item.get("request_id"), int) \
                and isinstance(item.get("result"), str):
            return {"type": item["type"], "request_id": item["request_id"], "result": item["result"]}
        if item.get("type") == "DOOR" and str(item.get("state")) in ("0", "1", "OPEN", "CLOSED"):
            return {"type": "DOOR", "state": "OPEN" if str(item["state"]) in ("1", "OPEN") else "CLOSED"}
        return None
    parts = text.split(",")
    if len(parts) == 3 and parts[0] in ("ACK", "EVENT", "ERROR") and parts[1].isdigit():
        return {"type": parts[0], "request_id": int(parts[1]), "result": parts[2]}
    if len(parts) == 2 and parts[0] == "DOOR" and parts[1] in ("0", "1"):
        return {"type": "DOOR", "state": "OPEN" if parts[1] == "1" else "CLOSED"}
    return None


class PC4Link:
    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._listeners: list[Callable[[dict], None]] = []
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.received = 0
        self._last_tracking = None  # (monotonic timestamp, body/face/dx/dy)

    @property
    def enabled(self) -> bool:
        return bool(self.host)

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    # ------------------------------------------------------------ 연결
    def start(self) -> None:
        if self.enabled and (self._thread is None or not self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(target=self._reader, name="pc4-link", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._drop()

    def _connect(self) -> socket.socket | None:
        with self._lock:
            if self._sock is None and self.enabled:
                try:
                    s = socket.create_connection((self.host, self.port), timeout=1.0)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    s.settimeout(0.5)
                    self._sock = s
                    self.last_error = None
                    log.info("PC4 Bridge 연결됨 %s:%s", self.host, self.port)
                except OSError as exc:
                    self.last_error = f"connect: {exc}"
            return self._sock

    def _drop(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            self._last_tracking = None

    def send(self, line: str) -> bool:
        """한 줄 보내기. 연결이 없으면 한 번 접속을 시도하고, 실패하면 False."""
        if not self.enabled:
            return False
        sock = self._sock or self._connect()
        if sock is None:
            return False
        try:
            with self._lock:
                sock.sendall(line.encode("ascii"))
                if line.startswith("TRACK,"):
                    state = self._v_tracking_state(line)
                    if state is not None:
                        self._last_tracking = (time.monotonic(), state)
            return True
        except OSError as exc:
            self.last_error = f"send: {exc}"
            self._drop()
            return False

    @staticmethod
    def _v_tracking_state(line: str):
        parts = line.strip().split(",")
        if len(parts) != 9 or parts[0] != "TRACK":
            return None
        try:
            body, face, x1, y1, x2, y2, width, height = map(int, parts[1:])
        except ValueError:
            return None
        if body not in (0, 1) or face not in (0, 1) or face > body or min(width, height) <= 0:
            return None
        if not body:
            return (0, 0, 0, 0)
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return None
        short_edge = min(width, height)
        dx = round(((x1 + x2) - width) * 1000 / (2 * short_edge))
        dy = round(((y1 + y2) - height) * 1000 / (2 * short_edge))
        return body, face, max(-1000, min(1000, dx)), max(-1000, min(1000, dy))

    def send_open_pulse(self) -> bool:
        """Send one 0→1→0 pulse for a V bridge, preserving fresh tracking values.

        The three complete lines share the tracking socket lock. Failed pulses are
        never queued or replayed on reconnect. The final zero rearms the Arduino.
        """
        if not self.enabled:
            return False
        sock = self._sock or self._connect()
        if sock is None:
            return False
        try:
            with self._lock:
                state = (0, 0, 0, 0)
                if self._last_tracking is not None:
                    created, latest = self._last_tracking
                    if time.monotonic() - created <= 1.5:
                        state = latest
                body, face, dx, dy = state
                prefix = f"V,{body},{face},{dx},{dy},"
                sock.sendall((prefix + "0\n" + prefix + "1\n" + prefix + "0\n").encode("ascii"))
            return True
        except OSError as exc:
            self.last_error = f"send: {exc}"
            self._drop()
            return False

    # ------------------------------------------------------------ 받기
    def _reader(self) -> None:
        buf = b""
        while not self._stop.is_set():
            sock = self._sock or self._connect()
            if sock is None:
                self._stop.wait(RECONNECT_SEC)
                continue
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                self.last_error = f"recv: {exc}"
                self._drop()
                buf = b""
                continue
            if not chunk:                       # Bridge가 연결을 끊음
                self.last_error = "closed by PC4"
                self._drop()
                buf = b""
                self._stop.wait(RECONNECT_SEC)
                continue
            buf += chunk
            if len(buf) > 8192:
                buf = b""
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                if len(raw) > MAX_LINE:
                    continue
                item = parse_feedback(raw.decode("ascii", errors="replace"))
                if item is None:
                    continue
                self.received += 1
                for fn in list(self._listeners):
                    try:
                        fn(item)
                    except Exception:  # noqa: BLE001
                        log.exception("PC4 응답 처리 오류")

    def status(self) -> dict:
        return {"host": self.host or None, "port": self.port, "connected": self.connected,
                "received": self.received, "last_error": self.last_error}
