"""SR-02 환자 관리: 등록 · 조회 · 수정 · 삭제(비활성화) · 카드 연결.

환자뿐 아니라 직원(Staff) · 관리자(Admin)도 같은 API로 등록한다(관리자 GUI 등록 화면이 같기 때문).
    role      ID 형식   출입 권한(can_open, 비우면 자동)
    Patient   P0001     없음  → 얼굴 인식돼도 문 안 열림  (명세 2. case 1)
    Staff     S0001     있음  → 얼굴 인식되면 문 열림     (명세 2. case 2)
    Admin     A0001     있음
/api/v1/persons 도 같은 기능이다(이름만 다름).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import auth
from ..db import get_conn
from ..events import publish
from ..schemas import ROLE_CAN_OPEN, ROLE_TYPE, PatientCreate, PatientUpdate, RFIDAssign

# main.py가 /api/v1/patients 와 /api/v1/persons 두 주소로 연결한다
router = APIRouter(tags=["SR-02 환자·직원 관리"], dependencies=[Depends(auth.admin)])

PERSON_COLS = {"name", "gender", "birth_date", "can_open", "person_type"}
ID_PREFIX = {"PATIENT": ("P", "hcc.patient_seq"), "STAFF": ("S", "hcc.staff_seq"), "ADMIN": ("A", "hcc.admin_seq")}
PROFILE_COLS = {"height_cm", "weight_kg", "blood_type", "medical_history", "medications", "room_number"}

SELECT_PATIENT = """
    SELECT p.person_id, initcap(p.person_type) AS role, p.name, p.gender, p.birth_date,
           date_part('year', age(p.birth_date))::int AS age,
           p.can_open, p.is_active, p.created_at, p.updated_at,
           f.height_cm, f.weight_kg, f.blood_type, f.medical_history, f.medications, f.room_number,
           (SELECT card_uid FROM hcc.rfid_cards r WHERE r.person_id = p.person_id AND r.is_active LIMIT 1) AS card_uid,
           EXISTS (SELECT 1 FROM hcc.face_templates t WHERE t.person_id = p.person_id AND t.is_active) AS face_registered
    FROM hcc.persons p LEFT JOIN hcc.patient_profiles f USING (person_id)
    WHERE p.person_type IN ('PATIENT', 'STAFF', 'ADMIN')
"""


def _get(conn, person_id: str) -> dict | None:
    return conn.execute(SELECT_PATIENT + " AND p.person_id = %s", (person_id,)).fetchone()


@router.get("")
def list_patients(include_inactive: bool = False, role: str | None = None, limit: int = 100, offset: int = 0):
    """등록된 사람 목록. role=Patient/Staff/Admin 으로 거를 수 있다."""
    ptype = ROLE_TYPE.get(role) if role else None
    if role and not ptype:
        raise HTTPException(422, "role은 Patient, Staff, Admin 중 하나입니다")
    with get_conn() as conn:
        rows = conn.execute(SELECT_PATIENT + """ AND (%s OR p.is_active) AND (%s::text IS NULL OR p.person_type = %s)
                            ORDER BY p.person_id LIMIT %s OFFSET %s""",
                            (include_inactive, ptype, ptype, min(limit, 500), offset)).fetchall()
    return {"items": rows}


@router.post("", status_code=201)
def create_patient(body: PatientCreate):
    v = body.model_dump()
    ptype = ROLE_TYPE[v["role"]]
    can_open = ROLE_CAN_OPEN[v["role"]] if v["can_open"] is None else v["can_open"]
    prefix, seq = ID_PREFIX[ptype]
    with get_conn() as conn:
        # ID는 서버가 발급: P0001 / S0001 / A0001 ... (삭제돼도 재사용하지 않음)
        while True:   # 기존 DB에 같은 코드가 이미 있으면 다음 번호로
            pid = conn.execute(f"SELECT '{prefix}' || lpad(nextval('{seq}')::text, 4, '0') AS id").fetchone()["id"]
            if not conn.execute("SELECT 1 FROM hcc.persons WHERE person_id = %s", (pid,)).fetchone():
                break
        conn.execute("""INSERT INTO hcc.persons (person_id, person_type, name, gender, birth_date, can_open)
                        VALUES (%s, %s, %s, %s, %s, %s)""",
                     (pid, ptype, v["name"], v["gender"], v["birth_date"], can_open))
        conn.execute("""INSERT INTO hcc.patient_profiles
                        (person_id, height_cm, weight_kg, blood_type, medical_history, medications, room_number)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                     (pid, v["height_cm"], v["weight_kg"], v["blood_type"],
                      v["medical_history"], v["medications"], v["room_number"]))
        row = _get(conn, pid)
    publish("patient_changed", {"person_id": pid, "role": v["role"], "action": "created"})
    return row


@router.get("/{person_id}")
def get_patient(person_id: str):
    with get_conn() as conn:
        row = _get(conn, person_id)
    if not row:
        raise HTTPException(404, "등록되지 않은 ID입니다")
    return row


@router.patch("/{person_id}")
def update_patient(person_id: str, body: PatientUpdate):
    changes = body.model_dump(exclude_unset=True)
    role = changes.pop("role", None)
    if role:                                   # 역할을 바꾸면 출입 권한도 역할에 맞게 (따로 보내면 그 값)
        changes["person_type"] = ROLE_TYPE[role]
        changes.setdefault("can_open", ROLE_CAN_OPEN[role])
    if changes.get("can_open") is None:
        changes.pop("can_open", None)
    with get_conn() as conn:
        found = conn.execute("""SELECT 1 FROM hcc.persons WHERE person_id = %s
                                AND person_type IN ('PATIENT','STAFF','ADMIN') AND is_active FOR UPDATE""",
                             (person_id,)).fetchone()
        if not found:
            raise HTTPException(404, "활성 상태인 사람이 없습니다")
        if not conn.execute("SELECT 1 FROM hcc.patient_profiles WHERE person_id = %s", (person_id,)).fetchone():
            conn.execute("INSERT INTO hcc.patient_profiles (person_id) VALUES (%s)", (person_id,))
        for cols, table in ((PERSON_COLS, "persons"), (PROFILE_COLS, "patient_profiles")):
            part = {k: v for k, v in changes.items() if k in cols}
            if part:
                sets = ", ".join(f"{k} = %s" for k in part)   # 키는 위 고정 목록에서만 옴
                conn.execute(f"UPDATE hcc.{table} SET {sets} WHERE person_id = %s", (*part.values(), person_id))
        row = _get(conn, person_id)
    publish("patient_changed", {"person_id": person_id, "role": row["role"], "action": "updated"})
    return row


@router.delete("/{person_id}")
def deactivate_patient(person_id: str):
    """비활성화하고 얼굴 특징벡터는 삭제, 카드는 해제한다(출입 불가). 과거 기록(알람·출입)은 유지."""
    with get_conn() as conn:
        row = conn.execute("""UPDATE hcc.persons SET is_active = FALSE, can_open = FALSE
                              WHERE person_id = %s AND is_active
                              RETURNING person_id""", (person_id,)).fetchone()
        if not row:
            raise HTTPException(404, "활성 상태인 사람이 없습니다")
        conn.execute("DELETE FROM hcc.face_templates WHERE person_id = %s", (person_id,))
        conn.execute("UPDATE hcc.rfid_cards SET is_active = FALSE WHERE person_id = %s", (person_id,))
    publish("patient_changed", {"person_id": person_id, "action": "deleted"})
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
