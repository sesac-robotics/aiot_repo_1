# Current CCTV 최종 변경

- Future CCTV의 큰 영상/작은 지도 배치를 Current에 적용했습니다.
- 알림에는 FALL만 표시합니다. 환자 이름, ID, 낙상 시간을 표시하지 않습니다.
- 지도에도 FALL과 빨간 범위만 표시합니다.
- Current에는 Clip 팝업이 없고 Confirm 버튼으로 확인합니다. Confirm 후에도 알림은 FALL로 유지됩니다.
- Current의 얼굴 등록/도어 화면은 사진 없는 기존 구성을 유지합니다.

## 영상 연결 범위
기본값으로 Main Service의 `http://127.0.0.1:8101/api/v1/live/frame/camera_1`를 5 FPS로 조회합니다.
JPEG와 분석 JSON을 함께 받아 사람 bbox·관절을 영상 위에 표시하고, `fall_confirmed`일 때만 FALL 알림을 표시합니다.
개인 식별 정보나 발생 시간은 CCTV 화면에 표시하지 않습니다.

서버 주소 또는 카메라 ID가 다르면 실행 전에 환경 변수를 지정합니다.

```bash
TSAFE_FRAME_IMAGE_URL=http://192.168.15.135:8101/api/v1/live/frame/camera_1/image python run.py
```

메타데이터 URL을 별도로 쓸 때만 `TSAFE_FRAME_METADATA_URL`을 지정합니다. GUI는 조회 전용이므로 AI 전송과 Main Service 수신 동작에는 영향을 주지 않습니다.

## 실행
압축 해제 후 GUI/Current 폴더에서 .venv/bin/python3 run.py를 실행합니다.
기존 프로젝트의 GUI 폴더 바로 아래에서 run.py를 실행하는 구조라면 Current의 내용 전체를 해당 GUI 폴더에 넣으십시오.
기존 data/people.local.json은 교체 전 보관하십시오.
