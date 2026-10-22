# Current CCTV 최종 변경

- Future CCTV의 큰 영상/작은 지도 배치를 Current에 적용했습니다.
- 알림에는 FALL만 표시합니다. 환자 이름, ID, 낙상 시간을 표시하지 않습니다.
- 지도에도 FALL과 빨간 범위만 표시합니다.
- Current에는 Clip 팝업이 없고 Confirm 버튼으로 확인합니다. Confirm 후에도 알림은 FALL로 유지됩니다.
- Current의 얼굴 등록/도어 화면은 사진 없는 기존 구성을 유지합니다.

## 영상 연결 범위
이 ZIP만 실행하면 Camera 01 · Preview와 함께 샘플 정지 이미지를 표시합니다.
실제 CCTV 전송 방식/주소는 첨부 파일에 없으므로 수신 프로그램은 연결하지 않았습니다.
기존 영상 수신 코드가 프레임을 받으면 GUI 스레드에서 App.set_cctv_frame(QImage 또는 QPixmap)을 호출해 갱신할 수 있습니다.
낙상 신호는 App.set_fall_detected(True/False)로 반영할 수 있으며 개인 식별/발생 시간은 받지 않습니다.
워커에서 수신할 경우 Qt Signal로 GUI 스레드에 전달하십시오.

## 실행
압축 해제 후 GUI/Current 폴더에서 .venv/bin/python3 run.py를 실행합니다.
기존 프로젝트의 GUI 폴더 바로 아래에서 run.py를 실행하는 구조라면 Current의 내용 전체를 해당 GUI 폴더에 넣으십시오.
기존 data/people.local.json은 교체 전 보관하십시오.
