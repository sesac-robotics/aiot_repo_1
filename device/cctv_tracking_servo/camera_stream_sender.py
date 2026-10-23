"""PC4 camera sender: stream the physical camera to the PC2 detector."""

import argparse
import socket
import sys
import time
from pathlib import Path

import cv2

WEBCAM_DIR = Path(__file__).resolve().parents[2] / "service" / "ai_service" / "face_recongnition" / "webcam"
sys.path.insert(0, str(WEBCAM_DIR))
from camera_stream import send_frame  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--pc2-host", required=True)
    ap.add_argument("--pc2-port", type=int, default=8766)
    ap.add_argument("--fps", type=float, default=15.0)
    args = ap.parse_args()
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {args.camera}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    interval = 1.0 / max(args.fps, 1.0)
    try:
        while True:
            sock = None
            try:
                sock = socket.create_connection((args.pc2_host, args.pc2_port), timeout=3)
                print(f"[CAMERA] connected to PC2 {args.pc2_host}:{args.pc2_port}", flush=True)
                while True:
                    started = time.monotonic()
                    ok, frame = cap.read()
                    if not ok:
                        raise ConnectionError("camera frame unavailable")
                    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok:
                        send_frame(sock, encoded.tobytes())
                    remaining = interval - (time.monotonic() - started)
                    if remaining > 0:
                        time.sleep(remaining)
            except (OSError, ConnectionError, ValueError) as exc:
                print(f"[CAMERA] disconnected: {exc}; retrying", flush=True)
                time.sleep(2)
            finally:
                if sock is not None:
                    sock.close()
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    main()
