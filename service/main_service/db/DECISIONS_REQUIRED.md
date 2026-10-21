# HCC DB v0.1 — 팀원 확인 및 설계 가정

## 설계에 사용한 확정 정보

- 팀 Confluence `System Requirements`의 SR-01~SR-06, 기존 T01~T07 SQLite PoC 구조를 기준으로 하였다.
- PC2 얼굴 인식팀의 실제 등록/캡처 JSON 필드: `patient_id`, `event_id`, `device_id`, `status`, `timestamp`, `model=buffalo_l`, `embedding_dim=512`, `embedding` 등.
- 현재 제공된 얼굴 등록 데이터 2건(`patient_001`, `patient_002`)에는 성명·병력이 없다. `persons.name`은 **임시 NULL**로 둔다. 가짜 이름/병력을 만들지 않는다.
- SR-07은 공식 Confluence 요구사항 문서에서 확인되지 않아 설계 대상에서 제외했다.

## 반드시 합의할 P0 결정

1. **공통 ID**: 기존 PoC `P001`/`S001`과 실제 얼굴 JSON `patient_001`/`patient_002`의 매핑 여부. 같은 인물로 자동 취급하지 않음.
2. **얼굴 비교 책임**: PC2는 embedding만 보낼지, 자체 ID와 confidence도 보낼지. 본 v0.1의 기준은 **PC1에서 DB 전체 등록자와 비교**.
3. **얼굴 비교 모델·임계값**: `buffalo_l`/512차원 가정. `0.5`는 임시 값, 실제 환경 평가 후 변경.
4. **모듈별 입력·출력 형식**: 낙상 `event_type`, `camera_id`, `track_id`, `bbox_xyxy`의 실제 샘플 및 카메라 ID 표준.
5. **RFID 권한 시나리오**: 임시 방문객 카드의 유효기간, 발급·회수, 카드 주인 식별, 출입 정책 확정.
6. **환자 정보 CRUD의 소유권**: 환자 관리 GUI·서버 API·DB 쓰기 경계를 팀원들과 확정.
7. **문 상태 센싱**: ACK는 통신 수락이며 물리 문 열림 증거가 아님. 문 실제 센서의 사용 여부 결정.
8. **계정·권한**: 누가 관리자 로그인 인증을 제공하며, `handled_by` 직원 ID를 어떻게 전달할지 결정.

## v0.1의 임시 설계 가정 (변경 가능)

- `persons.person_type` = PATIENT / STAFF / VISITOR.
- 기본 `can_open=FALSE` (안전 우선). 직원 자동 허용 정책은 수동·명시적 설정 없이는 적용되지 않음.
- 환자 삭제는 `is_active=FALSE`로 비활성화하며 얼굴·카드도 비활성화. **실제 영구 삭제·보존 기간·권리행사 절차는 별도 정책 수립 필요**.
- 얼굴 등록자별/모델별 대표 임베딩은 하나(`UNIQUE(person_id,model)`), 재등록 시 덮어쓰기.
- `face_templates.embedding`은 수신 데이터를 Python 측에서 L2 정규화 후 `vector(512)`로 저장.
- 얼굴 **등록** 임베딩은 보관하지만, 얼굴 **식별 시도** 임베딩은 별도 영구 보관하지 않음. 사건에는 메타데이터·유사도만 저장.
- `cameras` 및 `doors`는 이벤트·명령 전에 등록해야 FK를 만족함.
- 낙상과 알람 관계는 1 : 0..1 (감지 이벤트당 알람 최대 하나).
- 시각은 timezone 포함 UTC 저장, GUI에서는 `Asia/Seoul`로 표시.
- `physical_verified=TRUE`는 진짜 센서/독립 검증이 있는 경우에만 가능. 기존 T03/T07 결과는 `SIMULATED`.
- HCC v0.1의 `schema.sql`은 신규 DB 초기화용이며 운영 데이터가 있는 DB의 자동 스키마 마이그레이션을 지원하지 않음.

## 개인정보·보안

- 의료·생체 식별 데이터는 민감함. 시연에는 **합성 또는 사용 동의를 받은 데이터**만 사용.
- DB는 PC1 로컬에만 바인딩하고, 앱 계정·방화벽·백업·접근 기록 정책을 별도로 마련.
- 개발용 compose 비밀번호는 실운영·팀 공용 시스템에서 반드시 교체하고 소스 관리에 커밋하지 않음.
- `run_db_smoke.py`는 DB 처리만 확인하며 영상·모터·실제 출입 인증 시험이 아님.
