# PC2 얼굴 임베딩 HTTP 연동 적용

기존 face_recognition 폴더에 다음 파일만 덮어씁니다.
- app/face_service.py
- docker-compose.yml
- start.sh
- .env.example (실제 .env는 덮어쓰지 않음)

webcam 코드, common.py, 모델 및 기존 data 파일은 유지합니다.

## 설정
프로젝트 폴더에서 기존 .env가 있으면 편집하고, 없으면 .env.example을 .env로 복사합니다.

PC1_EMBEDDING_URL=http://192.168.15.130:8101/api/v1/face/identify
PC1_EMBEDDING_TOKEN=개인적으로 받은 DEVICE 토큰
DEVICE_ID=PC2

.env는 docker-compose.yml과 같은 폴더에 둡니다. chmod 600 .env로 접근 권한을 제한합니다.
기존 셸의 같은 이름 환경변수는 .env보다 우선하므로, 이전 테스트 변수가 있다면 unset합니다.
실제 .env와 토큰은 공유하거나 git에 커밋하지 마세요.

## 실행
chmod +x start.sh
./start.sh --camera 2

start.sh가 Docker를 실행하고 웹캠 프로그램을 실행합니다. PC1 접속 실패로 실행을 막지 않습니다.
기존 Python 가상환경 경로와 모델/GPU/볼륨 설정은 유지했습니다.
PC1 TCP 옵션은 추가하지 않아도 됩니다. 기존 좌표 TCP 기능은 변경하지 않았습니다.

## 확인
docker compose logs -f --tail=50 insightface

정상: [EXTRACT] ... OK dim: 512 -> [PC1 SENT] ... result: MATCHED 또는 UNKNOWN
새 사진을 찍어서 확인합니다. 예전 output JSON은 자동으로 outbox에 추가하지 않습니다.

## 폴더
- data/output: 추출 결과 원본 JSON
- data/outbox: HTTP 전송 대기 JSON
- data/responses: 정상 HTTP 응답 JSON (event_id별 파일)
- data/rejected: 재시도하지 않는 4xx 요청과 오류 요약
- data/processed: 처리 완료 이미지

## 오류 정책
- URL 없음: 로컬 추출/저장만 수행, 새 outbox 생성 안 함
- URL 있음, 토큰 없음: outbox에 대기, 송신하지 않음
- HTTP 2xx + accepted=true + event_id 일치: 응답 저장 후 outbox 삭제
- 401/403: 프로세스 내 전송 일시중지; 토큰 수정 후 컨테이너 재생성
- 408/429 및 서버/네트워크 오류: outbox 유지, 약 5초 이상 간격 재시도
- 다른 4xx(400/404/422 등): rejected로 이동; 요청/경로 수정 후 수동 재대기 필요
- 비정상 응답 JSON: outbox 유지

컨테이너 재생성:
docker compose up -d --force-recreate insightface

정상 응답의 MATCHED/UNKNOWN은 서버 비교 결과이며 실제 문 동작을 의미하지 않습니다.
새로운 세션 자동 승인 정책이나 좌표 TCP 프로토콜은 수정하지 않았습니다.

## 검증
Python 문법 및 Bash 문법 통과.
로컬 HTTP 서버에서 Bearer 전달, 정상 응답 파일 저장/대기열 삭제,
422 격리, 500 대기열 유지, 401 전송중지, 토큰 미설정 대기, 재개를 검증했습니다.
실제 PC1/GPU/웹캠/Docker 실행은 사용자 장비에서 확인해야 합니다.
