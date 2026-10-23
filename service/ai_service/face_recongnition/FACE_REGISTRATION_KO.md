# 서버 사람 ID에 맞춘 얼굴 등록

사람 정보와 얼굴 벡터는 같은 서버 `person_id`로 연결합니다. 얼굴 벡터 JSON의
필드명은 기존 API 호환을 위해 `patient_id`이지만, 환자뿐 아니라 직원·관리자의
`person_id`도 이 필드에 넣습니다. `face_db_export.py`는 사진 폴더 이름을 그대로
`patient_id`에 기록합니다.

## 현재 준비 순서: 사람 DB 먼저, 벡터 추출은 나중에

먼저 환자·직원 정보를 서버 API로 등록해 발급된 `P0001`, `S0001` 등의 ID를
보관합니다. 이 단계에는 얼굴 사진이나 벡터가 필요하지 않습니다. 환자와 직원은
별도 DB가 아니라 같은 사람 테이블에서 역할로 구분됩니다. 벡터 등록 전에는 얼굴
식별 결과가 `UNKNOWN`일 수 있습니다.

나중에 사진 폴더를 해당 서버 ID로 준비하고 아래 2~4단계를 진행하면 됩니다.

일반 출입 인식용 캡처는 `data/captures/face_날짜_시각.jpg`로 저장되며 사람 ID를
새로 발급하는 등록 기능이 아닙니다. `face_register.py`는 기존 로컬 비교용 등록 도구로,
`data/registered/<ID>/`의 폴더 이름을 그대로 로컬 레지스트리에 저장합니다.
서버 DB에 연결할 벡터 JSON은 `face_db_export.py`로 만듭니다. 두 도구 모두
`person1`을 자동으로 `S0001`로 변환하지 않으므로 사진 폴더에 실제 서버 ID를 사용합니다.

## 1. 사람을 먼저 등록

GUI는 `GUI_DB_MODE=external`로 실행하고 `pc1_db_adapter.py`도 실행해야 합니다.
GUI의 로컬 데모 ID나 저장 대기 중인 `pending_...` ID를 사용하지 마세요.
서버 저장이 끝난 뒤 등록 화면에 표시되는 ID를 사용합니다.

GUI 없이 서버 API로 등록할 수도 있습니다. 다음 예시는 인증이 꺼진 로컬 서버용입니다.

```bash
curl -sS -X POST http://127.0.0.1:8101/api/v1/patients \
  -H 'Content-Type: application/json' \
  -d '{"name":"등록할 직원 이름","role":"Staff"}'
```

응답의 `person_id`를 확인하세요. `S0001`은 예시이며 실제 응답값을 사용합니다.

| 역할 | 서버 ID 예시 | 기본 출입 권한 |
|---|---|---|
| Patient | P0001 | 거부 |
| Staff | S0001 | 허용 |
| Admin | A0001 | 허용 |

권한 판정은 ID 첫 글자를 추측해서 하지 않고 서버 DB의 실제 역할·`can_open`으로
결정합니다. 관리자가 `can_open`을 별도로 설정했다면 그 값이 적용됩니다.
사람/얼굴 등록은 실제 문 열림이나 Arduino 연결까지 보장하는 것은 아닙니다.

## 2. 같은 ID로 사진 폴더 생성

```text
service/ai_service/face_recongnition/data/db_registered/
└── S0001/                 ← 실제 서버 응답의 person_id
    ├── front.jpg
    └── side.jpg
```

각 사진에는 등록할 사람 한 명의 얼굴이 있어야 합니다. 폴더 이름은 이름이나
임의의 `patient_001` 대신 서버에서 확인한 ID를 사용하세요.

## 3. 얼굴 벡터 추출

현재 한 컴퓨터 시험에서 사용하는 InsightFace 컨테이너가 실행 중이면:

```bash
docker exec aiot-singlepc-insightface python3 face_db_export.py
```

Compose로 실행했다면 해당 얼굴 인식 폴더에서:

```bash
docker compose exec insightface python3 face_db_export.py
```

결과는 `data/db_embeddings/S0001.json`입니다. 사진을 교체한 뒤 다시 추출할 때는
`--overwrite`를 붙입니다. 기존 사진·모델·가상환경은 Git에 업로드하지 않습니다.

## 4. 서버로 전송

저장소 루트에서:

```bash
python3 service/main_service/tools/send_face_json.py \
  service/ai_service/face_recongnition/data/db_embeddings/ \
  --server 127.0.0.1:8101
```

도구는 모든 등록 JSON의 ID를 먼저 서버에서 확인합니다. 없는 ID, 비활성 ID,
조회 실패가 있으면 등록 전송을 시작하지 않고 실패 코드로 종료합니다.
따라서 이 도구는 서버의 기존 자동 환자 생성 기능에 의존하지 않습니다.

인증을 켠 서버에서는 `TSAFE_ADMIN_TOKEN`(사람 ID 조회)과 `DEVICE_TOKEN`(얼굴 등록)을
설정하거나 `--admin-token`, `--token` 옵션을 사용합니다. 실제 토큰은 커밋하지 않습니다.
이미 잘못된 ID로 등록한 데이터가 있다면 이 수정이 자동으로 사람을 병합하거나
역할을 바꾸지는 않습니다. 올바른 서버 ID로 사진 폴더를 준비하여 다시 등록하세요.
