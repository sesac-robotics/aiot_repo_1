"""가짜 Door MCU. 실물 없이 SR-04(문 상태 표시)를 시험할 때 쓴다.

실제 MCU처럼 서버의 문 상태 포트(기본 8103)에 TCP로 접속해 1(열림) / 0(닫힘)을 보낸다.

    python tools/fake_door_mcu.py                     # 열림 → 3초 → 닫힘 을 반복
    python tools/fake_door_mcu.py --once 1            # 1 한 번만 보내고 연결 유지
    python tools/fake_door_mcu.py --server 192.168.0.10 --door door_02
"""
from __future__ import annotations

import argparse
import socket
import time

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8103)
    ap.add_argument("--door", default="", help="비우면 1/0만 보냄 (서버 기본 문 door_01)")
    ap.add_argument("--once", choices=["0", "1"])
    ap.add_argument("--interval", type=float, default=3.0)
    a = ap.parse_args()

    with socket.create_connection((a.server, a.port), timeout=5) as s:
        print(f"[MCU] 서버 {a.server}:{a.port} 접속", flush=True)
        values = [a.once] if a.once else ["1", "0"]
        while True:
            for v in values:
                line = f"{a.door},{v}" if a.door else v
                s.sendall((line + "\n").encode())
                print(f"[MCU] 보냄: {line}  ({'열림' if v == '1' else '닫힘'})", flush=True)
                time.sleep(a.interval)
            if a.once:
                input("[MCU] Enter를 누르면 연결을 끊습니다 ")
                break
