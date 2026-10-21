# PC2 TCP 적용 방법

프로젝트 최상위에서 ZIP을 풀면 webcam/의 두 파일만 교체합니다.
기존 start.sh, Docker HTTP 서비스, .env는 유지합니다.

.env 끝에 아래 두 줄을 추가하세요. 같은 키가 이미 있으면 기존 값을 수정하세요.

```dotenv
PC1_TRACKING_HOST=192.168.15.130
PC1_TRACKING_PORT=8102
```

```bash
./start.sh --camera 2 --debug
```

기본 카메라가 0이면 --camera 0으로 변경합니다.
--debug는 전송 대상으로 만든 좌표를 출력합니다. 서버 수신 확인 로그는 아닙니다.
[TCP] Connected는 TCP 연결 성공이며, 실제 파싱 성공은 PC1 로그로 확인합니다.
[TCP] Connection failed는 서버 미실행 등 연결 실패입니다. 2초 간격 재시도합니다.

사람+얼굴: T,T,x,y,dx,dy\n
사람만: T,N,x,y,dx,dy\n
대상없음: N,N\n

좌표는 최대 10Hz 전송합니다. 얼굴 중심을 우선 사용하며 없으면 몸 중심입니다.
x,y는 영상 픽셀 좌표, dx=x-영상너비//2, dy=y-영상높이//2입니다.
별도 스레드에서 최신 메시지만 유지하고 0.5초보다 오래 대기한 메시지는 버립니다.
종료 시 N,N 전송을 시도합니다. PC1도 미수신 시 추적 정지 처리가 필요합니다.
PC2에는 Serial 연결이 필요 없습니다. TCP 별도 인증은 현재 없습니다.

설정 우선순위: 명령행 > 셸 환경변수 > 프로젝트 .env > 기본 IP/포트.
HTTP 디바이스 토큰은 TCP 연결에 사용하지 않습니다.
로컬 테스트: ./start.sh --camera 2 --no-tcp

검증: Python 문법 검사 및 로컬 TCP 소켓 송수신. 실제 PC1/웹캠 통합 검증은 별도입니다.
