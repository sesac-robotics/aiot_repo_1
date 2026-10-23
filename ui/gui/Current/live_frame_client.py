"""Qt-safe polling client for Main Service's latest-frame endpoints."""
from __future__ import annotations

import json
import base64
import binascii
import time
from collections import deque
from datetime import datetime, timezone

from PyQt6 import QtCore as C, QtGui as G, QtNetwork as N


class LiveFrameClient(C.QObject):
    """Fetch the latest JPEG and its analysis metadata without blocking the GUI."""

    frame_received = C.pyqtSignal(G.QPixmap, object)
    connection_error = C.pyqtSignal(str)
    metrics_updated = C.pyqtSignal(object)

    def __init__(self, image_url: str, metadata_url: str | None = None, *, interval_ms: int = 50,
                 buffer_frames: int = 2, parent=None):
        super().__init__(parent)
        self.image_url = image_url
        self.metadata_url = metadata_url or image_url.removesuffix("/image")
        self.snapshot_url = self.metadata_url.rstrip("/") + "/snapshot"
        self.network = N.QNetworkAccessManager(self)
        self.timer = C.QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.fetch)
        if buffer_frames < 0 or buffer_frames > 10:
            raise ValueError("buffer_frames must be between 0 and 10")
        self.buffer_frames = buffer_frames
        self.presenter = C.QTimer(self)
        self.presenter.setInterval(interval_ms)
        self.presenter.timeout.connect(self._present_next)
        if interval_ms not in (50, 100, 200):
            raise ValueError("interval_ms must be 50, 100, or 200")
        self._snapshot_in_flight = False
        self._pending_frames: deque[tuple[G.QPixmap, dict]] = deque()
        self._presenting = buffer_frames == 0
        self._last_error = ""
        self._frame_times: deque[float] = deque(maxlen=120)
        self._ages_ms: deque[float] = deque(maxlen=120)
        self._dropped_mismatch = 0

    def start(self):
        self.fetch()
        self.timer.start()
        self.presenter.start()

    def stop(self):
        self.timer.stop()
        self.presenter.stop()

    def fetch(self):
        if self._snapshot_in_flight:
            return
        self._fetch_snapshot()

    def _fetch_snapshot(self):
        self._snapshot_in_flight = True
        request = N.QNetworkRequest(C.QUrl(self.snapshot_url))
        request.setRawHeader(b"Cache-Control", b"no-cache")
        reply = self.network.get(request)
        reply.finished.connect(lambda: self._handle_snapshot(reply))

    def _emit_metrics(self, metadata: dict) -> None:
        now = time.time()
        self._frame_times.append(now)
        observed = metadata.get("observed_at")
        if isinstance(observed, str):
            try:
                self._ages_ms.append(max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(observed)).total_seconds() * 1000))
            except ValueError:
                pass
        fps = 0.0 if len(self._frame_times) < 2 else (len(self._frame_times) - 1) / (self._frame_times[-1] - self._frame_times[0])
        ages = sorted(self._ages_ms)
        p95 = ages[min(len(ages) - 1, int(len(ages) * .95))] if ages else None
        self.metrics_updated.emit({"fps": round(fps, 2), "age_ms": None if not ages else round(sum(ages) / len(ages), 1),
                                   "age_p95_ms": None if p95 is None else round(p95, 1), "dropped": self._dropped_mismatch})

    def _handle_snapshot(self, reply):
        self._snapshot_in_flight = False
        try:
            pixmap = G.QPixmap()
            failed = reply.error() != N.QNetworkReply.NetworkError.NoError
            payload = json.loads(bytes(reply.readAll())) if not failed else {}
            metadata = payload.get("metadata") if isinstance(payload, dict) else None
            jpeg = payload.get("jpeg_base64") if isinstance(payload, dict) else None
            if failed or not isinstance(metadata, dict) or not isinstance(jpeg, str) or not pixmap.loadFromData(base64.b64decode(jpeg, validate=True), "JPEG"):
                message = reply.errorString() if failed else "Invalid JPEG frame"
                if message != self._last_error:
                    self.connection_error.emit(message)
                    self._last_error = message
                return
            self._last_error = ""
            self._pending_frames.append((pixmap, metadata))
            # Do not let a burst turn into seconds of stale video.  At most
            # one frame beyond the intentional presentation buffer survives.
            while len(self._pending_frames) > self.buffer_frames + 1:
                self._pending_frames.popleft()
                self._dropped_mismatch += 1
        except (UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
            message = "Invalid live-frame snapshot"
            if message != self._last_error:
                self.connection_error.emit(message)
                self._last_error = message
        finally:
            reply.deleteLater()

    def _present_next(self):
        """Present at a steady cadence after a tiny, bounded jitter buffer."""
        if not self._presenting:
            if len(self._pending_frames) < self.buffer_frames:
                return
            self._presenting = True
        if not self._pending_frames:
            return
        pixmap, metadata = self._pending_frames.popleft()
        self.frame_received.emit(pixmap, metadata)
        self._emit_metrics(metadata)
