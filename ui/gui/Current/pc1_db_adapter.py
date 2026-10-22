"""Connects the GUI to the supplied T-SAFE PC1 v0.3 patient APIs.

Start GUI with GUI_DB_MODE=external, then run this adapter. Card writes are handled
by a separate card device process via device_client.GUIClient, never fabricated.
"""
import json,os,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import quote
from urllib.error import HTTPError,URLError
from device_client import GUIClient
class PC1:
 def __init__(self):
  self.url=os.getenv('TSAFE_PC1_URL','http://127.0.0.1:8101').rstrip('/');self.token=os.getenv('TSAFE_ADMIN_TOKEN','')
  if not self.token:raise ValueError('TSAFE_ADMIN_TOKEN is required')
 def call(self,method,path,payload=None):
  req=Request(self.url+path,method=method,data=None if payload is None else json.dumps(payload).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
  with urlopen(req,timeout=5) as r:return json.load(r)
 def patients(self):
  out=[];offset=0
  while True:
   rows=self.call('GET',f'/api/v1/patients?limit=500&offset={offset}')['items'];out.extend(rows)
   if len(rows)<500:return [self.convert(p) for p in out]
   offset+=500
 @staticmethod
 def convert(p):
  blood=(p.get('blood_type') or '').upper().replace(' ','');rh='-' if '-' in blood else '+' if '+' in blood else '';blood=blood.replace('RH','').replace('+','').replace('-','')
  text=lambda k:'' if p.get(k) is None else str(p[k])
  return {'id':p['person_id'],'name':text('name'),'role':'Patient','sex':{'M':'Male','F':'Female'}.get(p.get('gender'),''),'birth':text('birth_date')[:10],'blood':blood,'rh':rh,'height':text('height_cm'),'weight':text('weight_kg'),'disease':text('medical_history'),'medicine':text('medications'),'added':text('created_at')[:10]}
 def save(self,request):
  p=request['person']
  if p['role']!='Patient':raise ValueError('PC1 v0.3 patient API supports Patient only; Staff/Admin needs a server endpoint')
  fields={'name':p['name'],'gender':{'Male':'M','Female':'F'}[p['sex']],'birth_date':p['birth'],'height_cm':float(p['height']),'weight_kg':float(p['weight']),'blood_type':'Rh'+p['rh']+' '+p['blood'],'medical_history':p['disease'],'medications':p['medicine']}
  if request.get('old_id'):return self.call('PATCH','/api/v1/patients/'+quote(request['old_id'],safe=''),fields)
  return self.call('POST','/api/v1/patients',fields)
 def delete(self,request):return self.call('DELETE','/api/v1/patients/'+quote(request['person']['id'],safe=''))
def run():
 gui=GUIClient();pc1=PC1();done=set();last_sync=0
 cache_path=Path(__file__).parent/'data/pc1_adapter_results.json'
 results=json.loads(cache_path.read_text()) if cache_path.exists() else {}
 while True:
  try:
   if time.monotonic()-last_sync>5:gui.send({'type':'patients_sync','items':pc1.patients()});last_sync=time.monotonic()
   for req in gui.requests():
    if req['type'] not in ['db_save','db_delete'] or req['request_id'] in done:continue
    result={'type':'db_result' if req['type']=='db_save' else 'db_delete_result','request_id':req['request_id']}
    if req['request_id'] in results:
     gui.send(results[req['request_id']]);done.add(req['request_id']);continue
    try:
     r=pc1.save(req) if req['type']=='db_save' else pc1.delete(req);result.update(success=True,person_id=r['person_id'])
    except HTTPError as exc:result.update(success=False,message='PC1 HTTP '+str(exc.code))
    except (ValueError,KeyError,URLError,TimeoutError,OSError) as exc:result.update(success=False,message=str(exc) if isinstance(exc,ValueError) else 'PC1 connection/response failed')
    results[req['request_id']]=result;temporary=cache_path.with_suffix('.tmp');temporary.write_text(json.dumps(results));temporary.replace(cache_path)
    gui.send(result);done.add(req['request_id']);last_sync=0
  except (HTTPError,URLError,TimeoutError,OSError,ValueError,KeyError):print('Waiting for GUI / PC1 server. Check address, token and server state.',flush=True)
  time.sleep(.3)
if __name__=='__main__':
 try:run()
 except KeyboardInterrupt:pass
