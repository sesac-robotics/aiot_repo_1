"""Central-server delivery primitives for video, analysis, and fall events."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from fall_pipeline import FallState, TrackJudgment


class CentralDeliveryError(RuntimeError):
    """Raised when a configured central-server delivery cannot complete."""


@dataclass
class HttpFrameAnalysisPublisher:
    """Post a raw display JPEG and its matching analysis JSON in one request.

    JSON coordinates remain in original-frame pixels.  The receiver uses
    ``source_size`` and ``image.display_size`` to render boxes and landmarks.
    """

    endpoint: str
    max_width: int = 640
    jpeg_quality: int = 70
    max_fps: float = 5.0
    timeout_seconds: float = 3.0
    bearer_token: str | None = None
    _last_sent_at: float = field(init=False, default=float("-inf"))

    def __post_init__(self) -> None:
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("frame API endpoint must start with http:// or https://")
        if self.max_width <= 0:
            raise ValueError("frame max width must be positive")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("JPEG quality must be in [1, 100]")
        if self.max_fps <= 0 or self.timeout_seconds <= 0:
            raise ValueError("frame max FPS and timeout must be positive")

    def _display_frame(self, frame: np.ndarray) -> np.ndarray:
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("frame analysis publisher expects a uint8 BGR image")
        height, width = frame.shape[:2]
        if width <= self.max_width:
            return frame
        target_height = max(1, round(height * self.max_width / width))
        return cv2.resize(frame, (self.max_width, target_height), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _multipart_body(*, metadata: dict[str, object], jpeg: bytes, boundary: str) -> bytes:
        delimiter = f"--{boundary}\r\n".encode("ascii")
        body = bytearray()
        body.extend(delimiter)
        body.extend(b'Content-Disposition: form-data; name="metadata"\r\n')
        body.extend(b"Content-Type: application/json; charset=utf-8\r\n\r\n")
        body.extend(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        body.extend(b"\r\n")
        body.extend(delimiter)
        body.extend(b'Content-Disposition: form-data; name="image"; filename="frame.jpg"\r\n')
        body.extend(b"Content-Type: image/jpeg\r\n\r\n")
        body.extend(jpeg)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("ascii"))
        return bytes(body)

    def publish(self, frame: np.ndarray, metadata: dict[str, object]) -> int | None:
        """Post one analysis message, or return None when rate-limited."""
        now = time.monotonic()
        if now - self._last_sent_at < 1.0 / self.max_fps:
            return None
        display = self._display_frame(frame)
        ok, encoded = cv2.imencode(
            ".jpg", display, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        )
        if not ok:
            raise CentralDeliveryError("could not JPEG-encode frame analysis")
        image_height, image_width = display.shape[:2]
        payload = dict(metadata)
        payload["message_type"] = "frame_analysis"
        image = payload.get("image")
        image = {} if not isinstance(image, dict) else dict(image)
        image["format"] = "jpeg"
        image["display_size"] = [image_width, image_height]
        image["coordinate_system"] = "original_frame_pixels"
        payload["image"] = image
        boundary = f"TSAFEFrame{uuid.uuid4().hex}"
        body = self._multipart_body(metadata=payload, jpeg=encoded.tobytes(), boundary=boundary)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        }
        token = self.bearer_token or os.environ.get("T_SAFE_FRAME_BEARER_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if not 200 <= response.status < 300:
                    raise CentralDeliveryError(f"frame API returned HTTP {response.status}")
                self._last_sent_at = now
                return response.status
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise CentralDeliveryError(f"could not deliver frame analysis: {exc}") from exc


def build_fall_event(
    judgment: TrackJudgment,
    *,
    camera: dict[str, str],
    observed_at: str,
    source_size: tuple[int, int],
) -> dict[str, object]:
    """Build one server-ready event for the first confirmed fall state."""
    if judgment.state is not FallState.FALL_CONFIRMED:
        raise ValueError("only fall_confirmed judgments can be delivered as fall events")
    if judgment.confirmed_frame_id is None or judgment.confirmed_at is None:
        raise ValueError("confirmed fall judgment must include confirmation frame and time")
    camera_id = camera.get("camera_id", "").strip()
    if not camera_id:
        raise ValueError("camera.camera_id must not be empty")
    width, height = source_size
    if width <= 0 or height <= 0:
        raise ValueError("source_size must contain positive width and height")
    return {
        "event_type": "fall_confirmed",
        "camera_id": camera_id,
        "camera_location": camera.get("location", "unspecified"),
        "track_id": judgment.track_id,
        "state": judgment.state.value,
        "observed_at": observed_at,
        "confirmed_at": judgment.confirmed_at,
        "confirmed_frame_id": judgment.confirmed_frame_id,
        "confirmed_timestamp_ms": judgment.confirmed_timestamp_ms,
        "fall_probability": judgment.fall_probability,
        "confirmed_score": judgment.confirmed_score,
        "peak_fall_probability": judgment.peak_fall_probability,
        "geometry_score": judgment.geometry_score,
        "frames_collected": judgment.frames_collected,
        "source_size": [width, height],
        "coordinate_system": "original_frame_pixels",
    }


def event_deduplication_key(payload: dict[str, object]) -> tuple[str, int, int]:
    """Return a stable key so a latched event is posted only once."""
    camera_id = payload.get("camera_id")
    track_id = payload.get("track_id")
    confirmed_frame_id = payload.get("confirmed_frame_id")
    if not isinstance(camera_id, str) or not isinstance(track_id, int) or not isinstance(confirmed_frame_id, int):
        raise ValueError("fall event lacks camera_id, track_id, or confirmed_frame_id")
    return camera_id, track_id, confirmed_frame_id


def rtsp_publish_command(
    *,
    ffmpeg_binary: str,
    destination: str,
    width: int,
    height: int,
    fps: float,
) -> list[str]:
    """Build a low-latency H.264 RTSP/TCP publish command for BGR frames."""
    if not destination.startswith(("rtsp://", "rtsps://")):
        raise ValueError("RTSP publish destination must start with rtsp:// or rtsps://")
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("width, height, and fps must be positive")
    return [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel", "warning",
        "-f", "rawvideo",
        "-pixel_format", "bgr24",
        "-video_size", f"{width}x{height}",
        "-framerate", f"{fps:.3f}",
        "-i", "pipe:0",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-g", str(max(1, round(fps))),
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        destination,
    ]


def mpegts_udp_publish_command(
    *,
    ffmpeg_binary: str,
    destination: str,
    width: int,
    height: int,
    fps: float,
) -> list[str]:
    """Build a low-latency H.264 MPEG-TS-over-UDP command for BGR frames."""
    if not destination.startswith("udp://"):
        raise ValueError("UDP publish destination must start with udp://")
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("width, height, and fps must be positive")
    return [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel", "warning",
        "-f", "rawvideo",
        "-pixel_format", "bgr24",
        "-video_size", f"{width}x{height}",
        "-framerate", f"{fps:.3f}",
        "-i", "pipe:0",
        "-an",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-g", str(max(1, round(fps))),
        # A UDP receiver may join after the publisher starts.  Repeat codec
        # and MPEG-TS headers at every IDR frame so it can decode without a
        # coordinated full-stack restart.
        "-x264-params", "repeat-headers=1",
        "-mpegts_flags", "+resend_headers",
        "-f", "mpegts",
        destination,
    ]


@dataclass
class RtspFramePublisher:
    """Send already-annotated OpenCV BGR frames to a central RTSP server."""

    destination: str
    width: int
    height: int
    fps: float
    ffmpeg_binary: str = "ffmpeg"
    protocol: str = field(init=False, default="rtsp-tcp")
    _process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._process is not None:
            return
        command = rtsp_publish_command(
            ffmpeg_binary=self.ffmpeg_binary,
            destination=self.destination,
            width=self.width,
            height=self.height,
            fps=self.fps,
        )
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise CentralDeliveryError(f"cannot start FFmpeg RTSP publisher: {exc}") from exc

    def write(self, frame: np.ndarray) -> None:
        if frame.shape != (self.height, self.width, 3) or frame.dtype != np.uint8:
            raise ValueError(
                f"expected uint8 BGR frame ({self.height}, {self.width}, 3), got {frame.shape} {frame.dtype}"
            )
        self.start()
        assert self._process is not None and self._process.stdin is not None
        if self._process.poll() is not None:
            raise CentralDeliveryError("FFmpeg RTSP publisher exited before accepting a frame")
        try:
            self._process.stdin.write(frame.tobytes())
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CentralDeliveryError("FFmpeg RTSP publisher rejected a frame") from exc

    def close(self) -> None:
        if self._process is None:
            return
        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None


@dataclass
class UdpMpegTsFramePublisher(RtspFramePublisher):
    """Send already-annotated BGR frames as H.264 MPEG-TS over UDP."""

    protocol: str = field(init=False, default="udp-mpegts")

    def start(self) -> None:
        if self._process is not None:
            return
        command = mpegts_udp_publish_command(
            ffmpeg_binary=self.ffmpeg_binary,
            destination=self.destination,
            width=self.width,
            height=self.height,
            fps=self.fps,
        )
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise CentralDeliveryError(f"cannot start FFmpeg UDP publisher: {exc}") from exc


def make_frame_publisher(
    *,
    protocol: str,
    destination: str,
    width: int,
    height: int,
    fps: float,
) -> RtspFramePublisher:
    """Select the configured annotated-video transport implementation."""
    if protocol == "rtsp-tcp":
        return RtspFramePublisher(destination=destination, width=width, height=height, fps=fps)
    if protocol == "udp-mpegts":
        return UdpMpegTsFramePublisher(destination=destination, width=width, height=height, fps=fps)
    raise ValueError(f"unsupported stream protocol: {protocol}")


@dataclass(frozen=True)
class HttpJsonEventPublisher:
    """Post a confirmed-fall JSON event with bounded retry attempts."""

    endpoint: str
    timeout_seconds: float = 5.0
    retries: int = 2
    bearer_token: str | None = None

    def __post_init__(self) -> None:
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("event API endpoint must start with http:// or https://")
        if self.timeout_seconds <= 0 or self.retries < 0:
            raise ValueError("timeout_seconds must be positive and retries must be non-negative")

    def publish(self, payload: dict[str, object]) -> int:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = self.bearer_token or os.environ.get("CENTRAL_EVENT_BEARER_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    status = response.status
                    if 200 <= status < 300:
                        return status
                    raise CentralDeliveryError(f"event API returned HTTP {status}")
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, CentralDeliveryError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
        raise CentralDeliveryError(f"could not deliver fall event after {self.retries + 1} attempt(s): {last_error}")
