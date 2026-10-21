"""HCC PostgreSQL v0.1 DB access layer (PC1 Main Server only).

Uses psycopg 3 + pgvector. Not an HTTP server; call these methods behind PC1's API.
All SQL values are parameterized; no face vectors or credentials are logged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from validation import validate_captured, validate_registered, require_aware_time


class HCCDatabase:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv("HCC_DB_DSN")
        if not self.dsn:
            raise RuntimeError("Missing HCC_DB_DSN environment variable. See README.md")

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        from pgvector.psycopg import register_vector
        conn = psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)
        register_vector(conn)
        return conn

    def init_schema(self, path: str | Path | None = None) -> None:
        """Install extension and tables. For a NEW DB only; no automatic migrations."""
        import psycopg
        sql_file = Path(path) if path is not None else Path(__file__).with_name("schema.sql")
        with psycopg.connect(self.dsn, connect_timeout=5, autocommit=True) as conn:
            conn.execute(sql_file.read_text(encoding="utf-8"), prepare=False)

    def health(self) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database() db_name, current_user db_user, version() version")
                info = cur.fetchone()
                cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                ext = cur.fetchone()
                cur.execute("""SELECT table_name FROM information_schema.tables
                            WHERE table_schema='hcc' AND table_type='BASE TABLE' ORDER BY table_name""")
                tables = [r["table_name"] for r in cur.fetchall()]
        return {"database": info["db_name"], "user": info["db_user"],
                "pgvector_version": ext["extversion"] if ext else None,
                "tables": tables, "table_count": len(tables)}

    def list_persons(self) -> list[dict]:
        with self._connect() as conn:
            return conn.execute("""SELECT person_id, person_type, name, gender, birth_date,
                                       can_open, is_active, created_at, updated_at
                                FROM hcc.persons ORDER BY person_id""").fetchall()

    def upsert_patient(self, person_id: str, name: str, *, gender: str | None = None,
                       birth_date: str | None = None, height_cm: float | None = None,
                       weight_kg: float | None = None, blood_type: str | None = None,
                       medical_history: str | None = None, medications: str | None = None,
                       room_number: str | None = None) -> None:
        """SR-02 basic upsert. Missing optional fields in a subsequent call become NULL.
        Do not use this as a PATCH endpoint without first fetching the current row.
        """
        if not person_id.strip() or len(person_id) > 40 or not name.strip():
            raise ValueError("valid person_id <=40 and nonempty name are required")
        for field, value in (("height_cm", height_cm), ("weight_kg", weight_kg)):
            if value is not None and (not math.isfinite(float(value)) or float(value) <= 0):
                raise ValueError(f"{field} must be positive")
        with self._connect() as conn:
            old = conn.execute("SELECT person_type FROM hcc.persons WHERE person_id=%s", (person_id,)).fetchone()
            if old and old["person_type"] != "PATIENT":
                raise ValueError("person_id belongs to a non-patient")
            conn.execute("""INSERT INTO hcc.persons(person_id,person_type,name,gender,birth_date)
                            VALUES(%s,'PATIENT',%s,%s,%s)
                            ON CONFLICT (person_id) DO UPDATE SET name=EXCLUDED.name,
                            gender=EXCLUDED.gender,birth_date=EXCLUDED.birth_date,is_active=TRUE""",
                         (person_id,name,gender,birth_date))
            conn.execute("""INSERT INTO hcc.patient_profiles(person_id,height_cm,weight_kg,blood_type,
                            medical_history,medications,room_number)
                            VALUES(%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (person_id) DO UPDATE SET
                            height_cm=EXCLUDED.height_cm,weight_kg=EXCLUDED.weight_kg,
                            blood_type=EXCLUDED.blood_type,medical_history=EXCLUDED.medical_history,
                            medications=EXCLUDED.medications,room_number=EXCLUDED.room_number""",
                         (person_id,height_cm,weight_kg,blood_type,medical_history,medications,room_number))

    def deactivate_person(self, person_id: str) -> bool:
        """Logical deactivation: preserve event history; does not purge biometric data."""
        with self._connect() as conn:
            row = conn.execute("UPDATE hcc.persons SET is_active=FALSE,can_open=FALSE WHERE person_id=%s RETURNING person_id",(person_id,)).fetchone()
            if row:
                conn.execute("UPDATE hcc.face_templates SET is_active=FALSE WHERE person_id=%s",(person_id,))
                conn.execute("UPDATE hcc.rfid_cards SET is_active=FALSE WHERE person_id=%s",(person_id,))
            return row is not None

    def import_registered_face(self, payload: dict) -> dict:
        """Import PC2's patient_<n>.json. Create unnamed PATIENT shell if not yet registered.
        Explicitly validate data; reject staff/visitor ID collision.
        """
        from pgvector import Vector
        person_id, model, embedding, images_used, created_at = validate_registered(payload)
        with self._connect() as conn:
            conn.execute("""INSERT INTO hcc.persons(person_id,person_type,name)
                            VALUES(%s,'PATIENT',NULL) ON CONFLICT (person_id) DO NOTHING""",(person_id,))
            person = conn.execute("SELECT person_type FROM hcc.persons WHERE person_id=%s",(person_id,)).fetchone()
            if person["person_type"] != "PATIENT":
                raise ValueError("registered patient_id collides with staff/visitor")
            row = conn.execute("""INSERT INTO hcc.face_templates
                      (person_id,model,embedding,embedding_normalized,images_used,created_at,is_active)
                      VALUES(%s,%s,%s,TRUE,%s,COALESCE(%s,NOW()),TRUE)
                      ON CONFLICT (person_id,model) DO UPDATE SET embedding=EXCLUDED.embedding,
                          embedding_normalized=TRUE,images_used=EXCLUDED.images_used,
                          created_at=EXCLUDED.created_at,is_active=TRUE
                      RETURNING template_id,person_id,model,images_used""",
                (person_id, model, Vector(embedding), images_used, created_at)).fetchone()
        return row

    def match_face(self, captured: dict, *, threshold: float = 0.5,
                   camera_id: str | None = None) -> dict:
        """SR-05 exact nearest-neighbor identification against ALL active registered faces.
        Not an access-grant command. Threshold 0.5 is only team provisional.
        A repeated event_id returns the first saved decision (idempotent).
        """
        from pgvector import Vector
        if not math.isfinite(threshold) or not (-1 <= threshold <= 1):
            raise ValueError("threshold must be within [-1, 1]")
        event_id, model, emb, occurred_at = validate_captured(captured)
        qvec = Vector(emb)
        with self._connect() as conn:
            old = conn.execute("""SELECT event_id,person_id,result,similarity,model
                                  FROM hcc.identification_events WHERE event_id=%s""",(event_id,)).fetchone()
            if old:
                if old['model'] != model:
                    raise ValueError('event_id was previously used for a different model')
                return {**old,"duplicate": True, "threshold": None, "note":"original result; threshold not persisted"}
            nearest = conn.execute("""SELECT t.person_id, p.can_open, p.person_type,
                                 1 - (t.embedding <=> %s) AS similarity
                                 FROM hcc.face_templates t JOIN hcc.persons p ON t.person_id=p.person_id
                                 WHERE t.model=%s AND t.is_active AND p.is_active
                                 ORDER BY t.embedding <=> %s, t.person_id LIMIT 1""",
                                (qvec,model,qvec)).fetchone()
            score = float(nearest["similarity"]) if nearest else None
            matched = score is not None and score >= threshold
            pid = nearest["person_id"] if matched else None
            result = "MATCHED" if matched else "UNKNOWN"
            record = conn.execute("""INSERT INTO hcc.identification_events
                    (event_id,person_id,method,camera_id,result,similarity,model,occurred_at)
                    VALUES(%s,%s,'FACE',%s,%s,%s,%s,%s)
                    ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                    (event_id,pid,camera_id,result,score,model,occurred_at)).fetchone()
            if not record:
                existing = conn.execute("""SELECT event_id,person_id,result,similarity,model FROM hcc.identification_events
                                           WHERE event_id=%s""", (event_id,)).fetchone()
                return {**existing,"duplicate":True,"threshold":None,"note":"concurrent duplicate"}
        return {"event_id":event_id,"person_id":pid,"result":result,
                "similarity":score,"threshold":threshold,"duplicate":False,
                "access_eligible": bool(matched and nearest["can_open"]),
                "note":"access_eligible is DB policy only; no door command has been sent"}

    def register_camera(self, camera_id: str, camera_type: str = "CCTV", location: str | None = None):
        with self._connect() as conn:
            return conn.execute("""INSERT INTO hcc.cameras(camera_id,camera_type,location)
                    VALUES(%s,%s,%s) ON CONFLICT (camera_id) DO UPDATE SET
                    camera_type=EXCLUDED.camera_type,location=EXCLUDED.location
                    RETURNING camera_id,camera_type,location""",(camera_id,camera_type,location)).fetchone()

    def register_door(self, door_id: str, location: str | None = None):
        with self._connect() as conn:
            return conn.execute("""INSERT INTO hcc.doors(door_id,location) VALUES(%s,%s)
                    ON CONFLICT (door_id) DO UPDATE SET location=EXCLUDED.location
                    RETURNING door_id,location,reported_state,state_source""",(door_id,location)).fetchone()

    def record_fall(self, *, event_id: str, camera_id: str, occurred_at: str,
                    event_type: str = "FALL", person_id: str | None = None,
                    track_id: str | None = None, location: str | None = None,
                    confidence: float | None = None, bbox_xyxy: list | None = None) -> dict:
        """SR-03: create exactly one fall + linked alert per event_id; caller registers camera first."""
        from psycopg.types.json import Jsonb
        when = require_aware_time(occurred_at)
        if confidence is not None and not (math.isfinite(confidence) and 0 <= confidence <= 1):
            raise ValueError("confidence must be 0..1")
        if bbox_xyxy is not None and (len(bbox_xyxy) != 4 or any(isinstance(x, bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in bbox_xyxy)):
            raise ValueError("bbox_xyxy must contain four finite numbers")
        with self._connect() as conn:
            new = conn.execute("""INSERT INTO hcc.fall_events(event_id,person_id,camera_id,track_id,event_type,
                        occurred_at,location,confidence,bbox_xyxy)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                        (event_id,person_id,camera_id,track_id,event_type,when,location,confidence,
                         Jsonb(bbox_xyxy) if bbox_xyxy is not None else None)).fetchone()
            alert = conn.execute("""INSERT INTO hcc.alerts(fall_event_id) VALUES(%s)
                         ON CONFLICT (fall_event_id) DO NOTHING
                         RETURNING alert_id,fall_event_id,status""",(event_id,)).fetchone()
            if not alert:
                alert = conn.execute("""SELECT alert_id,fall_event_id,status FROM hcc.alerts
                                        WHERE fall_event_id=%s""",(event_id,)).fetchone()
        return {"event_id":event_id, "recorded": new is not None, "alert": alert}

    def acknowledge_alert(self, alert_id: str, staff_id: str | None = None) -> bool:
        with self._connect() as conn:
            return conn.execute("""UPDATE hcc.alerts SET status='ACKNOWLEDGED',acknowledged_at=NOW(),
                          handled_by=COALESCE(%s,handled_by)
                          WHERE alert_id=%s AND status='ACTIVE' RETURNING alert_id""",
                          (staff_id,alert_id)).fetchone() is not None

    def resolve_alert(self, alert_id: str, staff_id: str | None = None,
                      note: str | None = None) -> bool:
        with self._connect() as conn:
            return conn.execute("""UPDATE hcc.alerts SET status='RESOLVED',resolved_at=NOW(),
                          handled_by=COALESCE(%s,handled_by),resolution_note=%s
                          WHERE alert_id=%s AND status IN ('ACTIVE','ACKNOWLEDGED') RETURNING alert_id""",
                          (staff_id,note,alert_id)).fetchone() is not None

    def request_door_command(self, door_id: str, action: str, source: str = "GUI",
                             requested_by: str | None = None) -> dict:
        """Records a PENDING command. DOES NOT control physical hardware."""
        with self._connect() as conn:
            return conn.execute("""INSERT INTO hcc.door_commands(door_id,action,request_source,requested_by)
                 VALUES(%s,%s,%s,%s) RETURNING command_id,door_id,action,ack_status""",
                 (door_id,action,source,requested_by)).fetchone()

    def record_door_response(self, command_id: str, ack_status: str, *,
                             reported_state: str | None = None,
                             physical_verified: bool = False,
                             state_source: str = "MCU_REPORT",
                             error_code: str | None = None) -> dict:
        """ACK confirms command response only; physical_verified must be backed by real sensing."""
        if ack_status not in ("ACCEPTED", "ERROR", "TIMEOUT"):
            raise ValueError("ack_status must be ACCEPTED, ERROR, or TIMEOUT")
        if state_source == "SENSOR" and not physical_verified:
            raise ValueError("SENSOR state_source needs physical verification")
        if physical_verified and state_source != "SENSOR":
            raise ValueError("physical_verified requires state_source=SENSOR")
        with self._connect() as conn:
            row = conn.execute("""UPDATE hcc.door_commands SET ack_status=%s,
                         reported_state=%s,physical_verified=%s,responded_at=NOW(),error_code=%s
                         WHERE command_id=%s AND ack_status='PENDING'
                         RETURNING command_id,door_id,ack_status,reported_state,physical_verified""",
                        (ack_status,reported_state,physical_verified,error_code,command_id)).fetchone()
            if row and reported_state is not None and ack_status == 'ACCEPTED':
                conn.execute("""UPDATE hcc.doors SET reported_state=%s,state_source=%s,updated_at=NOW()
                            WHERE door_id=%s""",(reported_state,state_source,row['door_id']))
            if not row:
                row = conn.execute("""SELECT command_id,door_id,ack_status,reported_state,physical_verified
                                       FROM hcc.door_commands WHERE command_id=%s""",(command_id,)).fetchone()
            if row is None:
                raise ValueError("unknown command_id")
        return row

    def register_rfid(self, card_uid: str, person_id: str, *, valid_until: str | None = None) -> dict:
        expiration = require_aware_time(valid_until) if valid_until else None
        with self._connect() as conn:
            return conn.execute("""INSERT INTO hcc.rfid_cards(card_uid,person_id,valid_until)
                     VALUES(%s,%s,%s) ON CONFLICT(card_uid) DO UPDATE SET
                     person_id=EXCLUDED.person_id,valid_until=EXCLUDED.valid_until,is_active=TRUE
                     RETURNING card_uid,person_id,is_active,valid_until""",
                     (card_uid,person_id,expiration)).fetchone()

    def resolve_rfid(self, card_uid: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("""SELECT r.card_uid,r.person_id,p.person_type,p.can_open
                       FROM hcc.rfid_cards r JOIN hcc.persons p ON r.person_id=p.person_id
                       WHERE r.card_uid=%s AND r.is_active AND p.is_active
                       AND r.valid_from <= NOW()
                       AND (r.valid_until IS NULL OR r.valid_until > NOW())""",(card_uid,)).fetchone()
        return {"status":"MATCHED" if row else "UNKNOWN", "person_id":row['person_id'] if row else None,
                "access_eligible":bool(row and row['can_open'])}

    def record_access_decision(self, door_id: str, person_id: str | None = None,
                               identification_event_id: str | None = None,
                               command_id: str | None = None) -> dict:
        """Derive ALLOW/DENY from DB, not from a client-supplied boolean."""
        with self._connect() as conn:
            person = conn.execute("SELECT can_open,is_active FROM hcc.persons WHERE person_id=%s",(person_id,)).fetchone() if person_id else None
            grant = bool(person and person['is_active'] and person['can_open'])
            return conn.execute("""INSERT INTO hcc.access_events
               (door_id,person_id,identification_event_id,command_id,decision,reason)
               VALUES(%s,%s,%s,%s,%s,%s)
               RETURNING access_id,decision,reason,occurred_at""",
               (door_id,person_id if person else None,identification_event_id,command_id,
                'ALLOW' if grant else 'DENY', 'ACTIVE_PERMISSION' if grant else 'NO_ACTIVE_PERMISSION')).fetchone()
