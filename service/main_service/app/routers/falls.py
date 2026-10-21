"""SR-03 낙상 · 알람: 낙상 이벤트 수신 → 알람 생성 → GUI 팝업 → 확인/해제."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from .. import auth
from ..db import get_conn
from ..events import publish
from ..schemas import AlertUpdate, CameraCreate, FallEvent

router = APIRouter(tags=["SR-03 낙상·알람"])


@router.post("/api/v1/cameras", status_code=201, dependencies=[Depends(auth.admin)])
def create_camera(body: CameraCreate):
    with get_conn() as conn:
        return conn.execute("""INSERT INTO hcc.cameras (camera_id, camera_type, location) VALUES (%s, %s, %s)
                               ON CONFLICT (camera_id) DO UPDATE
                                 SET camera_type = EXCLUDED.camera_type, location = EXCLUDED.location
                               RETURNING *""", (body.camera_id, body.camera_type, body.location)).fetchone()


@router.get("/api/v1/cameras", dependencies=[Depends(auth.admin)])
def list_cameras():
    with get_conn() as conn:
        return {"items": conn.execute("SELECT * FROM hcc.cameras ORDER BY camera_id").fetchall()}


@router.post("/api/v1/falls", status_code=201, dependencies=[Depends(auth.device)])
def receive_fall(body: FallEvent):
    """낙상 감지 모듈이 호출. 같은 event_id를 다시 보내면 기존 알람을 돌려준다(중복 방지)."""
    import json
    with get_conn() as conn:
        cam = conn.execute("SELECT location FROM hcc.cameras WHERE camera_id = %s", (body.camera_id,)).fetchone()
        if not cam:
            raise HTTPException(409, f"등록되지 않은 카메라입니다: {body.camera_id}")
        location = body.location or cam["location"]   # 위치가 없으면 카메라 위치로
        new = conn.execute("""
            INSERT INTO hcc.fall_events (event_id, person_id, camera_id, track_id, event_type,
                                         occurred_at, location, confidence, bbox_xyxy)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
            (body.event_id, body.person_id, body.camera_id, body.track_id, body.event_type, body.occurred_at,
             location, body.confidence, json.dumps(body.bbox_xyxy) if body.bbox_xyxy else None)).fetchone()
        if new:
            conn.execute("INSERT INTO hcc.alerts (fall_event_id) VALUES (%s)", (body.event_id,))
        alert = conn.execute("""SELECT a.alert_id, a.status, f.event_id, f.event_type, f.camera_id, f.location,
                                       f.occurred_at, f.person_id, p.name AS person_name, f.confidence
                                FROM hcc.alerts a JOIN hcc.fall_events f ON f.event_id = a.fall_event_id
                                LEFT JOIN hcc.persons p ON p.person_id = f.person_id
                                WHERE a.fall_event_id = %s""", (body.event_id,)).fetchone()
    if new:
        publish("fall_alert", alert)
    return {**alert, "duplicate": new is None}


@router.get("/api/v1/alerts", dependencies=[Depends(auth.admin)])
def list_alerts(status: Literal["ACTIVE", "ACKNOWLEDGED", "RESOLVED"] | None = None, limit: int = 100):
    with get_conn() as conn:
        return {"items": conn.execute("""
            SELECT a.*, f.event_type, f.camera_id, f.location, f.occurred_at, f.person_id, f.confidence
            FROM hcc.alerts a JOIN hcc.fall_events f ON f.event_id = a.fall_event_id
            WHERE (%s::text IS NULL OR a.status = %s) ORDER BY a.started_at DESC LIMIT %s""",
            (status, status, min(limit, 500))).fetchall()}


def _update(alert_id: str, sql: str, params: tuple, new_status: str):
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(409, "알람이 없거나 이미 처리됐습니다")
    publish("alert_update", {"alert_id": alert_id, "status": new_status})
    return {"alert_id": alert_id, "status": new_status}


@router.post("/api/v1/alerts/{alert_id}/acknowledge", dependencies=[Depends(auth.admin)])
def acknowledge(alert_id: str, body: AlertUpdate):
    return _update(alert_id, """UPDATE hcc.alerts SET status = 'ACKNOWLEDGED', acknowledged_at = NOW(),
                                handled_by = %s WHERE alert_id = %s::uuid AND status = 'ACTIVE' RETURNING alert_id""",
                   (body.staff_id, alert_id), "ACKNOWLEDGED")


@router.post("/api/v1/alerts/{alert_id}/resolve", dependencies=[Depends(auth.admin)])
def resolve(alert_id: str, body: AlertUpdate):
    """SR-03 '상황 해제' 버튼."""
    return _update(alert_id, """UPDATE hcc.alerts SET status = 'RESOLVED', resolved_at = NOW(),
                                acknowledged_at = COALESCE(acknowledged_at, NOW()),
                                handled_by = COALESCE(%s, handled_by), resolution_note = %s
                                WHERE alert_id = %s::uuid AND status <> 'RESOLVED' RETURNING alert_id""",
                   (body.staff_id, body.note, alert_id), "RESOLVED")
