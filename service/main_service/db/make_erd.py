"""Generate editable diagrams.net ERD (pure SVG-like mxGraph XML shapes)."""
from __future__ import annotations
import html
from pathlib import Path
from xml.etree import ElementTree as E

OUT = Path(__file__).with_name('HCC_ERD_v0.1.drawio')

TABLES = {
 'persons': ('인물 공통', 'IDENTITY', [
   ('person_id','VARCHAR(40)','PK'),('person_type','VARCHAR(20)',''),('name','VARCHAR(100)',''),
   ('gender','VARCHAR(20)',''),('birth_date','DATE',''),('can_open','BOOLEAN',''),
   ('is_active','BOOLEAN',''),('created_at','TIMESTAMPTZ',''),('updated_at','TIMESTAMPTZ','')]),
 'patient_profiles': ('환자 상세', 'IDENTITY', [
   ('person_id','VARCHAR(40)','PK, FK'),('height_cm','NUMERIC(5,1)',''),('weight_kg','NUMERIC(5,1)',''),
   ('blood_type','VARCHAR(3)',''),('medical_history','TEXT',''),('medications','TEXT',''),('room_number','VARCHAR(20)','')]),
 'face_templates': ('등록 얼굴', 'IDENTITY', [
   ('template_id','UUID','PK'),('person_id','VARCHAR(40)','FK'),('model','VARCHAR(50)',''),
   ('embedding','vector(512)',''),('embedding_normalized','BOOLEAN',''),('images_used','SMALLINT',''),
   ('is_active','BOOLEAN',''),('created_at','TIMESTAMPTZ','')]),
 'rfid_cards': ('RFID 카드', 'IDENTITY', [
   ('card_uid','VARCHAR(64)','PK'),('person_id','VARCHAR(40)','FK'),('is_active','BOOLEAN',''),
   ('valid_from','TIMESTAMPTZ',''),('valid_until','TIMESTAMPTZ',''),('created_at','TIMESTAMPTZ','')]),
 'cameras': ('카메라', 'DEVICE', [
   ('camera_id','VARCHAR(32)','PK'),('location','VARCHAR(100)',''),('camera_type','VARCHAR(20)',''),
   ('is_active','BOOLEAN',''),('last_seen_at','TIMESTAMPTZ','')]),
 'fall_events': ('낙상 이벤트', 'EVENT', [
   ('event_id','VARCHAR(64)','PK'),('person_id','VARCHAR(40)','FK?'),('camera_id','VARCHAR(32)','FK'),
   ('track_id','VARCHAR(64)',''),('event_type','VARCHAR(24)',''),('occurred_at','TIMESTAMPTZ',''),
   ('location','VARCHAR(100)',''),('confidence','REAL',''),('bbox_xyxy','JSONB',''),('received_at','TIMESTAMPTZ','')]),
 'alerts': ('알람 처리 이력', 'EVENT', [
   ('alert_id','UUID','PK'),('fall_event_id','VARCHAR(64)','FK, UQ'),('status','VARCHAR(20)',''),
   ('started_at','TIMESTAMPTZ',''),('acknowledged_at','TIMESTAMPTZ',''),('resolved_at','TIMESTAMPTZ',''),
   ('handled_by','VARCHAR(40)','FK?'),('resolution_note','TEXT','')]),
 'doors': ('출입문', 'DEVICE', [
   ('door_id','VARCHAR(32)','PK'),('location','VARCHAR(100)',''),('reported_state','VARCHAR(16)',''),
   ('state_source','VARCHAR(20)',''),('updated_at','TIMESTAMPTZ','')]),
 'identification_events': ('얼굴·RFID 식별 이력', 'EVENT', [
   ('event_id','VARCHAR(64)','PK'),('person_id','VARCHAR(40)','FK?'),('method','VARCHAR(12)',''),
   ('camera_id','VARCHAR(32)','FK?'),('reader_id','VARCHAR(32)',''),('result','VARCHAR(24)',''),
   ('similarity','REAL',''),('model','VARCHAR(50)',''),('occurred_at','TIMESTAMPTZ','')]),
 'access_events': ('출입 권한 판정 이력', 'EVENT', [
   ('access_id','UUID','PK'),('identification_event_id','VARCHAR(64)','FK?'),
   ('person_id','VARCHAR(40)','FK?'),('door_id','VARCHAR(32)','FK'),('decision','VARCHAR(12)',''),
   ('reason','VARCHAR(100)',''),('command_id','UUID','FK?'),('occurred_at','TIMESTAMPTZ','')]),
 'door_commands': ('도어 명령·ACK', 'DEVICE', [
   ('command_id','UUID','PK'),('door_id','VARCHAR(32)','FK'),('requested_by','VARCHAR(40)','FK?'),
   ('request_source','VARCHAR(20)',''),('action','VARCHAR(12)',''),('ack_status','VARCHAR(20)',''),
   ('reported_state','VARCHAR(16)',''),('physical_verified','BOOLEAN',''),('requested_at','TIMESTAMPTZ',''),
   ('responded_at','TIMESTAMPTZ',''),('error_code','VARCHAR(64)','')])
}

COLOR = {
 'IDENTITY': ('#EDF5FF','#2563A6','#D8E9FE'),
 'EVENT': ('#F2F0FF','#6252A3','#E4DEFF'),
 'DEVICE': ('#FFF4E9','#AD682A','#FFE4C8'),
}

# Relations are named foreign-key links, not assumed runtime message paths.
RELS = [
 ('persons','patient_profiles','1 : 0..1'),
 ('persons','face_templates','1 : N'),
 ('persons','rfid_cards','1 : N'),
 ('persons','fall_events','1 : N'),
 ('persons','identification_events','1 : N'),
 ('persons','alerts','1 : N / 처리자'),
 ('persons','door_commands','1 : N / 요청자'),
 ('persons','access_events','1 : N'),
 ('cameras','fall_events','1 : N'),
 ('cameras','identification_events','1 : N'),
 ('fall_events','alerts','1 : 0..1'),
 ('doors','door_commands','1 : N'),
 ('doors','access_events','1 : N'),
 ('identification_events','access_events','1 : N'),
 ('door_commands','access_events','1 : N')
]

layout_all = {
 'persons':(60,160), 'patient_profiles':(460,160),'face_templates':(860,160),'rfid_cards':(1260,160),
 'cameras':(60,660),'fall_events':(460,660),'alerts':(860,660),'doors':(1260,660),
 'identification_events':(60,1170),'access_events':(460,1170),'door_commands':(860,1170),
}

subsets = [
 ('전체 ERD · 11 tables', list(TABLES), layout_all, 1710, 1730),
 ('상세 1 · 인물과 인증', ['persons','patient_profiles','face_templates','rfid_cards','identification_events','access_events','doors','door_commands'], {
  'persons':(80,180),'patient_profiles':(505,180),'face_templates':(930,180),'rfid_cards':(1355,180),
  'identification_events':(80,770),'access_events':(505,770),'doors':(930,770),'door_commands':(1355,770)
 },1840,1330),
 ('상세 2 · 낙상과 장치', ['persons','cameras','fall_events','alerts','doors','door_commands'], {
  'persons':(80,170),'cameras':(530,170),'fall_events':(980,170),
  'alerts':(80,790),'doors':(530,790),'door_commands':(980,790)
 },1480,1400)
]

root = E.Element('mxfile', {'host':'app.diagrams.net','modified':'2026-10-19T00:00:00.000Z',
                            'agent':'HCC-ERD-generator','version':'24.7.17','type':'device'})


def add_vertex(mxroot, cid, label, x,y,w,h,style):
    cell=E.SubElement(mxroot,'mxCell',{'id':cid,'value':label,'style':style,'vertex':'1','parent':'1'})
    E.SubElement(cell,'mxGeometry',{'x':str(x),'y':str(y),'width':str(w),'height':str(h),'as':'geometry'})
    return cell

for idx,(page, keys, coords, width,height) in enumerate(subsets,1):
    diagram=E.SubElement(root,'diagram',{'id':f'hcc-erd-{idx}','name':page})
    model=E.SubElement(diagram,'mxGraphModel',{'dx':'1500','dy':'900','grid':'1','gridSize':'10','guides':'1',
                'tooltips':'1','connect':'1','arrows':'1','fold':'1','page':'1','pageScale':'1',
                'pageWidth':str(width),'pageHeight':str(height),'math':'0','shadow':'0'})
    mr=E.SubElement(model,'root');E.SubElement(mr,'mxCell',{'id':'0'});E.SubElement(mr,'mxCell',{'id':'1','parent':'0'})
    add_vertex(mr,f'p{idx}-title','HCC · PostgreSQL ERD v0.1',55,35,930,52,
        'rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=none;fontSize=28;fontStyle=1;fontColor=#1E3450;align=left;')
    add_vertex(mr,f'p{idx}-subtitle','PK 기본키  |  FK 외래키  |  ? NULL 허용  |  UQ 고유 제약  |  관계선은 DB 참조 관계',55,97,1150,33,
        'rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=none;fontSize=14;fontColor=#61738B;align=left;')
    cardw=354 if idx==1 else 368
    for key in keys:
        desc, group, fields=TABLES[key];x,y=coords[key];bg,ink,head=COLOR[group]
        rows=[f'<div style="font-family:Arial;line-height:1.35;font-size:12px;color:#27394A">']
        rows.append(f'<div style="font-size:16px;font-weight:bold;color:{ink};padding-bottom:2px">{html.escape(key)}</div>')
        rows.append(f'<div style="font-size:11px;color:#53667E;padding-bottom:8px">{html.escape(desc)}</div>')
        for field,datatype,tag in fields:
            fkcolor='#AA5F21' if 'FK' in tag else '#335B9E' if 'PK' in tag else '#556575'
            label=f'<span style="color:{fkcolor};font-weight:bold">{html.escape(tag)}</span> ' if tag else ''
            rows.append(f'<div style="border-top:1px solid #E0E5EB;padding-top:2px;">{label}<b>{html.escape(field)}</b> <span style="color:#64748B">{html.escape(datatype)}</span></div>')
        rows.append('</div>')
        # Fit to number of fields, with consistent row height.
        cardh=68 + 27*len(fields)
        value=''.join(rows)
        add_vertex(mr,f'p{idx}-{key}',value,x,y,cardw,cardh,
                   'rounded=1;arcSize=4;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacing=14;'
                   f'fillColor={bg};strokeColor={ink};strokeWidth=1.8;overflow=fill;')
    # Edges inserted after vertices; keep behind cards using mxGraph z-order sorting in editor.
    for j,(a,b,rel) in enumerate(RELS,1):
        if a not in keys or b not in keys: continue
        edge=E.SubElement(mr,'mxCell',{'id':f'p{idx}-edge{j}','value':rel,
            'style':'edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=22;'
                    'html=1;endArrow=classic;endFill=1;startArrow=none;strokeWidth=1.4;'
                    'strokeColor=#8999AF;fontSize=12;fontColor=#66788C;labelBackgroundColor=#FFFFFF;',
            'edge':'1','parent':'1','source':f'p{idx}-{a}','target':f'p{idx}-{b}'})
        E.SubElement(edge,'mxGeometry',{'relative':'1','as':'geometry'})
    add_vertex(mr,f'p{idx}-footer',
        '설계 기준: SR-01~06 / 실제 얼굴 인식 JSON buffalo_l·512차원 / 2026-10-19  |  SR-07 공식 정의 미확인  |  영상 원본은 DB에 저장하지 않음',
        55,height-65,width-100,35,
        'rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=none;fontSize=12;fontColor=#718096;align=left;')

E.indent(root,space='  ')
E.ElementTree(root).write(OUT,encoding='utf-8',xml_declaration=True)
print('Created:',OUT, 'bytes:',OUT.stat().st_size,'pages',len(subsets))
