"""낙상감지팀용: 낙상을 감지했을 때 서버로 알리는 코드. (표준 라이브러리만 사용)

이 파일의 send_fall() 함수를 run_fall_detection.py에 복사해서 쓰면 된다.

    from fall_sender_example import FallSender
    sender = FallSender("http://192.168.0.10:8101")      # 서버 PC 주소
    ...
    if is_fall:                                          # 내 모델이 낙상이라고 판단했을 때
        sender.send_fall(camera_id="CAM01", confidence=0.91, bbox_xyxy=[x1, y1, x2, y2], track_id=str(tid))

단독 시험:  python examples/fall_sender_example.py --server http://127.0.0.1:8101

- 같은 사람이 계속 쓰러져 있으면 프레임마다 보내지 말고, cooldown(기본 10초) 동안 한 번만 보낸다.
- 서버가 꺼져 있어도 감지 루프가 멈추지 않도록 타임아웃 2초, 실패하면 False만 돌려준다.
- event_id가 같으면 서버는 알람을 하나만 만든다(재전송해도 안전).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone


class FallSender:
    def __init__(self, server: str = "http://127.0.0.1:8101", token: str = "", cooldown_sec: float = 10.0):
        self.url = server.rstrip("/") + "/api/v1/falls"
        self.token = token
        self.cooldown_sec = cooldown_sec
        self._last_sent: dict[str, float] = {}       # (camera, track)별 마지막 전송 시각

    def send_fall(self, camera_id: str = "CAM01", confidence: float | None = None,
                  bbox_xyxy: list[float] | None = None, track_id: str | None = None,
                  event_type: str = "FALL") -> bool:
        key = f"{camera_id}:{track_id}"
        now = time.monotonic()
        if now - self._last_sent.get(key, -1e9) < self.cooldown_sec:
            return False                             # 방금 보낸 낙상이면 생략
        body = {
            "event_id": uuid.uuid4().hex,            # 이벤트마다 새 ID
            "camera_id": camera_id,                  # 서버에 등록된 카메라 (기본 CAM01)
            "occurred_at": datetime.now(timezone.utc).isoformat(),   # 시간대 포함 필수
            "event_type": event_type,                # FALL / COLLAPSE / SUSPECTED_FALL
            "confidence": None if confidence is None else round(float(confidence), 4),
            "bbox_xyxy": None if bbox_xyxy is None else [float(v) for v in bbox_xyxy],
            "track_id": track_id,
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=2) as r:
                res = json.loads(r.read())
            self._last_sent[key] = now
            print(f"[FALL SENT] alert_id={res.get('alert_id')} duplicate={res.get('duplicate')}", flush=True)
            return True
        except urllib.error.HTTPError as e:
            print(f"[FALL REJECTED] {e.code} {e.read().decode(errors='replace')}", flush=True)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[FALL WAIT] 서버 연결 실패: {e}", flush=True)
        return False


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8101")
    ap.add_argument("--camera", default="CAM01")
    a = ap.parse_args()
    s = FallSender(a.server)
    s.send_fall(camera_id=a.camera, confidence=0.91, bbox_xyxy=[100, 120, 260, 400], track_id="1")
    s.send_fall(camera_id=a.camera, confidence=0.95, bbox_xyxy=[100, 120, 260, 400], track_id="1")  # 쿨다운으로 생략됨
