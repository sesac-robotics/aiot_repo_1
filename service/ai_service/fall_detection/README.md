# Fall Detection

YOLO26 Pose와 ByteTrack으로 사람별 COCO-17 관절을 추적하고, 40프레임 관절 특징을
LSTM에 전달해 쓰러짐 후보를 판단하는 AI 컴포넌트다.

## 설정과 실행

안정적으로 유지할 기본값은 `config.py`에서 바꾼다. 모델 파일명, GPU/CPU 선택,
YOLO confidence, Fall 판정 기준, 미리보기, 선택적 메인 서비스 전송 주소가 여기에
있다. 카메라의 RTSP 주소도 확인된 뒤 `RTSP_SOURCES`에만 넣는다. 코드에는 카메라
주소가 들어 있지 않다.

프로젝트의 기본 카메라는 `config.py`의 `DEFAULT_RTSP_SOURCE`, `CAMERA_ID`,
`CAMERA_LOCATION`에서 정한다. 따라서 평소에는 인자 없이 실행하면 된다.

```bash
cd /home/jang/dev_ws/git_project_tenji/service/ai_service/fall_detection
PYENV_VERSION=proj_iot pyenv exec python run_fall_detection.py
```

일회성 입력은 `--source <영상경로>`, `--rtsp-source <이름>`, 또는
`--camera-index 0`으로 덮어쓸 수 있다. 결과 영상이나 JSONL을 저장하려면 `--output`,
`--results-jsonl` 옵션을 사용한다.

## PC1 분석 결과 전송

`config.py`의 `FRAME_API_URL`을 PC1 HTTP 주소로 설정하면, AI는 분석을 계속 수행하면서
설정한 `FRAME_MAX_FPS`(기본 20)마다 하나의 `frame_analysis` 메시지를 보낸다. 현재
Main Service 수신 주소는 `http://<PC1_IP>:8101/api/v1/live/frame`이다. 전송마다
원본 화면을 축소한 JPEG와 같은 프레임의 box·COCO-17 관절·track ID·낙상 판단 상태를
함께 보낸다. 낙상 확정 이벤트가 있으면 `event.new_events`에 추가된다.

이미지는 JSON Base64가 아니라 동일 HTTP `multipart/form-data` 요청의 `image` JPEG
파트에 들어간다. `metadata` JSON에는 `source_size`, `image.display_size`,
`coordinate_system: "original_frame_pixels"`가 포함되므로, PC1/GUI는 수신 JPEG 위에
좌표를 비율 변환해 다시 그릴 수 있다. AI는 이 모드에서 영상에 박스나 글자를 그려
전송하지 않는다.

`FRAME_API_URL` 모드와 기존 `EVENT_API_URL` 또는 `STREAM_PUBLISH_URL`은 함께 켤 수
없다. 하나의 분석 메시지 계약으로 통일하기 위한 제한이다.

## 파일 구성

| 파일 | 역할 |
| --- | --- |
| `run_fall_detection.py` | 시작 파일. 영상/RTSP를 열고 YOLO Pose + ByteTrack 추적, LSTM 추론, 화면·결과 출력과 선택적 서버 전송을 연결한다. |
| `config.py` | 이 컴포넌트의 모델, GPU, 추론 기준, RTSP, 출력 및 선택적 서버 전송 기본값을 한 곳에서 관리한다. |
| `adapters.py` | Ultralytics YOLO 결과를 파이프라인이 사용하는 `PoseObservation` 형식으로 변환하고, COCO-17 관절·bbox·track ID 배열의 정합성을 검사한다. |
| `fall_pipeline.py` | 사람별 Track ID마다 관절 특징의 시간 창을 관리하고, 자세 기하 조건과 LSTM 점수를 함께 사용해 `normal`, `possible_fall`, `fall_confirmed`, `recovered` 상태를 계산한다. |
| `lstm_predictor.py` | `lstm_fall_model.pth`를 읽어 40 x 3 특징 입력의 Fall 확률을 반환하는 PyTorch 어댑터다. |
| `central_delivery.py` | 원본 JPEG + 분석 JSON을 한 HTTP 요청으로 보내는 전송 도구와, 기존 확정 이벤트/주석 영상 전송 호환 기능을 포함한다. |
| `requirements.txt` | 이 컴포넌트의 Python 의존성 목록이다. GPU를 쓸 경우 설치 환경에 맞는 PyTorch를 먼저 준비해야 한다. |
| `models/yolo26m-pose.pt` | 사람 bbox와 COCO-17 관절을 추론하는 YOLO26 Pose 모델이다. |
| `models/lstm_fall_model.pth` | Track별 40프레임 관절 특징에서 Fall 확률을 계산하는 LSTM 가중치다. |

## 범위

이 폴더는 쓰러짐 후보를 분석하는 AI만 담당한다. 환자 정보·GUI·문 제어·DB는 여기서
처리하지 않는다. `fall_confirmed`는 모델과 규칙 기반의 후보 결과이며, 실제 상황의
정답 또는 안전 판단을 보증하지 않는다.
