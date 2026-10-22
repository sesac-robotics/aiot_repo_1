"""Client for an existing card/recognition/door process; contains no device driver."""
import json,os
from pathlib import Path
from urllib.request import Request,urlopen
class GUIClient:
 def __init__(self,url=None,token=None):
  self.url=(url or os.getenv('GUI_EVENT_URL','http://127.0.0.1:8765')).rstrip('/')
  self.token=token or os.getenv('GUI_EVENT_TOKEN') or (Path(__file__).parent/'data/gui_event.token').read_text().strip()
 def call(self,method,path,data=None):
  req=Request(self.url+path,method=method,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
  with urlopen(req,timeout=5) as r:return json.load(r)
 def send(self,event):return self.call('POST','/events',event)
 def requests(self):return self.call('GET','/requests')['items']
