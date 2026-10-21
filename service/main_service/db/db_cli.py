#!/usr/bin/env python3
"""Run local DB preparation and small PC1 integration tests (no HTTP server)."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from hcc_db import HCCDatabase
from validation import read_object


def show(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main():
    p = argparse.ArgumentParser(description="HCC PostgreSQL v0.1 CLI - use HCC_DB_DSN")
    cmd = p.add_subparsers(dest="command", required=True)
    cmd.add_parser("init", help="create PostgreSQL schema and pgvector extension")
    cmd.add_parser("health", help="print database and tables")
    cmd.add_parser("persons", help="list registered people without sensitive embedding data")
    patient = cmd.add_parser("add-patient", help="register / replace a patient's basic profile")
    patient.add_argument("--id", required=True)
    patient.add_argument("--name", required=True)
    patient.add_argument("--gender")
    patient.add_argument("--birth-date")
    patient.add_argument("--height", type=float)
    patient.add_argument("--weight", type=float)
    patient.add_argument("--blood-type")
    patient.add_argument("--room")
    imported = cmd.add_parser("import-face", help="load one PC2 registration JSON")
    imported.add_argument("--file", required=True)
    impdir = cmd.add_parser("import-faces", help="load all registration JSONs in a directory")
    impdir.add_argument("--dir", required=True)
    match = cmd.add_parser("match-face", help="identify one PC2 captured face JSON")
    match.add_argument("--file", required=True)
    match.add_argument("--threshold", type=float, default=0.5)
    cam = cmd.add_parser("add-camera", help="register camera before fall event")
    cam.add_argument("--id", required=True)
    cam.add_argument("--type", default="CCTV", choices=["CCTV","FACE","TRACKING","OTHER"])
    cam.add_argument("--location")
    door = cmd.add_parser("add-door", help="register door before command")
    door.add_argument("--id", required=True)
    door.add_argument("--location")
    fall = cmd.add_parser("demo-fall", help="save a synthetic FALL event and alert (NOT real AI)")
    fall.add_argument("--camera", required=True)
    command = cmd.add_parser("demo-door", help="save a PENDING command only (NOT real motor control)")
    command.add_argument("--door", required=True)
    command.add_argument("--action", default="STATUS", choices=["OPEN","CLOSE","STATUS"])
    deactivate = cmd.add_parser("deactivate", help="deactivate person + face templates/cards, preserve history")
    deactivate.add_argument("--id", required=True)
    a = p.parse_args()
    db = HCCDatabase()
    if a.command == "init":
        db.init_schema(); show({"schema":"initialized", "health":db.health()})
    elif a.command == "health": show(db.health())
    elif a.command == "persons": show(db.list_persons())
    elif a.command == "add-patient":
        db.upsert_patient(a.id,a.name,gender=a.gender,birth_date=a.birth_date,height_cm=a.height,
            weight_kg=a.weight,blood_type=a.blood_type,room_number=a.room)
        show({"status":"saved","patient_id":a.id})
    elif a.command == "import-face": show(db.import_registered_face(read_object(a.file)))
    elif a.command == "import-faces":
        files=sorted(Path(a.dir).glob("*.json"))
        if not files: raise SystemExit("No *.json files found")
        show([db.import_registered_face(read_object(f)) for f in files])
    elif a.command == "match-face":
        show(db.match_face(read_object(a.file),threshold=a.threshold))
    elif a.command == "add-camera": show(db.register_camera(a.id,a.type,a.location))
    elif a.command == "add-door": show(db.register_door(a.id,a.location))
    elif a.command == "demo-fall":
        show(db.record_fall(event_id=f"demo-{uuid4().hex}",camera_id=a.camera,
                            occurred_at=datetime.now(timezone.utc).isoformat(),
                            confidence=0.95, location="DEMO: synthetic fall"))
    elif a.command == "demo-door": show(db.request_door_command(a.door,a.action,source="GUI"))
    elif a.command == "deactivate": show({"deactivated": db.deactivate_person(a.id)})


if __name__ == "__main__":
    main()
