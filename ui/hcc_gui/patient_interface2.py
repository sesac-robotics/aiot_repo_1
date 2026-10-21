import sys
from PyQt6.QtWidgets import *
from PyQt6.QtGui import *
from PyQt6 import uic
from PyQt6.QtCore import *
import cv2, imutils
import serial
import struct
from MyThread import MyTimer
import time
import psycopg2



from_class = uic.loadUiType("patient_interface1.ui")[0]

class WindowClass(QMainWindow, from_class) :
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.setWindowTitle('project1')

        self.aboGroup = QButtonGroup(self)
        self.aboGroup.addButton(self.bloodaButton)
        self.aboGroup.addButton(self.bloodbButton)
        self.aboGroup.addButton(self.bloodabButton)
        self.aboGroup.addButton(self.bloodoButton)

        self.rhGroup = QButtonGroup(self)
        self.rhGroup.addButton(self.bloodrhpButton)
        self.rhGroup.addButton(self.bloodrhmButton)

        self.genderGroup = QButtonGroup(self)
        self.genderGroup.addButton(self.manButton)
        self.genderGroup.addButton(self.womanButton)

        self.patientTable.setColumnCount(9)
        self.patientTable.setHorizontalHeaderLabels(["no","name", "sex", "age", "blood type","height", "weight", "disase", "meds"])
        self.patientTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.patientTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.patientTable.itemDoubleClicked.connect(self.patientTable_clicked)
        self.patientTable.resizeColumnsToContents()

        self.readTable.setColumnCount(9)
        self.readTable.setHorizontalHeaderLabels(["no","name", "sex", "age", "blood type","height", "weight", "disase", "meds"])
        self.readTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.readTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.readTable.itemDoubleClicked.connect(self.readTable_clicked)
        self.readTable.resizeColumnsToContents()


        self.uid = bytes(4)
        self.conn = serial.Serial(port='/dev/ttyACM0', baudrate=9600, timeout=1)
        self.pixmap = QPixmap()
        self.cameraOn = False
        self.camera_timer = MyTimer()
        self.camera_timer.setInterval(0.1)
        self.count = 0
        self.enrollButton.clicked.connect(self.enroll)
        self.captureButton.clicked.connect(self.capture)
        self.cameraButton.clicked.connect(self.clickCamera)
        self.readButton.clicked.connect(self.readRfid)
        self.camera_timer.timeout.connect(self.updateCamera)

        try:
            self.db_conn = psycopg2.connect(
                host="localhost",
                database="hospital_db",        # 방금 만든 DB 이름
                user="postgres",               # 관리자 ID
                password="1212",     # ◀◀◀ 아까 설정한 비밀번호로 꼭 바꿔주세요!
                port="5432"
            )
            self.cursor = self.db_conn.cursor()
            print("[DB 연결 성공] PostgreSQL 데이터베이스와 연동되었습니다.")
            
            # 프로그램 시작할 때 기존 DB에 저장되어 있던 환자 목록을 UI 테이블에 로드
            self.load_patients_from_db()
            
        except Exception as e:
            print(f"[DB 연결 실패] 데이터베이스 연결 오류: {e}")
            self.db_conn = None

        


        
        
        self.fileButton.clicked.connect(self.file)

    def load_patients_from_db(self):
        if not self.db_conn: return
        try:
            self.patientTable.setRowCount(0)
            self.cursor.execute("SELECT p_num, name, gender, age, blood_type, height, weight, disease, meds FROM patients ORDER BY p_num ASC;")
            rows = self.cursor.fetchall()
            for row_data in rows:
                row = self.patientTable.rowCount()
                self.patientTable.insertRow(row)
                for col, data in enumerate(row_data):
                    self.patientTable.setItem(row, col, QTableWidgetItem(str(data)))
            self.patientTable.resizeColumnsToContents()
        except Exception as e:
            print(f"[DB 로드 실패] {e}")

    def updateCamera(self):
        retval,image = self.video.read()
        if retval:
            self.image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            h,w,c = self.image.shape
            qimage = QImage(self.image.data, w, h, w*c, QImage.Format.Format_RGB888)
            self.pixmap = self.pixmap.fromImage(qimage)
            self.pixmap = self.pixmap.scaled(self.imageLabel.width(), self.imageLabel.height())
            self.imageLabel.setPixmap(self.pixmap)
        
        # 목록에서 환자를 더블클릭하면 해당 행의 데이터를 입력창들에 자동으로 채워주는 메서드
    def patientTable_clicked(self, item):
        try:
            current_row = self.patientTable.currentRow()
            if current_row == -1: return

            # 1. 테이블 행 내의 9개 컬럼 셀 데이터 차례대로 취득
            p_num = self.patientTable.item(current_row, 0).text()
            name = self.patientTable.item(current_row, 1).text()
            gender = self.patientTable.item(current_row, 2).text()
            age_str = self.patientTable.item(current_row, 3).text()
            blood = self.patientTable.item(current_row, 4).text()
            height = self.patientTable.item(current_row, 5).text()
            weight = self.patientTable.item(current_row, 6).text()
            disease = self.patientTable.item(current_row, 7).text()
            meds = self.patientTable.item(current_row, 8).text()

            # 2. QLineEdit창 전체 역복원
            self.numberEdit.setText(p_num)
            self.nameEdit.setText(name)
            self.heightEdit.setText(height)
            self.weightEdit.setText(weight)
            self.diseaseEdit.setText(disease)
            self.medEdit.setText(meds)
            
            # 나이를 기반으로 QDateEdit 세팅 역산 (2026 - 나이)
            if age_str.isdigit():
                birth_year = 2026 - int(age_str)
                self.birthEdit.setDate(QDate(birth_year, self.birthEdit.date().month(), self.birthEdit.date().day()))

            # 3. 성별 라디오 버튼 동기화
            if gender == "M": self.manButton.setChecked(True)
            elif gender == "F": self.womanButton.setChecked(True)

            # 4. 혈액형 라디오 버튼 복원 동기화
            if "Rh+" in blood: self.bloodrhpButton.setChecked(True)
            elif "Rh-" in blood: self.bloodrhmButton.setChecked(True)

            if "AB" in blood: self.bloodabButton.setChecked(True)
            elif "A" in blood: self.bloodaButton.setChecked(True)
            elif "B" in blood: self.bloodbButton.setChecked(True)
            elif "O" in blood: self.bloodoButton.setChecked(True)

            print(f"[선택 로드 완료] NO.{p_num} {name} 환자의 기록이 제어판에 로드되었습니다.")
        except Exception as e:
            print(f"[테이블 복원 처리 실패] {e}")

    def readTable_clicked(self, item):
        try:
            current_row = self.readTable.currentRow()
            if current_row == -1: return

            p_num = self.readTable.item(current_row, 0).text()
            name = self.readTable.item(current_row, 1).text()
            gender = self.readTable.item(current_row, 2).text()
            age_str = self.readTable.item(current_row, 3).text()
            blood = self.readTable.item(current_row, 4).text()
            height = self.readTable.item(current_row, 5).text()
            weight = self.readTable.item(current_row, 6).text()
            disease = self.readTable.item(current_row, 7).text()
            meds = self.readTable.item(current_row, 8).text()

            self.numberEdit.setText(p_num)
            self.nameEdit.setText(name)
            self.heightEdit.setText(height)
            self.weightEdit.setText(weight)
            self.diseaseEdit.setText(disease)
            self.medEdit.setText(meds)
            
            if age_str.isdigit():
                birth_year = 2026 - int(age_str)
                self.birthEdit.setDate(QDate(birth_year, self.birthEdit.date().month(), self.birthEdit.date().day()))

            if gender == "M": self.manButton.setChecked(True)
            elif gender == "F": self.womanButton.setChecked(True)

            if "Rh+" in blood: self.bloodrhpButton.setChecked(True)
            elif "Rh-" in blood: self.bloodrhmButton.setChecked(True)

            if "AB" in blood: self.bloodabButton.setChecked(True)
            elif "A" in blood: self.bloodaButton.setChecked(True)
            elif "B" in blood: self.bloodbButton.setChecked(True)
            elif "O" in blood: self.bloodoButton.setChecked(True)

        except Exception as e:
            print(f"[readTable 클릭 복원 오류] {e}")




    def file(self):
        file = QFileDialog.getOpenFileName(filter='Image (*.png *.jpg)')

        image = cv2.imread(file[0])
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        h,w,c = image.shape
        qimage = QImage(image.data, w, h, w*c, QImage.Format.Format_BGR888)
        self.pixmap = self.pixmap.fromImage(qimage)
        self.pixmap = self.pixmap.scaled(self.label.width(), self.label.height())

        self.imageLabel.setPixmap(self.pixmap)

    def enroll(self):
        try:
            # 1. 화면의 입력값 추출 및 가공
            num_str = self.numberEdit.text()
            name_str = self.nameEdit.text()
            disease_str = self.diseaseEdit.text()
            med_str = self.medEdit.text()
            height_str = self.heightEdit.text()
            weight_str = self.weightEdit.text()
            
            # 나이 정수형 변환 및 연도 계산 (2026년 기준)
            birth_year = self.birthEdit.date().year()
            age = 2026 - birth_year                     

            # 예외 처리: 숫자형 데이터 안전하게 파싱
            try:
                p_num = int(num_str) if num_str else 0
                height = float(height_str) if height_str else 0.0
                weight = float(weight_str) if weight_str else 0.0
            except ValueError:
                print("[경고] 고유 번호(NO.)는 정수, 키/몸무게는 숫자만 입력 가능합니다.")
                return

            # 아두이노 전송용 바이트 객체와 테이블 출력용 문자열 변수를 각각 생성 (★ 누락 해결)
            gender_bytes = b'M' if self.manButton.isChecked() else b'F'
            gender_str = "M" if self.manButton.isChecked() else "F"

            # 혈액형 문자열 합성
            rh_type = "Rh-" if self.bloodrhmButton.isChecked() else "Rh+"
            abo_type = "O"
            if self.bloodaButton.isChecked(): abo_type = "A"
            elif self.bloodbButton.isChecked(): abo_type = "B"
            elif self.bloodabButton.isChecked(): abo_type = "AB"
            elif self.bloodoButton.isChecked(): abo_type = "O"
            blood_str = f"{rh_type} {abo_type}"

            if self.db_conn:
                try:
                    sql = """
                        INSERT INTO patients (p_num, name, gender, age, blood_type, height, weight, disease, meds)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (p_num) 
                        DO UPDATE SET name=%s, gender=%s, age=%s, blood_type=%s, height=%s, weight=%s, disease=%s, meds=%s;
                    """
                    data = (p_num, name_str, gender_str, age, blood_str, height, weight, disease_str, med_str,
                            name_str, gender_str, age, blood_str, height, weight, disease_str, med_str)
                    self.cursor.execute(sql, data)
                    self.db_conn.commit()
                except Exception as db_err:
                    self.db_conn.rollback()
                    print(f"[DB 저장 실패] {db_err}")

            # 2. struct 바이너리 패킷 생성 및 인코딩 패딩
            name_bytes = name_str.encode('utf-8')[:10].ljust(10, b'\x00')
            disease_bytes = disease_str.encode('utf-8')[:15].ljust(15, b'\x00')
            med_bytes = med_str.encode('utf-8')[:15].ljust(15, b'\x00')
            blood_bytes = blood_str.encode('utf-8').ljust(6, b'\x00')
            
            # 포맷 규칙: NO(i), 이름(10s), 성별(c), 나이(i), 혈액형(6s), 키(f), 몸무게(f), 질병(15s), 복용약(15s) -> 총 63바이트
            packet = struct.pack('<i 10s c i 6s f f 15s 15s', 
                     p_num, name_bytes, gender_bytes, age, blood_bytes, height, weight, disease_bytes, med_bytes)
            
            # 아두이노 전송
            if self.conn and self.conn.is_open:
                self.conn.write(b'\x02') 
                self.conn.write(packet)
                print(f"[RFID 전송] NO.{p_num} {name_str} 데이터 송신 완료 ({len(packet)} bytes)")

             

            self.load_patients_from_db()
            print(f"[리스트 갱신 완료] DB에서 최신 환자 목록을 불러와 표를 새로고침했습니다.")
            
            print(f"[리스트 갱신 완료] NO.{p_num} {name_str} 환자 목록에 정상 등록되었습니다.")

        except Exception as e:
            print(f"[enroll 오류 발생] {e}")



    def readRfid(self):
        try:
            if not self.conn or not self.conn.is_open:
                print("[오류] 시리얼 포트 연결을 확인하세요.")
                return

            print("[RFID 조회] 카드를 대고 기다려주세요...")
            # 1. 아두이노에 읽기 명령 헤더(0x03) 송신
            self.conn.write(b'\x03')

            # 2. 응답 마커(0x04) 확인용 타임아웃 루프 (최대 2초 대기)
            start_time = time.time()
            response_started = False
            
            while time.time() - start_time < 2.0:
                if self.conn.in_waiting > 0:
                    marker = self.conn.read(1)
                    if marker == b'\x04':
                        response_started = True
                        break
                time.sleep(0.01)

            if not response_started:
                print("[조회 실패] 아두이노로부터 응답이 없거나 카드가 인식되지 않았습니다.")
                return

            # 3. 데이터 패킷 크기(63바이트)만큼 수신 대기
            packet_size = 63
            start_time = time.time()
            while self.conn.in_waiting < packet_size:
                if time.time() - start_time > 1.0:
                    print("[오류] 데이터 수신 중 타임아웃이 발생했습니다.")
                    return
                time.sleep(0.01)

            packet = self.conn.read(packet_size)

            # 4. struct를 사용하여 바이너리 패킷 언패킹 (63바이트 규격 일치)
            # NO(i), 이름(10s), 성별(c), 나이(i), 혈액형(6s), 키(f), 몸무게(f), 질병(15s), 복용약(15s)
            unpacked = struct.unpack('<i 10s c i 6s f f 15s 15s', packet)

            # 5. 각 항목 인덱스 별 데이터 추출 및 디코딩 연산
            p_num = unpacked[0]
            name_str = unpacked[1].decode('utf-8').strip('\x00')
            gender_str = unpacked[2].decode('utf-8')
            age = unpacked[3]
            blood_str = unpacked[4].decode('utf-8').strip('\x00')
            
            height_val = unpacked[5]
            weight_val = unpacked[6]
            height_str = f"{height_val:.1f}" if height_val > 0 else "0"
            weight_str = f"{weight_val:.1f}" if weight_val > 0 else "0"
            
            disease_str = unpacked[7].decode('utf-8').strip('\x00')
            med_str = unpacked[8].decode('utf-8').strip('\x00')

            # 6. UI 제어판 입력창들에 값 세팅
            self.numberEdit.setText(str(p_num))
            self.nameEdit.setText(name_str)
            self.heightEdit.setText(height_str)
            self.weightEdit.setText(weight_str)
            self.diseaseEdit.setText(disease_str)
            self.medEdit.setText(med_str)

            # 나이를 기반으로 QDateEdit 세팅 역산 (2026 - 나이)
            birth_year = 2026 - age
            self.birthEdit.setDate(QDate(birth_year, self.birthEdit.date().month(), self.birthEdit.date().day()))

            # 성별 라디오 버튼 동기화
            if gender_str == "M": 
                self.manButton.setChecked(True)
            elif gender_str == "F": 
                self.womanButton.setChecked(True)

            # 혈액형 라디오 버튼 복원 동기화
            if "Rh+" in blood_str: self.bloodrhpButton.setChecked(True)
            elif "Rh-" in blood_str: self.bloodrhmButton.setChecked(True)

            if "AB" in blood_str: self.bloodabButton.setChecked(True)
            elif "A" in blood_str: self.bloodaButton.setChecked(True)
            elif "B" in blood_str: self.bloodbButton.setChecked(True)
            elif "O" in blood_str: self.bloodoButton.setChecked(True)

            # 7. 우측 readTable 리스트 목록에 조회한 행 실시간 추가
            row = self.readTable.rowCount()
            self.readTable.insertRow(row)
            self.readTable.setItem(row, 0, QTableWidgetItem(str(p_num)))
            self.readTable.setItem(row, 1, QTableWidgetItem(name_str))
            self.readTable.setItem(row, 2, QTableWidgetItem(gender_str))
            self.readTable.setItem(row, 3, QTableWidgetItem(str(age)))
            self.readTable.setItem(row, 4, QTableWidgetItem(blood_str))
            self.readTable.setItem(row, 5, QTableWidgetItem(height_str))
            self.readTable.setItem(row, 6, QTableWidgetItem(weight_str))
            self.readTable.setItem(row, 7, QTableWidgetItem(disease_str))
            self.readTable.setItem(row, 8, QTableWidgetItem(med_str))

            print(f"[RFID 로드 완료] NO.{p_num} {name_str} 환자 데이터 출력 및 리스트 등록 성공")
            role_str = "Unknown"
            if 1 <= p_num <= 3000:
                role_str = "Patient"
            elif 3001 <= p_num <= 4000:
                role_str = "Visitor"
            elif 4001 <= p_num <= 5000:
                role_str = "Staff"

            # rfid_signal.txt 파일에 "역할:번호:이름" 형태로 한 줄 쓰기
            try:
                with open("rfid_signal.txt", "w", encoding="utf-8") as f:
                    f.write(f"{role_str}:{p_num}:{name_str}")
                print(f"[파일 신호 저장 완료] {role_str}:{name_str}")
            except Exception as file_err:
                print(f"[파일 저장 실패] {file_err}")              
        except Exception as e:
            print(f"[RFID 조회 오류] {e}")
   

    def capture(self):
        try:
            # 1. cameraOn 상태이고 현재 프레임(self.image)이 존재하는지 확인
            if not self.cameraOn or not hasattr(self, 'image'):
                print("[캡처 실패] 카메라가 켜져 있지 않거나 영상 프레임이 없습니다.")
                return
            
            # 2. numberEdit에서 텍스트를 가져와 파일 이름 설정 (비어있으면 기본값 사용)
            p_num = self.numberEdit.text().strip()
            if not p_num:
                p_num = f"captured_{int(time.time())}" # 번호가 없으면 현재 시간 기반 저장
                
            filename = f"{p_num}.jpg"
            
            # 3. OpenCV는 BGR 형식을 사용하므로 RGB 상태인 self.image를 다시 BGR로 변환하여 저장
            save_image = cv2.cvtColor(self.image, cv2.COLOR_RGB2BGR)
            
            # 4. 이미지 파일 저장 실행
            success = cv2.imwrite(filename, save_image)
            
            if success:
                print(f"[캡처 완료] 환자 번호 이미지 저장 성공: {filename}")
            else:
                print("[캡처 실패] 파일 저장 중 오류가 발생했습니다.")
                
        except Exception as e:
            print(f"[capture 오류 발생] {e}")

    def clickCamera(self):
        if self.cameraOn == False:
            self.cameraButton.setText('camera off')
            self.cameraOn = True
            self.camera_timer.start()
            self.video = cv2.VideoCapture(-1)
            self.captureButton.show()
        else:
            self.cameraButton.setText('camera on')
            self.cameraOn = False
            self.camera_timer.stop()
            self.video.release()
            self.captureButton.hide()



if __name__ == "__main__":
    app = QApplication(sys.argv)
    myWindows = WindowClass()
    myWindows.show()
    sys.exit(app.exec())