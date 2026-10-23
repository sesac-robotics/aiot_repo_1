"""Authenticated local event ingress; no serial or door actuation happens here."""
import json,os,secrets,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from datetime import datetime
from PyQt6.QtCore import QObject,pyqtSignal
class Signals(QObject):
 incoming=pyqtSignal(dict)
 request_ready=pyqtSignal(dict)
def validate(e):
 if not isinstance(e,dict):raise ValueError('JSON object required')
 typ=e.get('type')
 if typ not in ['card','card_write_result','db_result','recognition','door','fall','patients_sync','db_delete_result']:raise ValueError('Unsupported type')
 if typ=='card':
  if type(e.get('present')) is not bool:raise ValueError('present must be boolean')
  if e['present'] and (not isinstance(e.get('uid'),str) or not e['uid'].strip()):raise ValueError('uid required')
 elif typ in ['card_write_result','db_result','db_delete_result']:
  if not isinstance(e.get('request_id'),str) or type(e.get('success')) is not bool:raise ValueError('request_id and boolean success required')
 elif typ=='patients_sync':
  if not isinstance(e.get('items'),list) or len(e['items'])>10000:raise ValueError('items must be a list')
  required=['id','name','role','sex','birth','blood','rh','height','weight','disease','medicine','added']
  for p in e['items']:
   if not isinstance(p,dict) or any(not isinstance(p.get(k),str) for k in required):raise ValueError('Invalid person fields')
 elif typ=='recognition':
  if type(e.get('allowed')) is not bool:raise ValueError('Authoritative allowed boolean required')
  if e.get('role') not in ['Patient','Staff','Admin','Unknown']:raise ValueError('role must be Patient, Staff, Admin or Unknown')
  if not isinstance(e.get('name'),str) or not e['name'].strip():raise ValueError('name required')
 elif typ=='door':
  if e.get('state') not in ['OPEN','CLOSED','UNKNOWN']:raise ValueError('Invalid door state')
 elif typ=='fall':
  if type(e.get('detected')) is not bool:raise ValueError('detected must be boolean')
 for key,value in e.items():
  if isinstance(value,str) and len(value)>4096:raise ValueError('String too long')
 if e.get('timestamp'):
  dt=datetime.fromisoformat(e['timestamp'].replace('Z','+00:00'))
  if dt.tzinfo is None:raise ValueError('Timestamp requires timezone')
 return e
class Bridge:
 def __init__(self,signals,store,request_snapshot,root,port=8765):
  self.signals=signals;self.store=store;self.request_snapshot=request_snapshot
  path=root/'data/gui_event.token';self.token=os.getenv('GUI_EVENT_TOKEN','')
  if not self.token:
   if not path.exists():
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(32))
   self.token=path.read_text().strip()
  if len(self.token)<16:raise ValueError('GUI_EVENT_TOKEN needs at least 16 characters')
  bridge=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def reply(self,status,data):
    body=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
   def authorized(self):return secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+bridge.token)
   def do_GET(self):
    if not self.authorized():self.reply(401,{'error':'Unauthorized'});return
    if self.path=='/health':self.reply(200,{'status':'ready'})
    elif self.path=='/requests':self.reply(200,{'items':bridge.request_snapshot()})
    elif self.path=='/events':self.reply(200,{'items':bridge.store.events()})
    else:self.reply(404,{'error':'Not found'})
   def do_POST(self):
    if not self.authorized():self.reply(401,{'error':'Unauthorized'});return
    if self.path!='/events':self.reply(404,{'error':'Not found'});return
    try:
     n=int(self.headers.get('Content-Length','0'))
     if not 0<n<=65536:raise ValueError('Body size must be 1..65536 bytes')
     e=validate(json.loads(self.rfile.read(n)));bridge.signals.incoming.emit(e)
    except (ValueError,TypeError,UnicodeError) as exc:self.reply(400,{'error':str(exc)});return
    self.reply(202,{'accepted':True,'persisted':False})
  self.server=ThreadingHTTPServer((os.getenv('GUI_EVENT_HOST','127.0.0.1'),port),Handler);self.server.daemon_threads=True
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
 def close(self):self.server.shutdown();self.server.server_close()
