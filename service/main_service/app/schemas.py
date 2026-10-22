"""API 입력 형식. 잘못된 값은 FastAPI가 자동으로 422 + 이유를 돌려준다.

SR별로 구역을 나눠 두었다. 필드를 추가할 때는 이 파일과 sql/01_schema.sql을 같이 고친다.
"""
from __future__ import annotations

import math
from datetime import date, datetime
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
class PatientCreate(_Strict):
    name: str = Field(min_length=1, max_length=100)
    gender: Literal["M", "F"] | None = None
    birth_date: date | None = None
    height_cm: float | None = Field(default=None, gt=0, le=300)
    weight_kg: float | None = Field(default=None, gt=0, le=500)
    blood_type: str | None = Field(default=None, max_length=10)   # 예: A+, AB-
    medical_history: str | None = None
    medications: str | None = None
    room_number: str | None = Field(default=None, max_length=20)
    can_open: bool = False                                        # 출입 허용 여부

    _f = field_validator("height_cm", "weight_kg")(_finite)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("이름이 비어 있습니다")
        return v.strip()

    @field_validator("birth_date")
    @classmethod
    def _birth(cls, v: date | None) -> date | None:
        if v and v > date.today():
            raise ValueError("생년월일이 미래입니다")
        return v


class PatientUpdate(PatientCreate):
    """보낸 필드만 바뀐다 (PATCH)."""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    can_open: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            raise ValueError("이름은 비울 수 없습니다")
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
    """추적 좌표 또는 Bounding Box.

    새 클라이언트는 body/face와 x1..frame_height를 보낸다. 구형 클라이언트의
    x/y/dx/dy 형식도 계속 허용한다.
    """
    camera_id: str = Field(min_length=1, max_length=32)
    timestamp: datetime
    state: Literal["T", "N"]
    x: int | None = Field(default=None, ge=0, le=8192)
    y: int | None = Field(default=None, ge=0, le=8192)
    dx: int | None = Field(default=None, ge=-1000, le=1000)
    dy: int | None = Field(default=None, ge=-1000, le=1000)
    face_detected: bool = False
    body_detected: bool | None = None
    x1: int | None = Field(default=None, ge=-1, le=8192)
    y1: int | None = Field(default=None, ge=-1, le=8192)
    x2: int | None = Field(default=None, ge=-1, le=8192)
    y2: int | None = Field(default=None, ge=-1, le=8192)
    frame_width: int | None = Field(default=None, ge=1, le=4096)
    frame_height: int | None = Field(default=None, ge=1, le=4096)

    _t = field_validator("timestamp")(_aware)

    @model_validator(mode="after")
    def _coords(self):
        bbox = (self.x1, self.y1, self.x2, self.y2,
                self.frame_width, self.frame_height)
        has_bbox = any(v is not None for v in bbox)
        if has_bbox:
            if any(v is None for v in bbox):
                raise ValueError("Bounding Box 필드는 모두 필요합니다")
            body = self.body_detected if self.body_detected is not None else self.state == "T"
            if self.body_detected is not None and self.body_detected != (self.state == "T"):
                raise ValueError("body_detected와 state가 일치하지 않습니다")
            if self.face_detected and not body:
                raise ValueError("body가 없으면 face_detected일 수 없습니다")
            if body:
                if not (0 <= self.x1 < self.x2 <= self.frame_width and
                        0 <= self.y1 < self.y2 <= self.frame_height):
                    raise ValueError("Bounding Box 범위를 벗어났습니다")
            elif (self.x1, self.y1, self.x2, self.y2) != (-1, -1, -1, -1):
                raise ValueError("대상 없음 Bounding Box는 -1이어야 합니다")
            return self
        coords = (self.x, self.y, self.dx, self.dy)
        if self.state == "T" and None in coords:
            raise ValueError("state=T이면 x,y,dx,dy가 모두 필요합니다")
        if self.state == "N" and any(c is not None for c in coords):
            raise ValueError("state=N이면 좌표를 보내지 않습니다")
        return self
