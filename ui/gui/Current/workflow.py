import os,json,uuid,threading,math
from datetime import date,datetime
from zoneinfo import ZoneInfo
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
from PyQt6 import QtWidgets as W,QtCore as C
from event_store import Store
from event_bridge import Signals,Bridge,validate
class WorkflowMixin:
 def init_store(self,root):
  self.root=root;self.db_mode=os.getenv('GUI_DB_MODE','local').lower();self.store=Store(root,seed=self.db_mode!='external');self.people=self.store.people();self.card_uid=None;self.active_registration=None;self.pending={};self.pending_lock=threading.Lock();self.seen=set();self.db_mode=os.getenv('GUI_DB_MODE','local').lower()
  if self.db_mode not in ['local','external']:raise ValueError('GUI_DB_MODE must be local or external')
 def init_workflow(self):
  self.events=Signals();self.events.incoming.connect(self.receive_event);self.bridge=None;self.reset_status()
  self.confirming_alert_id=None;self.acknowledged_alert_id=None;self.resolving_alert_id=None
  self.events.confirmation_result.connect(self.confirmation_result)
  self.events.resolution_result.connect(self.resolution_result)
  d=self.windows['door_access'];d.identityName.setText('Waiting');d.identityId.setText('ID  —')
  if hasattr(d,'identityRole'):d.identityRole.setText('—')
  for key in ['sex','birth','blood','rh','height','weight','disease','medicine']:getattr(d,'value_'+key).setText('—')
  d.accessStatus.setText('Waiting for recognition');self.update_door('UNKNOWN',False)
  c=self.windows['cctv'];c.confirmButton.clicked.disconnect();c.confirmButton.clicked.connect(self.confirm_fall);self.set_fall_detected(False)
  port=int(os.getenv('GUI_EVENT_PORT','8765'))
  if port:
   try:self.bridge=Bridge(self.events,self.store,self.request_snapshot,self.root,port);self.app.aboutToQuit.connect(self.bridge.close)
   except (OSError,ValueError) as exc:W.QMessageBox.warning(self.windows['home'],'Event connection',str(exc))
 def confirm_fall(self):
  """Acknowledge the central alert; SQLite records only the GUI-side audit log."""
  c=self.windows['cctv'];alert_id=getattr(self,'active_alert_id',None)
  if not alert_id:
   self.record('Fall confirmation unavailable',details='No central alert ID was supplied')
   return
  if alert_id==getattr(self,'acknowledged_alert_id',None):
   self.resolve_fall()
   return
  self.confirming_alert_id=alert_id
  c.confirmButton.setText('Confirming…');c.confirmButton.setEnabled(False)
  base=os.getenv('TSAFE_PC1_URL','http://127.0.0.1:8101').rstrip('/')
  staff_id=os.getenv('TSAFE_STAFF_ID','').strip()
  def request_confirmation():
   try:
    payload={} if not staff_id else {'staff_id':staff_id}
    req=Request(base+'/api/v1/alerts/'+alert_id+'/acknowledge',data=json.dumps(payload).encode(),method='POST',headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=5) as response:result=json.load(response)
    self.events.confirmation_result.emit({'alert_id':alert_id,'success':result.get('status')=='ACKNOWLEDGED'})
   except HTTPError as exc:self.events.confirmation_result.emit({'alert_id':alert_id,'success':False,'message':'Server HTTP '+str(exc.code)})
   except (URLError,TimeoutError,OSError,ValueError) as exc:self.events.confirmation_result.emit({'alert_id':alert_id,'success':False,'message':'Server connection failed'})
  threading.Thread(target=request_confirmation,name='fall-acknowledge',daemon=True).start()
 def resolve_fall(self):
  """Close an acknowledged central alert; video tracking remains independent."""
  c=self.windows['cctv'];alert_id=getattr(self,'active_alert_id',None)
  if not alert_id or alert_id!=getattr(self,'acknowledged_alert_id',None):return
  self.resolving_alert_id=alert_id
  c.confirmButton.setText('Resolving…');c.confirmButton.setEnabled(False)
  base=os.getenv('TSAFE_PC1_URL','http://127.0.0.1:8101').rstrip('/')
  staff_id=os.getenv('TSAFE_STAFF_ID','').strip()
  def request_resolution():
   try:
    payload={} if not staff_id else {'staff_id':staff_id}
    req=Request(base+'/api/v1/alerts/'+alert_id+'/resolve',data=json.dumps(payload).encode(),method='POST',headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=5) as response:result=json.load(response)
    self.events.resolution_result.emit({'alert_id':alert_id,'success':result.get('status')=='RESOLVED'})
   except HTTPError as exc:self.events.resolution_result.emit({'alert_id':alert_id,'success':False,'message':'Server HTTP '+str(exc.code)})
   except (URLError,TimeoutError,OSError,ValueError) as exc:self.events.resolution_result.emit({'alert_id':alert_id,'success':False,'message':'Server connection failed'})
  threading.Thread(target=request_resolution,name='fall-resolve',daemon=True).start()
 def confirmation_result(self,result):
  c=self.windows['cctv']
  if result.get('alert_id')!=getattr(self,'active_alert_id',None):return
  if result.get('success'):
   self.confirming_alert_id=None;self.acknowledged_alert_id=result['alert_id'];c.fallAlert.setText('CONFIRMED');c.confirmButton.setText('상황 종료');c.confirmButton.setEnabled(True);self.record('Fall confirmed',details='Central server acknowledged',payload={'event_id':'fall-ack-'+result['alert_id']})
  else:
   self.confirming_alert_id=None;c.confirmButton.setText('Confirm');c.confirmButton.setEnabled(True);self.record('Fall confirm failed',details=result.get('message','Central server rejected acknowledgement'))
 def resolution_result(self,result):
  c=self.windows['cctv']
  if result.get('alert_id')!=getattr(self,'active_alert_id',None):return
  self.resolving_alert_id=None
  if result.get('success'):
   self.active_alert_id=None;self.acknowledged_alert_id=None;self.set_fall_detected(False);self.record('Fall resolved',details='Central server resolved incident',payload={'event_id':'fall-resolve-'+result['alert_id']})
  else:
   c.confirmButton.setText('상황 종료');c.confirmButton.setEnabled(True);self.record('Fall resolve failed',details=result.get('message','Central server rejected resolution'))
 def request_snapshot(self):
  with self.pending_lock:return json.loads(json.dumps(list(self.pending.values())))
 def queue(self,data):
  data=dict(data,request_id=uuid.uuid4().hex)
  with self.pending_lock:self.pending[data['request_id']]=data
  self.events.request_ready.emit(dict(data));return data['request_id']
 def status(self,name,text,kind='waiting'):
  c=getattr(self.windows['register'],name);c.setText(text);color={'waiting':('#f0f3f0','#60786c'),'ok':('#e1f2e7','#217449'),'fail':('#fde8e5','#b63d36'),'pending':('#fff3d9','#8b671f')}[kind];c.setStyleSheet(f'background:{color[0]};color:{color[1]};border:1px solid #d4e2db;border-radius:5px;padding:8px;font-size:17px;');c.setWordWrap(True)
 def reset_status(self):
  self.status('cardStatus','Card: '+self.card_uid if self.card_uid else 'Card: Not detected','ok' if self.card_uid else 'waiting');self.status('dbStatus','DB: Not saved');self.status('cardWriteStatus','Card write: Not written');self.windows['register'].saveButton.setEnabled(True)
 def read_form(self):
  w=self.windows['register'];p={}
  for k in ['name','id','role','sex','birth','blood','rh','height','weight','disease','medicine']:
   c=getattr(w,k);p[k]=(c.currentText() if isinstance(c,W.QComboBox) else c.toPlainText() if isinstance(c,W.QTextEdit) else c.text()).strip()
  if self.db_mode=='external' and not p['id']:p['id']='pending_'+uuid.uuid4().hex[:12]
  if not p['name'] or not p['id']:raise ValueError('Name and ID are required.')
  if not self.edit_id and any(x['id']==p['id'] for x in self.people):raise ValueError('This ID already exists.')
  if date.fromisoformat(p['birth'])>date.today():raise ValueError('Birth cannot be in the future.')
  for k in ['height','weight']:
   if not math.isfinite(float(p[k])) or float(p[k])<=0:raise ValueError('Height and Weight must be positive finite numbers.')
  old=next((x for x in self.people if x['id']==self.edit_id),None);p['added']=old['added'] if old else date.today().isoformat();return p
 def save(self):
  w=self.windows['register']
  try:p=self.read_form()
  except (ValueError,TypeError) as exc:W.QMessageBox.warning(w,'Check information',str(exc));return False
  operation=uuid.uuid4().hex;self.active_registration={'operation':operation,'person':p,'old_id':self.edit_id,'uid':self.card_uid,'db_saved':False,'card_queued':False}
  self.status('cardWriteStatus','Card write: Waiting');self.status('dbStatus','DB: Saving…','pending')
  if self.db_mode=='external':
   self.queue({'type':'db_save','operation':operation,'person':p,'old_id':self.edit_id});w.saveButton.setEnabled(False);w.saveStatus.setText('Waiting for DB server result');return True
  try:self.finish_db(self.active_registration)
  except Exception as exc:self.status('dbStatus','DB: Failed','fail');self.status('cardWriteStatus','Card write: Not started');w.saveStatus.setText(str(exc));return False
  return True
 def finish_db(self,registration):
  p=registration['person'];self.store.save_person(p,registration.get('old_id'));registration['db_saved']=True;self.people=self.store.people();self.reset_people();self.record('Registration',p['name'],p['id'],'DB saved ('+self.db_mode+')')
  if self.active_registration and registration['operation']==self.active_registration['operation']:
   self.active_registration=registration;self.edit_id=p['id'];self.windows['register'].id.setText(p['id']);self.windows['register'].id.setReadOnly(True);self.windows['register'].saveButton.setEnabled(True);self.status('dbStatus','DB: Saved'+(' (local)' if self.db_mode=='local' else ' (server)'),'ok');self.windows['register'].saveStatus.setText('DB saved. Waiting for card write result.');self.queue_card()
 def queue_card(self):
  r=self.active_registration
  if not r or not r['db_saved'] or r['card_queued']:return
  if not r['uid']:
   if not self.card_uid:self.status('cardWriteStatus','Card write: Waiting for card','pending');return
   r['uid']=self.card_uid
  r['card_queued']=True;self.queue({'type':'card_write','operation':r['operation'],'uid':r['uid'],'person':r['person']});self.status('cardWriteStatus','Card write: Waiting for device','pending')
 def delete(self):
  p=self.selected()
  if not p:return
  if self.db_mode=='external':
   if W.QMessageBox.question(self.windows['people'],'Deactivate person',f"Deactivate {p['name']}?",W.QMessageBox.StandardButton.Yes|W.QMessageBox.StandardButton.No)==W.QMessageBox.StandardButton.Yes:self.queue({'type':'db_delete','person':p});self.windows['people'].deleteButton.setEnabled(False)
   return
  if W.QMessageBox.question(self.windows['people'],'Delete person',f"Delete {p['name']}?",W.QMessageBox.StandardButton.Yes|W.QMessageBox.StandardButton.No)!=W.QMessageBox.StandardButton.Yes:return
  try:self.store.delete_person(p['id']);self.record('Person deleted',p['name'],p['id'],'Local GUI DB');self.people=self.store.people();self.refresh()
  except Exception as exc:W.QMessageBox.warning(self.windows['people'],'Delete failed',str(exc))
 def record(self,event,name='Unknown',person_id=None,details='',payload=None):
  e=payload or {};self.store.event(event,name,person_id,details,e.get('event_id'),e.get('timestamp'));self.setup_logs()
 def setup_logs(self):
  w=self.windows['logs'];rows=self.store.events();self.logs=[];w.logsTable.setRowCount(len(rows))
  for i,r in enumerate(rows):
   try:time=datetime.fromisoformat(r['time']).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S')
   except ValueError:time=r['time']
   vals=[time,r['event'],r['name'],r['details']];self.logs.append(vals)
   for j,value in enumerate(vals):w.logsTable.setItem(i,j,W.QTableWidgetItem(value))
  selected=w.eventFilter.currentText();w.eventFilter.blockSignals(True);w.eventFilter.clear();w.eventFilter.addItems(['All events']+sorted(set(r['event'] for r in rows)));w.eventFilter.setCurrentText(selected if selected in [w.eventFilter.itemText(i) for i in range(w.eventFilter.count())] else 'All events');w.eventFilter.blockSignals(False)
  if not getattr(self,'logs_connected',False):
   w.search.textChanged.connect(self.filter_logs);w.dateFilter.textChanged.connect(self.filter_logs);w.eventFilter.currentTextChanged.connect(self.filter_logs);w.resetButton.clicked.connect(self.reset_logs);self.logs_connected=True
  self.filter_logs()
 def filter_logs(self,*args):
  w=self.windows['logs']
  for i,row in enumerate(self.logs):w.logsTable.setRowHidden(i,not(w.search.text().lower() in row[2].lower() and (w.eventFilter.currentIndex()==0 or w.eventFilter.currentText()==row[1]) and w.dateFilter.text() in row[0]))
 def reset_logs(self):
  w=self.windows['logs'];w.search.clear();w.dateFilter.clear();w.eventFilter.setCurrentIndex(0)
 def update_door(self,state,record=True,payload=None):
  w=self.windows['door_access'];w.doorState.setText(state);bg={'OPEN':'#21843d','CLOSED':'#bf4943','UNKNOWN':'#7b8882'}[state];w.doorState.setStyleSheet(f'background:{bg};color:white;border-radius:8px;font-size:44px;font-weight:700;')
  if record:self.record({'OPEN':'Door opened','CLOSED':'Door closed','UNKNOWN':'Door unknown'}[state],details='Door sensor',payload=payload)
 def recognize(self,e):
  w=self.windows['door_access'];p=next((p for p in self.people if p['id']==e.get('person_id')),{});w.identityName.setText(e['name']);w.identityId.setText('ID  '+(e.get('person_id') or '—'))
  if hasattr(w,'identityRole'):w.identityRole.setText(e['role'])
  for key in ['sex','birth','blood','rh','height','weight','disease','medicine']:getattr(w,'value_'+key).setText(str(p.get(key,'—'))+(' cm' if key=='height' and key in p else ' kg' if key=='weight' and key in p else ''))
  text='Allowed' if e['allowed'] else 'Denied';w.accessStatus.setText(text);w.accessStatus.setStyleSheet('font-size:25px;font-weight:700;color:'+('#217449' if e['allowed'] else '#b63d36')+';');self.record('Access allowed' if e['allowed'] else 'Access denied',e['name'],e.get('person_id'),'Recognition result · '+e['role'],e)
 def receive_event(self,e):
  try:
   validate(e)
   if e.get('event_id') in self.seen:return
   typ=e['type']
   if typ=='card':
    self.card_uid=e['uid'].strip() if e['present'] else None;self.status('cardStatus','Card: '+self.card_uid if self.card_uid else 'Card: Not detected','ok' if self.card_uid else 'waiting');self.record('Card detected' if self.card_uid else 'Card removed',details='Reader event',payload=e);self.queue_card()
   elif typ=='patients_sync':
    if self.db_mode!='external':raise ValueError('Patient sync requires external DB mode')
    self.store.replace_people(e['items']);self.people=self.store.people();self.refresh()
   elif typ=='db_delete_result':
    with self.pending_lock:req=self.pending.get(e['request_id'])
    if req is None or req['type']!='db_delete':raise ValueError('Unknown delete request')
    p=req['person']
    if e['success']:self.store.delete_person(p['id']);self.people=self.store.people();self.refresh();self.record('Person deactivated',p['name'],p['id'],'DB server confirmed',e)
    else:self.record('Delete failed',p['name'],p['id'],e.get('message','DB server failed'),e);self.windows['people'].deleteButton.setEnabled(True)
    with self.pending_lock:self.pending.pop(e['request_id'],None)
   elif typ in ['db_result','card_write_result']:
    with self.pending_lock:req=self.pending.get(e['request_id'])
    if req is None:raise ValueError('Unknown or completed request_id')
    if req['type']!=('db_save' if typ=='db_result' else 'card_write'):raise ValueError('Result type does not match request')
    if typ=='card_write_result' and e.get('uid')!=req['uid']:raise ValueError('Card UID does not match pending write')
    if typ=='db_result' and e['success'] and (not isinstance(e.get('person_id'),str) or not e['person_id'].strip()):e=dict(e,success=False,message='PC1 response is missing person_id')
    active=self.active_registration and self.active_registration['operation']==req['operation']
    if typ=='db_result':
     r={'operation':req['operation'],'person':dict(req['person']),'old_id':req.get('old_id'),'uid':self.active_registration.get('uid') if active else None,'db_saved':False,'card_queued':False}
     if e['success']:
      if e.get('person_id'):r['person']['id']=e['person_id']
      self.finish_db(r)
     else:
      self.record('DB save failed',req['person']['name'],req['person']['id'],e.get('message','DB server rejected save'),e)
      if active:self.status('dbStatus','DB: Failed','fail');self.status('cardWriteStatus','Card write: Not started');self.windows['register'].saveButton.setEnabled(True)
    else:
     self.record('Card written' if e['success'] else 'Card write failed',req['person']['name'],req['person']['id'],e.get('message','Device result'),e)
     if active:self.status('cardWriteStatus','Card write: '+('Success' if e['success'] else 'Failed'),'ok' if e['success'] else 'fail');self.windows['register'].saveStatus.setText('DB saved · Card '+('written' if e['success'] else 'write failed; press Save to retry'))
    with self.pending_lock:self.pending.pop(e['request_id'],None)
   elif typ=='recognition':self.recognize(e)
   elif typ=='door':self.update_door(e['state'],True,e)
   elif typ=='fall':
    if e['detected'] and e.get('alert_id'):
     if e['alert_id']!=getattr(self,'active_alert_id',None):self.confirming_alert_id=None;self.acknowledged_alert_id=None;self.resolving_alert_id=None
     self.active_alert_id=e['alert_id']
    if not e['detected'] and e.get('alert_id')==getattr(self,'active_alert_id',None):self.active_alert_id=None;self.confirming_alert_id=None;self.acknowledged_alert_id=None;self.resolving_alert_id=None
    self.set_fall_detected(e['detected'])
    if e['detected']:self.record('Fall',details='Camera 01',payload=e)
   if e.get('event_id'):self.seen.add(e['event_id'])
  except Exception as exc:self.windows['register'].saveStatus.setText('Event rejected: '+str(exc))
