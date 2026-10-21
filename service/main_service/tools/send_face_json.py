"""얼굴팀용: 이미 가지고 있는 JSON 파일을 그대로 서버에 보낸다. (표준 라이브러리만 사용)

    python tools/send_face_json.py db_embeddings/            # 폴더 안 *.json 전부
    python tools/send_face_json.py patient_001.json out/abc.json --server 192.168.0.10:8101

파일 내용으로 자동 구분:
    patient_id 가 있으면 → 등록   POST /api/v1/face/templates
    event_id  가 있으면 → 식별   POST /api/v1/face/identify
식별 JSON의 timestamp에 시간대가 없으면 보내기 전에 +09:00(한국)을 붙인다.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def send(server: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(f"http://{server}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except urllib.error.URLError as e:
        sys.exit(f"서버에 연결할 수 없습니다: {server} ({e.reason})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="JSON 파일 또는 폴더")
    ap.add_argument("--server", default="127.0.0.1:8101")
    a = ap.parse_args()

    files: list[Path] = []
    for p in map(Path, a.paths):
        files += sorted(p.glob("*.json")) if p.is_dir() else [p]
    files.sort(key=lambda f: "event_id" in f.read_text(encoding="utf-8"))   # 등록 먼저, 식별 나중

    for f in files:
        body = json.loads(f.read_text(encoding="utf-8"))
        if "patient_id" in body:
            code, res = send(a.server, "/api/v1/face/templates", body)
            info = f"등록 {body['patient_id']}" + (" (환자 자동 생성)" if res.get("patient_created") else "")
        elif "event_id" in body:
            ts = body.get("timestamp")
            if isinstance(ts, str) and datetime.fromisoformat(ts.replace("Z", "+00:00")).tzinfo is None:
                body["timestamp"] = ts + "+09:00"
            code, res = send(a.server, "/api/v1/face/identify", body)
            info = f"식별 → {res.get('result')} {res.get('person_id') or ''} 유사도={res.get('similarity')}"
        else:
            print(f"[건너뜀] {f.name}: patient_id도 event_id도 없음")
            continue
        print(f"[{code}] {f.name}: {info if code < 300 else res.get('detail')}")


if __name__ == "__main__":
    main()
