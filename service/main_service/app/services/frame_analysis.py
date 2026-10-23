"""최신 AI 분석 프레임의 짧은 메모리 보관소.

AI는 JPEG와 좌표 JSON을 함께 보내며, 서버는 카메라별 최신 한 장만 보관한다.
영상을 DB에 무제한 적재하지 않아도 GUI가 최신 화면을 조회할 수 있게 하는 용도다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections import deque
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


class CameraPresenceCache:
    """Avoid a database round trip for every video frame.

    A camera is inserted on first sight, then ``last_seen_at`` is refreshed
    only periodically.  Failed DB work is deliberately not cached so the next
    frame can recover after the database returns.
    """

    def __init__(self, refresh_seconds: float = 30.0) -> None:
        self.refresh_seconds = refresh_seconds
        self._next_refresh: dict[str, datetime] = {}
        self._lock = Lock()

    def needs_refresh(self, camera_id: str) -> bool:
        now = datetime.now(timezone.utc)
        with self._lock:
            return now >= self._next_refresh.get(camera_id, datetime.min.replace(tzinfo=timezone.utc))

    def mark_refreshed(self, camera_id: str) -> None:
        with self._lock:
            self._next_refresh[camera_id] = datetime.now(timezone.utc) + timedelta(seconds=self.refresh_seconds)

    def invalidate(self, camera_id: str) -> None:
        with self._lock:
            self._next_refresh.pop(camera_id, None)


camera_presence = CameraPresenceCache()


class ProcessingMetrics:
    """Bounded server-side timing summary exposed in frame POST responses."""

    def __init__(self, size: int = 120) -> None:
        self._samples: deque[float] = deque(maxlen=size)
        self._lock = Lock()

    def add(self, elapsed_ms: float) -> dict[str, float | int]:
        with self._lock:
            self._samples.append(elapsed_ms)
            ordered = sorted(self._samples)
            return {"count": len(ordered), "avg_ms": round(sum(ordered) / len(ordered), 2),
                    "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 2)}


processing_metrics = ProcessingMetrics()
