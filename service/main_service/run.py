"""서버 실행 (Windows / Mac / Linux 공통). .env 없이도 실행된다.

    python run.py                                   # 0.0.0.0:8101, 기본 DB(Docker의 hcc)
    python run.py --db postgresql://postgres:비번@localhost:5432/hcc   # 내 PC의 PostgreSQL 사용
    python run.py --port 9000                       # 포트 변경
    python run.py --reload                          # 코드 저장하면 자동 재시작 (개발용)

--db 로 준 DB가 비어 있으면 테이블을 자동으로 만든다 (pgvector 확장 필요).
설정 우선순위: 명령행 옵션 > 환경변수 > .env 파일 > 기본값
"""
import argparse
import os
import socket


def my_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))       # 실제로 보내지 않음. 내 LAN IP 확인용
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8101)
    ap.add_argument("--db", help="PostgreSQL 주소. 예: postgresql://postgres:1234@localhost:5432/hcc")
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    if a.db:
        os.environ["HCC_DB_DSN"] = a.db              # app을 불러오기 전에 설정해야 반영됨

    import uvicorn
    print(f"\n  내 PC에서:     http://localhost:{a.port}/docs")
    print(f"  다른 PC에서:   http://{my_ip()}:{a.port}/docs\n")
    uvicorn.run("app.main:app", host="0.0.0.0", port=a.port, reload=a.reload)
