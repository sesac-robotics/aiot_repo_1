# 출입문 얼굴 인식 카메라: PC4 UDP → PC2 YOLO → PC1

PC4 USB 카메라 영상을 UDP로 PC2에 보내고, PC2가 얼굴·몸 바운딩 박스를 PC1의 기존 TCP 8102 수신기로 전송합니다. 서버·DB·GUI 설정이나 코드는 변경하지 않습니다.

## PC4 송신

OpenCV가 설치된 Python 환경에서 실행합니다. `start.sh`는 처음 실행할 때 `.env.example`을 `.env`로 복사합니다. `PC2_HOST`를 `.env`에 설정하거나 다음처럼 실제 PC2 IP를 지정하세요.

```bash
bash device/door_face_camera/start.sh --pc2-ip <PC2_IP> --camera 0
```

기본값은 UDP 8766, 640×480, 초당 15프레임, JPEG 품질 65입니다. 미리보기가 필요 없으면 `--no-preview`를 추가하세요. 카메라 번호는 연결된 장치에 맞게 지정합니다.

## PC2 수신·검출

기존 YOLO 가상환경과 모델(`webcam/yolo11n.pt`), 얼굴 인식 Docker 설정을 사용합니다. 저장소 루트에서 다음을 실행합니다.

```bash
bash service/ai_service/face_recongnition/start.sh --pc1-host <PC1_IP> --udp-port 8766
```

이 실행기는 기존 InsightFace Docker 서비스를 시작한 뒤 새 `detect_yolo.py --source udp`를 실행합니다. 캡처 이미지는 기존 `data/captures` 경로에 저장됩니다. UDP 8766을 PC2 방화벽에서 허용하고 PC1 TCP 수신기를 실행해 두세요.

Docker/TCP 없이 영상 수신·검출만 확인하려면 기존 YOLO Python 환경에서 실행합니다.

```bash
cd service/ai_service/face_recongnition/webcam
python3 detect_yolo.py --source udp --udp-port 8766 --debug --no-tcp
```

같은 PC에서 시험할 때는 PC4 송신기의 `--pc2-ip`를 `127.0.0.1`로 지정합니다. 카메라를 직접 연결하려면 `detect_yolo.py --source local --camera 0`도 사용할 수 있습니다.

## 프로토콜

- UDP: JPEG를 최대 1200바이트 조각으로 나눕니다. 헤더는 `!4sBIHHI`이며 magic `PC4F`, 버전 1, 프레임 ID(uint32), 조각 번호/개수(uint16), 전체 JPEG 길이(uint32)입니다.
- PC2 → PC1: `body,face,fx1,fy1,fx2,fy2,bx1,by1,bx2,by2` 고정 10필드 CSV 뒤에 줄바꿈을 붙입니다. 검출 상태는 `T`/`N`, 없는 박스 좌표는 `-1`입니다. 예: 몸만 검출되면 `T,N,-1,-1,-1,-1,10,20,300,470`입니다.
- 영상이 1.5초 이상 끊기거나 TCP 송신기가 종료되면 `N,N,-1,-1,-1,-1,-1,-1,-1,-1`을 보냅니다.

기존 TCP 영상 송수신 파일과 `detect_yolov3.py`, `test/1desktoptest.py`는 유지합니다. 이 UDP 경로로 실행할 때는 같은 카메라를 사용하는 기존 `camera_stream_sender.py`를 함께 실행하지 마세요. 실제 `.env`, 가상환경, 캡처 이미지는 업로드하지 않습니다.
