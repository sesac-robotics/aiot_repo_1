"""T-SAFE standalone UI demo. No camera/network/door hardware is connected."""
import os,sys,json,re
from pathlib import Path
from datetime import date,datetime
import xml.etree.ElementTree as ET
from PyQt6 import QtWidgets as W, QtCore as C, QtGui as G, uic
ROOT=Path(__file__).resolve().parent
class Resizer(C.QObject):
 def __init__(self,win,path):
  super().__init__(win);self.win=win;self.base=[];self.style=win.styleSheet()
  for node in ET.parse(path).findall('.//widget'):
   if node.get('name')=='Form':continue
   obj=win.findChild(W.QWidget,node.get('name'))
   if obj:
    self.base.append((obj,C.QRect(obj.geometry())))
    if obj.objectName()=='cameraImage':obj._sourcePixmap=G.QPixmap(obj.pixmap());obj.setScaledContents(False);obj.setAlignment(C.Qt.AlignmentFlag.AlignCenter)
  win.installEventFilter(self)
 def eventFilter(self,obj,event):
  if event.type()==C.QEvent.Type.Resize:
   scale=min(obj.size().width()/1280,obj.size().height()/800);ox=int((obj.size().width()-1280*scale)/2);oy=int((obj.size().height()-800*scale)/2)
   for child,rect in self.base:
    top=child.parent() is obj;child.setGeometry(int(rect.x()*scale)+(ox if top else 0),int(rect.y()*scale)+(oy if top else 0),int(rect.width()*scale),int(rect.height()*scale))
   for child,rect in self.base:
    if hasattr(child,'_sourcePixmap'):
     pix=child._sourcePixmap.scaled(child.size(),C.Qt.AspectRatioMode.KeepAspectRatioByExpanding,C.Qt.TransformationMode.SmoothTransformation);child.setPixmap(pix.copy((pix.width()-child.size().width())//2,(pix.height()-child.size().height())//2,child.size().width(),child.size().height()))
   obj.setStyleSheet(re.sub(r'(\d+)px',lambda m:str(max(1,round(int(m[1])*scale)))+'px',self.style))
  return False

class App:
 def __init__(self):
  self.app=W.QApplication.instance() or W.QApplication(sys.argv);self.app.setStyle('Fusion');self.app.setStyleSheet((ROOT/'skin.qss').read_text());self.windows={};self.edit_id=None
  self.data_path=ROOT/'data/people.local.json'
  self.people=json.loads((self.data_path if self.data_path.exists() else ROOT/'data/demo_people.json').read_text())
  for path in (ROOT/'ui').glob('*.ui'):
   old=os.getcwd();os.chdir(path.parent)
   try:win=uic.loadUi(str(path))
   finally:os.chdir(old)
   font=next((f for f in ['Malgun Gothic','Noto Sans','DejaVu Sans'] if f in G.QFontDatabase.families()),'Arial');win.setStyleSheet((ROOT/'skin.qss').read_text().replace('font-family: "Malgun Gothic", "Noto Sans", "DejaVu Sans"',f'font-family: "{font}"'))
   self.windows[path.stem]=win
   if path.stem!="clip":
    win.resizer=Resizer(win,path);win.setMinimumSize(960,600)
   if hasattr(win,'homeButton'):win.homeButton.clicked.connect(lambda:self.show('home'))
  self.windows['register'].homeButton.clicked.disconnect();self.windows['register'].homeButton.setText('‹ People');self.windows['register'].homeButton.clicked.connect(lambda:self.show('people'))
  for name,dest in [('People','people'),('CCTV','cctv'),('Logs','logs'),('Door','door_access')]:getattr(self.windows['home'],'open'+name).clicked.connect(lambda _,dest=dest:self.show(dest))
  p=self.windows['people'];p.addButton.clicked.connect(self.add);p.editButton.clicked.connect(self.edit);p.deleteButton.clicked.connect(self.delete)
  p.search.textChanged.connect(self.refresh);p.dateFilter.textChanged.connect(self.refresh);p.roleFilter.currentTextChanged.connect(self.refresh);p.bloodFilter.currentTextChanged.connect(self.refresh);p.resetButton.clicked.connect(self.reset_people);p.peopleTable.itemSelectionChanged.connect(self.profile)
  self.windows['register'].saveButton.clicked.connect(self.save)
  self.configure_tables();self.refresh();self.setup_logs()
  for box in self.windows['register'].findChildren(W.QTextEdit):box.document().setDocumentMargin(0)
  c=self.windows['cctv']
  if hasattr(c,'confirmButton'):c.confirmButton.clicked.connect(lambda:c.fallAlert.setText('Fall confirmed'))
  if 'clip' in self.windows:
   self.clip=self.windows['clip'];self.clip.setWindowModality(C.Qt.WindowModality.ApplicationModal);self.clip.resize(900,620);self.clip.closeButton.clicked.connect(self.clip.close);self.clip.playButton.clicked.connect(self.play);self.timer=C.QTimer();self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);c.viewClipButton.clicked.connect(self.open_clip);c.mapImage.mousePressEvent=lambda event:self.open_clip();c.mapImage.setCursor(C.Qt.CursorShape.PointingHandCursor);c.fallRange.mousePressEvent=lambda event:self.open_clip();c.fallRange.setCursor(C.Qt.CursorShape.PointingHandCursor);self.clip.closeEvent=lambda event:self.timer.stop()
  # main pages use one shared geometry; clip remains a smaller independent child.
  self.geometry=None
  area=self.app.primaryScreen().availableGeometry();scale=min(1,(area.width()-40)/1280,(area.height()-60)/800)
  for k,win in self.windows.items():
   if k!='clip':win.resize(round(1280*scale),round(800*scale))
 def configure_tables(self):
  for win in self.windows.values():
   for t in win.findChildren(W.QTableWidget):t.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch);t.verticalHeader().hide();t.verticalHeader().setDefaultSectionSize(60);t.setSelectionMode(W.QAbstractItemView.SelectionMode.SingleSelection)
 def show(self,key):
  for k,win in self.windows.items():
   if k!='clip' and win.isVisible():self.geometry=win.geometry();win.hide()
  win=self.windows[key]
  if self.geometry:win.setGeometry(self.geometry)
  win.show();win.raise_()
 def selected(self):
  t=self.windows['people'].peopleTable;r=t.currentRow()
  if r<0:return None
  return next((p for p in self.people if p['id']==t.item(r,0).text()),None)
 def refresh(self,*args):
  win=self.windows['people'];q=win.search.text().lower();role=win.roleFilter.currentText();blood=win.bloodFilter.currentText();dt=win.dateFilter.text();rows=[p for p in self.people if (q in p['name'].lower() or q in p['id'].lower()) and (role=='All roles' or role==p['role']) and (blood=='All blood' or blood==p['blood']) and dt in p['added']]
  t=win.peopleTable;t.blockSignals(True);t.setRowCount(len(rows))
  for i,p in enumerate(rows):
   for j,v in enumerate([p['id'],p['name'],p['role'],p['blood']+p['rh']]):t.setItem(i,j,W.QTableWidgetItem(v))
  t.blockSignals(False);win.count.setText(f'{len(rows)} people');win.editButton.setEnabled(bool(rows));win.deleteButton.setEnabled(bool(rows))
  if rows:t.selectRow(0)
  self.profile()
 def profile(self):
  p=self.selected();win=self.windows['people'];win.personName.setText(p['name'] if p else 'No person selected')
  for key in ['id','role','sex','birth','blood','rh','height','weight','disease','medicine']:
   value=p[key] if p else '—'
   # Rh is displayed in its own field.
   if p and key=='height':value+=' cm'
   if p and key=='weight':value+=' kg'
   # Field captions are separate native labels.
   label=getattr(win,'profile'+key);label.setText(value);label.setWordWrap(True)
 def reset_people(self):
  w=self.windows['people'];w.search.clear();w.dateFilter.clear();w.roleFilter.setCurrentIndex(0);w.bloodFilter.setCurrentIndex(0);self.refresh()
 def form(self,p=None):
  w=self.windows['register'];self.edit_id=p['id'] if p else None
  for key in ['name','id','role','sex','birth','blood','rh','height','weight','disease','medicine']:
   control=getattr(w,key);v=p[key] if p else ''
   if isinstance(control,W.QComboBox):control.setCurrentText(v if v else control.itemText(0))
   elif isinstance(control,W.QTextEdit):control.setPlainText(v)
   else:control.setText(v)
  w.id.setReadOnly(bool(p));w.pageTitle.setText('Edit Person' if p else 'Register');w.saveStatus.setText('Update this person' if p else 'Auto capture ready · Save to add this person to People');self.show('register')
 def add(self):self.form()
 def edit(self):
  if self.selected():self.form(self.selected())
 def persist(self):self.data_path.write_text(json.dumps(self.people,ensure_ascii=False,indent=2),encoding='utf-8')
 def save(self):
  w=self.windows['register'];p={}
  for k in ['name','id','role','sex','birth','blood','rh','height','weight','disease','medicine']:
   c=getattr(w,k);p[k]=(c.currentText() if isinstance(c,W.QComboBox) else c.toPlainText() if isinstance(c,W.QTextEdit) else c.text()).strip()
  error=''
  if not p['name'] or not p['id']:error='Name and ID are required.'
  elif not self.edit_id and any(x['id']==p['id'] for x in self.people):error='This ID already exists.'
  else:
   try:date.fromisoformat(p['birth'])
   except ValueError:error='Birth must be a valid YYYY-MM-DD date.'
   for k in ['height','weight']:
    try:
     if float(p[k])<=0:raise ValueError()
    except ValueError:error='Height and Weight must be positive numbers.'
  if error:W.QMessageBox.warning(w,'Check information',error);return False
  if self.edit_id:
   old=next(x for x in self.people if x['id']==self.edit_id);p['added']=old['added'];old.update(p)
  else:p['added']=date.today().isoformat();self.people.append(p)
  try:self.persist()
  except OSError as e:W.QMessageBox.critical(w,'Save failed',str(e));return False
  self.reset_people();self.show('people');return True
 def delete(self):
  p=self.selected()
  if p and W.QMessageBox.question(self.windows['people'],'Delete person',f"Delete {p['name']}?",W.QMessageBox.StandardButton.Yes|W.QMessageBox.StandardButton.No)==W.QMessageBox.StandardButton.Yes:
   self.people.remove(p)
   try:self.persist()
   except OSError as e:W.QMessageBox.critical(self.windows['people'],'Save failed',str(e))
   self.refresh()
 def setup_logs(self):
  w=self.windows['logs'];self.logs=[[w.logsTable.item(i,j).text() for j in range(4)] for i in range(w.logsTable.rowCount())]
  def refresh(*args):
   for i,row in enumerate(self.logs):w.logsTable.setRowHidden(i,not(w.search.text().lower() in row[2].lower() and (w.eventFilter.currentIndex()==0 or w.eventFilter.currentText()==row[1]) and w.dateFilter.text() in row[0]))
  w.search.textChanged.connect(refresh);w.dateFilter.textChanged.connect(refresh);w.eventFilter.currentTextChanged.connect(refresh)
  def reset():w.search.clear();w.dateFilter.clear();w.eventFilter.setCurrentIndex(0)
  w.resetButton.clicked.connect(reset)
 def open_clip(self):self.clip.show();self.clip.raise_()
 def play(self):
  if self.timer.isActive():self.timer.stop();self.clip.playButton.setText('Play')
  else:
   if self.clip.timeline.value()==20:self.clip.timeline.setValue(0)
   self.timer.start();self.clip.playButton.setText('Pause')
 def tick(self):
  v=self.clip.timeline.value()+1;self.clip.timeline.setValue(v)
  if v>=20:self.timer.stop();self.clip.playButton.setText('Play')
 def run(self):self.show('home');return self.app.exec()
if __name__=='__main__':sys.exit(App().run())
