"""SR-02 환자 관리: 등록 · 조회 · 수정 · 삭제(비활성화) · 카드 연결."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import auth
from ..db import get_conn
from ..schemas import PatientCreate, PatientUpdate, RFIDAssign

router = APIRouter(prefix="/api/v1/patients", tags=["SR-02 환자 관리"], dependencies=[Depends(auth.admin)])

PERSON_COLS = {"name", "gender", "birth_date", "can_open"}
PROFILE_COLS = {"height_cm", "weight_kg", "blood_type", "medical_history", "medications", "room_number"}

SELECT_PATIENT = """
    SELECT p.person_id, p.name, p.gender, p.birth_date,
           date_part('year', age(p.birth_date))::int AS age,
           p.can_open, p.is_active, p.created_at, p.updated_at,
           f.height_cm, f.weight_kg, f.blood_type, f.medical_history, f.medications, f.room_number,
           (SELECT card_uid FROM hcc.rfid_cards r WHERE r.person_id = p.person_id AND r.is_active LIMIT 1) AS card_uid,
           EXISTS (SELECT 1 FROM hcc.face_templates t WHERE t.person_id = p.person_id AND t.is_active) AS face_registered
    FROM hcc.persons p LEFT JOIN hcc.patient_profiles f USING (person_id)
    WHERE p.person_type = 'PATIENT'
"""


def _get(conn, person_id: str) -> dict | None:
    return conn.execute(SELECT_PATIENT + " AND p.person_id = %s", (person_id,)).fetchone()


@router.get("")
def list_patients(include_inactive: bool = False, limit: int = 100, offset: int = 0):
    with get_conn() as conn:
        rows = conn.execute(SELECT_PATIENT + " AND (%s OR p.is_active) ORDER BY p.person_id LIMIT %s OFFSET %s",
                            (include_inactive, min(limit, 500), offset)).fetchall()
    return {"items": rows}


@router.post("", status_code=201)
def create_patient(body: PatientCreate):
    v = body.model_dump()
    with get_conn() as conn:
        # 환자 코드는 서버가 발급: P0001, P0002, ... (삭제돼도 재사용하지 않음)
        while True:   # 기존 DB에 같은 코드가 이미 있으면 다음 번호로
            pid = conn.execute("SELECT 'P' || lpad(nextval('hcc.patient_seq')::text, 4, '0') AS id").fetchone()["id"]
            if not conn.execute("SELECT 1 FROM hcc.persons WHERE person_id = %s", (pid,)).fetchone():
                break
        conn.execute("""INSERT INTO hcc.persons (person_id, person_type, name, gender, birth_date, can_open)
                        VALUES (%s, 'PATIENT', %s, %s, %s, %s)""",
                     (pid, v["name"], v["gender"], v["birth_date"], v["can_open"]))
        conn.execute("""INSERT INTO hcc.patient_profiles
                        (person_id, height_cm, weight_kg, blood_type, medical_history, medications, room_number)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                     (pid, v["height_cm"], v["weight_kg"], v["blood_type"],
                      v["medical_history"], v["medications"], v["room_number"]))
        return _get(conn, pid)


@router.get("/{person_id}")
def get_patient(person_id: str):
    with get_conn() as conn:
        row = _get(conn, person_id)
    if not row:
        raise HTTPException(404, "환자가 없습니다")
    return row


@router.patch("/{person_id}")
def update_patient(person_id: str, body: PatientUpdate):
    changes = body.model_dump(exclude_unset=True)
    with get_conn() as conn:
        found = conn.execute("""SELECT 1 FROM hcc.persons WHERE person_id = %s
                                AND person_type = 'PATIENT' AND is_active FOR UPDATE""", (person_id,)).fetchone()
        if not found:
            raise HTTPException(404, "활성 환자가 없습니다")
        for cols, table in ((PERSON_COLS, "persons"), (PROFILE_COLS, "patient_profiles")):
            part = {k: v for k, v in changes.items() if k in cols}
            if part:
                sets = ", ".join(f"{k} = %s" for k in part)   # 키는 위 고정 목록에서만 옴
                conn.execute(f"UPDATE hcc.{table} SET {sets} WHERE person_id = %s", (*part.values(), person_id))
        return _get(conn, person_id)


@router.delete("/{person_id}")
def deactivate_patient(person_id: str):
    """환자를 비활성화하고 얼굴 특징벡터는 삭제, 카드는 해제한다. 과거 기록(알람·출입)은 유지."""
    with get_conn() as conn:
        row = conn.execute("""UPDATE hcc.persons SET is_active = FALSE, can_open = FALSE
                              WHERE person_id = %s AND person_type = 'PATIENT' AND is_active
                              RETURNING person_id""", (person_id,)).fetchone()
        if not row:
            raise HTTPException(404, "활성 환자가 없습니다")
        conn.execute("DELETE FROM hcc.face_templates WHERE person_id = %s", (person_id,))
        conn.execute("UPDATE hcc.rfid_cards SET is_active = FALSE WHERE person_id = %s", (person_id,))
    return {"person_id": person_id, "is_active": False, "face_deleted": True, "card_released": True}


@router.put("/{person_id}/card")
def assign_card(person_id: str, body: RFIDAssign):
    """카드 UID를 환자에게 연결 (한 환자에 카드 1장, 기존 카드는 해제)."""
    uid = body.card_uid.upper()
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM hcc.persons WHERE person_id = %s AND is_active", (person_id,)).fetchone():
            raise HTTPException(404, "활성 환자가 없습니다")
        owner = conn.execute("SELECT person_id FROM hcc.rfid_cards WHERE card_uid = %s AND is_active",
                             (uid,)).fetchone()
        if owner and owner["person_id"] != person_id:
            raise HTTPException(409, f"다른 사람({owner['person_id']})에게 연결된 카드입니다")
        conn.execute("UPDATE hcc.rfid_cards SET is_active = FALSE WHERE person_id = %s", (person_id,))
        conn.execute("""INSERT INTO hcc.rfid_cards (card_uid, person_id, is_active) VALUES (%s, %s, TRUE)
                        ON CONFLICT (card_uid) DO UPDATE SET person_id = EXCLUDED.person_id, is_active = TRUE""",
                     (uid, person_id))
    return {"person_id": person_id, "card_uid": uid}
