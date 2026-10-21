# T-SAFE PC1 중앙 서버

GUI · PC2(얼굴 인식) · 낙상 감지 모듈 · Door MCU · CCTV MCU가 모두 연결되는 **중앙 서버**입니다.
각자 **자기 PC에서 서버를 켜서 개발·시험**할 수 있고, 시연 때는 PC1 한 대에서 켜고 모두 거기에 접속합니다.

```
 GUI ───────── HTTP / WebSocket ──┐
 PC2 얼굴 인식 ─ HTTP ─────────────┤
 낙상 감지 모듈 ─ HTTP ────────────┼──► [중앙 서버 :8101] ──► DB (PostgreSQL + pgvector)
 PC2 추적 ───── HTTP 또는 TCP 8102 ┤          │
 Door MCU ───── TCP 8103 (1/0) ────┘          └──► CCTV MCU (USB Serial, 팬틸트)
```

---

## 1. 실행하기

> **먼저 알아둘 것**
> - **`.env` 파일은 없어도 됩니다.** 모든 설정에 기본값이 있고, 바꿀 것만 명령행 옵션(`--db`, `--port`)으로 줍니다.
> - **가상환경은 서버 전용으로 따로** 만드세요(`.venv`). 낙상·얼굴 AI가 쓰는 가상환경(torch, YOLO 등)에 서버 패키지를 섞지 않습니다. 패키지 버전은 범위로만 지정돼 있어 Python 3.10 이상이면 됩니다.
> - **서버에 데이터를 보내기만 하는 팀**(낙상, 얼굴)은 자기 가상환경에 아무것도 설치할 필요가 없습니다. 예제 코드(`examples/fall_sender_example.py`, `tools/*.py`)는 표준 라이브러리만 씁니다.

### 방법 A. Docker로 전부 (가장 쉬움)
Windows·Mac은 [Docker Desktop](https://www.docker.com/products/docker-desktop/), Linux는 Docker Engine을 설치한 뒤:
```bash
cd service/main_service
docker compose up -d --build        # DB + 서버. 처음에는 몇 분 걸림. 설정 파일 필요 없음
```
브라우저에서 **http://localhost:8101/docs** → 모든 API가 나오고, 화면에서 바로 눌러 시험할 수 있습니다.

| 하고 싶은 것 | 명령 |
|---|---|
| 로그 보기 | `docker compose logs -f server` |
| 끄기 (데이터 유지) | `docker compose down` |
| DB까지 완전히 초기화 | `docker compose down -v` |
| 코드 고친 뒤 다시 켜기 | `docker compose up -d --build` |

### 방법 B. 내 PC에 이미 PostgreSQL이 있을 때 (Docker 없이)
pgvector 확장이 설치된 PostgreSQL이면 됩니다. **빈 DB 하나만 만들면 테이블은 서버가 처음 켜질 때 자동으로 만듭니다.**
```bash
# 1) 빈 DB 만들기 (한 번만). pgAdmin에서 만들어도 됨
psql -U postgres -c "CREATE DATABASE hcc;"

# 2) 서버 전용 가상환경
cd service/main_service
python -m venv .venv
.venv\Scripts\activate                        # Windows
source .venv/bin/activate                     # Mac / Linux
pip install -r requirements.txt

# 3) 실행: 내 PostgreSQL 계정·비밀번호를 그대로 넣는다
python run.py --db postgresql://postgres:내비번@localhost:5432/hcc
```
처음 실행하면 `[DB] 빈 DB → 테이블 자동 생성`, `[DB] 준비 완료`가 나옵니다.
`pgvector 확장이 설치되지 않은 PostgreSQL입니다`가 나오면 pgvector를 설치하거나(`db/README.md`) 방법 C를 쓰세요.

### 방법 C. DB만 Docker, 서버는 Python으로 (서버 코드를 고치는 사람에게 추천)
pgvector 설치가 번거로우면 DB만 Docker로 켭니다. 코드를 저장하면 서버가 자동으로 다시 켜집니다.
```bash
docker compose up -d postgres                 # DB만
# 가상환경은 방법 B의 2)와 같음
python run.py --reload                        # --db 없이 실행하면 이 Docker DB에 붙음
```

> 설정을 계속 같은 값으로 쓰고 싶으면 `.env.example`을 `.env`로 복사해서 고쳐도 됩니다. 우선순위: 명령행 옵션 > 환경변수 > `.env` > 기본값.

### 켜졌는지 확인
`http://localhost:8101/health` → `"server":"ok"`, `"db":"ok"`가 나오면 정상입니다.

---

## 2. 다른 PC에서 접속하기 (시연 · 통합 시험)

1. 서버를 켠 PC의 IP를 확인합니다. `python run.py`로 켜면 화면에 표시되고, 아니면 Windows는 `ipconfig`, Mac/Linux는 `ip addr`로 봅니다. 예: `192.168.0.10`
2. 방화벽에서 포트를 허용합니다.
   - **Windows:** 처음 실행할 때 뜨는 방화벽 창에서 "허용"
   - **Linux:** `sudo ufw allow 8101 && sudo ufw allow 8103`
3. 다른 PC에서 `http://192.168.0.10:8101/health`로 확인합니다.
4. 모든 PC가 **같은 공유기(Wi-Fi)**에 연결돼 있어야 합니다. 학교망처럼 기기끼리 통신이 막힌 곳이라면 개인 공유기를 쓰세요.

| 포트 | 누가 쓰나 | 내용 |
|---|---|---|
| **8101** | GUI, PC2, 낙상 모듈 | REST API, WebSocket `/ws`, 문서 `/docs` |
| **8103** | Door MCU | 문 상태 `1`/`0` (TCP) |
| 8102 | PC2 (선택) | 추적 좌표 TCP (`.env`의 `PC2_TCP_ENABLED=true`일 때만) |
| 5432 | 서버만 | DB. 서버 PC 안에서만 열림 (다른 PC는 접속하지 않음) |

---

## 3. 역할별 사용법

### GUI 담당
- 환자 목록·등록·수정·삭제, 문 상태, 알람 목록과 해제는 **REST API**로 처리합니다.
- 문 상태 변화, 낙상 알람 팝업, 얼굴 식별 결과는 **WebSocket `ws://<서버>:8101/ws`**에 접속만 해 두면 자동으로 옵니다.
- 예제: `python examples/gui_client_example.py --server 192.168.0.10:8101` (requests와 websockets 필요)
- ⚠ 현재 `ui/hcc_gui`는 DB(`hospital_db`/`patients`)에 psycopg2로 직접 붙고, 서버를 TCP 8080 텍스트(`FALL`, `DOOR_IS_OPEN` …)로 기다립니다. 이 서버와는 아직 연결되지 않으니 위 REST/WebSocket으로 바꿔야 합니다.

### PC2 얼굴 인식 담당 (`service/ai_service/face_recongnition`)
- **지금 코드 그대로 연결됩니다.** PC2의 `.env`에 아래 두 줄만 넣으면 `face_service.py`가 식별 결과를 서버로 보냅니다.
  ```dotenv
  PC1_EMBEDDING_URL=http://<서버IP>:8101/api/v1/face/identify
  PC1_EMBEDDING_TOKEN=dev        # 서버 인증이 꺼져 있으면 아무 값이나 OK (비우면 전송 보류하는 문서 규칙 대비)
  ```
- 서버 응답에는 항상 `"accepted": true`와 같은 `event_id`가 들어 있어 outbox에서 지워집니다. `NoFace`·`MultipleFaces`·`Error`도 받기만 하고(`processed: false`) 판정하지 않습니다.
- **등록:** `face_db_export.py`가 만든 `db_embeddings/<patient_id>.json`을 그대로 보냅니다.
  `python tools/send_face_json.py <db_embeddings 폴더> --server <서버IP>:8101`
  처음 보는 `patient_id`(예: `patient_001`)는 이름 없는 환자로 자동 생성됩니다(`FACE_AUTO_CREATE_PATIENT=true`). 이름 등은 나중에 GUI에서 채웁니다.
- **식별:** 서버가 DB에서 가장 비슷한 사람을 찾아 `MATCHED`/`UNKNOWN`으로 답합니다. 이미 저장된 결과 JSON(`output/*.json`)도 같은 도구로 다시 보낼 수 있습니다.
- **추적 좌표:** `webcam/detect_yolov3.py`가 TCP 8102로 보내는 `T,T,x,y,dx,dy` / `T,N,...` / `N,N` 줄을 그대로 받습니다. PC2 `.env`의 `PC1_TRACKING_HOST`를 서버 IP로 바꾸면 됩니다. (`PC2_TCP_ENABLED` 기본 켜짐)
- 실물 없이 형식 확인: `python tools/fake_pc2.py register P0001` → `python tools/fake_pc2.py identify P0001`

### 낙상 감지 모듈 담당 (`service/ai_service/fall_detection`)
**① 서버 켜기 (내 PC에서 혼자 시험할 때)**
```bash
cd service/main_service
docker compose up -d          # DB + 서버가 한 번에 켜짐 → http://localhost:8101/docs
```
Docker가 없으면 1장의 방법 B(내 PostgreSQL에 `--db`로 연결)를 따릅니다. 팀 공용 서버(PC1)에 붙을 때는 이 단계가 필요 없습니다.

**② 낙상 감지 코드에 붙이기**: `examples/fall_sender_example.py`의 `FallSender`를 복사해서 씁니다.
```python
from fall_sender_example import FallSender
sender = FallSender("http://127.0.0.1:8101")       # 공용 서버면 PC1 IP
if is_fall:                                         # 모델이 낙상이라고 판단했을 때
    sender.send_fall(camera_id="CAM01", confidence=score, bbox_xyxy=[x1, y1, x2, y2], track_id=str(tid))
```
- 같은 사람(track)은 10초에 한 번만 보냅니다. 서버가 꺼져 있어도 2초 후 포기하고 감지 루프는 계속 돕니다.
- 보내는 값: `event_id`, `camera_id`, `occurred_at`(시간대 포함)은 필수이고 `confidence`, `bbox_xyxy`, `track_id`는 선택입니다. `event_type`은 `FALL` / `COLLAPSE` / `SUSPECTED_FALL` 중 하나입니다.
- 같은 `event_id`를 다시 보내도 알람은 하나만 생깁니다. 위치를 안 보내면 카메라 위치(`CAM01` → `room_101`)를 씁니다.

**③ 잘 들어갔는지 확인**: `python examples/fall_sender_example.py` → `[FALL SENT]`가 나오면 성공입니다. 알람 목록은 http://localhost:8101/api/v1/alerts 에서 봅니다.

| 응답 | 뜻 |
|---|---|
| 200 | 알람 생성 (GUI에 `fall_alert`가 실시간으로 뜸) |
| 409 등록되지 않은 카메라 | 기본은 `CAM01`만 등록돼 있음. 다른 ID는 `/docs`의 `POST /api/v1/cameras`로 먼저 등록 |
| 422 | 필드 형식 오류. `detail`에 틀린 필드가 나옴 |
| 503 | 서버는 켜졌는데 DB가 꺼짐 → `docker compose up -d` |

### Door MCU 담당
- 문을 열고 닫는 판단은 **MCU가 직접** 합니다. 서버는 결과만 받습니다.
- 서버 IP의 **TCP 8103**에 접속해, 문 상태가 바뀔 때마다 `1\n`(열림) 또는 `0\n`(닫힘)을 보냅니다. 문이 여러 개면 `door_02,1\n`처럼 ID를 붙입니다.
- **줄 끝에 반드시 `\n`**을 붙입니다(`Serial.println`/`client.println`이면 자동). `\r\n`도 됩니다.
- 연결이 끊기면 GUI에 "연결 끊김"으로 표시되니, 끊기면 다시 접속하도록 만듭니다. 서버는 응답을 보내지 않습니다.
- ESP32 예제: `examples/esp32_door_state/esp32_door_state.ino`
- 실물 없이 시험: `python tools/fake_door_mcu.py --server 192.168.0.10`

### CCTV MCU (팬틸트) 담당
- 서버를 켠 PC에 USB로 연결하고 `.env`에 `TRACK_SERIAL_PORT`를 설정합니다. Windows는 `COM3`, Linux는 `/dev/ttyUSB0` 형식입니다.
- 서버가 보내는 줄은 `T,x,y,dx,dy\n`(추적 중)과 `N\n`(대상 없음 → 정지) 두 가지입니다. 추적이 1.5초 끊기면 서버가 자동으로 `N`을 보냅니다.

### 서버 담당
- 5장의 폴더 구조를 보고 해당 SR 파일만 고치면 됩니다.
- 고친 뒤에는 `pytest -q`를 돌립니다.

---

## 4. API 한눈에 보기

자세한 입력·출력 형식은 `http://<서버>:8101/docs`에 다 나옵니다.

| SR | 부르는 쪽 | 메서드 · 주소 | 하는 일 |
|---|---|---|---|
| 공통 | 누구나 | `GET /health` | 서버·DB·장치 연결 상태 |
| SR-02 | GUI | `GET /api/v1/patients` | 환자 목록 (이름·나이·성별·방·얼굴 등록 여부) |
| SR-02 | GUI | `POST /api/v1/patients` | 환자 등록 → 코드 `P0001`부터 서버가 발급 |
| SR-02 | GUI | `GET / PATCH / DELETE /api/v1/patients/{id}` | 조회 / 수정(보낸 필드만) / 삭제(비활성화 + 얼굴 데이터 삭제) |
| SR-02 | GUI | `PUT /api/v1/patients/{id}/card` | 카드 UID 연결 |
| SR-02 | PC2 | `POST /api/v1/face/templates` | 얼굴 특징벡터 등록 |
| SR-05 | PC2 | `POST /api/v1/face/identify` | 얼굴 식별 (DB 유사도 검색) |
| SR-04 | Door MCU | TCP 8103 `1` / `0` | 문 상태 보고 |
| SR-04 | GUI | `GET /api/v1/doors`, `GET /api/v1/doors/{id}/history` | 문 상태·연결 여부 / Door History |
| SR-03 | 낙상 모듈 | `POST /api/v1/falls` | 낙상 이벤트 → 알람 생성 |
| SR-03 | GUI | `GET /api/v1/alerts?status=ACTIVE` | 알람 목록 |
| SR-03 | GUI | `POST /api/v1/alerts/{id}/acknowledge`, `/resolve` | 알람 확인 / 상황 해제 |
| SR-06 | PC2 | `POST /api/v1/tracking/observations` (또는 TCP 8102) | 추적 좌표 → CCTV MCU |
| 기타 | GUI | `GET/POST /api/v1/cameras`, `POST /api/v1/doors` | 카메라·문 등록 |

**WebSocket `/ws` 메시지**: `{"type": "...", "ts": "...", "data": {...}}`

| type | 언제 | data 예 |
|---|---|---|
| `door_state` | 문 열림·닫힘, MCU 연결 끊김 | `{"door_id":"door_01","state":"OPEN","online":true}` |
| `fall_alert` | 낙상 알람 발생 (팝업) | 알람 ID, 위치, 시각, 환자, 신뢰도 |
| `alert_update` | 알람 확인·해제 | `{"alert_id":"...","status":"RESOLVED"}` |
| `face_result` | 얼굴 식별 결과 | `result`, `person_id`, `name`, `similarity`, `can_open` |
| `face_registered` | 얼굴 등록 완료 | `{"person_id":"P0001"}` |

**공통 규칙**
- **JSON 키:** `snake_case`
- **시각:** 시간대가 포함된 ISO 8601 (`2026-10-21T15:00:00+09:00`). 시간대가 없으면 422
- **ID:** 환자 `P0001`(서버 발급), 문 `door_01`, 카메라 `CAM01`(추적·낙상) · `FACE01`(문 앞 얼굴)
- **에러:** `{"detail": "..."}`

| 코드 | 의미 |
|---|---|
| 200, 201 | 성공, 생성 |
| 404 | 대상 없음 |
| 409 | 중복이거나 상태 충돌 |
| 422 | 입력 형식 오류 (`detail`에 어느 필드가 왜 틀렸는지 나옴) |

**역할 원칙**
- **DB는 서버만** 접근합니다.
- **판단은 서버가** 합니다(얼굴 일치, 알람). 단, **출입문 개폐는 Door MCU가** 판단합니다.

---

## 5. 파일 설명

```
tsafe_server/
├─ run.py                    서버 실행 (python run.py). 접속 주소를 화면에 표시
├─ docker-compose.yml        DB + 서버를 한 번에 실행하는 설정
├─ Dockerfile                서버 컨테이너 만드는 방법
├─ requirements.txt          Python 패키지 목록 (시험한 버전으로 고정)
├─ .env / .env.example       설정값 (아래 7장). .env는 Git에 올리지 않음
├─ pytest.ini                시험 설정
│
├─ app/                      ★ 서버 코드
│  ├─ main.py                앱 생성 · 라우터 연결 · /health · /ws · 시작할 때 DB 준비
│  ├─ config.py              모든 설정값 (.env 자동 읽기)
│  ├─ schemas.py             API 입력 형식과 검사 규칙 (SR별 구역)
│  ├─ db.py                  DB 연결 도우미
│  ├─ auth.py                토큰 인증 (AUTH_ENABLED=true일 때만)
│  ├─ events.py              WebSocket 실시간 알림 (type 목록)
│  ├─ routers/               API — SR별 한 파일
│  │  ├─ patients.py         SR-02 환자 등록·조회·수정·삭제, 카드 연결
│  │  ├─ face.py             SR-02/05 얼굴 등록, 얼굴 식별 (유사도 SQL = MATCH_SQL)
│  │  ├─ doors.py            SR-04 문 상태·이력 조회
│  │  ├─ falls.py            SR-03 카메라 등록, 낙상 수신, 알람 확인·해제
│  │  └─ tracking.py         SR-06 추적 좌표 수신
│  └─ services/              장치 통신
│     ├─ door_receiver.py    Door MCU 1/0 수신 (TCP 8103)
│     └─ tracking.py         CCTV MCU 시리얼 전송, PC2 추적 TCP 수신 (8102)
│
├─ sql/                      서버가 쓰는 DB 추가분
│  ├─ 02_server_upgrade.sql  환자 번호표, 혈액형 길이, 문 상태 이력 테이블 (서버가 켜질 때 자동 적용)
│  └─ 03_seed.sql            기본 카메라(CAM01, FACE01)와 문(door_01)
│
├─ db/                       팀 DB 패키지 원본 (수정하지 않음)
│  ├─ schema.sql             11개 테이블 정의 ← DB 구조의 기준
│  ├─ HCC_ERD_v0.1.drawio    ERD (draw.io로 열기)
│  ├─ DECISIONS_REQUIRED.md  팀이 정해야 할 사항
│  ├─ hcc_db.py, db_cli.py   서버 없이 터미널에서 DB 다루는 도구
│  ├─ run_db_smoke.py        DB 단독 시험
│  ├─ compose.yaml           DB만 따로 띄우는 원본 설정 (루트 docker-compose.yml과 동시에 켜지 말 것)
│  └─ README.md 외           DB 패키지 설명
│
├─ tools/                    실물 없이 시험하는 가짜 장치
│  ├─ fake_pc2.py            가짜 PC2·낙상 모듈 (register / identify / fall / track)
│  └─ fake_door_mcu.py       가짜 Door MCU (1/0 전송)
│
├─ examples/                 각 팀원이 참고할 클라이언트 예제
│  ├─ gui_client_example.py  GUI: REST 호출 + WebSocket 수신
│  ├─ fall_sender_example.py  낙상감지팀: 낙상 알림 보내는 함수 (복사해서 사용)
│  └─ esp32_door_state/      Door MCU: ESP32 Arduino 코드
│
└─ tests/test_api.py         서버 전체 시험 (SR별 시나리오)
```

**새 기능을 추가할 때:** `schemas.py`에 입력 형식 → `routers/해당SR.py`에 함수 → `/docs`에서 확인 → `tests/test_api.py`에 시험 추가.
DB 컬럼을 추가할 때는 `db/schema.sql`이 아니라 `sql/02_server_upgrade.sql`에 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`로 씁니다. 그래야 이미 만든 DB에도 자동으로 반영됩니다.

---

## 6. 시험

```bash
docker compose up -d postgres
pytest -q
```
> 시험은 시작할 때 **환자·이벤트 데이터를 지웁니다.** 시연 데이터가 있는 DB에서는 돌리지 마세요.

손으로 확인하려면 `/docs`에서 각 API의 "Try it out"을 누르면 됩니다.

---

## 7. 설정값 (`.env`)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `HCC_DB_DSN` | `postgresql://hcc_app:change_this_dev_password@127.0.0.1:5432/hcc` | DB 주소. `python run.py --db ...`로도 지정 (Docker로 켜면 자동 설정) |
| `AUTH_ENABLED` | `false` | `true`면 토큰이 필요함 |
| `ADMIN_TOKEN`, `DEVICE_TOKEN` | 비어 있음 | GUI용 / 장치(PC2·낙상 모듈)용 토큰. 요청 헤더 `Authorization: Bearer <토큰>` |
| `FACE_THRESHOLD` | `0.5` | 얼굴 유사도 기준. 얼굴팀 실측 후 조정 |
| `FACE_AUTO_CREATE_PATIENT` | `true` | 얼굴 등록 때 처음 보는 환자 ID면 이름 없는 환자로 자동 생성. `false`면 404 |
| `DOOR_TCP_PORT` | `8103` | Door MCU 접속 포트 |
| `DOOR_ID` | `door_01` | MCU가 ID 없이 1/0만 보낼 때 쓰는 문 ID |
| `TRACK_SERIAL_PORT` | 비어 있음 | CCTV MCU 시리얼 포트 (`COM3`, `/dev/ttyUSB0`). 비우면 수신만 |
| `TRACK_SERIAL_BAUD` | `115200` | CCTV MCU 통신 속도 |
| `TRACK_CAMERA_ID` | `CAM01` | 추적 카메라 ID |
| `PC2_TCP_ENABLED`, `PC2_TCP_PORT` | `true`, `8102` | PC2 추적 좌표를 TCP로 받을지 |

---

## 8. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| `/health`에 `"db": "error..."` 또는 503 응답 | DB가 켜져 있는지 확인: `docker compose ps`. 방법 B면 `--db` 주소·비밀번호, 방법 C면 `docker compose up -d postgres` |
| 다른 PC에서 접속이 안 됨 | 서버를 `run.py` 또는 Docker로 켰는지(`0.0.0.0`), 방화벽, 같은 공유기인지 |
| `Address already in use` | 서버가 이미 켜져 있음. 이전 창을 끄거나 `python run.py --port 9000` |
| 422 응답 | 응답의 `detail`에 틀린 필드가 나옴. 시각에 `+09:00`이 빠지는 실수가 가장 흔함 |
| 얼굴 등록이 404 | `FACE_AUTO_CREATE_PATIENT=false`로 둔 경우. GUI에서 환자를 먼저 등록하거나 설정을 `true`로 |
| 추적 `received`가 안 늘어남 | 줄 끝 `\n` 누락(여러 줄이 한 줄로 붙어 버려짐), PC2의 `PC1_TRACKING_HOST` 확인 |
| 낙상이 409 | 카메라 ID가 등록되지 않음. `POST /api/v1/cameras`로 먼저 등록 |
| 문 상태가 안 바뀜 | MCU가 8103에 접속했는지 `/health`의 `doors_online`으로 확인. 줄 끝에 `\n`이 있는지 |
| Docker에서 시리얼 포트 사용 | Windows·Mac Docker는 USB를 넘기기 어려움 → CCTV MCU를 쓰는 PC는 방법 B나 C로 실행 |

---

## 9. 이전 버전(v0.4.0)에서 바뀐 점

| 항목 | 이전 | 지금 |
|---|---|---|
| 구조 | `main.py` 650줄에 전부 | SR별 파일로 분리 |
| 실행 | 16자 토큰 2개가 없으면 서버가 안 켜짐 | `python run.py` 또는 `docker compose up` 한 줄. 인증은 필요할 때 켬 |
| DB 준비 | 마이그레이션을 직접 적용 | 서버가 켜질 때 자동 적용 (`sql/02`) |
| 문 (SR-04) | GUI 명령을 ERROR로만 기록 | Door MCU가 판단하고 1/0을 보고 → 서버가 기록·표시 |
| 얼굴 등록 | 10분짜리 등록 요청 ID 발급 후 수신 | 활성 환자 코드로 바로 저장 |
| 환자 ID | `patient_` + 무작위 20자 | `P0001` 순번 |
| GUI 실시간 알림 | 없음 | WebSocket `/ws` |
| 낙상 이벤트 | PC2가 보냄 (`/falls/events`) | PC1 낙상 모듈이 보냄 (`/falls`) |

**이번 범위에서 뺀 것** (필요해지면 Jira 이슈로): RFID 카드 실물 읽기·쓰기와 63바이트 동기화, 얼굴 등록 요청 세션, MCU `mcu_v02` 프로토콜.

**팀이 정해야 할 것**은 `db/DECISIONS_REQUIRED.md`에 있습니다. 특히 다음 세 가지입니다.
- 얼굴팀 기존 데이터의 `patient_001`과 서버가 발급하는 `P0001`을 어떻게 연결할지
- 얼굴 유사도 기준값
- 출입 허용(`can_open`)을 누가 설정할지
