"""GUI 담당용 예제: 서버 API 호출 + 실시간 알림(WebSocket) 받기.

    pip install requests websockets
    python examples/gui_client_example.py --server 192.168.0.10:8101

PyQt/웹 등 어떤 GUI에서든 같은 방식으로 쓰면 된다.
"""
import argparse
import asyncio
import json

import requests
import websockets

ap = argparse.ArgumentParser()
ap.add_argument("--server", default="127.0.0.1:8101")
SERVER = ap.parse_args().server
API = f"http://{SERVER}/api/v1"


def demo_rest() -> None:
    # 환자 등록 (SR-02) — GUI patients 표와 같은 필드. 같은 p_num이면 덮어씀
    r = requests.put(f"{API}/patients/7", json={"name": "홍길동", "gender": "M", "age": 76, "blood_type": "Rh+ A",
                                                "height": 170, "weight": 65, "disease": "고혈압", "meds": "아스피린"})
    print("등록:", r.status_code, r.json().get("p_num") if r.ok else r.json())

    # 환자 목록 (SR-01 간단 정보: 이름·나이·성별)
    for p in requests.get(f"{API}/patients").json()["items"]:
        age = f"{p['age']}세" if p["age"] is not None else "나이 미입력"
        print(f"  {p['p_num']} {p['role']} {p['name']} {age} {p['gender'] or '-'}  얼굴등록={p['face_registered']}")

    # 문 상태 (SR-01/04)
    for d in requests.get(f"{API}/doors").json()["items"]:
        print(f"  문 {d['door_id']}: {d['reported_state']}  연결={'O' if d['online'] else 'X'}")

    # 처리 안 된 낙상 알람 (SR-03)
    for a in requests.get(f"{API}/alerts", params={"status": "ACTIVE"}).json()["items"]:
        print(f"  알람 {a['alert_id']} 위치={a['location']} 시각={a['occurred_at']}")
        # 상황 해제 버튼:  requests.post(f"{API}/alerts/{a['alert_id']}/resolve", json={"note": "확인"})


async def listen() -> None:
    """서버가 보내는 실시간 이벤트. GUI에서는 type별로 화면을 갱신하면 된다."""
    async with websockets.connect(f"ws://{SERVER}/ws") as ws:
        print("\n실시간 알림 대기 중... (Ctrl+C 종료)")
        async for text in ws:
            msg = json.loads(text)
            t, d = msg["type"], msg["data"]
            if t == "door_state":
                print("문 상태:", d["door_id"], d["state"], "" if d["online"] else "(연결 끊김)")
            elif t == "fall_alert":
                print("!! 낙상 알람 팝업:", d["location"], d["occurred_at"], d.get("person_name"))
            elif t == "alert_update":
                print("알람 상태 변경:", d["alert_id"], d["status"])
            elif t == "face_result":
                print("얼굴 식별:", d["result"], d.get("p_num"), d.get("role"), d.get("name"))
            else:
                print(t, d)


if __name__ == "__main__":
    demo_rest()
    try:
        asyncio.run(listen())
    except KeyboardInterrupt:
        pass
