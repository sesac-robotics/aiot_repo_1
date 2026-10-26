"""PC4 USB camera preview + UDP JPEG frame sender (PC2 receiver: udp_camera.py)."""
import argparse
import math
import socket
import struct
import time
import cv2
import os
from pathlib import Path

def read_config():
    values = {"PC2_HOST": "192.168.15.129", "PC2_UDP_PORT": "8766", "PC4_FPS": "15", "PC4_JPEG_QUALITY": "65"}
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (x.strip() for x in line.split("=", 1))
            if key in values:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
                    value = value[1:-1]
                values[key] = value
    for key in values:
        if key in os.environ:
            values[key] = os.environ[key]
    return values

MAGIC = b'PC4F'
VERSION = 1
HEADER = struct.Struct('!4sBIHHI')
CHUNK_SIZE = 1200
MAX_JPEG = 400_000

def main():
    config = read_config()
    parser = argparse.ArgumentParser()
    parser.add_argument('--pc2-ip', default=config['PC2_HOST'], help='PC2 LAN/VPN IP (from .env by default)')
    parser.add_argument('--port', type=int, default=int(config['PC2_UDP_PORT']))
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--fps', type=float, default=float(config['PC4_FPS']))
    parser.add_argument('--quality', type=int, default=int(config['PC4_JPEG_QUALITY']))
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--no-preview', action='store_true')
    args = parser.parse_args()
    if not args.pc2_ip:
        parser.error("PC2_HOST가 없습니다. .env 설정 또는 --pc2-ip 옵션이 필요합니다.")
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    if args.fps <= 0 or not 1 <= args.quality <= 100:
        parser.error('--fps > 0, --quality in [1,100] required')
    cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise SystemExit(f'[PC4] Cannot open /dev/video{args.camera}')
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fid = 0
    interval = 1.0 / args.fps
    last_send = 0.0
    sent = 0
    print(f'[PC4] UDP JPEG -> {args.pc2_ip}:{args.port}, {args.fps:g} fps', flush=True)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print('[PC4] Camera read failed', flush=True)
                break
            now = time.monotonic()
            if now - last_send >= interval:
                # Force a common coordinate system on PC2; no letterboxing or mirroring.
                if frame.shape[1] != args.width or frame.shape[0] != args.height:
                    frame = cv2.resize(frame, (args.width, args.height))
                encoded, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
                if encoded:
                    payload = jpg.tobytes()
                    if len(payload) <= MAX_JPEG:
                        count = math.ceil(len(payload) / CHUNK_SIZE)
                        for idx in range(count):
                            chunk = payload[idx*CHUNK_SIZE:(idx+1)*CHUNK_SIZE]
                            header = HEADER.pack(MAGIC, VERSION, fid, idx, count, len(payload))
                            sock.sendto(header + chunk, (args.pc2_ip, args.port))
                        if sent % 30 == 0:
                            print(f'[PC4] frame={fid} jpeg={len(payload)} bytes packets={count}', flush=True)
                        sent += 1
                        fid = (fid + 1) & 0xffffffff
                    else:
                        print('[PC4] JPEG too large; reduce quality', flush=True)
                last_send = now
            if not args.no_preview:
                cv2.imshow('PC4 Camera (UDP to PC2)', frame)
                if cv2.waitKey(1) & 0xff == ord('q'):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        sock.close()
        if not args.no_preview:
            cv2.destroyAllWindows()

if __name__ == '__main__':
    main()