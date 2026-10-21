"""AI의 JPEG + pose/fall JSON 수신 API.

AI는 ``metadata`` JSON 파트와 ``image`` JPEG 파트를 한 multipart 요청으로 보낸다.
카메라별 최신 프레임만 메모리에 유지하고, 새 낙상 이벤트는 기존 SR-03 처리로 넘긴다.
"""
from __future__ import annotations

from hashlib import sha256
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from .. import auth
from ..events import publish
from ..schemas import FallEvent, FrameAnalysis
from ..services.frame_analysis import store
from .falls import receive_fall

router = APIRouter(tags=["AI 실시간 분석"])

MAX_JPEG_BYTES = 2 * 1024 * 1024


def _event_id(camera_id: str, track_id: int | str, confirmed_frame_id: int) -> str:
    raw = f"{camera_id}\x1f{track_id}\x1f{confirmed_frame_id}".encode("utf-8")
    return "frame-fall-" + sha256(raw).hexdigest()[:32]


def _store_fall_events(frame: FrameAnalysis) -> list[dict]:
    stored: list[dict] = []
    for event in frame.event.new_events:
        result = receive_fall(FallEvent(
            event_id=_event_id(event.camera_id, event.track_id, event.confirmed_frame_id),
            camera_id=event.camera_id,
            occurred_at=event.confirmed_at,
            event_type="FALL",
            track_id=str(event.track_id),
            location=frame.camera.location,
            confidence=event.fall_probability,
        ))
        stored.append({"event_id": result["event_id"], "duplicate": result["duplicate"]})
    return stored


@router.post("/api/v1/live/frame", status_code=201, dependencies=[Depends(auth.device)])
async def receive_frame_analysis(
    metadata: str = Form(...),
    image: UploadFile = File(...),
):
    """수신 후 최신 프레임을 갱신하고, 포함된 새 낙상 이벤트를 영속화한다."""
    if image.content_type not in {"image/jpeg", "image/pjpeg"}:
        raise HTTPException(415, "image은 JPEG여야 합니다")
    jpeg = await image.read(MAX_JPEG_BYTES + 1)
    if not jpeg or len(jpeg) > MAX_JPEG_BYTES or not jpeg.startswith(b"\xff\xd8"):
        raise HTTPException(422, "유효한 2MB 이하 JPEG image가 필요합니다")
    try:
        frame = FrameAnalysis.model_validate(json.loads(metadata))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(422, "metadata JSON 형식이 올바르지 않습니다") from exc

    saved_events = _store_fall_events(frame)
    snapshot = store.put(frame.camera.camera_id, frame.model_dump(mode="json"), jpeg)
    # JPEG는 WebSocket으로 보내지 않는다. GUI는 image endpoint에서 필요할 때 가져간다.
    publish("frame_analysis", {
        "camera_id": frame.camera.camera_id,
        "frame_id": frame.frame_id,
        "received_at": snapshot.received_at,
        "image_url": f"/api/v1/live/frame/{frame.camera.camera_id}/image",
        "metadata_url": f"/api/v1/live/frame/{frame.camera.camera_id}",
    })
    return {"accepted": True, "camera_id": frame.camera.camera_id,
            "frame_id": frame.frame_id, "new_events": saved_events}


@router.get("/api/v1/live/frame/{camera_id}", dependencies=[Depends(auth.admin)])
def latest_frame_metadata(camera_id: str):
    snapshot = store.get(camera_id)
    if snapshot is None:
        raise HTTPException(404, "수신한 프레임이 없습니다")
    return {**snapshot.metadata, "received_at": snapshot.received_at}


@router.get("/api/v1/live/frame/{camera_id}/image", dependencies=[Depends(auth.admin)])
def latest_frame_image(camera_id: str):
    snapshot = store.get(camera_id)
    if snapshot is None:
        raise HTTPException(404, "수신한 프레임이 없습니다")
    return Response(snapshot.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
