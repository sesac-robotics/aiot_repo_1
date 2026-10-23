"""서버 설정. 모든 값은 환경변수(.env)에서 읽고, 없으면 개발용 기본값을 쓴다.

팀원이 바꿀 일이 있는 값은 전부 여기 한 곳에 모여 있다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """프로젝트 폴더의 .env를 읽는다 (이미 설정된 환경변수가 우선). Windows에서도 그대로 동작."""
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # DB
    # 팀 DB 패키지(db/)와 같은 변수 HCC_DB_DSN을 쓴다. 기본값은 db/compose 개발 계정.
    database_url: str = os.getenv("HCC_DB_DSN") or os.getenv(
        "DATABASE_URL", "postgresql://hcc_app:change_this_dev_password@localhost:5432/hcc")

    # 인증: 개발 중에는 꺼 두고(false) 시연·배포 때만 켠다.
    auth_enabled: bool = _bool("AUTH_ENABLED", False)
    admin_token: str = os.getenv("ADMIN_TOKEN", "")    # GUI(관리자)용
    device_token: str = os.getenv("DEVICE_TOKEN", "")  # PC2·낙상 모듈 등 장치용

    # SR-05 얼굴 유사도 기준값 (코사인 유사도, -1~1). 얼굴팀 실측 후 조정.
    face_threshold: float = float(os.getenv("FACE_THRESHOLD", "0.5"))
    face_model: str = os.getenv("FACE_MODEL", "buffalo_l")
    face_dim: int = int(os.getenv("FACE_DIM", "512"))
    # 얼굴 등록 때 처음 보는 patient_id면 이름 없는 환자를 자동 생성 (얼굴팀 단독 시험용)
    face_auto_create_patient: bool = _bool("FACE_AUTO_CREATE_PATIENT", True)
    # SR-04 Door MCU가 문 상태(1/0)를 보내는 TCP 포트
    door_tcp_port: int = int(os.getenv("DOOR_TCP_PORT", "8103"))
    default_door_id: str = os.getenv("DOOR_ID", "door_01")   # MCU가 ID 없이 1/0만 보낼 때

    # SR-06 추적 → PC4 CCTV MCU. PC4를 직접 연결하면 serial 대신 TCP를 설정한다.
    track_serial_port: str = os.getenv("TRACK_SERIAL_PORT", "")
    track_serial_baud: int = int(os.getenv("TRACK_SERIAL_BAUD", "115200"))
    # PC4 Bridge (device/cctv_tracking_servo/pc4_bridge.py). 추적(TRACK)과 문 열기(DOOR,OPEN)가 같은 연결을 쓴다.
    # Bridge는 접속을 1개만 받으므로 서버가 하나의 연결로 둘 다 보낸다. PC4_HOST 또는 TRACK_PC4_HOST 중 아무거나.
    track_pc4_host: str = os.getenv("PC4_HOST") or os.getenv("TRACK_PC4_HOST", "")
    track_pc4_port: int = int(os.getenv("PC4_PORT") or os.getenv("TRACK_PC4_PORT", "8765"))
    # door: DOOR,OPEN,id; v: V,body,face,dx,dy,doorPulse for the PC4 V bridge.
    pc4_door_protocol: str = os.getenv("PC4_DOOR_PROTOCOL", "door").strip().lower()
    # Demo display only; never substitutes for a PC4 acknowledgement in the DB.
    gui_door_open_on_send: bool = os.getenv("GUI_DOOR_OPEN_ON_SEND", "false").lower() == "true"
    # PC2가 10칸 형식(T,T,얼굴4,몸4)으로 보낼 때 화면 크기를 모르므로 이 값을 쓴다
    track_frame_width: int = int(os.getenv("TRACK_FRAME_WIDTH", "640"))
    track_frame_height: int = int(os.getenv("TRACK_FRAME_HEIGHT", "480"))
    track_camera_id: str = os.getenv("TRACK_CAMERA_ID", "CAM01")

    # SR-04 문 열기 명령 (서버 → PC4 → Arduino)
    door_cmd_timeout_sec: float = float(os.getenv("DOOR_CMD_TIMEOUT_SEC", "5"))      # 명세 ⑥: 5초 내 ACK/EVENT 없으면 실패
    door_open_cooldown_sec: float = float(os.getenv("DOOR_OPEN_COOLDOWN_SEC", "10"))  # 같은 사람 얼굴이 연달아 와도 1번만 열기

    # 3-① PC3 실시간 프레임 (JPEG + 분석 JSON)
    live_frame_max_bytes: int = int(os.getenv("LIVE_FRAME_MAX_BYTES", str(3 * 1024 * 1024)))

    # 관리자 GUI 이벤트 수신기 (ui/gui/Current/event_bridge.py, HTTP 8765). 서버가 인식·문·낙상을 보낸다.
    gui_event_enabled: bool = _bool("GUI_EVENT_ENABLED", True)
    gui_event_url: str = os.getenv("GUI_EVENT_URL", "http://127.0.0.1:8765")
    gui_event_token: str = os.getenv("GUI_EVENT_TOKEN", "")
    # 토큰을 비워 두면 같은 PC의 GUI가 만든 토큰 파일을 읽는다
    gui_event_token_file: str = os.getenv("GUI_EVENT_TOKEN_FILE", str(
        Path(__file__).resolve().parents[3] / "ui" / "gui" / "Current" / "data" / "gui_event.token"))

    # PC2 → PC1 추적값 TCP 수신 (새 TRACK Bounding Box, 기존 T/T,N,N도 호환)
    pc2_tcp_enabled: bool = _bool("PC2_TCP_ENABLED", True)
    pc2_tcp_port: int = int(os.getenv("PC2_TCP_PORT", "8102"))


settings = Settings()
