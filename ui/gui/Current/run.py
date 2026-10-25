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

from workflow import WorkflowMixin
from live_frame_client import LiveFrameClient
from frame_mapper import annotations_from_metadata
class App(WorkflowMixin):
 def __init__(self):
  self.app=W.QApplication.instance() or W.QApplication(sys.argv);self.app.setStyle('Fusion');self.app.setStyleSheet((ROOT/'skin.qss').read_text());self.windows={};self.edit_id=None
  self.init_store(ROOT)
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
  # A second demo process can write to the same SQLite file.  Polling only
  # refreshes the Logs view when the stored rows actually changed.
  self.logs_refresh_timer=C.QTimer(self.app);self.logs_refresh_timer.setInterval(int(os.getenv('TSAFE_LOG_REFRESH_MS','500')));self.logs_refresh_timer.timeout.connect(self.refresh_logs_if_changed);self.logs_refresh_timer.start()
  for box in self.windows['register'].findChildren(W.QTextEdit):box.document().setDocumentMargin(0)
  c=self.windows['cctv']
  if hasattr(c,'confirmButton'):c.confirmButton.clicked.connect(lambda:c.confirmButton.setText('Confirmed'))
  if 'clip' in self.windows:
   self.clip=self.windows['clip'];self.clip.setWindowModality(C.Qt.WindowModality.ApplicationModal);self.clip.resize(900,620);self.clip.closeButton.clicked.connect(self.clip.close);self.clip.playButton.clicked.connect(self.play);self.timer=C.QTimer();self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);c.viewClipButton.clicked.connect(self.open_clip);c.mapImage.mousePressEvent=lambda event:self.open_clip();c.mapImage.setCursor(C.Qt.CursorShape.PointingHandCursor);self.clip.closeEvent=lambda event:self.timer.stop()
  # main pages use one shared geometry; clip remains a smaller independent child.
  self.geometry=None
  area=self.app.primaryScreen().availableGeometry();scale=min(1,(area.width()-40)/1280,(area.height()-60)/800)
  for k,win in self.windows.items():
   if k!='clip':win.resize(round(1280*scale),round(800*scale))
  self.init_workflow()
  self.init_live_frame()
 def init_live_frame(self):
  image_url=os.getenv('TSAFE_FRAME_IMAGE_URL','http://127.0.0.1:8101/api/v1/live/frame/camera_1/image')
  metadata_url=os.getenv('TSAFE_FRAME_METADATA_URL') or None
  # Atomic snapshots avoid metadata/image races; 50 ms matches the AI's
  # configured 20 FPS ceiling on a local network.
  poll_ms=int(os.getenv('TSAFE_FRAME_POLL_MS','50'))
  buffer_frames=int(os.getenv('TSAFE_FRAME_BUFFER_FRAMES','2'))
  self.live_frame=LiveFrameClient(image_url,metadata_url,interval_ms=poll_ms,buffer_frames=buffer_frames,parent=self.windows['cctv'])
  self.live_frame.frame_received.connect(self.receive_live_frame)
  self.live_frame.connection_error.connect(self.live_frame_error)
  self.live_frame.metrics_updated.connect(self.live_frame_metrics)
  self.app.aboutToQuit.connect(self.live_frame.stop)
  self.live_frame.start()
 def receive_live_frame(self,pixmap,metadata):
  annotations,result=annotations_from_metadata(metadata)
  if getattr(self,'fall_suppressed',False):
   # "상황 종료" applies a GUI-only override to the resolved incident.
   # The AI keeps publishing its diagnosis, but it is no longer presented as
   # an active fall until the central service creates a new alert.
   annotations=[dict(item,state='normal') for item in annotations]
   result=dict(result,state='normal')
  self.set_cctv_frame(pixmap,annotations)
  # A durable central alert stays visible even if later video frames no
  # longer classify the person as fallen; only its RESOLVED event clears it.
  self.set_fall_detected(result['state']=='fall' or bool(getattr(self,'active_alert_id',None)))
  self.windows['cctv'].liveCaption.setText(f"Camera 1 · Live · {result['person_count']} person(s)")
 def live_frame_error(self,message):
  self.windows['cctv'].liveCaption.setText('Camera 1 · Waiting for Main Service · '+message)
 def live_frame_metrics(self,metrics):
  age='—' if metrics['age_ms'] is None else f"{metrics['age_ms']:.0f}ms (p95 {metrics['age_p95_ms']:.0f}ms)"
  self.windows['cctv'].liveCaption.setText(f"Camera 1 · Live · {metrics['fps']:.1f} FPS · age {age} · drop {metrics['dropped']}")
 def set_cctv_frame(self,frame,annotations=None):
  """Draw person boxes and binary NORMAL/FALL event tags in the CCTV tab."""
  pixmap=G.QPixmap.fromImage(frame) if isinstance(frame,G.QImage) else frame
  if not isinstance(pixmap,G.QPixmap) or pixmap.isNull():
   raise ValueError('frame must be a non-empty QImage or QPixmap')
  pixmap=pixmap.copy();painter=G.QPainter(pixmap)
  try:
   for annotation in annotations or []:
    color=G.QColor('#d52e28') if annotation.get('state')=='fall' else G.QColor('#ffbc48')
    painter.setPen(G.QPen(color,max(2,pixmap.width()//500)))
    box=annotation.get('bbox')
    if box is not None:
     x1,y1,x2,y2=(round(float(v)*pixmap.width()) if i%2==0 else round(float(v)*pixmap.height()) for i,v in enumerate(box))
     painter.drawRect(min(x1,x2),min(y1,y2),abs(x2-x1),abs(y2-y1))
     tag='FALL' if annotation.get('state')=='fall' else 'NORMAL'
     font=G.QFont();font.setBold(True);font.setPointSize(max(9,pixmap.width()//55));painter.setFont(font)
     metrics=G.QFontMetrics(font);tag_rect=metrics.boundingRect(tag).adjusted(-7,-4,7,4)
     tag_x=min(x1,x2);tag_y=max(0,min(y1,y2)-tag_rect.height())
     painter.fillRect(tag_x,tag_y,tag_rect.width(),tag_rect.height(),color)
     painter.setPen(G.QPen(G.QColor('#111827')))
     painter.drawText(tag_x+7,tag_y+metrics.ascent()+4,tag)
  finally:painter.end()
  c=self.windows['cctv'];c.liveImage.setPixmap(pixmap);c.liveCaption.setText('Camera 1 · Live')
 def set_fall_detected(self,detected):
  """Update the anonymous alert; CCTV never assigns an identity or incident time."""
  c=self.windows['cctv'];c.fallAlert.setVisible(bool(detected));c.fallRange.setVisible(bool(detected))
  alert_id=getattr(self,'active_alert_id',None)
  if not detected or not alert_id:
   c.confirmButton.setText('Confirm');c.confirmButton.setEnabled(False)
  elif alert_id==getattr(self,'confirming_alert_id',None):
   c.confirmButton.setText('Confirming…');c.confirmButton.setEnabled(False)
  elif alert_id==getattr(self,'resolving_alert_id',None):
   c.confirmButton.setText('Resolving…');c.confirmButton.setEnabled(False)
  elif alert_id==getattr(self,'acknowledged_alert_id',None):
   c.fallAlert.setText('CONFIRMED')
   c.confirmButton.setText('상황 종료');c.confirmButton.setEnabled(True)
  else:
   c.fallAlert.setText('FALL')
   c.confirmButton.setText('Confirm');c.confirmButton.setEnabled(True)
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
  self.active_registration=None;self.reset_status()
  w=self.windows['register'];self.edit_id=p['id'] if p else None
  for key in ['name','id','role','sex','birth','blood','rh','height','weight','disease','medicine']:
   control=getattr(w,key);v=p[key] if p else ''
   if isinstance(control,W.QComboBox):control.setCurrentText(v if v else control.itemText(0))
   elif isinstance(control,W.QTextEdit):control.setPlainText(v)
   else:control.setText(v)
  w.id.setReadOnly(bool(p) or self.db_mode=='external');w.id.setPlaceholderText('Server assigned' if self.db_mode=='external' else 'ID');w.pageTitle.setText('Edit Person' if p else 'Register');w.saveStatus.setText('Update this person' if p else 'Save patient details, then wait for the card write result');self.show('register')
 def add(self):self.form()
 def edit(self):
  if self.selected():self.form(self.selected())
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
