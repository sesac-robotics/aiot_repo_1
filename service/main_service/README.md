# T-SAFE PC1 중앙 서버 (v2)

관리자 GUI · PC2(얼굴 인식·추적) · PC3(낙상 감지) · PC4(Door kit)가 모두 연결되는 **중앙 서버**입니다.
각자 **자기 PC에서 서버를 켜서 개발·시험**할 수 있고, 시연 때는 PC1 한 대에서 켜고 모두 거기에 접속합니다.
v2는 팀이 만든 **시퀀스 다이어그램과 통신 명세표**(1. 환자 관리, 2. 문 출입, 3. 모니터링·낙상)를 그대로 구현했습니다.

```
                                ┌──────────────── PC1 ────────────────┐
 PC2 얼굴 벡터 ── HTTP 8101 ──► │                                      │
 PC2 추적 좌표 ── TCP 8102 ───► │        중앙 서버 :8101  ───► DB      │ ── TCP 8765 ──► PC4 Bridge ── USB ──► Arduino
 PC3 영상+분석 ── HTTP 8101 ──► │  (판정·기록·중계)        PostgreSQL  │ ◄── ACK/EVENT ─┘   (서보 추적, 문 모터)
                                │        │ HTTP 8765 (이벤트)          │
                                │        ▼                             │
                                │   관리자 GUI (ui/gui/Current)        │
                                └──────────────────────────────────────┘
```

---

## 1. 실행하기

> **먼저 알아둘 것**
> - **`.env` 파일은 없어도 됩니다.** 모든 설정에 기본값이 있고, 바꿀 것만 명령행 옵션(`--db`, `--port`)이나 환경변수로 줍니다.
> - **가상환경은 서버 전용으로 따로** 만드세요(`.venv`). 낙상·얼굴 AI가 쓰는 가상환경(torch, YOLO 등)에 서버 패키지를 섞지 않습니다. Python 3.10 이상.
> - **DB를 서버보다 늦게 켜도 됩니다.** 서버가 5초마다 다시 시도해서, DB가 켜지는 순간 테이블을 준비합니다.

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
```bash
docker compose up -d postgres                 # DB만
# 가상환경은 방법 B의 2)와 같음
python run.py --reload                        # --db 없이 실행하면 이 Docker DB에 붙음
```

### 시연(PC1)에서 켤 때
PC4 Bridge 주소만 알려주면 됩니다. 관리자 GUI는 같은 PC1에서 켜면 토큰을 자동으로 찾습니다.
```bash
PC4_HOST=<PC4 IP> python run.py                # Windows PowerShell: $env:PC4_HOST="<PC4 IP>"; python run.py
```

### 켜졌는지 확인
`http://localhost:8101/health`

| 항목 | 정상 |
|---|---|
| `db` | `"ok"` |
| `pc4.connected` | `true` (PC4 Bridge에 연결됨) |
| `gui_events.token` | `true` (GUI 토큰을 찾음), `sent`가 늘어남 |
| `tracking_mcu` | `"pc4_tcp"` |

---

## 2. 포트와 다른 PC에서 접속하기

| 포트 | 어디서 → 어디로 | 내용 | 명세 |
|---|---|---|---|
| **8101** | PC2·PC3·GUI → PC1 | HTTP API, 문서 `/docs`, WebSocket `/ws` | 1, 2-④, 3-①② |
| **8102** | PC2 → PC1 | 추적 좌표 TCP | 2-② |
| **8765** | PC1 → PC4 | 서버가 PC4 Bridge에 **접속**해서 TRACK·DOOR 전송, ACK/EVENT 수신 | ③, ⑥ |
| 8765 | 서버 → GUI (PC1 안) | 관리자 GUI 이벤트 수신기 (`ui/gui/Current/event_bridge.py`) | 2-⑥⑧, 3-② |
| 8103 | Door MCU → PC1 | (예전 방식) 문 상태 `1`/`0`. 지금은 PC4 Bridge 경유를 씀 | – |
| 5432 | 서버만 | DB. PC1 안에서만 | 3-③ |

1. 서버 PC의 IP 확인: `python run.py`로 켜면 화면에 표시. 예 `192.168.0.10`
2. 방화벽 허용: **Linux** `sudo ufw allow 8101 && sudo ufw allow 8102` / **Windows** 처음 실행 때 "허용"
3. 다른 PC에서 `http://192.168.0.10:8101/health`로 확인
4. 모든 PC가 **같은 공유기(Wi-Fi)**에 연결돼 있어야 합니다.

---

## 3. 시퀀스 다이어그램별 동작

### 1. 환자 정보 관리 (등록 · 수정 · 삭제)
관리자 GUI의 `pc1_db_adapter.py`가 서버 API를 부릅니다. **환자뿐 아니라 직원·관리자도 같은 화면에서 등록**합니다.

| 역할 (`role`) | ID (서버 발급) | 출입 권한 (`can_open`) |
|---|---|---|
| `Patient` | `P0001`… | 없음 → 얼굴 인식돼도 문 안 열림 |
| `Staff` | `S0001`… | 있음 → 얼굴 인식되면 문 열림 |
| `Admin` | `A0001`… | 있음 |

- **등록:** `POST /api/v1/patients` (또는 `/api/v1/persons`) → 201 + 서버가 발급한 `person_id`
- **수정:** `PATCH /api/v1/patients/{person_id}` (보낸 필드만). `role`을 바꾸면 출입 권한도 역할에 맞게 바뀜
- **삭제:** `DELETE /api/v1/patients/{person_id}` → 비활성화 + 얼굴 특징벡터 삭제 + 카드 해제 (출입 불가). 과거 기록은 남음
- **목록:** `GET /api/v1/patients?role=Staff` (role 생략 시 전체)

### 2. 문 출입제어 — 얼굴 인식
```
PC2 ──② TRACK 좌표 (8102)──► 서버 ──③ TRACK (8765)──► PC4 → Arduino (서보가 얼굴 따라감)
PC2 ──④ 얼굴 벡터 (HTTP)───► 서버 ──⑤ DB 검색 → 번호·역할 확인
   case 1 환자/모르는 얼굴: 서버 ──⑥ 출입 거부──► GUI (recognition, allowed:false)
   case 2 직원·관리자    : 서버 ──⑥ DOOR,OPEN,<번호>──► PC4 ──⑦ ACK / EVENT OPEN_COMPLETED──► 서버
                           서버 ──⑧ 문 OPEN + 직원 이름──► GUI (recognition allowed:true, door OPEN)
```
- 판정은 `hcc.access_events`(ALLOW/DENY와 이유), 문 명령은 `hcc.door_commands`(PENDING → ACCEPTED/ERROR/TIMEOUT)에 남습니다.
- **5초** 안에 PC4 응답이 없으면 TIMEOUT. 재전송은 하지 않습니다(늦게 도착한 OPEN이 엉뚱한 때 문을 여는 것 방지).
- 같은 사람 얼굴이 연달아 와도 **10초에 한 번만** 엽니다(`DOOR_OPEN_COOLDOWN_SEC`).
- 문 닫기는 Arduino가 스스로 하고(`EVENT CLOSE_COMPLETED`), 서버는 CLOSED로 기록합니다.
- 관리자가 GUI에서 직접 열어야 하면 `POST /api/v1/doors/door_01/open` (응답 202, 결과는 이벤트로 옴).

### 3. 모니터링 및 낙상 감지
```
PC3 ──① JPEG + 분석 JSON (POST /api/v1/live/frame, 초당 최대 5장)──► 서버 (카메라별 최신 1장만 메모리에)
GUI ──② GET /api/v1/live/frame/{camera_id}/image, /{camera_id}──► 서버 (최신 영상·관절·bbox)
       ③ event.new_events에 fall_confirmed가 있을 때만 → hcc.fall_events + hcc.alerts 저장 → GUI fall:true
알람 해제: GUI ──① POST /api/v1/alerts/resolve_active──► 서버 ──② RESOLVED 저장 ──③ GUI fall:false (NORMAL)
```
- 수신 코드는 낙상 담당 브랜치(`feature/fall_detection`)의 `frame_analysis` 그대로이고, 처음 보는 카메라(`camera_1`)는 자동 등록합니다.
- 같은 낙상(카메라·track·확정 프레임이 같음)을 다시 보내도 알람은 하나입니다.

---

## 4. 역할별 사용법

### 관리자 GUI 담당 (`ui/gui/Current`)
- **환자 DB:** `GUI_DB_MODE=external`로 GUI를 켜고 `pc1_db_adapter.py`를 실행합니다. `TSAFE_PC1_URL=http://<PC1>:8101`, `TSAFE_ADMIN_TOKEN=아무값`(서버 인증이 꺼져 있을 때).
- **실시간 이벤트:** GUI를 켜면 서버가 GUI의 이벤트 수신기(`http://127.0.0.1:8765/events`)로 아래를 보냅니다. GUI가 같은 PC1에 있으면 토큰(`data/gui_event.token`)을 서버가 자동으로 읽습니다.

  | 이벤트 | 언제 |
  |---|---|
  | `{"type":"recognition","person_id","name","role","allowed"}` | 문 앞 얼굴 인식 (모르는 얼굴은 `Unknown`) |
  | `{"type":"door","state":"OPEN"\|"CLOSED"\|"UNKNOWN"}` | 문 열림·닫힘 (UNKNOWN은 연결 끊김) |
  | `{"type":"fall","detected":true\|false}` | 낙상 알람 발생 / 해제 |
- **영상:** `GET /api/v1/live/frame/camera_1/image` (JPEG), `GET /api/v1/live/frame/camera_1` (관절·bbox JSON)
- **⚠ Emergency release(Confirm) 버튼**은 `POST /api/v1/alerts/resolve_active`를 불러야 서버 알람이 해제됩니다.

### PC2 얼굴 인식 담당 (`service/ai_service/face_recongnition`)
- **식별(2-④):** PC2 `.env`에 `PC1_EMBEDDING_URL=http://<PC1>:8101/api/v1/face/identify`, `PC1_EMBEDDING_TOKEN=dev`. 응답에 `allowed`, `role`, `door_command`가 옵니다.
- **등록:** `face_db_export.py`가 만든 JSON을 `python tools/send_face_json.py <db_embeddings 폴더> --server <PC1>:8101`. **폴더 이름(=patient_id)을 서버의 `person_id`(P0001, S0001…)와 같게** 해야 출입 판정이 맞습니다.
- **추적(2-②):** `webcam/detect_yolov3.py`의 `PC1_TRACKING_HOST`를 PC1 IP로. 받는 형식:
  - `TRACK,body,face,x1,y1,x2,y2,width,height` (지금 PC2 코드)
  - `T,T,fx1,fy1,fx2,fy2,bx1,by1,bx2,by2` (명세표 10칸, 미검출 -1. 얼굴이 있으면 얼굴 박스, 없으면 몸 박스를 따라감)
  - 구형 `T,T,x,y,dx,dy` / `N,N`

### PC3 낙상 감지 담당 (`service/ai_service/fall_detection`)
- `config.py`의 `FRAME_API_URL = "http://<PC1>:8101/api/v1/live/frame"`. 그 외 할 일 없음.
- 응답 201 `{"accepted":true, "new_events":[{"event_id","duplicate"}]}`. 422면 metadata 형식, 415/422면 JPEG 문제.
- 예전 단건 방식 `POST /api/v1/falls`도 계속 됩니다(`examples/fall_sender_example.py`).

### PC4 Door kit 담당 (`device/cctv_tracking_servo`)
- PC4에서 `python pc4_bridge.py --serial-port <Arduino 포트> --tcp-port 8765`. 서버가 PC4에 **접속**합니다(Bridge는 접속을 1개만 받으므로 다른 프로그램이 먼저 붙어 있으면 안 됨).
- 서버가 보내는 줄: `TRACK,...` (최대 10Hz, 1.5초 입력 없으면 `TRACK,0,0,-1,-1,-1,-1,w,h`), `DOOR,OPEN,<번호>`
- 서버가 받는 줄: Bridge가 바꿔 주는 `{"type":"ACK"|"EVENT"|"ERROR","request_id","result"}`
- **⚠ 문 상태 `DOOR,1` / `DOOR,0`:** 지금 Bridge는 이 줄을 서버로 넘기지 않습니다. 넘겨 주면(텍스트 그대로 또는 `{"type":"DOOR","state":1}`) 서버는 바로 받습니다.

---

## 5. API 한눈에 보기

자세한 입력·출력 형식은 `http://<서버>:8101/docs`에 다 나옵니다.

| 명세 | 부르는 쪽 | 메서드 · 주소 | 하는 일 |
|---|---|---|---|
| 공통 | 누구나 | `GET /health` | 서버·DB·PC4·GUI 연결 상태 |
| 1 | GUI | `GET / POST /api/v1/patients` (= `/api/v1/persons`) | 목록(`?role=`) / 등록 |
| 1 | GUI | `GET / PATCH / DELETE /api/v1/patients/{id}` | 조회 / 수정 / 삭제(비활성화) |
| 1 | GUI | `PUT /api/v1/patients/{id}/card` | 카드 UID 연결 |
| 2-④ | PC2 | `POST /api/v1/face/templates` | 얼굴 특징벡터 등록 |
| 2-④~⑧ | PC2 | `POST /api/v1/face/identify` | 얼굴 식별 → 출입 판정 → 문 열기 |
| 2-② | PC2 | TCP 8102 (또는 `POST /api/v1/tracking/observations`) | 추적 좌표 → PC4 |
| 2 case 3 | GUI | `POST /api/v1/doors/{id}/open` | 관리자 원격 문 열기 (202) |
| 2 | GUI | `GET /api/v1/doors`, `/{id}/history`, `/{id}/commands`, `/{id}/access` | 문 상태 / 열림·닫힘 이력 / 명령 기록 / 출입 판정 기록 |
| 3-① | PC3 | `POST /api/v1/live/frame` (multipart: `metadata`, `image`) | 실시간 영상+분석, 낙상 확정 시 알람 |
| 3-② | GUI | `GET /api/v1/live/frame/{camera_id}/image`, `/{camera_id}` | 최신 JPEG / 최신 분석 JSON (없으면 404) |
| 3 | PC3 | `POST /api/v1/falls` | (예전) 낙상 1건 |
| 3 | GUI | `GET /api/v1/alerts?status=ACTIVE` | 알람 목록 |
| 3 해제 | GUI | `POST /api/v1/alerts/resolve_active` | Emergency release: 미해제 알람 모두 해제 |
| 3 | GUI | `POST /api/v1/alerts/{id}/acknowledge`, `/resolve` | 알람 하나 확인 / 해제 |
| 기타 | GUI | `GET/POST /api/v1/cameras`, `POST /api/v1/doors` | 카메라·문 등록 |

**WebSocket `/ws`** (웹 화면 등 다른 GUI용): `{"type": "...", "ts": "...", "data": {...}}`
`door_state`, `door_command`, `face_result`, `face_registered`, `fall_alert`, `alert_update`, `frame_analysis`, `patient_changed`

**공통 규칙**
- **시각:** 시간대 포함 ISO 8601 (`2026-10-21T15:00:00+09:00`, `+0900`도 됨). 시간대가 없으면 422
- **ID:** 사람 `P0001`/`S0001`/`A0001`(서버 발급), 문 `door_01`, 카메라 `CAM01`(추적) · `FACE01`(문 앞) · `camera_1`(낙상)
- **에러:** `{"detail": "..."}` — 404 없음, 409 상태 충돌, 422 형식 오류, 503 DB 꺼짐
- **DB는 서버만** 접근합니다.

---

## 6. 파일 설명

```
service/main_service/
├─ main_service.py / run.py  서버 실행 (python run.py). 접속 주소를 화면에 표시
├─ docker-compose.yml        DB + 서버를 한 번에 실행
├─ requirements.txt          Python 패키지 (버전은 범위로만)
├─ .env.example              설정 예시 (없어도 됨)
│
├─ app/
│  ├─ main.py                앱 생성 · 라우터 연결 · /health · /ws · DB 준비(실패 시 5초마다 재시도)
│  ├─ config.py              모든 설정값
│  ├─ schemas.py             API 입력 형식과 검사 규칙
│  ├─ events.py              실시간 이벤트 허브 (WebSocket + GUI 등 다른 통로)
│  ├─ routers/
│  │  ├─ patients.py         1  환자·직원·관리자 등록·조회·수정·삭제
│  │  ├─ face.py             2  얼굴 등록, 식별 → 출입 판정 → 문 열기
│  │  ├─ doors.py            2  문 상태·이력·명령·출입 기록, 원격 열기
│  │  ├─ tracking.py         2  추적 좌표 HTTP 수신
│  │  ├─ frame_analysis.py   3  실시간 영상·분석 수신, 최신 프레임 조회 (낙상 담당 코드)
│  │  └─ falls.py            3  낙상 기록, 알람 확인·해제
│  └─ services/
│     ├─ pc4_link.py         PC4 Bridge와의 TCP 연결 하나 (TRACK·DOOR 공용, ACK/EVENT 수신)
│     ├─ door_control.py     문 열기 명령 (요청 번호, 응답 처리, 5초 시간 초과)
│     ├─ tracking.py         PC2 좌표 수신(8102) → PC4 전달, 10Hz 제한, 1.5초 정지
│     ├─ gui_notify.py       관리자 GUI 이벤트 수신기로 recognition/door/fall 전송
│     ├─ frame_analysis.py   카메라별 최신 프레임 보관 (낙상 담당 코드)
│     └─ door_receiver.py    문 상태 기록 (+ 예전 TCP 8103 수신)
│
├─ sql/02_server_upgrade.sql 서버가 쓰는 DB 추가분 (켜질 때 자동 적용): 역할, 문 명령 번호 등
├─ sql/03_seed.sql           기본 카메라·문
├─ db/                       팀 DB 패키지 원본 (schema.sql = DB 구조 기준, ERD)
├─ tools/                    가짜 장치 (fake_pc2.py, fake_door_mcu.py, send_face_json.py)
├─ examples/                 클라이언트 예제
└─ tests/
   ├─ test_api.py            기본 기능 시험
   └─ test_v2.py             시퀀스 다이어그램 흐름 시험 (가짜 PC4 Bridge·가짜 GUI 수신기 사용)
```

**DB 컬럼을 추가할 때**는 `db/schema.sql`이 아니라 `sql/02_server_upgrade.sql`에 `ADD COLUMN IF NOT EXISTS`로 씁니다. 이미 만든 DB에도 자동으로 반영됩니다.

---

## 7. 시험

```bash
docker compose up -d postgres
pytest -q                    # 17개
```
> 시험은 시작할 때 **사람·이벤트 데이터를 지웁니다.** 시연 데이터가 있는 DB에서는 돌리지 마세요.

v2는 팀원 실제 코드로도 확인했습니다: `pc4_bridge.py`(가상 시리얼의 가짜 Arduino), `coordinate_sender.py`, 낙상 담당의 `HttpFrameAnalysisPublisher`, GUI의 `pc1_db_adapter.py`와 `event_bridge.py`.

---

## 8. 설정값 (환경변수 또는 `.env`)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `HCC_DB_DSN` | `postgresql://hcc_app:change_this_dev_password@127.0.0.1:5432/hcc` | DB 주소. `python run.py --db ...`로도 지정 |
| `PC4_HOST`, `PC4_PORT` | 비어 있음, `8765` | PC4 Bridge 주소. 비우면 추적·문 명령을 보내지 않음 (`TRACK_PC4_HOST`도 같음) |
| `DOOR_ID` | `door_01` | 문 ID |
| `DOOR_CMD_TIMEOUT_SEC` | `5` | 문 열기 응답 기다리는 시간 |
| `DOOR_OPEN_COOLDOWN_SEC` | `10` | 같은 사람 얼굴로 다시 열기까지 간격 |
| `GUI_EVENT_URL` | `http://127.0.0.1:8765` | 관리자 GUI 이벤트 수신기 |
| `GUI_EVENT_TOKEN` | 비어 있음 | 비우면 `ui/gui/Current/data/gui_event.token`을 읽음 (`GUI_EVENT_TOKEN_FILE`로 경로 변경) |
| `GUI_EVENT_ENABLED` | `true` | GUI 이벤트 전송 여부 |
| `FACE_THRESHOLD` | `0.5` | 얼굴 유사도 기준 |
| `FACE_AUTO_CREATE_PATIENT` | `true` | 처음 보는 얼굴 등록 ID면 이름 없는 환자로 자동 생성 |
| `PC2_TCP_ENABLED`, `PC2_TCP_PORT` | `true`, `8102` | PC2 추적 좌표 TCP |
| `TRACK_FRAME_WIDTH/HEIGHT` | `640`, `480` | PC2가 10칸 형식으로 보낼 때의 화면 크기 |
| `TRACK_SERIAL_PORT` | 비어 있음 | (PC4 없이) 서버에 Arduino를 직접 꽂을 때만 |
| `AUTH_ENABLED`, `ADMIN_TOKEN`, `DEVICE_TOKEN` | `false` | 시연·배포 때 토큰 인증 |

---

## 9. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| `/health`의 `db`가 error, 503 응답 | DB가 켜져 있는지. 서버는 5초마다 다시 붙으니 DB만 켜면 됨 |
| `pc4.connected: false` | PC4에서 `pc4_bridge.py`가 켜져 있는지, `PC4_HOST`, 8765 방화벽, 다른 프로그램이 Bridge에 먼저 붙어 있지 않은지 |
| 직원 얼굴인데 문이 안 열림 | 식별 응답의 `role`·`allowed`·`door_command.status` 확인. `ERROR PC4_UNAVAILABLE`이면 위 항목, `TIMEOUT`이면 Bridge↔Arduino 시리얼 |
| 문 명령이 `SKIPPED` | 같은 사람이 10초 안에 다시 인식됨 (정상) |
| GUI에 이벤트가 안 옴 | `/health`의 `gui_events`: `token:false`면 GUI를 먼저 켜거나 `GUI_EVENT_TOKEN` 설정, `last_error`에 401이면 토큰 불일치 |
| Docker로 켰는데 GUI 이벤트가 안 옴 | 컨테이너 안의 127.0.0.1은 PC1이 아님 → `.env`에 `GUI_EVENT_URL=http://host.docker.internal:8765`, `GUI_EVENT_TOKEN=<GUI의 data/gui_event.token 내용>`. GUI의 `GUI_EVENT_HOST=0.0.0.0`. 시연은 방법 B·C 권장 |
| 낙상 프레임이 422 | metadata가 `frame_analysis` 형식인지, `source_size`와 `image.source_size`가 같은지 |
| 추적 `received`가 안 늘어남 | 줄 끝 `\n`, PC2의 `PC1_TRACKING_HOST` |
| `Address already in use` | 서버가 이미 켜져 있음. 이전 창을 끄거나 `python run.py --port 9000` |

---

## 10. v1에서 바뀐 점 (v2)

| 항목 | v1 | v2 |
|---|---|---|
| 등록 대상 | 환자만 | 환자·직원·관리자 (역할별 출입 권한) |
| 문 | MCU가 판단, 서버는 상태만 받음 | **서버가 판정 → PC4로 `DOOR,OPEN` → ACK/EVENT 확인** (명세 ⑥) |
| 추적 | PC1에 직접 꽂은 시리얼 | PC4 Bridge TCP (문 명령과 같은 연결), 명세표 10칸 형식 지원 |
| 낙상 | 단건 이벤트 `/falls` | 실시간 영상+분석 `/live/frame` (낙상 담당 코드), GUI 영상 조회 |
| 알람 해제 | 알람 ID 필요 | `resolve_active` (Emergency release) |
| GUI 알림 | WebSocket만 | 관리자 GUI 이벤트 수신기로 recognition/door/fall 전송 |
| 서버 시작 | DB가 먼저 켜져 있어야 함 | DB가 늦게 켜져도 자동 준비 |
