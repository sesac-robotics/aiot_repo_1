"""Small length-prefixed JPEG stream used between PC4 and PC2.

PC4 owns the physical camera.  PC2 connects as a receiver and runs the face
and person detectors on the received frames.  Each packet is a 4-byte big
endian JPEG length followed by the JPEG bytes.
"""

from __future__ import annotations

import socket
import struct


MAX_JPEG_BYTES = 2 * 1024 * 1024


def send_frame(sock: socket.socket, jpeg: bytes) -> None:
    if not jpeg or len(jpeg) > MAX_JPEG_BYTES:
        raise ValueError("JPEG frame is empty or too large")
    sock.sendall(struct.pack("!I", len(jpeg)) + jpeg)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("camera stream disconnected")
        chunks.extend(chunk)
    return bytes(chunks)


def recv_frame(sock: socket.socket):
    """Receive one JPEG and decode it with OpenCV."""
    import cv2
    import numpy as np

    size = struct.unpack("!I", recv_exact(sock, 4))[0]
    if size == 0 or size > MAX_JPEG_BYTES:
        raise ValueError("invalid JPEG frame size")
    data = recv_exact(sock, size)
    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("invalid JPEG frame")
    return frame
