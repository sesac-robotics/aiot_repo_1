"""SR-04 출입문: Door MCU가 보내는 상태(1/0)를 조회한다. 서버는 문에 명령하지 않는다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import auth
from ..db import get_conn
from ..schemas import DoorCreate
from ..services.door_receiver import receiver

router = APIRouter(prefix="/api/v1/doors", tags=["SR-04 출입문"], dependencies=[Depends(auth.admin)])


def _with_online(row: dict) -> dict:
    return {**row, "online": row["door_id"] in receiver.connected}


@router.get("")
def list_doors():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM hcc.doors ORDER BY door_id").fetchall()
    return {"items": [_with_online(r) for r in rows]}


@router.post("", status_code=201)
def create_door(body: DoorCreate):
    with get_conn() as conn:
        return conn.execute("""INSERT INTO hcc.doors (door_id, location) VALUES (%s, %s)
                               ON CONFLICT (door_id) DO UPDATE SET location = EXCLUDED.location
                               RETURNING *""", (body.door_id, body.location)).fetchone()


@router.get("/{door_id}")
def get_door(door_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM hcc.doors WHERE door_id = %s", (door_id,)).fetchone()
    if not row:
        raise HTTPException(404, "문이 없습니다")
    return _with_online(row)


@router.get("/{door_id}/history")
def history(door_id: str, limit: int = 50):
    """Door History: 문이 열리고 닫힌 시각 (최신순)."""
    with get_conn() as conn:
        return {"items": conn.execute("""SELECT state, received_at FROM hcc.door_state_log
                                         WHERE door_id = %s ORDER BY received_at DESC LIMIT %s""",
                                      (door_id, min(limit, 500))).fetchall()}
