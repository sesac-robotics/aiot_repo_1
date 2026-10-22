# T-SAFE 최종 GUI — 시연 시나리오 반영

이번 변경은 Current에 적용했습니다. Future는 이전의 발전안 디자인 데모입니다.
Current는 Add 유지, 카드 감지/DB 저장/카드 쓰기 상태, Allowed/Denied,
실제 문 센서 상태, 실제 이벤트 저장/조회 기능을 포함합니다.

## 1. 설치와 실행

### 필요한 환경

- 권장 Python: **3.12** (현재 확인 환경: Ubuntu, Python 3.12.3).
- 데스크톱 화면이 있는 환경이 필요합니다. SSH/화면 없는 서버에서는 일반 창을 띄울 수 없습니다.
- 외부 Python 패키지는 `Current/requirements.txt`로 설치합니다.
  PyQt6 버전은 고정되어 있으며 SQLite와 HTTP 클라이언트는 Python 표준 라이브러리입니다.
  Windows에는 한국 시간대(`Asia/Seoul`) 처리를 위한 `tzdata`도 설치됩니다.
- Windows 실행 명령을 제공하지만 실제 Windows 환경 검증은 별도로 필요합니다.

저장소를 clone하거나 ZIP을 내려받은 뒤 **`gui` 폴더**에서 시작합니다.
폴더 이름은 소문자 `gui`이며 `Current` 내용물을 옮길 필요는 없습니다.
아래 첫 `cd gui`는 저장소 최상위 폴더를 기준으로 합니다.

### Linux (bash)

```bash
cd gui
python3 -m venv .venv
.venv/bin/python -m pip install -r Current/requirements.txt
.venv/bin/python Current/run.py
```

Ubuntu에서 가상환경 생성 시 `ensurepip` 오류가 나면 `python3-venv`를 먼저 설치합니다.
Qt 실행 시 `xcb` 관련 오류가 나면 시스템의 Qt/X11 공유 라이브러리를 확인합니다.
예를 들어 Ubuntu에서는 `libxcb-cursor0`가 필요할 수 있습니다.

### Windows (PowerShell)

Python 3.12와 Python Launcher(`py`)를 설치한 뒤 실행합니다.

```powershell
cd gui
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r Current\requirements.txt
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe Current\run.py
```

UTF-8 설정은 한글 JSON과 UI 파일을 읽기 위한 설정입니다.
가상환경의 Python을 직접 사용하므로 PowerShell 실행 정책을 바꿀 필요는 없습니다.

### 가상환경과 실행 스크립트

`.venv`는 각자 PC에서 만드는 환경으로, 다른 PC의 폴더를 복사해서 사용하지 않습니다.
의존성 목록은 `requirements.txt`로 공유하고 `.venv` 자체는 GitHub에 올리지 않습니다.
가상환경을 활성화하고 싶다면 `gui`에서 Linux는 `source .venv/bin/activate`,
Windows PowerShell은 `.\.venv\Scripts\Activate.ps1`을 사용합니다. 활성화는 선택 사항입니다.

위 설치를 마친 뒤에는 `gui`에서 `bash Current/start_linux.sh` 또는
Windows의 `Current/start_windows.bat`으로도 실행할 수 있습니다.
두 스크립트 모두 `gui/.venv`의 Python을 사용하며 의존성을 자동 설치하지는 않습니다.
Future 디자인 데모는 같은 가상환경으로 `Future/run.py` 또는 Future의 실행 스크립트를 사용합니다.
Future만 설치한다면 `Future/requirements.txt`를 사용합니다.

### GitHub에 포함할 파일

코드, `ui/`, `assets/`, `skin.qss`, `requirements.txt`, 문서와
`data/demo_people.json`은 함께 공유합니다. `gui/.gitignore`는 다음 실행 산출물을 제외합니다.

- `.venv/`, Python 캐시
- `data/*.sqlite3`와 SQLite 보조 파일 (환자·이벤트 기록)
- `data/people.local.json` (로컬 환자 정보)
- `data/gui_event.token` (이벤트 API 인증 토큰)
- `data/pc1_adapter_results.json`, `data/pc1_adapter_results.tmp` (연동 캐시)
- `.env` 파일 (개별 환경 설정)

기존 DB와 로컬 환자 파일은 업데이트 전에 따로 백업하십시오.
이미 Git으로 추적 중인 파일에는 `.gitignore`가 소급 적용되지 않습니다.
업로드 전 `git status --short`와 `git ls-files gui`로 포함 파일을 확인하십시오.
저장소 최상위에서 `git check-ignore -v gui/.venv/pyvenv.cfg gui/Current/data/gui_event.token`
명령으로 제외 규칙도 확인할 수 있습니다.

## 2. 기본 동작: local DB 모드

기본값은 local입니다. 환자 정보와 실제 수신 이벤트를 SQLite에 저장합니다.
파일: `Current/data/gui.sqlite3` (`gui` 기준).
처음 실행할 때만 기존 people.local.json이 있으면 가져오고, 없으면 가상 환자 예제를 가져옵니다.
최초 가져오기 이후에는 SQLite가 기준이며 JSON을 다시 읽어 덮어쓰지 않습니다.
로그에는 미리 만든 가상 출입 기록을 넣지 않습니다. 수신·저장된 이벤트만 표시합니다.
삭제 기능은 환자 정보를 삭제해도 기존 이벤트 이력은 유지합니다.

- Home → People → Add → 정보 입력 → Save.
- Card / DB / Card write 결과가 각각 표시됩니다.
- Save 후 결과를 볼 수 있도록 Register 페이지에 머무릅니다. People 버튼으로 목록으로 돌아갑니다.
- DB 저장이 성공해도 카드 쓰기 성공 응답이 없으면 카드 상태는 Waiting입니다.
- 카드가 없으면 DB 저장 후 Waiting for card로 기다립니다.
- 카드 쓰기 실패 시 DB 저장 성공 표시는 유지하고 카드 실패만 표시합니다.
- Door Access의 인식 판정과 문 센서 상태는 별도입니다. Allowed만으로 OPEN을 표시하지 않습니다.
- 미수신 상태는 Waiting / UNKNOWN입니다. GUI에 문 제어 버튼은 없습니다.
- CCTV는 큰 영상과 작은 지도, FALL만 표시합니다. 이름·낙상 시간을 표시하지 않습니다.
- 영상 수신 코드는 별도이며 기본 화면은 샘플 이미지입니다.
- Logs에서 이름/유형/날짜를 검색합니다. 한국 시간으로 표시하며 재실행 후에도 기록이 남습니다.

## 3. 기존 PC1 PostgreSQL 서버에 환자 관리 연결

PC1 v0.3의 환자 등록/조회/수정/비활성화 API를 사용하는 어댑터가 포함되어 있습니다.
PC1 서버와 PostgreSQL은 먼저 실행되어 있어야 합니다. 이 ZIP은 PC1 서버를 자동 설치/실행하지 않습니다.
PC1의 Rh+ ABO 저장에는 제공된 `migrations_001_blood_type.sql` 등 서버의 혈액형 마이그레이션 적용이 필요합니다.

터미널 1 (`gui` 폴더, Linux):

```bash
GUI_DB_MODE=external .venv/bin/python Current/run.py
```

터미널 2 (`gui` 폴더, 실제 URL과 관리자 토큰으로 변경):

```bash
export TSAFE_PC1_URL='http://127.0.0.1:8101'
export TSAFE_ADMIN_TOKEN='여기에_PC1_관리자_토큰'
.venv/bin/python Current/pc1_db_adapter.py
```

Windows PowerShell에서는 터미널 1에 `$env:GUI_DB_MODE = "external"`을 설정한 뒤
`.\.venv\Scripts\python.exe Current\run.py`를 실행합니다.
터미널 2에는 `$env:TSAFE_PC1_URL`과 `$env:TSAFE_ADMIN_TOKEN`을 설정한 뒤
`.\.venv\Scripts\python.exe Current\pc1_db_adapter.py`를 실행합니다.
각 터미널에서 `$env:PYTHONUTF8 = "1"`도 설정합니다.

이 모드에서는 샘플 환자를 사용하지 않고 서버 목록을 읽습니다.
Save는 서버 응답을 기다리며, 서버 성공 응답 이후에만 DB: Saved (server)가 됩니다.
신규 ID는 서버가 발급하며 성공 후 GUI에 반영됩니다. Delete는 서버 비활성화 API를 사용합니다.
서버 환자 목록은 약 5초마다 동기화합니다.
서버 모드의 GUI 캐시와 이벤트 기록은 `data/gui.external.sqlite3`에 저장합니다.
기존 PC1 v0.3 환자 API는 Patient만 지원합니다. Staff/Admin 신규 저장은 서버 API 확장이 필요하며,
어댑터는 이 경우 성공으로 표시하지 않고 오류를 돌려줍니다.
직원 인식 결과의 이름/권한 표시는 별도 recognition 이벤트로 받을 수 있습니다.
로그는 현재 GUI SQLite에 기록되며 PC1 서버의 출입/사고 테이블로 자동 전송하지 않습니다.

## 4. 실제 카드·인식·문·낙상 수신 연결

기본 이벤트 수신: `http://127.0.0.1:8765`.
인증 토큰은 최초 실행 시 `data/gui_event.token`에 생성됩니다.
`Current/device_client.py`를 기존 수신 프로그램에서 사용하거나 HTTP를 사용할 수 있습니다.
자세한 JSON 규격과 연결 예시는 `EVENT_INTEGRATION_KO.md`에 있습니다.

카드 드라이버와 기존 PC4/센서 수신 코드는 이 ZIP에 들어 있지 않습니다.
GUI는 장치 이벤트를 받아 표시하고 쓰기 요청을 제공하지만, 실제 카드 쓰기는 기존 장치 코드가 처리해야 합니다.
DB: Saved (local)는 PC1 DB 서버의 저장 완료를 뜻하지 않습니다. 서버 시연 시 external 모드를 사용하십시오.

## 5. 폴더/파일

| 경로 | 역할 |
|---|---|
| Current/ui/ | 편집 가능한 현재 Qt UI 6개 |
| Current/run.py | 화면 실행·페이지 이동 |
| Current/workflow.py | 카드/저장 상태, 권한/문 상태 반영, 로그 조회 |
| Current/event_store.py | SQLite 환자·이벤트 저장 |
| Current/event_bridge.py | 인증된 장치 이벤트 수신과 쓰기 요청 제공 |
| Current/device_client.py | 기존 장치 프로그램에서 사용하는 HTTP 클라이언트 |
| Current/pc1_db_adapter.py | 기존 PC1 v0.3 환자 API 연결 실행기 |
| Current/data/ | 실행 시 생성되는 DB/토큰 및 초기 샘플 JSON |
| Current/tests/ | GUI 흐름·이벤트 API·PC1 필드 매핑 테스트 |
| Current/assets/, skin.qss | 기존 배경·지도·사진·스킨 |
| Future/ | 이전 발전안 디자인 데모. Current의 새 기능은 포함하지 않음 |
| Previews/Current/ | 실제 Qt 기본 화면 캡처 |
| Previews/Scenario/ | 테스트 이벤트를 넣어 만든 시연 예시 캡처(실제 장비 검증 결과 아님) |

## 6. 검증 범위

오프스크린 Qt에서 카드 감지, DB 저장, 카드 성공/실패 응답 분리, UID 불일치 거부,
환자 Denied/직원 Allowed 표시, 판정과 문 상태 분리, 낙상 익명 표시,
이벤트 영속 저장·이름 검색·삭제, 인증된 HTTP 수신, PC1 API 필드 변환을 검사했습니다.
PC1 어댑터 HTTP 동작은 테스트 서버로 확인했으며 실제 PostgreSQL/리더기/도어 키트는 연결하지 않았습니다.

`gui` 폴더에서 실행합니다.

```bash
QT_QPA_PLATFORM=offscreen GUI_EVENT_PORT=0 .venv/bin/python -m unittest discover -s Current/tests -v
```

Windows PowerShell:

```powershell
$env:PYTHONUTF8 = "1"
$env:QT_QPA_PLATFORM = "offscreen"
$env:GUI_EVENT_PORT = "0"
.\.venv\Scripts\python.exe -m unittest discover -s Current\tests -v
```

테스트용 환경 변수는 해당 터미널에 남습니다. 일반 GUI 실행 시에는 새 터미널을 사용합니다.
