import os,sys,unittest,tempfile,shutil,json,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ['GUI_EVENT_PORT']='0'
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE))
import run
from event_bridge import Bridge,validate
from PyQt6.QtWidgets import QMessageBox
class WorkflowTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'GUI';shutil.copytree(BASE,self.root,ignore=shutil.ignore_patterns('*.sqlite3','gui_event.token','__pycache__','tests'));run.ROOT=self.root;os.environ['GUI_DB_MODE']='local';self.a=run.App()
 def tearDown(self):
  if self.a.bridge:self.a.bridge.close()
  for w in self.a.windows.values():w.close()
  self.tmp.cleanup()
 def fill(self):
  self.a.add();w=self.a.windows['register']
  for k,v in {'name':'Test Patient','id':'TEST001','birth':'1960-01-02','height':'170','weight':'65'}.items():getattr(w,k).setText(v)
 def test_card_save_and_results(self):
  a=self.a;a.receive_event({'type':'card','present':True,'uid':'CARD1'});self.fill();self.assertTrue(a.save());self.assertEqual(len(a.store.people()),5)
  req=a.request_snapshot()[0];self.assertEqual(req['type'],'card_write');self.assertIn('Waiting',a.windows['register'].cardWriteStatus.text())
  a.receive_event({'type':'card_write_result','request_id':req['request_id'],'uid':'WRONG','success':True});self.assertEqual(len(a.request_snapshot()),1)
  e={'type':'card_write_result','request_id':req['request_id'],'uid':'CARD1','success':True,'event_id':'result1'};a.receive_event(e);a.receive_event(e)
  self.assertEqual(len([r for r in a.store.events() if r['event']=='Card written']),1);self.assertIn('Success',a.windows['register'].cardWriteStatus.text());self.assertEqual(len(a.request_snapshot()),0)
  # DB cache and event history survive creating a new Store.
  from event_store import Store
  store=Store(self.root);self.assertEqual(len(store.people()),5);self.assertTrue(store.events())
 def test_recognition_does_not_change_door(self):
  a=self.a;d=a.windows['door_access'];self.assertEqual(d.doorState.text(),'UNKNOWN')
  a.receive_event({'type':'recognition','person_id':'P-002','name':'Jamie Lee','role':'Patient','allowed':False});self.assertEqual(d.accessStatus.text(),'Denied');self.assertEqual(d.doorState.text(),'UNKNOWN')
  a.receive_event({'type':'recognition','person_id':'S-001','name':'Robin Park','role':'Staff','allowed':True});self.assertEqual(d.accessStatus.text(),'Allowed');self.assertEqual(d.identityName.text(),'Robin Park');self.assertEqual(d.doorState.text(),'UNKNOWN')
  a.receive_event({'type':'door','state':'OPEN'});self.assertEqual(d.doorState.text(),'OPEN')
  a.receive_event({'type':'door','state':'CLOSED'});self.assertEqual(d.doorState.text(),'CLOSED')
  a.receive_event({'type':'fall','detected':True,'name':'IGNORED'});self.assertEqual(a.windows['cctv'].fallAlert.text(),'FALL');self.assertEqual(a.store.events()[0]['name'],'Unknown')
  a.windows['cctv'].confirmButton.click();self.assertEqual(a.store.events()[0]['event'],'Fall confirmed');self.assertEqual(a.windows['cctv'].fallAlert.text(),'FALL')
  w=a.windows['logs'];w.search.setText('Robin');self.assertEqual(sum(not w.logsTable.isRowHidden(i) for i in range(w.logsTable.rowCount())),1)
 def test_external_db_then_card(self):
  a=self.a;a.db_mode='external';a.receive_event({'type':'card','present':True,'uid':'CARD1'});self.fill();self.assertTrue(a.save());req=a.request_snapshot()[0];self.assertEqual(req['type'],'db_save');self.assertEqual(len(a.store.people()),4)
  a.receive_event({'type':'db_result','request_id':req['request_id'],'success':False});self.assertIn('Failed',a.windows['register'].dbStatus.text());self.assertEqual(a.request_snapshot(),[])
  self.assertTrue(a.save());req=a.request_snapshot()[0];a.receive_event({'type':'db_result','request_id':req['request_id'],'success':True,'person_id':'SERVER123'})
  self.assertEqual(a.windows['register'].id.text(),'SERVER123');self.assertEqual(a.request_snapshot()[0]['type'],'card_write');self.assertIn('server',a.windows['register'].dbStatus.text())
 def test_empty_logs_and_delete(self):
  a=self.a;self.assertEqual(a.windows['logs'].logsTable.rowCount(),0);a.reset_people();a.windows['people'].search.setText('P-001');orig=QMessageBox.question;QMessageBox.question=lambda *args:QMessageBox.StandardButton.Yes
  try:a.delete()
  finally:QMessageBox.question=orig
  self.assertEqual(len(a.store.people()),3);self.assertEqual(a.store.events()[0]['event'],'Person deleted')
 def test_authenticated_event_bridge(self):
  a=self.a;b=Bridge(a.events,a.store,a.request_snapshot,self.root,0)
  try:
   base='http://127.0.0.1:'+str(b.server.server_address[1])
   with self.assertRaises(HTTPError):urlopen(base+'/health')
   request=Request(base+'/events',data=json.dumps({'type':'door','state':'OPEN'}).encode(),headers={'Authorization':'Bearer '+b.token,'Content-Type':'application/json'})
   with urlopen(request) as r:self.assertEqual(r.status,202)
   for _ in range(20):a.app.processEvents();time.sleep(.005)
   self.assertEqual(a.windows['door_access'].doorState.text(),'OPEN')
   with urlopen(Request(base+'/events',headers={'Authorization':'Bearer '+b.token})) as r:self.assertEqual(json.load(r)['items'][0]['event'],'Door opened')
   with self.assertRaises(ValueError):validate({'type':'recognition','name':'Bad','role':'Patient'})
  finally:b.close()
if __name__=='__main__':unittest.main()
