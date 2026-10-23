"""관리자 GUI(ui/gui/Current)에 실시간 이벤트 보내기.

GUI는 HTTP 이벤트 수신기(event_bridge.py, 기본 http://127.0.0.1:8765)를 연다.
서버는 아래 이벤트를 POST /events (Authorization: Bearer <GUI 토큰>)로 보낸다.

    서버 이벤트                    → GUI 이벤트 (ui/gui/EVENT_INTEGRATION_KO.md 규격)
    face_result (얼굴 인식)         → {"type":"recognition","person_id","name","role","allowed"}
    door_state  (문 열림/닫힘)      → {"type":"door","state":"OPEN"|"CLOSED"}
    fall_alert  (낙상 알람 생성)    → {"type":"fall","detected":true}
    alert_update RESOLVED (해제)    → {"type":"fall","detected":false}

토큰: GUI_EVENT_TOKEN, 없으면 같은 PC의 GUI가 만든 ui/gui/Current/data/gui_event.token을 읽는다.
GUI가 꺼져 있어도 서버는 멈추지 않는다(보내기 실패는 버림, 한 번만 로그).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..events import hub

log = logging.getLogger("tsafe.gui")


def to_gui_event(event_type: str, data: dict) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    if event_type == "face_result" and data.get("result") in ("MATCHED", "UNKNOWN"):
        matched = data["result"] == "MATCHED"
        return {"type": "recognition",
                "person_id": data.get("person_id") or "",
                "name": (data.get("name") or data.get("person_id") or "Unknown") if matched else "Unknown",
                "role": data.get("role") if matched and data.get("role") in ("Patient", "Staff", "Admin") else "Unknown",
                "allowed": bool(data.get("allowed")),
                "event_id": f"face-{data['event_id']}", "timestamp": now}
    if event_type == "door_state" and data.get("state") in ("OPEN", "CLOSED"):
        return {"type": "door", "state": data["state"], "timestamp": now}
    if event_type == "door_state" and data.get("online") is False:
        return {"type": "door", "state": "UNKNOWN", "timestamp": now}
    if event_type == "fall_alert":
        # ``alert_id`` lets the GUI acknowledge the exact central alert.  It
        # is deliberately carried as event data, not used as the GUI's local
        # log/database key.
        return {"type": "fall", "detected": True, "alert_id": str(data.get("alert_id") or ""),
                "status": "ACTIVE", "event_id": f"fall-{data.get('alert_id')}", "timestamp": now}
    if event_type == "alert_update" and data.get("status") == "RESOLVED":
        return {"type": "fall", "detected": False, "alert_id": str(data.get("alert_id") or ""),
                "status": "RESOLVED", "event_id": f"fall-clear-{data.get('alert_id')}", "timestamp": now}
    return None


class GuiNotifier:
    def __init__(self) -> None:
        self.url = settings.gui_event_url.rstrip("/") + "/events"
        self._q: queue.Queue = queue.Queue(maxsize=200)
        self._thread: threading.Thread | None = None
        self.sent = 0
        self.failed = 0
        self.last_error: str | None = None

    def _token(self) -> str:
        if settings.gui_event_token:
            return settings.gui_event_token
        try:
            return Path(settings.gui_event_token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def start(self) -> None:
        if settings.gui_event_enabled and self._thread is None:
            hub.add_listener(self.on_event)
            self._thread = threading.Thread(target=self._worker, name="gui-notify", daemon=True)
            self._thread.start()

    def on_event(self, event_type: str, data: dict) -> None:
        item = to_gui_event(event_type, data)
        if item is not None:
            try:
                self._q.put_nowait(item)
            except queue.Full:
                self.failed += 1

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            token = self._token()
            req = urllib.request.Request(self.url, data=json.dumps(item, ensure_ascii=False).encode("utf-8"),
                                         method="POST", headers={"Content-Type": "application/json",
                                                                 "Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(req, timeout=2) as r:
                    r.read()
                self.sent += 1
                self.last_error = None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                self.failed += 1
                msg = f"{type(exc).__name__}: {exc}"
                if msg != self.last_error:
                    log.warning("GUI 이벤트 전송 실패 (%s): %s", self.url, msg)
                self.last_error = msg

    def status(self) -> dict:
        return {"enabled": settings.gui_event_enabled, "url": self.url, "token": bool(self._token()),
                "sent": self.sent, "failed": self.failed, "last_error": self.last_error}


notifier = GuiNotifier()
