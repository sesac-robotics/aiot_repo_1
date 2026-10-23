"""얼굴 JSON 전송. 등록 전 서버 사람 ID를 확인하며 사람을 자동 생성하지 않는다.

사람 등록 → 서버 person_id로 사진 폴더 생성 → face_db_export.py → 이 도구 실행.
예: python tools/send_face_json.py db_embeddings/ --server 127.0.0.1:8101
인증 사용 시 TSAFE_ADMIN_TOKEN(사람 확인), DEVICE_TOKEN(얼굴 전송)을 설정한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


def request(server: str, method: str, path: str, body: dict | None = None,
            token: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        server.rstrip("/") + path, method=method,
        data=None if body is None else json.dumps(body).encode(), headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="JSON 파일 또는 폴더")
    ap.add_argument("--server", default="127.0.0.1:8101")
    ap.add_argument("--token", default=os.getenv("DEVICE_TOKEN") or os.getenv("PC1_EMBEDDING_TOKEN", ""),
                    help="얼굴 API 디바이스 토큰")
    ap.add_argument("--admin-token", default=os.getenv("TSAFE_ADMIN_TOKEN") or os.getenv("ADMIN_TOKEN", ""),
                    help="사람 ID 확인용 관리자 토큰")
    args = ap.parse_args(argv)
    server = args.server if "://" in args.server else "http://" + args.server

    try:
        files = []
        for path in map(Path, args.paths):
            files.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])
        if not files:
            raise ValueError("전송할 JSON 파일이 없습니다")
        records = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in files]
        records.sort(key=lambda item: "event_id" in item[1])

        # 모든 등록 ID를 먼저 검사한다. 잘못된 ID가 있으면 일괄 전송을 시작하지 않는다.
        people = {}
        for path, body in records:
            if "patient_id" not in body:
                continue
            person_id = body["patient_id"]
            if not isinstance(person_id, str) or not person_id:
                raise ValueError(f"{path.name}: patient_id가 비어 있습니다")
            if person_id not in people:
                code, person = request(server, "GET", "/api/v1/patients/" +
                                       urllib.parse.quote(person_id, safe=""), token=args.admin_token)
                if code == 404:
                    raise ValueError(f"{person_id}: 서버에 없는 ID입니다. 사람을 먼저 등록하고 "
                                     "발급된 person_id로 사진 폴더를 만들어 벡터를 다시 추출하세요")
                if code != 200:
                    raise ValueError(f"{person_id}: 사람 확인 실패 HTTP {code}: {person.get('detail')}")
                if person.get("person_id") != person_id or person.get("is_active") is not True:
                    raise ValueError(f"{person_id}: 서버의 활성 사람 ID와 일치하지 않습니다")
                people[person_id] = person
            person = people[person_id]
            print(f"[확인] {person_id}: {person.get('name')} / {person.get('role')} / "
                  f"can_open={person.get('can_open')}")

        failed = False
        for path, body in records:
            if "patient_id" in body:
                endpoint = "/api/v1/face/templates"
                info = f"등록 {body['patient_id']}"
            elif "event_id" in body:
                ts = body.get("timestamp")
                if isinstance(ts, str) and datetime.fromisoformat(ts.replace("Z", "+00:00")).tzinfo is None:
                    body["timestamp"] = ts + "+09:00"
                endpoint = "/api/v1/face/identify"
                info = "식별"
            else:
                raise ValueError(f"{path.name}: patient_id도 event_id도 없습니다")
            code, result = request(server, "POST", endpoint, body, args.token)
            if not 200 <= code < 300:
                failed = True
            print(f"[{code}] {path.name}: {info} {result}")
        return int(failed)
    except (OSError, ValueError, TypeError) as exc:
        print(f"[실패] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
