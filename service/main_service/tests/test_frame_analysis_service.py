"""Unit checks for the in-memory live-frame path (no PostgreSQL required)."""
from __future__ import annotations

from app.services.frame_analysis import CameraPresenceCache, FrameAnalysisStore
from app.routers.frame_analysis import latest_frame_snapshot
from app.services.frame_analysis import store
import json


def test_latest_snapshot_replaces_older_frame_without_db() -> None:
    frames = FrameAnalysisStore()
    frames.put("cam", {"frame_id": 1}, b"old")
    frames.put("cam", {"frame_id": 2}, b"new")
    current = frames.get("cam")
    assert current is not None
    assert current.metadata["frame_id"] == 2
    assert current.jpeg == b"new"


def test_camera_presence_only_needs_database_on_first_or_invalidated_frame() -> None:
    cache = CameraPresenceCache(refresh_seconds=60)
    assert cache.needs_refresh("cam")
    cache.mark_refreshed("cam")
    assert not cache.needs_refresh("cam")
    cache.invalidate("cam")
    assert cache.needs_refresh("cam")


def test_snapshot_endpoint_returns_matching_metadata_and_jpeg() -> None:
    store.put("snapshot-test", {"frame_id": 7}, b"jpeg-bytes")
    response = latest_frame_snapshot("snapshot-test")
    payload = json.loads(response.body)
    assert payload["metadata"]["frame_id"] == 7
    assert payload["jpeg_base64"] == "anBlZy1ieXRlcw=="
