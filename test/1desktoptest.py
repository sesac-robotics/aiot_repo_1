#!/usr/bin/env python3
"""Linux desktop integration test: PC4 camera -> PC2 -> PC1 -> PC4 -> Uno.

Run from any directory: python3 test/1desktoptest.py
This starts the repository's real PC4 bridge, PC4 camera sender, PC2 detector,
and PC1 tracking TCP receiver on 127.0.0.1. PC1 is the tracking component only;
the database, identity permission decision, and GUI are outside this test.
Press q in the detector's video window to stop every component.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import venv


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
WEBCAM_DIR = ROOT / "service" / "ai_service" / "face_recongnition" / "webcam"
MAIN_DIR = ROOT / "service" / "main_service"
DEVICE_DIR = ROOT / "device" / "cctv_tracking_servo"
VENV_PYTHON = TEST_DIR / ".venv" / "bin" / "python"
PACKAGES = {
    "cv2": "opencv-python==4.11.0.86",
    "ultralytics": "ultralytics==8.4.178",
    "serial": "pyserial>=3.5,<4",
}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-port", help="Arduino path, e.g. /dev/ttyACM0; default: auto")
    parser.add_argument("--camera", type=int, default=0, help="Linux camera index; default: 0")
    parser.add_argument("--model", type=Path, help="Use an existing yolo11n.pt")
    parser.add_argument("--no-install", action="store_true", help="Never install Python packages")
    parser.add_argument("--pc1-port", type=int, default=8102)
    parser.add_argument("--pc4-port", type=int, default=8765)
    parser.add_argument("--video-port", type=int, default=8766)
    return parser.parse_args()


def ensure_packages(args):
    missing = [package for module, package in PACKAGES.items()
               if importlib.util.find_spec(module) is None]
    if not missing:
        return
    if args.no_install:
        raise RuntimeError("Missing Python packages: " + ", ".join(missing))
    if os.environ.get("DESKTOP_TEST_VENV") == "1":
        print("[SETUP] Installing: " + ", ".join(missing), flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)
        return
    if not VENV_PYTHON.exists():
        print("[SETUP] Creating test/.venv", flush=True)
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(VENV_PYTHON.parent)
    env = os.environ.copy()
    env["DESKTOP_TEST_VENV"] = "1"
    os.execve(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()),
                                *sys.argv[1:]], env)


def find_serial_port(requested):
    if requested:
        return requested
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    preferred = [item for item in ports if any(word in
                 (item.description + " " + (item.manufacturer or "")).lower()
                 for word in ("arduino", "ch340", "ch341", "usb serial", "cp210"))]
    choices = preferred or ports
    if len(choices) == 1:
        return choices[0].device
    available = ", ".join(item.device for item in ports) or "none"
    raise RuntimeError("Cannot uniquely identify Arduino serial port. Available: "
                       + available + ". Pass --serial-port /dev/ttyACM0")


def model_path(requested):
    if requested:
        path = requested.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    candidates = [WEBCAM_DIR / "yolo11n.pt", TEST_DIR / ".cache" / "yolo11n.pt",
                  ROOT.parent.parent / "outputs" / "yolo11n.pt"]
    for path in candidates:
        if path.is_file():
            return path
    cache = TEST_DIR / ".cache"
    cache.mkdir(exist_ok=True)
    print("[SETUP] Downloading YOLO model into test/.cache", flush=True)
    subprocess.run([sys.executable, "-c",
                    "from ultralytics import YOLO; YOLO('yolo11n.pt')"],
                   cwd=cache, check=True)
    path = cache / "yolo11n.pt"
    if not path.is_file():
        raise RuntimeError("YOLO download finished without creating yolo11n.pt")
    return path


def check_local_ports(ports):
    for port in ports:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"Local TCP port {port} is busy: {exc}") from exc


def start_child(name, command, cwd, ready_text=None):
    child = subprocess.Popen([sys.executable, "-u", *map(str, command)], cwd=cwd,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
    ready = threading.Event()

    def print_output():
        for line in child.stdout:
            print(f"[{name}] {line.rstrip()}", flush=True)
            if ready_text and ready_text in line:
                ready.set()

    threading.Thread(target=print_output, daemon=True).start()
    return child, ready


def wait_ready(name, child, ready, timeout):
    deadline = time.monotonic() + timeout
    while not ready.wait(0.2):
        if child.poll() is not None:
            raise RuntimeError(f"{name} exited with code {child.returncode}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"{name} did not become ready within {timeout}s")


def start_pc1(port, pc4_port, stop, ready, errors):
    # Import the same receiver/forwarding classes used by the Main Server.
    sys.path.insert(0, str(MAIN_DIR))
    from app.services.tracking import PC2TCPReceiver, TrackingBridge

    class VisibleBridge(TrackingBridge):
        def __init__(self):
            super().__init__("", 115200, "CAM01", "127.0.0.1", pc4_port)
            self.last_report = 0.0

        def forward(self, obs):
            result = super().forward(obs)
            now = time.monotonic()
            if now - self.last_report >= 1.0 or not result["accepted"]:
                print(f"[PC1] {obs['state']} face={obs.get('face_detected')} "
                      f"bbox={obs.get('x1'), obs.get('y1'), obs.get('x2'), obs.get('y2')} "
                      f"forwarded={result['forwarded']} ({result['reason']})", flush=True)
                self.last_report = now
            return result

    async def serve():
        bridge = VisibleBridge()
        receiver = PC2TCPReceiver(bridge, port)
        bridge.start()
        try:
            await receiver.start()
            print(f"[PC1] Listening for PC2 on TCP {port}; PC4 TCP {pc4_port}", flush=True)
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(0.1)
        finally:
            await receiver.stop()
            bridge.close()

    try:
        asyncio.run(serve())
    except Exception as exc:
        errors.put(exc)
        ready.set()


def main():
    args = arguments()
    if sys.platform != "linux":
        raise RuntimeError("This test script is for Linux desktop PCs. Use Linux or WSL with hardware access.")
    ensure_packages(args)
    serial_port = find_serial_port(args.serial_port)
    model = model_path(args.model)
    check_local_ports((args.pc1_port, args.pc4_port, args.video_port))
    print(f"[SETUP] Arduino={serial_port} camera={args.camera} model={model}", flush=True)

    children = []
    stop = threading.Event()
    ready = threading.Event()
    errors = queue.Queue()
    pc1_thread = None
    try:
        bridge, bridge_ready = start_child("PC4 bridge",
            [DEVICE_DIR / "pc4_bridge.py", "--listen-host", "127.0.0.1",
             "--serial-port", serial_port, "--tcp-port", args.pc4_port],
            DEVICE_DIR, "Arduino 已连接")
        children.append(bridge)
        wait_ready("PC4 bridge / Arduino", bridge, bridge_ready, 15)

        pc1_thread = threading.Thread(target=start_pc1,
            args=(args.pc1_port, args.pc4_port, stop, ready, errors), daemon=True)
        pc1_thread.start()
        if not ready.wait(10):
            raise RuntimeError("PC1 tracking receiver did not start")
        if not errors.empty():
            raise errors.get()

        detector, detector_ready = start_child("PC2 detector",
            [WEBCAM_DIR / "detect_yolov3.py", "--frame-listen-host", "127.0.0.1",
             "--frame-listen-port", args.video_port, "--pc1-host", "127.0.0.1",
             "--pc1-port", args.pc1_port, "--model", model, "--debug"],
            WEBCAM_DIR, "waiting for PC4")
        children.append(detector)
        wait_ready("PC2 detector", detector, detector_ready, 90)

        camera, _ = start_child("PC4 camera",
            [DEVICE_DIR / "camera_stream_sender.py", "--camera", args.camera,
             "--pc2-host", "127.0.0.1", "--pc2-port", args.video_port],
            DEVICE_DIR)
        children.append(camera)
        print("[TEST] Move your face left/right/up/down. Press q in the video window to stop.",
              flush=True)
        while True:
            if not errors.empty():
                raise errors.get()
            for name, child in zip(("PC4 bridge", "PC2 detector", "PC4 camera"), children):
                if child.poll() is not None:
                    if name == "PC2 detector" and child.returncode == 0:
                        return
                    raise RuntimeError(f"{name} exited with code {child.returncode}")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[TEST] Stopping", flush=True)
    finally:
        stop.set()
        # Stop the camera first so no more frames arrive, then the detector and bridge.
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if pc1_thread:
            pc1_thread.join(timeout=3)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
