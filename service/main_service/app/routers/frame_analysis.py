"""AI의 JPEG + pose/fall JSON 수신 API.

AI는 ``metadata`` JSON 파트와 ``image`` JPEG 파트를 한 multipart 요청으로 보낸다.
카메라별 최신 프레임만 메모리에 유지하고, 새 낙상 이벤트는 기존 SR-03 처리로 넘긴다.
"""
from __future__ import annotations

import asyncio
import base64
from hashlib import sha256
import json
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .. import auth
from ..db import get_conn
from ..events import publish
from ..schemas import FallEvent, FrameAnalysis
from ..services.frame_analysis import camera_presence, processing_metrics, store
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


def _ensure_camera(frame: FrameAnalysis) -> None:
    """낙상 기록(fall_events)은 등록된 카메라가 필요하다. AI 설정의 CAMERA_ID(camera_1)를 미리 등록하지
    않아도 되도록 첫 프레임에서 자동 등록한다(위치는 CAMERA_LOCATION)."""
    with get_conn() as conn:
        conn.execute("""INSERT INTO hcc.cameras (camera_id, camera_type, location, last_seen_at)
                        VALUES (%s, 'CCTV', %s, NOW())
                        ON CONFLICT (camera_id) DO UPDATE
                          SET last_seen_at = NOW(), location = COALESCE(hcc.cameras.location, EXCLUDED.location)""",
                     (frame.camera.camera_id, frame.camera.location))
    camera_presence.mark_refreshed(frame.camera.camera_id)


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

    started = time.perf_counter()
    # DB work is exceptional/periodic, never a per-frame operation.  Run the
    # synchronous psycopg calls off FastAPI's event loop.
    needs_db = camera_presence.needs_refresh(frame.camera.camera_id)
    if needs_db:
        try:
            await asyncio.to_thread(_ensure_camera, frame)
        except Exception:
            # Video remains available from memory while DB is temporarily
            # down.  Do not mark success: a later frame retries registration.
            if frame.event.new_events:
                raise
    if frame.event.new_events:
        # A confirmed fall must be durable.  Re-register immediately when a
        # restarted/cleared DB has forgotten a cached camera.
        if needs_db:
            pass
        else:
            await asyncio.to_thread(_ensure_camera, frame)
        saved_events = await asyncio.to_thread(_store_fall_events, frame)
    else:
        saved_events = []
    snapshot = store.put(frame.camera.camera_id, frame.model_dump(mode="json"), jpeg)
    # JPEG는 WebSocket으로 보내지 않는다. GUI는 image endpoint에서 필요할 때 가져간다.
    publish("frame_analysis", {
        "camera_id": frame.camera.camera_id,
        "frame_id": frame.frame_id,
        "received_at": snapshot.received_at,
        "image_url": f"/api/v1/live/frame/{frame.camera.camera_id}/image",
        "metadata_url": f"/api/v1/live/frame/{frame.camera.camera_id}",
    })
    processing_ms = (time.perf_counter() - started) * 1000
    return {"accepted": True, "camera_id": frame.camera.camera_id,
            "frame_id": frame.frame_id, "new_events": saved_events,
            "processing_ms": round(processing_ms, 2),
            "processing": processing_metrics.add(processing_ms)}


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
    return Response(snapshot.jpeg, media_type="image/jpeg", headers={
        "Cache-Control": "no-store",
        # GUI verifies this against metadata before drawing annotations.
        "X-TSAFE-Frame-Id": str(snapshot.metadata["frame_id"]),
    })


@router.get("/api/v1/live/frame/{camera_id}/snapshot", dependencies=[Depends(auth.admin)])
def latest_frame_snapshot(camera_id: str):
    """One atomic GUI read: matching analysis JSON and JPEG from one snapshot.

    The older metadata + image endpoints remain available, but two separate
    requests can straddle an AI update and force the GUI to discard a frame.
    This endpoint removes that race for the live viewer.
    """
    snapshot = store.get(camera_id)
    if snapshot is None:
        raise HTTPException(404, "수신한 프레임이 없습니다")
    return JSONResponse({
        "metadata": {**snapshot.metadata, "received_at": snapshot.received_at.isoformat()},
        "jpeg_base64": base64.b64encode(snapshot.jpeg).decode("ascii"),
    }, headers={"Cache-Control": "no-store"})
