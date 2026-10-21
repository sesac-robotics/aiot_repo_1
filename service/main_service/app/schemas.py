"""API 입력 형식. 잘못된 값은 FastAPI가 자동으로 422 + 이유를 돌려준다.

SR별로 구역을 나눠 두었다. 필드를 추가할 때는 이 파일과 sql/01_schema.sql을 같이 고친다.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import settings


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 모르는 필드가 오면 거부(오타 방지)


def _finite(v):
    if v is not None and not math.isfinite(v):
        raise ValueError("숫자가 올바르지 않습니다")
    return v


def _aware(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("시간대가 포함된 시각이어야 합니다 (예: 2026-10-21T15:00:00+09:00)")
    return v


# ---------------------------------------------------------------- SR-02 환자
# GUI(ui/hcc_gui)의 patients 표와 같은 필드 이름을 쓴다.
#   p_num  번호 = RFID 카드·얼굴 등록 ID와 같은 값. 번호 범위로 역할이 정해진다.
#          1~3000 환자(Patient) · 3001~4000 방문자(Visitor) · 4001~5000 직원(Staff)
P_NUM_MAX = 5000


def role_of(p_num: int) -> str:
    if 1 <= p_num <= 3000:
        return "Patient"
    if 3001 <= p_num <= 4000:
        return "Visitor"
    if 4001 <= p_num <= P_NUM_MAX:
        return "Staff"
    return "Unknown"


class PatientIn(_Strict):
    """GUI 등록 화면 → 서버. 같은 p_num으로 다시 보내면 덮어쓴다(GUI의 ON CONFLICT와 같음)."""
    p_num: int | None = Field(default=None, ge=1, le=P_NUM_MAX)   # PUT /patients/{p_num}이면 생략 가능
    name: str = Field(min_length=1, max_length=50)
    gender: Literal["M", "F"] | None = None
    age: int | None = Field(default=None, ge=0, le=150)
    blood_type: str | None = Field(default=None, max_length=10)   # 예: "Rh+ A", "Rh- AB"
    height: float | None = Field(default=None, ge=0, le=300)      # cm. 0은 "입력 안 함"으로 처리
    weight: float | None = Field(default=None, ge=0, le=500)      # kg. 0은 "입력 안 함"으로 처리
    disease: str | None = Field(default=None, max_length=100)
    meds: str | None = Field(default=None, max_length=100)

    _f = field_validator("height", "weight")(_finite)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("이름이 비어 있습니다")
        return v.strip()


class RFIDAssign(_Strict):
    card_uid: str = Field(min_length=4, max_length=64, pattern=r"^[0-9A-Fa-f]+$")


# ---------------------------------------------------------------- SR-02/05 얼굴
def _check_vector(v: list[float]) -> list[float]:
    if len(v) != settings.face_dim:
        raise ValueError(f"embedding은 {settings.face_dim}차원이어야 합니다")
    if any(not math.isfinite(x) for x in v):
        raise ValueError("embedding에 잘못된 숫자가 있습니다")
    norm = math.sqrt(sum(x * x for x in v))
    if norm <= 0:
        raise ValueError("embedding 크기가 0입니다")
    return [x / norm for x in v]  # 길이 1로 정규화해서 저장·비교


class FaceTemplate(BaseModel):
    """PC2 → PC1: 등록용 특징벡터 (환자 1명당 1개, 다시 보내면 덮어씀)."""
    model_config = ConfigDict(extra="ignore")
    patient_id: str = Field(min_length=1, max_length=40)
    model: str = settings.face_model
    embedding: list[float]
    images_used: int = Field(default=1, ge=1, le=100)

    _v = field_validator("embedding")(_check_vector)


class FaceIdentify(BaseModel):
    """PC2 → PC1: 문 앞에서 찍힌 얼굴의 특징벡터. 서버가 DB에서 가장 비슷한 사람을 찾는다."""
    model_config = ConfigDict(extra="ignore")
    event_id: str = Field(min_length=1, max_length=64)        # 중복 전송 판별용
    camera_id: str | None = Field(default=None, max_length=32)
    timestamp: datetime
    status: Literal["OK", "NoFace", "MultipleFaces", "Error"]
    model: str = settings.face_model
    embedding: list[float] | None = None

    _t = field_validator("timestamp")(_aware)

    @model_validator(mode="after")
    def _ok_needs_vector(self):
        if self.status == "OK":
            if self.embedding is None:
                raise ValueError("status=OK이면 embedding이 필요합니다")
            self.embedding = _check_vector(self.embedding)
        return self


# ---------------------------------------------------------------- SR-04 출입문
class DoorCreate(_Strict):
    door_id: str = Field(min_length=1, max_length=32)
    location: str | None = Field(default=None, max_length=100)



# ---------------------------------------------------------------- SR-03 낙상·알람
class CameraCreate(_Strict):
    camera_id: str = Field(min_length=1, max_length=32)
    camera_type: Literal["CCTV", "FACE", "TRACKING", "OTHER"] = "CCTV"
    location: str | None = Field(default=None, max_length=100)


class FallEvent(_Strict):
    """PC1 낙상 감지 모듈 → 서버."""
    event_id: str = Field(min_length=1, max_length=64)
    camera_id: str = Field(min_length=1, max_length=32)
    occurred_at: datetime
    event_type: Literal["FALL", "COLLAPSE", "SUSPECTED_FALL"] = "FALL"
    person_id: str | None = Field(default=None, max_length=40)
    track_id: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    bbox_xyxy: list[float] | None = Field(default=None, min_length=4, max_length=4)

    _t = field_validator("occurred_at")(_aware)


class AlertUpdate(_Strict):
    staff_id: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------- SR-06 추적
class TrackingObservation(_Strict):
    """추적 좌표. state=T면 x,y,dx,dy 필수, N(대상 없음)이면 좌표 없음."""
    camera_id: str = Field(min_length=1, max_length=32)
    timestamp: datetime
    state: Literal["T", "N"]
    x: int | None = Field(default=None, ge=0, le=8192)
    y: int | None = Field(default=None, ge=0, le=8192)
    dx: int | None = Field(default=None, ge=-1000, le=1000)
    dy: int | None = Field(default=None, ge=-1000, le=1000)
    face_detected: bool = False

    _t = field_validator("timestamp")(_aware)

    @model_validator(mode="after")
    def _coords(self):
        coords = (self.x, self.y, self.dx, self.dy)
        if self.state == "T" and None in coords:
            raise ValueError("state=T이면 x,y,dx,dy가 모두 필요합니다")
        if self.state == "N" and any(c is not None for c in coords):
            raise ValueError("state=N이면 좌표를 보내지 않습니다")
        return self
