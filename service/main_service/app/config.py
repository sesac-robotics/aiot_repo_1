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

    # SR-06 추적 → CCTV MCU (Arduino, USB Serial). 비워 두면 수신만 하고 전송 안 함.
    track_serial_port: str = os.getenv("TRACK_SERIAL_PORT", "")
    track_serial_baud: int = int(os.getenv("TRACK_SERIAL_BAUD", "115200"))
    track_camera_id: str = os.getenv("TRACK_CAMERA_ID", "CAM01")

    # GUI 모니터링 화면용 TCP 텍스트 (FALL / DOOR_IS_OPEN / RFID_IN:...). GUI 원래 형식 그대로
    gui_tcp_enabled: bool = _bool("GUI_TCP_ENABLED", True)
    gui_tcp_port: int = int(os.getenv("GUI_TCP_PORT", "8080"))

    # PC2 → PC1 추적값 TCP 수신 (한 줄 텍스트: "T,T,x,y,dx,dy" / "N,N")
    pc2_tcp_enabled: bool = _bool("PC2_TCP_ENABLED", True)
    pc2_tcp_port: int = int(os.getenv("PC2_TCP_PORT", "8102"))


settings = Settings()
