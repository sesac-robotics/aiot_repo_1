"""SR-04 출입문: 문 상태 조회 · 이력 · 문 열기 명령.

문 열기 명령은 두 가지 경우에만 서버가 PC4로 보낸다 (명세 ⑥):
    - 얼굴 인식된 사람이 출입 권한(직원·관리자)이 있을 때 → face.py가 자동으로
    - 관리자 GUI의 OPEN 버튼 → POST /api/v1/doors/{door_id}/open
문을 닫는 것은 Arduino가 스스로 한다(사람이 지나가고 5초 뒤). 서버는 닫힘 결과만 기록한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import auth
from ..db import get_conn
from ..schemas import DoorCreate, DoorOpen
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


@router.post("/{door_id}/open", status_code=202)
def open_door(door_id: str, body: DoorOpen | None = None):
    """관리자 GUI OPEN 버튼 (명세 2. case 3). 결과는 비동기:
    응답의 status가 PENDING이면 PC4에 보냄 → 문이 실제로 열리면 door_state OPEN 이벤트, 실패면 door_command ERROR/TIMEOUT.
    ERROR(PC4_UNAVAILABLE)면 PC4 Bridge 연결이 안 된 상태(/health의 pc4 확인)."""
    from ..services.door_control import controller
    return controller.open(door_id, "GUI", body.staff_id if body else None)


@router.get("/{door_id}/commands")
def commands(door_id: str, limit: int = 50):
    """문 열기 명령 기록 (최신순): 누가·언제·어떤 경로로·결과(ACCEPTED/ERROR/TIMEOUT)."""
    with get_conn() as conn:
        return {"items": conn.execute("""
            SELECT c.command_id, c.request_id, c.request_source, c.requested_by, p.name AS requested_by_name,
                   c.ack_status, c.reported_state, c.error_code, c.requested_at, c.responded_at
            FROM hcc.door_commands c LEFT JOIN hcc.persons p ON p.person_id = c.requested_by
            WHERE c.door_id = %s ORDER BY c.requested_at DESC LIMIT %s""", (door_id, min(limit, 500))).fetchall()}


@router.get("/{door_id}/access")
def access_log(door_id: str, limit: int = 50):
    """출입 판정 기록 (최신순): 얼굴 인식 결과 ALLOW/DENY와 이유."""
    with get_conn() as conn:
        return {"items": conn.execute("""
            SELECT a.occurred_at, a.decision, a.reason, a.person_id, p.name, initcap(p.person_type) AS role,
                   i.similarity, a.command_id
            FROM hcc.access_events a LEFT JOIN hcc.persons p ON p.person_id = a.person_id
            LEFT JOIN hcc.identification_events i ON i.event_id = a.identification_event_id
            WHERE a.door_id = %s ORDER BY a.occurred_at DESC LIMIT %s""", (door_id, min(limit, 500))).fetchall()}
