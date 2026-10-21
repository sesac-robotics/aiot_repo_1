"""SR-02 환자 관리: GUI(ui/hcc_gui)와 같은 필드로 등록 · 조회 · 삭제 · 카드 연결.

GUI patients 표          서버 DB
    p_num         ↔  hcc.persons.person_id (문자열 "2")   ← 얼굴 등록·RFID와 같은 번호
    name, gender  ↔  hcc.persons.name, gender
    age, blood_type, height, weight, disease, meds
                  ↔  hcc.patient_profiles.age, blood_type, height_cm, weight_kg, medical_history, medications
    번호 범위     →  persons.person_type (1~3000 PATIENT, 3001~4000 VISITOR, 4001~5000 STAFF)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import auth
from ..db import get_conn
from ..events import publish
from ..schemas import PatientIn, RFIDAssign, role_of

router = APIRouter(prefix="/api/v1/patients", tags=["SR-02 환자 관리"], dependencies=[Depends(auth.admin)])

PERSON_TYPE = {"Patient": "PATIENT", "Visitor": "VISITOR", "Staff": "STAFF"}

SELECT_PERSON = """
    SELECT p.person_id, p.name, p.gender,
           COALESCE(f.age, date_part('year', age(p.birth_date))::int) AS age,
           f.blood_type, f.height_cm::float AS height, f.weight_kg::float AS weight,
           f.medical_history AS disease, f.medications AS meds,
           p.person_type, p.is_active,
           (SELECT card_uid FROM hcc.rfid_cards r WHERE r.person_id = p.person_id AND r.is_active LIMIT 1) AS card_uid,
           EXISTS (SELECT 1 FROM hcc.face_templates t WHERE t.person_id = p.person_id AND t.is_active) AS face_registered
    FROM hcc.persons p LEFT JOIN hcc.patient_profiles f USING (person_id)
    WHERE TRUE
"""


def _out(row: dict) -> dict:
    """DB 한 줄 → GUI 형식. 숫자 ID는 int로(GUI의 p_num), 얼굴팀이 만든 'patient_001' 같은 ID는 그대로."""
    pid = row.pop("person_id")
    row["p_num"] = int(pid) if pid.isdigit() else pid
    row["role"] = role_of(row["p_num"]) if isinstance(row["p_num"], int) else row["person_type"].title()
    row.pop("person_type")
    return {"p_num": row.pop("p_num"), **row}


def _get(conn, person_id: str) -> dict | None:
    row = conn.execute(SELECT_PERSON + " AND p.person_id = %s", (person_id,)).fetchone()
    return _out(row) if row else None


@router.get("")
def list_patients(include_inactive: bool = False):
    """등록된 사람 전체 (GUI 목록). 번호 순."""
    with get_conn() as conn:
        rows = conn.execute(SELECT_PERSON + """ AND (%s OR p.is_active)
            ORDER BY (p.person_id ~ '^[0-9]+$') DESC,
                     CASE WHEN p.person_id ~ '^[0-9]+$' THEN p.person_id::int END, p.person_id""",
                            (include_inactive,)).fetchall()
    return {"items": [_out(r) for r in rows]}


@router.post("", status_code=200)
def enroll(body: PatientIn):
    """GUI 'enroll' 버튼. p_num이 이미 있으면 덮어쓴다(삭제했던 번호면 다시 살린다)."""
    if body.p_num is None:
        raise HTTPException(422, "p_num(번호)이 필요합니다")
    return _upsert(body.p_num, body)


@router.put("/{p_num}")
def enroll_put(p_num: int, body: PatientIn):
    if body.p_num is not None and body.p_num != p_num:
        raise HTTPException(422, "주소의 번호와 본문의 p_num이 다릅니다")
    if not 1 <= p_num <= 5000:
        raise HTTPException(422, "p_num은 1~5000 입니다")
    return _upsert(p_num, body)


def _upsert(p_num: int, b: PatientIn) -> dict:
    pid, role = str(p_num), role_of(p_num)
    zero_none = lambda v: v or None          # GUI는 빈 칸을 0.0으로 보냄 → 저장 안 함
    with get_conn() as conn:
        existed = conn.execute("SELECT is_active FROM hcc.persons WHERE person_id = %s", (pid,)).fetchone()
        conn.execute("""
            INSERT INTO hcc.persons (person_id, person_type, name, gender, can_open, is_active)
            VALUES (%s, %s, %s, %s, TRUE, TRUE)
            ON CONFLICT (person_id) DO UPDATE
               SET person_type = EXCLUDED.person_type, name = EXCLUDED.name, gender = EXCLUDED.gender,
                   can_open = TRUE, is_active = TRUE""",
            (pid, PERSON_TYPE[role], b.name, b.gender))
        conn.execute("""
            INSERT INTO hcc.patient_profiles (person_id, age, blood_type, height_cm, weight_kg, medical_history, medications)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (person_id) DO UPDATE
               SET age = EXCLUDED.age, blood_type = EXCLUDED.blood_type, height_cm = EXCLUDED.height_cm,
                   weight_kg = EXCLUDED.weight_kg, medical_history = EXCLUDED.medical_history,
                   medications = EXCLUDED.medications""",
            (pid, b.age, b.blood_type, zero_none(b.height), zero_none(b.weight), b.disease, b.meds))
        row = _get(conn, pid)
    created = existed is None or not existed["is_active"]
    publish("patient_changed", {"p_num": p_num, "action": "created" if created else "updated"})
    return {**row, "created": created}


@router.get("/{p_num}")
def get_patient(p_num: str):
    with get_conn() as conn:
        row = _get(conn, p_num)
    if not row:
        raise HTTPException(404, "등록되지 않은 번호입니다")
    return row


@router.delete("/{p_num}")
def delete_patient(p_num: str):
    """GUI 'Delete' 버튼. 비활성화하고 얼굴 특징벡터는 삭제, 카드는 해제. 과거 기록(알람·출입)은 유지."""
    with get_conn() as conn:
        row = conn.execute("""UPDATE hcc.persons SET is_active = FALSE, can_open = FALSE
                              WHERE person_id = %s AND is_active RETURNING person_id""", (p_num,)).fetchone()
        if not row:
            raise HTTPException(404, "등록되지 않은 번호입니다")
        conn.execute("DELETE FROM hcc.face_templates WHERE person_id = %s", (p_num,))
        conn.execute("UPDATE hcc.rfid_cards SET is_active = FALSE WHERE person_id = %s", (p_num,))
    publish("patient_changed", {"p_num": int(p_num) if p_num.isdigit() else p_num, "action": "deleted"})
    return {"p_num": int(p_num) if p_num.isdigit() else p_num, "deleted": True,
            "face_deleted": True, "card_released": True}


@router.put("/{p_num}/card")
def assign_card(p_num: str, body: RFIDAssign):
    """카드 UID를 사람에게 연결 (한 사람에 카드 1장, 기존 카드는 해제)."""
    uid = body.card_uid.upper()
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM hcc.persons WHERE person_id = %s AND is_active", (p_num,)).fetchone():
            raise HTTPException(404, "등록되지 않은 번호입니다")
        owner = conn.execute("SELECT person_id FROM hcc.rfid_cards WHERE card_uid = %s AND is_active",
                             (uid,)).fetchone()
        if owner and owner["person_id"] != p_num:
            raise HTTPException(409, f"다른 사람({owner['person_id']})에게 연결된 카드입니다")
        conn.execute("UPDATE hcc.rfid_cards SET is_active = FALSE WHERE person_id = %s", (p_num,))
        conn.execute("""INSERT INTO hcc.rfid_cards (card_uid, person_id, is_active) VALUES (%s, %s, TRUE)
                        ON CONFLICT (card_uid) DO UPDATE SET person_id = EXCLUDED.person_id, is_active = TRUE""",
                     (uid, p_num))
    return {"p_num": int(p_num) if p_num.isdigit() else p_num, "card_uid": uid}
