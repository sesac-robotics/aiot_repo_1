"""Qt-safe polling client for Main Service's latest-frame endpoints."""
from __future__ import annotations

import json

from PyQt6 import QtCore as C, QtGui as G, QtNetwork as N


class LiveFrameClient(C.QObject):
    """Fetch the latest JPEG and its analysis metadata without blocking the GUI."""

    frame_received = C.pyqtSignal(G.QPixmap, object)
    connection_error = C.pyqtSignal(str)

    def __init__(self, image_url: str, metadata_url: str | None = None, *, interval_ms: int = 200, parent=None):
        super().__init__(parent)
        self.image_url = image_url
        self.metadata_url = metadata_url or image_url.removesuffix("/image")
        self.network = N.QNetworkAccessManager(self)
        self.timer = C.QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.fetch)
        self._image_in_flight = False
        self._metadata_in_flight = False
        self._latest_metadata: object = None
        self._last_error = ""

    def start(self):
        self.fetch()
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def fetch(self):
        if self._image_in_flight:
            return
        self._fetch_metadata()
        self._image_in_flight = True
        request = N.QNetworkRequest(C.QUrl(self.image_url))
        request.setRawHeader(b"Cache-Control", b"no-cache")
        reply = self.network.get(request)
        reply.finished.connect(lambda: self._handle_image(reply))

    def _fetch_metadata(self):
        if self._metadata_in_flight:
            return
        self._metadata_in_flight = True
        request = N.QNetworkRequest(C.QUrl(self.metadata_url))
        request.setRawHeader(b"Cache-Control", b"no-cache")
        reply = self.network.get(request)
        reply.finished.connect(lambda: self._handle_metadata(reply))

    def _handle_metadata(self, reply):
        self._metadata_in_flight = False
        try:
            if reply.error() == N.QNetworkReply.NetworkError.NoError:
                self._latest_metadata = json.loads(bytes(reply.readAll()))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._latest_metadata = None
        finally:
            reply.deleteLater()

    def _handle_image(self, reply):
        self._image_in_flight = False
        try:
            data = bytes(reply.readAll())
            pixmap = G.QPixmap()
            failed = reply.error() != N.QNetworkReply.NetworkError.NoError
            if failed or not pixmap.loadFromData(data, "JPEG"):
                message = reply.errorString() if failed else "Invalid JPEG frame"
                if message != self._last_error:
                    self.connection_error.emit(message)
                    self._last_error = message
                return
            self._last_error = ""
            self.frame_received.emit(pixmap, self._latest_metadata)
        finally:
            reply.deleteLater()
