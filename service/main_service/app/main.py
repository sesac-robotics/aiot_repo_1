"""T-SAFE PC1 중앙 서버 진입점.

실행:  uvicorn app.main:app --host 0.0.0.0 --port 8101
문서:  http://<PC1_IP>:8101/docs   (모든 API를 화면에서 바로 시험 가능)

이 파일은 앱을 만들고 라우터를 연결만 한다. 기능 코드는 routers/ 에 SR별로 있다.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg
from psycopg.rows import dict_row

from .config import settings
from .db import get_conn
from .events import hub
from .routers import doors, face, falls, frame_analysis, patients, tracking
from .services.door_receiver import receiver as door_receiver
from .services.tracking import PC2TCPReceiver, bridge


UPGRADE_SQL = Path(__file__).resolve().parents[1] / "sql" / "02_server_upgrade.sql"
log = logging.getLogger("tsafe")


SQL_DIR = Path(__file__).resolve().parents[1]
SCHEMA_SQL = SQL_DIR / "db" / "schema.sql"
SEED_SQL = SQL_DIR / "sql" / "03_seed.sql"


def apply_db_upgrade() -> None:
    """DB 준비. 여러 번 실행해도 안전.

    - 빈 DB(테이블 없음)면 db/schema.sql → 서버 추가분 → 기본 장치(CAM01, door_01)까지 자동 생성
    - 팀 v0.1 DB가 이미 있으면 서버 추가분(시퀀스 등)만 적용
    그래서 팀원은 아무 PostgreSQL에 빈 DB 하나만 만들고 주소를 알려주면 된다.
    """
    try:
        # get_conn()은 vector 타입을 등록하므로, 확장이 아직 없는 빈 DB에서는 일반 연결로 준비한다
        with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
            fresh = conn.execute("SELECT to_regclass('hcc.persons') AS t").fetchone()["t"] is None
            if fresh:
                print("[DB] 빈 DB → 테이블 자동 생성", flush=True)
                conn.execute(SCHEMA_SQL.read_text(encoding="utf-8").replace("BEGIN;", "").replace("COMMIT;", ""))
            conn.execute(UPGRADE_SQL.read_text(encoding="utf-8"))
            if fresh:
                conn.execute(SEED_SQL.read_text(encoding="utf-8"))
        print("[DB] 준비 완료", flush=True)
    except Exception as exc:  # noqa: BLE001  DB가 아직 없으면 /health에서 확인 가능
        msg = str(exc)
        if "vector" in msg and ("extension" in msg or "control file" in msg):
            msg = "pgvector 확장이 설치되지 않은 PostgreSQL입니다. Docker 방법(A/B)을 쓰거나 pgvector를 설치하세요."
        print(f"[DB] 준비 안 됨: {msg}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_db_upgrade()
    hub.bind_loop(asyncio.get_running_loop())
    bridge.start()
    await door_receiver.start()
    receiver = PC2TCPReceiver(bridge, settings.pc2_tcp_port) if settings.pc2_tcp_enabled else None
    if receiver:
        await receiver.start()
    yield
    if receiver:
        await receiver.stop()
    await door_receiver.stop()
    bridge.close()


app = FastAPI(title="T-SAFE PC1 Server", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])  # 웹 GUI 허용


@app.exception_handler(psycopg.OperationalError)
async def db_down(_request, exc):
    """DB가 꺼져 있으면 500 대신 이유가 보이는 503으로 답한다."""
    log.error("DB 연결 실패: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "DB에 연결할 수 없습니다. DB(PostgreSQL)가 켜져 있는지 확인하세요."})

for r in (patients.router, face.router, doors.router, falls.router, tracking.router, frame_analysis.router):
    app.include_router(r)


@app.get("/health", tags=["시스템"])
def health():
    """서버·DB·장치 연결 상태. 팀원은 먼저 이걸로 접속 확인."""
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM hcc.persons LIMIT 1")
        db = "ok"
    except Exception as exc:  # noqa: BLE001
        db = f"error: {type(exc).__name__}"
    return {"server": "ok", "db": db, "auth_enabled": settings.auth_enabled,
            "door_tcp_port": settings.door_tcp_port, "doors_online": sorted(door_receiver.connected),
            "tracking_mcu": bridge.mode, "pc2_tcp": settings.pc2_tcp_port if settings.pc2_tcp_enabled else "off"}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    """GUI 실시간 알림. 접속만 해 두면 events.py의 type별 메시지가 온다."""
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()   # GUI가 보내는 내용은 쓰지 않음 (연결 유지용)
    except WebSocketDisconnect:
        hub.disconnect(ws)
