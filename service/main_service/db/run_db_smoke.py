#!/usr/bin/env python3
"""DB-only smoke test: creates temporary synthetic rows; cleans them in finally.

Requires an initialized HCC_DB_DSN, and DB containing NO precious data with the DEMO prefix.
This does not send HTTP requests or control hardware. The vector is synthetic, not a face.
"""
from datetime import datetime, timezone
import json
import math
from uuid import uuid4
from hcc_db import HCCDatabase


def main():
    db=HCCDatabase()
    unique=uuid4().hex[:12]
    patient_id=f"DEMO-P-{unique}"
    camera_id=f"DEMO-C-{unique}"
    door_id=f"DEMO-D-{unique}"
    card_id=f"DEMO-RFID-{unique}"
    event_id=f"DEMO-FACE-{unique}"
    fall_id=f"DEMO-FALL-{unique}"
    now=datetime.now(timezone.utc).isoformat()
    vector=[1.0]+[0.0]*511
    print("[NOTICE] synthetic DB smoke test; NO real AI inference and NO motor control")
    command=None
    try:
        db.upsert_patient(patient_id,"Synthetic Test Patient",height_cm=170,weight_kg=60)
        face=db.import_registered_face({"patient_id":patient_id,"model":"buffalo_l","embedding_dim":512,
                                  "embedding":vector,"images_used":1,"created_at":now})
        result=db.match_face({"event_id":event_id,"status":"OK","model":"buffalo_l",
                              "embedding_dim":512,"embedding":vector,"timestamp":now})
        assert result['person_id']==patient_id and result['result']=='MATCHED',result
        assert result['access_eligible'] is False, 'default patient access must be DENIED'
        duplicate=db.match_face({"event_id":event_id,"status":"OK","model":"buffalo_l",
                                 "embedding_dim":512,"embedding":vector,"timestamp":now})
        assert duplicate['duplicate']
        db.register_rfid(card_id,patient_id)
        assert db.resolve_rfid(card_id)['person_id']==patient_id
        db.register_camera(camera_id,'CCTV','DEMO-location')
        db.register_door(door_id,'DEMO-location')
        fall=db.record_fall(event_id=fall_id,camera_id=camera_id,occurred_at=now,
                            location='DEMO-location',confidence=0.95,bbox_xyxy=[0,0,100,100])
        assert fall['recorded']
        assert not db.record_fall(event_id=fall_id,camera_id=camera_id,occurred_at=now)['recorded']
        access=db.record_access_decision(door_id,patient_id,event_id)
        assert access['decision']=='DENY'  # no door OPEN command on denied access
        command=db.request_door_command(door_id,'STATUS','GUI')
        assert command['ack_status']=='PENDING'
        simulated=db.record_door_response(str(command['command_id']),'ACCEPTED',
                              reported_state='CLOSED',physical_verified=False,state_source='SIMULATED')
        assert not simulated['physical_verified']
        assert db.acknowledge_alert(str(fall['alert']['alert_id']))
        assert db.resolve_alert(str(fall['alert']['alert_id']),note='DEMO test resolved')
        print(json.dumps({"status":"PASS","synthetic_face_match":result['result'],
                          "access_policy":access['decision'],"fall_recorded":True,
                          "fall_duplicate_suppressed":True,"door_ack":"SIMULATED","physical_motor_test":False},
                         ensure_ascii=False,indent=2))
    finally:
        # Clean only the IDs generated during this run; retain pre-existing rows untouched.
        with db._connect() as conn:
            conn.execute("DELETE FROM hcc.access_events WHERE door_id=%s",(door_id,))
            conn.execute("DELETE FROM hcc.door_commands WHERE door_id=%s",(door_id,))
            conn.execute("DELETE FROM hcc.alerts WHERE fall_event_id=%s",(fall_id,))
            conn.execute("DELETE FROM hcc.fall_events WHERE event_id=%s",(fall_id,))
            conn.execute("DELETE FROM hcc.identification_events WHERE event_id=%s",(event_id,))
            conn.execute("DELETE FROM hcc.rfid_cards WHERE card_uid=%s",(card_id,))
            conn.execute("DELETE FROM hcc.face_templates WHERE person_id=%s",(patient_id,))
            conn.execute("DELETE FROM hcc.patient_profiles WHERE person_id=%s",(patient_id,))
            conn.execute("DELETE FROM hcc.persons WHERE person_id=%s",(patient_id,))
            conn.execute("DELETE FROM hcc.cameras WHERE camera_id=%s",(camera_id,))
            conn.execute("DELETE FROM hcc.doors WHERE door_id=%s",(door_id,))
        print('[CLEANUP] synthetic demo rows removed')

if __name__=='__main__':
    main()
