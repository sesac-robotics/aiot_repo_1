# PC2 얼굴 인식과 서버·GUI 연결

코드와 설정은 `aiot_repo_1`을 사용합니다. GUI는 `external` 모드로 실행하여 서버
PostgreSQL을 사람 정보의 기준으로 사용합니다. GUI의 `Current/data/gui.external.sqlite3`는
화면용 캐시와 로컬 이벤트 로그입니다. 데모 사람 데이터를 서버로 자동 등록하지 않습니다.

## 연결 구성

```text
PC4 카메라 → UDP 8766 → PC2 YOLO → TCP 8102 → PC1
                          └ 캡처 → InsightFace → HTTP 8101 → PC1
PC1 → HTTP 8767 /events → GUI: 이름·ID·역할·Allowed/Denied·로그
GUI ↔ DB 어댑터 ↔ HTTP 8101 → PC1: 사람 목록·등록·수정·삭제
```

GUI 이벤트 포트는 한 컴퓨터에서 PC4 Bridge의 TCP 8765와 겹치지 않도록 8767을
사용합니다. 기존 `start_linux.sh`/`start_windows.bat`는 데모 실행용으로 유지하고,
새 `Current/start_connected.sh`로 서버 연결용 GUI와 DB 어댑터를 함께 실행합니다.
Confirm/Emergency release 동작과 CCTV 영상 표시는 이번 변경 대상이 아닙니다.
문 상태는 실제 장치 이벤트를 받기 전까지 UNKNOWN이며 출입 허용만으로 OPEN으로 바꾸지 않습니다.

## 1. DB 켜기

이미 켜져 있으면 그대로 사용합니다. 이 명령은 기존 볼륨을 삭제하지 않습니다.

```bash
cd /home/sesac/dev_ws/aiot_repo_1/service/main_service
docker compose up -d postgres
```

## 2. 중앙 서버 켜기

기존 중앙 서버 터미널이 켜져 있다면 그 터미널에서 Ctrl+C로 서버만 종료하고
다음 명령으로 다시 시작합니다. DB·InsightFace·YOLO·카메라 터미널은 유지해도 됩니다.
서버 재시작 중 PC2 TCP/HTTP는 잠시 끊기고 기존 재연결·전송 대기 기능으로 다시 연결됩니다.

```bash
cd /home/sesac/dev_ws/aiot_repo_1
bash test/start_pc1_gui.sh
```

이 스크립트는 `GUI_EVENT_ENABLED=true`, `GUI_EVENT_URL=http://127.0.0.1:8767`과
이 저장소 GUI의 토큰 파일 경로를 지정합니다. 서버의 기존 DB·인증·PC4 설정을 사용합니다.
다른 DB 주소가 필요하면 `bash test/start_pc1_gui.sh --db <DB주소>`로 지정하세요.

## 3. GUI + DB 어댑터 켜기

이 PC에서 이미 설치된 GUI 가상환경을 재사용하는 명령입니다. 실행하는 소스는
`aiot_repo_1/ui/gui/Current`의 코드입니다.

```bash
cd /home/sesac/dev_ws/aiot_repo_1
GUI_PYTHON=/home/sesac/dev_ws/patient_care_fall_detection/gui/.venv/bin/python \
  bash ui/gui/Current/start_connected.sh
```

새 가상환경을 사용할 때는 한 번 준비한 뒤 환경변수 없이 실행합니다.

```bash
cd /home/sesac/dev_ws/aiot_repo_1
python3 -m venv ui/gui/.venv
ui/gui/.venv/bin/python -m pip install -r ui/gui/Current/requirements.txt
bash ui/gui/Current/start_connected.sh
```

실행기는 서버·DB·사람 API와 이벤트 전송 설정을 먼저 조회합니다. 서버가 꺼져 있거나
주소·인증·알림 설정이 맞지 않으면 오류를 표시하고 종료합니다. GUI가 준비된 뒤
DB 어댑터를 자동 시작하며 GUI 종료 또는 터미널 Ctrl+C 시 두 프로세스를 함께 종료합니다.
다른 GUI나 어댑터를 같은 설정으로 중복 실행하지 마세요.

화면 없이 설정만 확인하려면:

```bash
python3 ui/gui/Current/start_connected.py --check
```

서버 인증을 켰다면 실행 전 `TSAFE_ADMIN_TOKEN`에 해당 서버의 관리자 토큰을 설정합니다.
서버 인증이 꺼져 있으면 토큰 없이 사용할 수 있습니다. GUI 수신기 인증용 토큰은
`Current/data/gui_event.token`에 자동 생성되며 같은 PC의 서버·어댑터가 사용합니다.
토큰 내용은 Git에 올리지 않습니다.

## 4. 직접 확인하기

- People: 서버의 실제 사람 목록이 약 5초 주기로 동기화됩니다. 서버 DB가 비어 있으면 빈 목록이 정상입니다.
- 사람 등록: Add에서 역할과 정보를 입력하고 Save합니다. ID는 직접 입력하지 않고 서버 응답의
  P0001/S0001/A0001 등을 받습니다. DB: Saved (server)를 확인하세요.
- 카드 장치 없이도 사람 DB 저장은 완료됩니다. Card write: Waiting for card는 별도 장치 대기 상태입니다.
- 문 앞 인식: 기존 카메라·YOLO·InsightFace를 실행하고 얼굴을 촬영하면 Door 화면과 Logs에
  서버 판정이 표시됩니다. 벡터 미등록 상태라면 Unknown / Denied가 정상입니다.
- 서버 연결이 끊긴 상태에서 저장하면 DB: Failed로 표시됩니다. 연결 복구 후 Save로 재시도합니다.
- 얼굴 벡터 등록은 [별도 안내](../../service/ai_service/face_recongnition/FACE_REGISTRATION_KO.md)를 따릅니다.

서버와 GUI의 실제 통신 카운터는 다음 명령으로 확인합니다.

```bash
curl -s http://127.0.0.1:8101/health | python3 -m json.tool
```

`db: ok`, `gui_events.enabled: true`, `gui_events.token: true`를 확인하세요.
새 얼굴 판정 이벤트를 보낸 뒤 `gui_events.sent`가 증가하고 `last_error`가 null이면
서버 → GUI HTTP 전달이 성공한 것입니다. GUI에는 이후 발생한 이벤트가 전달됩니다.
GUI를 켜기 전의 출입 기록을 자동으로 모두 불러오는 기능은 이번 범위에 포함하지 않습니다.

## 검증 범위

`Current/tests/test_connected.py`는 화면 없는 실제 Qt, 임시 SQLite, 임시 HTTP 사람 API와
서버의 실제 `GuiNotifier`를 연결합니다. 직원/환자/미등록 표시, 서버 ID 반영, 인증 오류,
연결 실패 후 재시도, 결과 전달 실패 후 중복 등록 방지, 연결 실행기의 시작·종료를 확인합니다.
사용 중인 PostgreSQL이나 실제 사람·얼굴 데이터는 테스트하지 않습니다.
