"""Local GUI database. Each operation uses a separate SQLite connection."""
import json,sqlite3,uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
class Store:
 def __init__(self,root,seed=True):
  self.path=Path(root)/('data/gui.sqlite3' if seed else 'data/gui.external.sqlite3');self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.connect() as c:
   c.execute('CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,time TEXT NOT NULL,event TEXT NOT NULL,name TEXT NOT NULL,person_id TEXT,details TEXT NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT)')
   if not c.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone():
    source=Path(root)/'data/people.local.json'
    if not source.exists():source=Path(root)/'data/demo_people.json'
    for p in (json.loads(source.read_text(encoding='utf-8')) if seed else []):c.execute('INSERT OR REPLACE INTO people VALUES(?,?)',(p['id'],json.dumps(p,ensure_ascii=False)))
    c.execute("INSERT INTO meta VALUES('initialized','1')")
 @contextmanager
 def connect(self):
  c=sqlite3.connect(self.path,timeout=5);c.row_factory=sqlite3.Row
  try:
   with c:yield c
  finally:c.close()
 def people(self):
  with self.connect() as c:return [json.loads(r['data']) for r in c.execute('SELECT data FROM people ORDER BY id')]
 def save_person(self,p,old_id=None):
  with self.connect() as c:
   if old_id and old_id!=p['id']:c.execute('DELETE FROM people WHERE id=?',(old_id,))
   c.execute('INSERT OR REPLACE INTO people VALUES(?,?)',(p['id'],json.dumps(p,ensure_ascii=False)))
 def replace_people(self,people):
  with self.connect() as c:
   c.execute('DELETE FROM people')
   for p in people:c.execute('INSERT INTO people VALUES(?,?)',(p['id'],json.dumps(p,ensure_ascii=False)))
 def delete_person(self,id):
  with self.connect() as c:c.execute('DELETE FROM people WHERE id=?',(id,))
 def event(self,kind,name='Unknown',person_id=None,details='',event_id=None,timestamp=None):
  with self.connect() as c:
   result=c.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',(event_id or uuid.uuid4().hex,timestamp or datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec='seconds'),kind,name,person_id,details));return result.rowcount==1
 def events(self):
  with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM events ORDER BY time DESC,rowid DESC LIMIT 2000')]
