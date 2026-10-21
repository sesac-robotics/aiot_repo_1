import sys
from PyQt6.QtWidgets import *
from PyQt6.QtGui import *
from PyQt6 import uic
from PyQt6.QtCore import *
from PyQt6.QtNetwork import QTcpSocket, QHostAddress, QAbstractSocket
import cv2, imutils
import serial
import struct
from MyThread import MyTimer
import time
import os
import csv
from datetime import datetime
import subprocess
import psycopg2


from_class = uic.loadUiType("patient_monitoring.ui")[0]


# 백그라운드 웹캠 영상 캡처 스레드
class CameraThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)

    def __init__(self):
        super().__init__()
        self.running = False
        self.cap = None
        pass
        
    


    def run(self):
        # 0번 기본 웹캠 연결
        self.cap = cv2.VideoCapture(0)
        self.running = True
        
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                # OpenCV의 BGR 서식을 RGB 서식으로 변환
                rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_image.shape
                bytes_per_line = ch * w
                
                # QImage 객체 생성 후 메인 UI 스레드로 전송
                qt_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
                self.change_pixmap_signal.emit(qt_image)
            else:
                self.msleep(10)
                
        if self.cap:
            self.cap.release()

    def stop(self):
        self.running = False
        self.wait()


# 다중 상속 프레임 구조의 메인 윈도우 클래스
class WindowClass(QMainWindow, from_class) :
    def __init__(self):
        super().__init__()
        self.setupUi(self)          # UI 위젯들을 클래스 멤버로 직접 로드
        self.setWindowTitle('project1')
        self.resize(1168, 629)

        # 1. 알람 로그 테이블(alarmlogList) 초기화
        self.init_table_widget()

        # 2. 카메라 스레드 인스턴스 설정 및 최신 프레임 변수 생성
        self.camera_thread = CameraThread()
        self.camera_thread.change_pixmap_signal.connect(self.update_camera_image)
        self.current_frame = None

        # 3. TCP 소켓 초기화 및 시그널 연결
        self.socket = QTcpSocket(self)
        self.socket.connected.connect(self.on_connected)
        self.socket.readyRead.connect(self.on_ready_read)
        self.socket.disconnected.connect(self.on_disconnected)
        self.socket.errorOccurred.connect(self.on_error)

        self.enrollguiButton.clicked.connect(self.open_enroll_gui)

        # 4. UI 위젯 이벤트 연결
        self.serverButton.clicked.connect(self.connect_to_server)                     # 서버 연결 버튼
        self.opendoorButton.clicked.connect(lambda: self.send_command("DOOR_OPEN"))   # 문 열기 버튼
        self.closedoorButton.clicked.connect(lambda: self.send_command("DOOR_CLOSE")) # 문 닫기 버튼
        
        self.cameraButton.clicked.connect(self.toggle_camera)                         # camera 토글 버튼
        self.captureButton.clicked.connect(self.capture_image)                         # capture 버튼
        self.AlarmcvsButton.clicked.connect(self.export_log_to_csv)                   # Alarm log CVS Out 버튼
        
        
        self.AlarmcvsButton.clicked.connect(self.export_log_to_csv)                   # Alarm log CVS Out 버튼
        
        # 🔔 여기서부터 6줄 추가
        self.init_database()  # DB 연결
        self.list_model = QStringListModel()
        self.enrollList.setModel(self.list_model)
        self.load_enroll_list_from_db()  # 환자 리스트 로드
        self.enrolldeleteButton.clicked.connect(self.delete_patient)  # 삭제 버튼 연결
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500) # 500ms = 0.5초
        self.refresh_timer.timeout.connect(self.load_enroll_list_from_db)
        self.refresh_timer.start()


    def init_table_widget(self):
        self.alarmlogList.setColumnCount(2)
        self.alarmlogList.setHorizontalHeaderLabels(["Time", "Event Log"])
        self.alarmlogList.horizontalHeader().setStretchLastSection(True)
        self.alarmlogList.setColumnWidth(0, 130)

    # ◀ 추가할 메서드 1: 데이터베이스 연결
    def init_database(self):
        try:
            self.db_conn = psycopg2.connect(
                host="localhost",
                database="hospital_db",        
                user="postgres",               
                password="1212",     
                port="5432"
            )
            self.cursor = self.db_conn.cursor()
            print("[DB 연결 성공] 모니터링 프로그램이 PostgreSQL에 연동되었습니다.")
        except Exception as e:
            print(f"[DB 연결 실패] 모니터링 데이터베이스 오류: {e}")
            self.db_conn = None

    # ◀ 추가할 메서드 2: DB에서 ID, Name 읽어와 enrollList(QListView)에 뿌리기
    def load_enroll_list_from_db(self):
        if not self.db_conn: return
        try:
            # 1. 새로고침 직전 현재 마우스로 선택된 행 번호 기억하기
            selected_indexes = self.enrollList.selectedIndexes()
            saved_row = selected_indexes[0].row() if selected_indexes else -1

            self.cursor.execute("SELECT p_num, name FROM patients ORDER BY p_num ASC;")
            rows = self.cursor.fetchall()
            
            items = []
            for row in rows:
                items.append(f"ID: {row[0]} | Name: {row[1]}")
            
            self.list_model.setStringList(items)

            # 2. 리스트가 갱신된 직후 기억해 둔 행 번호로 다시 강제 선택 세팅
            if saved_row != -1 and saved_row < self.list_model.rowCount():
                restore_index = self.list_model.index(saved_row, 0)
                self.enrollList.setCurrentIndex(restore_index)
                
            # 🔔 [여기서부터 추가된 파일 감시 코드]
            if os.path.exists("rfid_signal.txt"):
                try:
                    with open("rfid_signal.txt", "r", encoding="utf-8") as f:
                        signal_data = f.read().strip()
                    
                    if signal_data:
                        # "역할:번호:이름" 데이터 분리 파싱
                        parts = signal_data.split(":")
                        role = parts[0]
                        num = parts[1]
                        name = parts[2]
                        
                        # 모니터링 화면 doorText 레이블에 HTML로 출력

                        if role == "Unknown":
                            display_html = f"""
                            <html><body>
                            <p style="margin-top:0px; margin-bottom:4px;">
                                <span style="font-size:30pt; color:red; font-weight:bold;">CLOSE</span>
                            </p>
                            <p style="margin-top:0px; margin-bottom:3px; font-size:15pt; color:dimgray; font-weight:bold;">
                                ({role})
                            </p>
                            <p style="margin-top:0px; margin-bottom:0px; font-size:15pt; color:dimgray; font-weight:bold;">
                                (No.{num} {name if name else "미등록"})
                            </p>
                            </body></html>
                            """
                            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self.add_log_row(current_time, f"🔒 Access Denied (Unknown No.{num})")
                        else:
                            display_html = f"""
                            <html><body>
                            <p style="margin-top:0px; margin-bottom:4px;">
                                <span style="font-size:30pt; color:blue; font-weight:bold;">OPEN</span>
                            </p>
                            <p style="margin-top:0px; margin-bottom:3px; font-size:15pt; color:black; font-weight:bold;">
                                ({role})
                            </p>
                            <p style="margin-top:0px; margin-bottom:0px; font-size:15pt; color:black; font-weight:bold;">
                                (No.{num} {name})
                            </p>
                            </body></html>
                            """
                            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self.add_log_row(current_time, f"🔓 Door Opened ({role}: {name} No.{num})")

                        # 레이블에 디자인 최종 반영
                        self.doorText.setText(display_html)


                        
                        # 알람 로그(alarmlogList)에 실시간 이벤트 추가
                        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self.add_log_row(current_time, f"🔓 Door Opened ({role}: {name} No.{num})")
                    
                    # 중복 처리 방지를 위해 확인 후 파일 즉시 삭제
                    os.remove("rfid_signal.txt")
                    
                except Exception as file_read_err:
                    print(f"[신호 파일 처리 오류] {file_read_err}")
                
        except Exception as e:
            print(f"[enrollList 로드 실패] {e}")



    # ◀ 추가할 메서드 3: 선택된 환자 DB에서 삭제하고 리스트 새로고침
    def delete_patient(self):
        if not self.db_conn: return
        
        selected_indexes = self.enrollList.selectedIndexes()
        if not selected_indexes:
            QMessageBox.warning(self, "삭제 실패", "삭제할 환자를 리스트에서 선택해 주세요.")
            return
            
        selected_text = selected_indexes[0].data()
        
        try:
            # "ID: 2 | Name: pikachu" 형태에서 숫자 ID와 이름 추출
            p_num_str = selected_text.split("ID:")[1].split("|")[0].strip()
            p_num = int(p_num_str)
            name_str = selected_text.split("Name:")[1].strip()
            
            reply = QMessageBox.question(self, "환자 기록 삭제", 
                                         f"NO.{p_num} {name_str} 환자의 기록을 DB에서 삭제하시겠습니까?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            
            if reply == QMessageBox.StandardButton.Yes:
                self.cursor.execute("DELETE FROM patients WHERE p_num = %s;", (p_num,))
                self.db_conn.commit()
                
                self.add_log_row(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"❌ Deleted Patient NO.{p_num}")
                self.load_enroll_list_from_db()  # 삭제 후 리스트 새로고침
                QMessageBox.information(self, "삭제 완료", "환자 데이터가 안전하게 삭제되었습니다.")
                
        except Exception as e:
            self.db_conn.rollback()
            print(f"[환자 삭제 실패] {e}")
            QMessageBox.critical(self, "오류", f"데이터 삭제 중 오류가 발생했습니다: {e}")


    def open_enroll_gui(self):
        target_script = "patient_interface2.py"
        
        # 같은 경로에 파일이 실제로 존재하는지 체크
        if os.path.exists(target_script):
            # 현재 실행 중인 파이썬 인터프리터(sys.executable)를 사용해 스크립트 실행
            subprocess.Popen([sys.executable, target_script])
            self.add_log_row(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "👤 Opened Enroll GUI")
        else:
            QMessageBox.critical(self, "실행 실패", f"'{target_script}' 파일을 찾을 수 없습니다.\n동일한 경로에 있는지 확인해 주세요.")
    # ------------------ 웹캠 제어 및 스냅샷 기능 ------------------

    # 웹캠 ON / OFF 토글 기능 (cameraButton)
    def toggle_camera(self):
        if not self.camera_thread.isRunning():
            self.camera_thread.start()
            self.cameraButton.setText("camera Close")
            self.add_log_row(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "📹 Camera 1 Started")
        else:
            self.camera_thread.stop()
            self.label.clear()  # 웹캠 라벨 초기화
            self.cameraButton.setText("camera Open")
            self.current_frame = None
            self.add_log_row(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "🛑 Camera 1 Stopped")

    # 웹캠 스레드로부터 이미지를 전달받아 라벨(label)에 출력하는 함수
    def update_camera_image(self, q_img):
        self.current_frame = q_img  # 캡처 기능을 위해 보관
        
        # label 크기에 딱 맞추어 비율 유지 상태로 출력
        pixmap = QPixmap.fromImage(q_img)
        scaled_pixmap = pixmap.scaled(self.label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(scaled_pixmap)

    # 현재 웹캠 화면 캡처 저장 기능 (captureButton)
    def capture_image(self):
        if self.current_frame is None or not self.camera_thread.isRunning():
            QMessageBox.warning(self, "캡처 실패", "카메라가 꺼져있거나 영상 데이터가 없습니다.")
            return

        # 현재 시간 기반의 기본 파일명 생성
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"capture_{timestamp}.jpg"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Captured Image", default_filename, "Images (*.png *.jpg *.jpeg)"
        )

        if file_path:
            try:
                success = self.current_frame.save(file_path)
                if success:
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.add_log_row(current_time, f"📸 Snapshot saved: {os.path.basename(file_path)}")
                    QMessageBox.information(self, "저장 성공", f"이미지가 성공적으로 저장되었습니다.\n{file_path}")
                else:
                    QMessageBox.critical(self, "오류", "이미지 파일 저장에 실패했습니다.")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"저장 중 에러 발생:\n{str(e)}")

    # ------------------ 알람 로그 및 파일 관리 기능 ------------------

    # 테이블 위젯(alarmlogList) 로그 기록 및 자동 스크롤 하단 이동
    def add_log_row(self, time_str, event_str):
        row_count = self.alarmlogList.rowCount()
        self.alarmlogList.insertRow(row_count)
        self.alarmlogList.setItem(row_count, 0, QTableWidgetItem(time_str))
        self.alarmlogList.setItem(row_count, 1, QTableWidgetItem(event_str))
        self.alarmlogList.scrollToBottom()

    # 알람 로그 테이블 데이터를 CSV 파일로 내보내기 (AlarmcvsButton)
    def export_log_to_csv(self):
        if self.alarmlogList.rowCount() == 0:
            QMessageBox.warning(self, "내보내기 실패", "저장할 로그 데이터가 없습니다.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Save Alarm Log", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            try:
                with open(file_path, mode='w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Time", "Event Log"])
                    for row in range(self.alarmlogList.rowCount()):
                        time_item = self.alarmlogList.item(row, 0).text() if self.alarmlogList.item(row, 0) else ""
                        event_item = self.alarmlogList.item(row, 1).text() if self.alarmlogList.item(row, 1) else ""
                        writer.writerow([time_item, event_item])
                QMessageBox.information(self, "저장 성공", "로그가 성공적으로 저장되었습니다.")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"파일 저장 중 에러 발생:\n{str(e)}")

    # ------------------ TCP 소켓 네트워크 기능 ------------------

    def connect_to_server(self):
        if self.socket.state() == QAbstractSocket.SocketState.UnconnectedState:
            server_ip = "127.0.0.1" 
            server_port = 8080
            self.serverButton.setText("Connecting...")
            self.serverButton.setEnabled(False)
            self.socket.connectToHost(QHostAddress(server_ip), server_port)
        else:
            self.socket.disconnectFromHost()

    def on_connected(self):
        self.serverButton.setText("Disconnect\nServer")
        self.serverButton.setEnabled(True)
        QMessageBox.information(self, "연결 성공", "모니터링 서버에 연결되었습니다.")

    def on_ready_read(self):
        while self.socket.bytesAvailable() > 0:
            data = self.socket.readAll().data()
            messages = data.decode('utf-8').strip().split('\n')
            for message in messages:
                message = message.strip()
                if not message: continue
                
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if "FALL" in message:
                    self.eventText.setText('<html><body><p><span style="font-size:22pt; color:red; font-weight:bold;">FALL</span></p></body></html>')
                    self.add_log_row(current_time, "🚨 Patient FALL Event Detected")
                elif "NORMAL" in message:
                    self.eventText.setText('<html><body><p><span style="font-size:22pt; color:green;">NORMAL</span></p></body></html>')
                    self.add_log_row(current_time, "🟢 Patient Status Normal")
                elif "RFID_IN" in message:
                    try:
                        parts = message.split(":")
                        role = parts[1]
                        num = parts[2]
                        name = parts[3]
                        
                        display_html = f"""
                        <html><body>
                        <p><span style="font-size:22pt; color:blue; font-weight:bold;">{role} OPEN</span></p>
                        <p><span style="font-size:16pt; color:black;">No. {num}</span></p>
                        <p><span style="font-size:18pt; color:darkcyan; font-weight:bold;">{name}</span></p>
                        </body></html>
                        """
                        self.doorText.setText(display_html)
                        self.add_log_row(current_time, f"🔓 Door Opened ({role}: {name} No.{num})")
                    except Exception as parse_err:
                        print(f"[RFID 메시지 파싱 오류] {parse_err}")                
                elif "DOOR_IS_OPEN" in message:
                    self.doorText.setText('<html><body><p><span style="font-size:22pt; color:blue;">OPEN</span></p></body></html>')
                    self.add_log_row(current_time, "🔓 Door Opened")
                elif "DOOR_IS_CLOSE" in message:
                    self.doorText.setText('<html><body><p><span style="font-size:22pt;">CLOSE</span></p></body></html>')
                    self.add_log_row(current_time, "🔒 Door Closed")

    def send_command(self, command):
        if self.socket.state() == QAbstractSocket.SocketState.ConnectedState:
            self.socket.write(f"{command}\n".encode('utf-8'))
        else:
            QMessageBox.warning(self, "전송 실패", "서버가 연결되어 있지 않습니다.")

    def on_disconnected(self):
        self.serverButton.setText("connect\nserver")
        self.serverButton.setEnabled(True)
        QMessageBox.warning(self, "연결 해제", "서버와의 연결이 끊어졌습니다.")

    def on_error(self, socket_error):
        self.serverButton.setText("connect\nserver")
        self.serverButton.setEnabled(True)
        QMessageBox.critical(self, "오류", f"서버 연결 중 오류 발생: {self.socket.errorString()}")

    # 프로그램 윈도우가 닫힐 때 카메라 스레드 안전 종료 처리
    def closeEvent(self, event):
        self.camera_thread.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    myWindows = WindowClass()
    myWindows.show()
    sys.exit(app.exec())