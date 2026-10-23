# PC2 얼굴 임베딩·좌표 전송 서비스

웹캠에서 사람·얼굴을 추적해 PC1으로 TCP 좌표를 전송합니다. 품질 조건을
만족한 얼굴을 촬영하면 Docker의 InsightFace 서비스가 512차원 임베딩을
추출하고 PC1 HTTP API로 전달합니다.

## 구성

- `start.sh`: Docker 서비스와 웹캠 프로그램 실행
- `app/face_service.py`, `app/common.py`: 임베딩 추출·HTTP 전송
- `app/`의 나머지 스크립트: 로컬 등록·비교·DB 내보내기 도구
- `webcam/detect_yolov3.py`: YOLO11 기본 모델을 사용하는 촬영·추적 코드
- `webcam/coordinate_sender.py`: TCP 좌표 송신
- `Dockerfile`, `docker-compose.yml`: GPU 임베딩 서비스 환경

이 폴더를 작업 디렉터리로 사용합니다. Dockerfile을 포함하므로 형제
`insightface/` 폴더 없이 설치할 수 있습니다.

## 설치와 모델 준비

Ubuntu 계열 Linux, NVIDIA GPU/드라이버, Docker Compose 및 NVIDIA Container
Toolkit이 필요합니다. 웹캠 화면 표시가 가능한 데스크톱 세션에서 실행합니다.

```bash
python3 -m venv webcam/.venv
webcam/.venv/bin/python -m pip install --upgrade pip
```

대상 GPU에 맞는 PyTorch/torchvision을 [공식 설치 안내](https://pytorch.org/get-started/locally/)에
따라 이 가상환경에 설치한 다음 직접 의존성을 설치합니다.
`requirements.txt`는 기존 장비에서 확인한 직접 의존성 버전이며 완전한
환경 잠금 파일은 아닙니다. 새 설치에 대한 통합 검증은 별도로 필요합니다.

```bash
webcam/.venv/bin/python -m pip install -r webcam/requirements.txt
```

모델 파일은 Git에 포함하지 않습니다. 기존 장비의 파일을 복사하거나
[Ultralytics](https://github.com/ultralytics/assets/releases) 및
[InsightFace 모델 안내](https://github.com/deepinsight/insightface/tree/master/python-package#model-zoo)의
출처에서 준비해 아래에 배치합니다.

```text
webcam/yolo11n.pt
.models/models/buffalo_l/*.onnx
```

`common.py`는 CUDA 실행 제공자를 요구합니다. 모델 디렉터리는 읽기 전용으로
마운트하므로 실행 전에 buffalo_l 모델 파일을 준비해야 합니다.

## 환경 설정

```bash
cp .env.example .env
chmod 600 .env
```

기존 `.env`가 있다면 복사로 덮어쓰지 말고 필요한 항목만 편집합니다.
PC1 HTTP URL·디바이스 토큰·TCP 주소를 설정합니다. HTTP URL이 비어 있으면
로컬 추출만 수행합니다. TCP 전송은 별도이며 `--no-tcp`로 끕니다.
실제 `.env`, 얼굴 사진, 임베딩, 결과, 모델, 가상환경은 커밋하지 않습니다.

기존 장비의 `../insightface/.models`를 계속 사용하려면 기존 `.env`에 다음을
추가합니다. 토큰 등 기존 설정은 유지합니다.

```dotenv
INSIGHTFACE_MODEL_DIR=../insightface/.models
```

새 설치는 기본값 `./.models`를 사용합니다. 포함된 Dockerfile은
[InsightFace 0.7.3](https://pypi.org/project/insightface/0.7.3/)과
[ONNX Runtime GPU 1.20.2](https://pypi.org/project/onnxruntime-gpu/1.20.2/)를
설치합니다. 기존 외부 Dockerfile에 적힌 InsightFace 2.1을 그대로 사용하지 않습니다.

## 실행

```bash
chmod +x start.sh
./start.sh --camera 2
```

로컬 촬영 테스트는 `./start.sh --camera 2 --no-tcp`로 실행합니다. HTTP 전송도
끄려면 `.env`의 `PC1_EMBEDDING_URL`을 비웁니다. 실행 폴더는 자동 생성됩니다.
등록·비교 도구의 입력 사진·레지스트리는 장비에서 별도로 준비합니다.

```bash
docker compose logs -f --tail=50 insightface
docker compose down
```

설정 변경 후 `docker compose up -d --force-recreate insightface`로 적용합니다.
웹캠 프로그램은 실행 터미널에서 Ctrl+C로 종료합니다.

HTTP 오류 정책은 [README_HTTP.md](README_HTTP.md), 좌표 형식과 TCP 설정은
[README_TCP.md](README_TCP.md)를 참고하세요. 두 문서의 ZIP·덮어쓰기 안내는
과거 배포 방식입니다. 새 설치는 이 문서를 따릅니다.

## 검증 범위

업로드 준비 시 Python/Bash 문법, Compose 구성 및 업로드 파일 제외 규칙을
검사합니다. Docker 이미지 빌드, GPU 모델 실행, 실제 웹캠 및 PC1 HTTP/TCP
통합 동작은 이번 업로드 준비 과정에서 검증하지 않았습니다.
기존 HTTP/TCP 문서의 검증 이력은 이번 실행 검증 결과와 구분해야 합니다.

## 서버 사람 ID와 얼굴 등록

사람을 먼저 서버에 등록한 뒤 발급된 ID를 사진 폴더 이름으로 사용합니다.
역할별 권한과 추출·전송 명령은 [얼굴 등록 안내](FACE_REGISTRATION_KO.md)를 참고하세요.
