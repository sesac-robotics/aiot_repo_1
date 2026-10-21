"""최신 AI 분석 프레임의 짧은 메모리 보관소.

AI는 JPEG와 좌표 JSON을 함께 보내며, 서버는 카메라별 최신 한 장만 보관한다.
영상을 DB에 무제한 적재하지 않아도 GUI가 최신 화면을 조회할 수 있게 하는 용도다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class FrameSnapshot:
    metadata: dict[str, Any]
    jpeg: bytes
    received_at: datetime


class FrameAnalysisStore:
    def __init__(self) -> None:
        self._items: dict[str, FrameSnapshot] = {}
        self._lock = Lock()

    def put(self, camera_id: str, metadata: dict[str, Any], jpeg: bytes) -> FrameSnapshot:
        snapshot = FrameSnapshot(metadata=metadata, jpeg=jpeg, received_at=datetime.now(timezone.utc))
        with self._lock:
            self._items[camera_id] = snapshot
        return snapshot

    def get(self, camera_id: str) -> FrameSnapshot | None:
        with self._lock:
            return self._items.get(camera_id)


store = FrameAnalysisStore()
