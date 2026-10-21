"""가짜 PC2 / 낙상 모듈. 실물 AI 없이 서버 API를 시험할 때 쓴다. (표준 라이브러리만 사용)

    python tools/fake_pc2.py register 2          # 2번(GUI에서 등록한 번호)의 얼굴 특징벡터 등록
    python tools/fake_pc2.py identify 2          # 2번과 비슷한 얼굴 → MATCHED
    python tools/fake_pc2.py identify stranger       # 처음 보는 얼굴 → UNKNOWN
    python tools/fake_pc2.py track                   # 추적 좌표 5개 전송
    python tools/fake_pc2.py fall                    # 낙상 이벤트 1건

옵션: --server http://192.168.0.10:8101   --token <DEVICE_TOKEN>
가짜 특징벡터는 이름으로 고정 생성하므로 같은 이름이면 항상 같은 벡터가 나온다.
"""
from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fake_vector(name: str, noise: float = 0.0) -> list[float]:
    rnd = random.Random(name)
    v = [rnd.gauss(0, 1) for _ in range(512)]
    if noise:
        n = random.Random()
        v = [x + n.gauss(0, noise) for x in v]
    return v


def post(path: str, body: dict) -> None:
    req = urllib.request.Request(ARGS.server + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {ARGS.token}"} if ARGS.token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            print(r.status, json.dumps(json.loads(r.read()), ensure_ascii=False, indent=1))
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["register", "identify", "track", "fall"])
    ap.add_argument("who", nargs="?", default="2")
    ap.add_argument("--server", default="http://127.0.0.1:8101")
    ap.add_argument("--token", default="")
    ARGS = ap.parse_args()

    if ARGS.cmd == "register":
        post("/api/v1/face/templates", {"patient_id": ARGS.who, "model": "buffalo_l",
                                        "embedding": fake_vector(ARGS.who), "images_used": 5})
    elif ARGS.cmd == "identify":
        post("/api/v1/face/identify", {"event_id": uuid.uuid4().hex[:16], "camera_id": "FACE01",
                                       "timestamp": now(), "status": "OK", "model": "buffalo_l",
                                       "embedding": fake_vector(ARGS.who, noise=0.3)})
    elif ARGS.cmd == "track":
        for i in range(5):
            post("/api/v1/tracking/observations", {"camera_id": "CAM01", "timestamp": now(), "state": "T",
                                                   "x": 300 + i * 10, "y": 240, "dx": 10, "dy": 0})
            time.sleep(0.15)
        post("/api/v1/tracking/observations", {"camera_id": "CAM01", "timestamp": now(), "state": "N"})
    elif ARGS.cmd == "fall":
        post("/api/v1/falls", {"event_id": uuid.uuid4().hex[:16], "camera_id": "CAM01", "occurred_at": now(),
                               "event_type": "FALL", "confidence": 0.91, "bbox_xyxy": [100, 120, 260, 400]})
