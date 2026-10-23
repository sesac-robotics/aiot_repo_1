# 실제 이벤트 연결 규격

Current는 장치 이벤트를 받아 화면/Logs/SQLite에 반영합니다.
실제 장치 드라이버를 자동 구현하거나 성공 결과를 임의 생성하지 않습니다.
아래 규격에 기존 코드의 실제 수신값/저장 결과를 매핑하십시오.

## 설정

- GUI_EVENT_PORT: 기본 8765. 0이면 HTTP 수신 비활성.
- GUI_EVENT_HOST: 기본 127.0.0.1. 다른 PC에서 직접 보내려면 0.0.0.0 등 수신 주소 지정.
- GUI_EVENT_TOKEN: 지정하지 않으면 data/gui_event.token에서 읽거나 최초 생성.
- GUI_DB_MODE: local(기본), external(PC1 어댑터/외부 DB 응답 대기).
- 클라이언트 GUI_EVENT_URL: 기본 http://127.0.0.1:8765.

같은 PC에서 device_client.GUIClient()는 토큰 파일을 자동 사용합니다.
다른 PC에서는 GUI_EVENT_URL과 동일한 GUI_EVENT_TOKEN을 설정하십시오.
Qt의 App.events.incoming.emit(dict)도 사용할 수 있습니다. 워커에서 Signal로 전달하십시오.
문 열기 명령은 제공하지 않습니다.

## API

모든 요청은 `Authorization: Bearer <토큰>` 헤더를 사용합니다.

| 요청 | 기능 |
|---|---|
| GET /health | GUI 이벤트 수신기 상태 |
| GET /requests | 처리 대기 중인 DB/카드 요청 목록 |
| POST /events | 장치 결과/센서 이벤트 전달 |
| GET /events | 저장된 최근 이벤트 최대 2,000개 조회 |

POST의 202는 수신/GUI 큐 전달을 뜻하며 저장 완료 응답이 아닙니다.
쓰기 결과 적용 후 GET /requests에서 해당 request_id가 없어지고 Logs에 결과가 저장됩니다.
요청 큐는 메모리에 있으므로 GUI를 다시 시작하면 대기 요청은 초기화됩니다.
이벤트 저장은 SQLite이므로 GUI 재실행 후에도 남습니다.
최대 요청 본문은 65,536 bytes입니다. 큰 환자 목록 동기화는 이 제한 내에서 사용하십시오.

## 카드 감지

```json
{"type":"card","present":true,"uid":"실제_UID"}
```

카드가 제거되면:

```json
{"type":"card","present":false}
```

카드 감지 시 Card 상태를 바꾸고, DB 저장을 마친 활성 등록이 카드 대기 중이면 쓰기 요청을 생성합니다.

## 카드 쓰기

Save + DB 성공 + 카드 UID 확인 후 GET /requests에 다음과 같은 요청이 생깁니다.

```json
{"type":"card_write","request_id":"GUI가_발급한_값","operation":"등록작업ID","uid":"실제_UID","person":{"id":"환자ID","name":"환자명","role":"Patient","sex":"Female","birth":"1960-01-01","blood":"A","rh":"+","height":"162","weight":"58","disease":"...","medicine":"...","added":"2026-10-22"}}
```

기존 카드 쓰기 코드가 실제 카드를 확인하고 쓰기를 수행한 후에만 다음 결과를 보냅니다.

```json
{"type":"card_write_result","request_id":"위_request_id","uid":"동일한_UID","success":true,"event_id":"장치가_정한_고유결과ID"}
```

실패 시 success:false와 message를 전달합니다. UID가 다르면 결과를 거부합니다.
카드 바이트 형식·저장 용량·읽기/쓰기 함수는 팀의 기존 카드 규격을 사용해야 합니다.
같은 쓰기 요청을 중복 실행하지 않도록 장치 쪽에서도 request_id를 기준으로 처리하십시오.

기존 장치 프로그램에서 사용할 연결 구조:

```python
from device_client import GUIClient
client = GUIClient()
# 실제 카드 읽기 이벤트가 발생한 시점:
client.send({"type": "card", "present": True, "uid": uid_from_reader})
# 기존 카드 프로그램의 루프에서:
for request in client.requests():
    if request["type"] != "card_write":
        continue
    # 아래는 팀의 실제 카드 쓰기 함수로 연결하는 지점입니다.
    # ok = existing_card_writer(request["uid"], request["person"])
    # client.send({"type":"card_write_result", "request_id":request["request_id"],
    #              "uid":request["uid"], "success":ok})
```

## DB 결과 (external 모드)

GUI는 type:db_save, request_id, operation, person, old_id를 요청 큐에 넣습니다.
포함된 pc1_db_adapter.py가 기존 PC1 API를 호출한 뒤 다음 결과를 보냅니다.

```json
{"type":"db_result","request_id":"DB요청ID","success":true,"person_id":"서버가_발급한_ID"}
```

success:false면 카드 쓰기를 시작하지 않습니다. 성공이면 DB 상태를 갱신하고 카드 쓰기 요청을 생성합니다.
서버 저장 성공 후 GUI 저장 캐시에 반영하며 상태에 (server)를 표시합니다.
PC1 DB/인증 토큰/혈액형 스키마를 올바르게 설정해야 합니다.
외부 DB 삭제 요청은 db_delete, 결과는 db_delete_result입니다. PC1 어댑터가 처리합니다.
서버 목록 동기화 patients_sync는 포함된 어댑터가 담당하므로 별도 구현이 필요하지 않습니다.

## 출입 판정

환자 출입 거부 예시:

```json
{"type":"recognition","person_id":"P-002","name":"환자명","role":"Patient","allowed":false}
```

직원 출입 허용 예시:

```json
{"type":"recognition","person_id":"S-001","name":"직원명","role":"Staff","allowed":true}
```

role은 Patient/Staff/Admin/Unknown 중 하나입니다.
allowed는 서버/출입 담당 코드가 조회한 실제 판정값을 전달해야 합니다.
GUI는 역할만 보고 임의로 허용하지 않습니다. 식별되지 않으면 name:Unknown, role:Unknown, allowed:false를 보냅니다.
환자 DB에 동일한 person_id가 있으면 Personal/Health 정보를 함께 표시합니다.
이 이벤트는 문 상태를 바꾸지 않습니다.

## 문 센서

```json
{"type":"door","state":"OPEN"}
```

state는 OPEN/CLOSED/UNKNOWN입니다. OPEN은 초록색, CLOSED는 빨간색입니다.
문 센서 이벤트에는 사람 식별을 추정해 붙이지 않습니다.

## 낙상

```json
{"type":"fall","detected":true}
```

해제는 detected:false입니다. CCTV에는 FALL만 표시합니다. 로그의 낙상 이름도 Unknown입니다.
실제 발생/수신 기록의 시간은 로그에 저장하지만 CCTV 알림에는 표시하지 않습니다.
지도 좌표는 기존 샘플 범위이며 실제 좌표 변환은 별도로 연결해야 합니다.
Confirm은 알림 확인 이벤트를 기록하며 실제 낙상 센서 상태를 변경하지 않습니다.

## 이벤트 시간과 중복

선택적으로 event_id(이벤트별 고유 문자열), timestamp(타임존 포함 ISO 8601)를 전달할 수 있습니다.
timestamp가 없으면 GUI 수신/처리 시간을 기록합니다. UI에서는 한국 시간으로 표시합니다.
event_id가 있으면 같은 실행 중 중복 수신을 무시하고 DB 이벤트도 같은 ID로 중복 저장하지 않습니다.
한 요청의 서로 다른 이벤트에는 서로 다른 event_id를 사용하십시오.

## 영상

기존 영상 수신 코드에서 GUI 스레드에 전달해 다음을 호출합니다.

```python
app.set_cctv_frame(qimage_or_qpixmap)
```

영상 수신 프로토콜/카메라 URL 연결은 별도입니다. 새 모듈은 문 모터를 제어하지 않습니다.
