"""SR-02/05 얼굴: 특징벡터 등록, 문 앞 얼굴 식별(DB 유사도 검색 → 누구인지 판정)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import auth
from ..config import settings
from ..db import get_conn
from ..events import publish
from ..schemas import FaceIdentify, FaceTemplate

router = APIRouter(prefix="/api/v1/face", tags=["SR-02/05 얼굴"], dependencies=[Depends(auth.device)])

# 가장 비슷한 등록 얼굴 1명. <=> 는 pgvector의 코사인 거리(0=같음), 유사도 = 1 - 거리
MATCH_SQL = """
    SELECT t.person_id, p.person_type, p.name, p.can_open,
           1 - (t.embedding <=> %(q)s) AS similarity
    FROM hcc.face_templates t JOIN hcc.persons p USING (person_id)
    WHERE t.is_active AND p.is_active AND t.model = %(model)s
    ORDER BY t.embedding <=> %(q)s
    LIMIT 1
"""


@router.post("/templates")
def register_template(body: FaceTemplate):
    """PC2가 등록 사진에서 뽑은 벡터를 저장. 같은 환자·모델이면 덮어씀.

    처음 보는 patient_id(예: 얼굴팀의 patient_001)는 FACE_AUTO_CREATE_PATIENT=true(기본)일 때
    이름 없는 환자로 자동 생성한다. 이름 등은 나중에 GUI에서 채운다. false면 404.
    """
    import numpy as np
    created = False
    with get_conn() as conn:
        person = conn.execute("SELECT is_active FROM hcc.persons WHERE person_id = %s",
                              (body.patient_id,)).fetchone()
        if person is None and settings.face_auto_create_patient:
            conn.execute("INSERT INTO hcc.persons (person_id, person_type, name) VALUES (%s, 'PATIENT', NULL)",
                         (body.patient_id,))
            conn.execute("INSERT INTO hcc.patient_profiles (person_id) VALUES (%s)", (body.patient_id,))
            created = True
        elif person is None:
            raise HTTPException(404, "등록되지 않은 환자입니다 (먼저 GUI에서 환자를 등록하세요)")
        elif not person["is_active"]:
            raise HTTPException(409, "삭제(비활성화)된 환자입니다")
        row = conn.execute("""
            INSERT INTO hcc.face_templates (person_id, model, embedding, images_used, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
            ON CONFLICT (person_id, model) DO UPDATE
               SET embedding = EXCLUDED.embedding, images_used = EXCLUDED.images_used,
                   is_active = TRUE, created_at = NOW()
            RETURNING template_id, person_id, model, images_used, created_at""",
            (body.patient_id, body.model, np.array(body.embedding, dtype=np.float32), body.images_used)).fetchone()
    publish("face_registered", {"person_id": body.patient_id, "patient_created": created})
    return {**row, "patient_created": created}


@router.post("/identify")
def identify(body: FaceIdentify):
    """문 앞 얼굴 → 가장 비슷한 사람 검색 → MATCHED / UNKNOWN 판정. (문 개폐는 Door MCU가 판단)"""
    if body.status != "OK":
        # 얼굴 없음·여러 명·오류: 받기만 하고 판단하지 않음
        return {"event_id": body.event_id, "accepted": True, "processed": False,
                "result": body.status.upper(), "person_id": None}

    import numpy as np
    q = np.array(body.embedding, dtype=np.float32)
    with get_conn() as conn:
        best = conn.execute(MATCH_SQL, {"q": q, "model": body.model}).fetchone()
        matched = best is not None and best["similarity"] >= settings.face_threshold
        result = "MATCHED" if matched else "UNKNOWN"
        inserted = conn.execute("""
            INSERT INTO hcc.identification_events
                (event_id, person_id, method, camera_id, result, similarity, model, occurred_at)
            VALUES (%s, %s, 'FACE', %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
            (body.event_id, best["person_id"] if matched else None,
             body.camera_id if _camera_exists(conn, body.camera_id) else None,
             result, best["similarity"] if best else None, body.model, body.timestamp)).fetchone()

    duplicate = inserted is None
    response = {
        "event_id": body.event_id, "accepted": True, "processed": True,   # 얼굴팀 전송 대기열이 accepted로 삭제 판단
        "result": result, "duplicate": duplicate,
        "person_id": best["person_id"] if matched else None,
        "name": best["name"] if matched else None,
        "similarity": round(best["similarity"], 4) if best else None,
        "can_open": bool(matched and best["can_open"]),
    }
    if not duplicate:
        publish("face_result", response)
    return response


def _camera_exists(conn, camera_id: str | None) -> bool:
    return bool(camera_id) and conn.execute("SELECT 1 FROM hcc.cameras WHERE camera_id = %s",
                                            (camera_id,)).fetchone() is not None
