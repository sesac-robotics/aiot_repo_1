import unittest,os,threading,json,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pc1_db_adapter import PC1
class AdapterTest(unittest.TestCase):
 def test_patient_api_mapping(self):
  seen=[]
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def reply(self,data):
    body=json.dumps(data).encode();self.send_response(200);self.end_headers();self.wfile.write(body)
   def do_POST(self):
    data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));seen.append((self.path,data));self.reply({'person_id':'patient_SERVER'})
   def do_PATCH(self):self.do_POST()
   def do_DELETE(self):seen.append((self.path,None));self.reply({'person_id':'patient_SERVER','is_active':False})
   def do_GET(self):self.reply({'items':[{'person_id':'patient_SERVER','name':'Patient','gender':'M','blood_type':'Rh- O','birth_date':'1960-01-01','created_at':'2026-10-22T00:00:00Z'}]})
  srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=srv.serve_forever,daemon=True).start()
  old_url=os.environ.get('TSAFE_PC1_URL');old_token=os.environ.get('TSAFE_ADMIN_TOKEN');os.environ['TSAFE_PC1_URL']='http://127.0.0.1:'+str(srv.server_address[1]);os.environ['TSAFE_ADMIN_TOKEN']='test-token'
  try:
   c=PC1();person={'name':'Patient','id':'pending','role':'Patient','sex':'Male','birth':'1960-01-01','blood':'O','rh':'-','height':'170','weight':'65','disease':'None','medicine':'None'}
   self.assertEqual(c.save({'person':person})['person_id'],'patient_SERVER');self.assertEqual(seen[0][0],'/api/v1/patients');self.assertEqual(seen[0][1]['blood_type'],'Rh- O');self.assertNotIn('id',seen[0][1])
   c.save({'person':person,'old_id':'patient_SERVER'});self.assertTrue(seen[-1][0].endswith('/patient_SERVER'));rows=c.patients();self.assertEqual(rows[0]['blood'],'O');self.assertEqual(rows[0]['rh'],'-');self.assertEqual(rows[0]['sex'],'Male')
   c.delete({'person':{'id':'patient_SERVER'}});self.assertIsNone(seen[-1][1])
   with self.assertRaises(ValueError):c.save({'person':dict(person,role='Staff')})
  finally:
   srv.shutdown();srv.server_close()
   for k,v in [('TSAFE_PC1_URL',old_url),('TSAFE_ADMIN_TOKEN',old_token)]:
    if v is None:os.environ.pop(k,None)
    else:os.environ[k]=v
if __name__=='__main__':unittest.main()
