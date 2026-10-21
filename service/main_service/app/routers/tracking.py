"""SR-06 추적: 추적 좌표 수신 → CCTV MCU 팬틸트로 중계."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import auth
from ..schemas import TrackingObservation
from ..services.tracking import bridge

router = APIRouter(prefix="/api/v1/tracking", tags=["SR-06 추적"])


@router.post("/observations", dependencies=[Depends(auth.device)])
def observation(body: TrackingObservation):
    result = bridge.forward(body.model_dump())
    if not result["accepted"]:
        raise HTTPException(409, result["reason"])
    return result


@router.get("/status", dependencies=[Depends(auth.admin)])
def status():
    return {"mode": bridge.mode, "camera_id": bridge.camera_id,
            "received": bridge.received, "last": bridge.last}
