"""PC4 UDP JPEG frame receiver. Newest complete frame only."""
import math
import socket
import struct
import time
import cv2
import numpy as np

MAGIC = b'PC4F'
VERSION = 1
HEADER = struct.Struct('!4sBIHHI')  # magic,version,frame_id,index,count,jpeg_length
CHUNK_SIZE = 1200
MAX_JPEG = 400_000
MAX_PARTS = math.ceil(MAX_JPEG / CHUNK_SIZE)

class UDPFrameReceiver:
    def __init__(self, host='0.0.0.0', port=8766, allow_ip=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.settimeout(0.08)
        self.allow_ip = allow_ip
        self.pending = {}
        self.last_id = None
        self.last_frame_at = 0.0
        print(f'[UDP] Listening on {host}:{port}; allow_ip={allow_ip or "any"}', flush=True)

    def read(self):
        deadline = time.monotonic() + 0.10
        while time.monotonic() < deadline:
            try:
                packet, addr = self.sock.recvfrom(1500)
            except socket.timeout:
                break
            if self.allow_ip and addr[0] != self.allow_ip:
                continue
            if len(packet) < HEADER.size:
                continue
            magic, version, fid, index, count, length = HEADER.unpack_from(packet)
            if magic != MAGIC or version != VERSION or not 1 <= count <= MAX_PARTS or index >= count or not 1 <= length <= MAX_JPEG:
                continue
            if self.last_id is not None and ((fid - self.last_id) & 0xffffffff) >= 0x80000000:
                continue
            part = packet[HEADER.size:]
            if not part or len(part) > CHUNK_SIZE:
                continue
            now = time.monotonic()
            for key, entry in list(self.pending.items()):
                if now - entry['time'] > 0.5:
                    del self.pending[key]
            key = (addr[0], addr[1], fid)
            entry = self.pending.get(key)
            if entry is None:
                if len(self.pending) >= 4:
                    self.pending.pop(next(iter(self.pending)))
                entry = {'time':now, 'parts':{}, 'count':count, 'length':length}
                self.pending[key] = entry
            if entry['count'] != count or entry['length'] != length:
                del self.pending[key]
                continue
            entry['parts'][index] = part
            if len(entry['parts']) == count:
                payload = b''.join(entry['parts'][i] for i in range(count))
                del self.pending[key]
                if len(payload) != length or not payload.startswith(b'\xff\xd8'):
                    continue
                frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                self.last_id = fid
                self.last_frame_at = time.monotonic()
                # Never replay the previously completed frame; only return a fresh one.
                return True, frame
        return False, None

    def release(self):
        self.sock.close()
