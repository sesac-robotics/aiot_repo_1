"""Kit PC：单个 TCP 客户端的 JSON Lines 与 Arduino 文本串口桥。"""

import argparse
import json
import select
import serial
import socket
import time

MAX_TCP_LINE = 512
MAX_SERIAL_LINE = 128
RECONNECT_INTERVAL = 2.0


def number(value, low, high):
    return type(value) is int and low <= value <= high


def encode_command(item):
    """严格验证 TCP 包；不合法的命令不会写到 Arduino。"""
    if not isinstance(item, dict):
        return None
    if item.get("type") == "TRACK":
        keys = ("body_detected", "face_detected", "x1", "y1", "x2",
                "y2", "frame_width", "frame_height")
        if set(item) != set(keys) | {"type"}:
            return None
        b, f, x1, y1, x2, y2, w, h = (item[k] for k in keys)
        if not (number(b, 0, 1) and number(f, 0, 1) and f <= b and
                number(w, 1, 4096) and number(h, 1, 4096)):
            return None
        if b:
            if not (number(x1, 0, w-1) and number(y1, 0, h-1) and
                    number(x2, x1+1, w) and number(y2, y1+1, h)):
                return None
        elif (x1, y1, x2, y2) != (-1, -1, -1, -1):
            return None
        return f"TRACK,{b},{f},{x1},{y1},{x2},{y2},{w},{h}\n"
    if item.get("type") == "DOOR" and set(item) == {
            "type", "action", "request_id"}:
        action, request_id = item["action"], item["request_id"]
        if action in ("TEST", "OPEN") and number(request_id, 1, 2147483647):
            return f"DOOR,{action},{request_id}\n"
    return None


def encode_text(raw):
    """Allow PC1 Main Server to send the Arduino text protocol directly."""
    line = raw.decode("ascii").strip()
    parts = line.split(",")
    if parts == ["N"] or parts == ["N", "N"]:
        return "N\n"
    if len(parts) == 9 and parts[0] == "TRACK":
        try:
            body, face, x1, y1, x2, y2, width, height = map(int, parts[1:])
        except ValueError:
            return None
        if body not in (0, 1) or face not in (0, 1) or face > body:
            return None
        if not (1 <= width <= 4096 and 1 <= height <= 4096):
            return None
        if body == 0 and (x1, y1, x2, y2) != (-1, -1, -1, -1):
            return None
        if body and not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return None
        return line + "\n"
    if len(parts) == 3 and parts[0] == "DOOR" and parts[1] in ("TEST", "OPEN"):
        try:
            request_id = int(parts[2])
        except ValueError:
            return None
        if 1 <= request_id <= 2147483647:
            return line + "\n"
    return None


def decode_feedback(raw):
    try:
        parts = raw.decode("ascii").strip().split(",")
        if len(parts) != 3 or parts[0] not in ("ACK", "EVENT", "ERROR"):
            return None
        request_id = int(parts[1])
        if not number(request_id, 1, 2147483647) or not parts[2].isupper():
            return None
        return {"type": parts[0], "request_id": request_id,
                "result": parts[2]}
    except (UnicodeError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--tcp-port", type=int, default=8765)
    parser.add_argument("--serial-port", default="/dev/ttyACM0")
    args = parser.parse_args()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.listen_host, args.tcp_port))
    listener.listen(1)
    listener.setblocking(False)
    client = None
    serial_port = None
    tcp_buffer = bytearray()
    serial_buffer = bytearray()
    reply_queue = bytearray()
    next_connect = 0.0
    print(f"Kit Bridge 监听 {args.listen_host}:{args.tcp_port}")
    try:
        while True:
            now = time.monotonic()
            if serial_port is None and now >= next_connect:
                try:
                    serial_port = serial.Serial(args.serial_port, 115200,
                                                timeout=0, write_timeout=0.05)
                    serial_buffer.clear()
                    print(f"Arduino 已连接 {args.serial_port}")
                except (OSError, serial.SerialException) as exc:
                    print(f"Arduino 未连接：{exc}")
                    next_connect = now + RECONNECT_INTERVAL
            readers = [listener] + ([client] if client else [])
            writers = [client] if client and reply_queue else []
            ready_r, ready_w, _ = select.select(readers, writers, [], 0.01)
            if listener in ready_r:
                new_client, address = listener.accept()
                new_client.setblocking(False)
                if client:
                    new_client.close()
                else:
                    client = new_client
                    tcp_buffer.clear()
                    reply_queue.clear()
                    print(f"控制端已连接 {address}")
            if client and client in ready_r:
                try:
                    chunk = client.recv(4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    client.close()
                    client = None
                    tcp_buffer.clear()
                    reply_queue.clear()
                    print("控制端已断开")
                else:
                    tcp_buffer.extend(chunk)
                    if len(tcp_buffer) > 4096:
                        client.close()
                        client = None
                        tcp_buffer.clear()
                        reply_queue.clear()
                    while client and b"\n" in tcp_buffer:
                        raw, _, rest = tcp_buffer.partition(b"\n")
                        tcp_buffer = bytearray(rest)
                        if len(raw) > MAX_TCP_LINE:
                            continue
                        try:
                            item = json.loads(raw)
                            line = encode_command(item)
                        except (ValueError, UnicodeError):
                            item = {}
                            line = encode_text(raw)
                        if line is None:
                            continue
                        if serial_port is None:
                            # 断线时明确丢弃；绝不缓存 OPEN 等待重连后执行。
                            if item.get("type") == "DOOR":
                                reply_queue.extend((json.dumps({
                                    "type": "ERROR", "request_id": item["request_id"],
                                    "result": "SERIAL_UNAVAILABLE"}) + "\n").encode("ascii"))
                            continue
                        try:
                            data = line.encode("ascii")
                            if serial_port.write(data) != len(data):
                                raise serial.SerialTimeoutException("串口写入不完整")
                        except (OSError, serial.SerialException,
                                serial.SerialTimeoutException):
                            serial_port.close()
                            serial_port = None
                            serial_buffer.clear()
                            next_connect = time.monotonic() + RECONNECT_INTERVAL
                            # 写失败的 OPEN 不自动重试；结果由上层按超时处理。
            if serial_port is not None:
                try:
                    count = min(serial_port.in_waiting, 1024)
                    if count:
                        serial_buffer.extend(serial_port.read(count))
                except (OSError, serial.SerialException):
                    serial_port.close()
                    serial_port = None
                    serial_buffer.clear()
                    next_connect = time.monotonic() + RECONNECT_INTERVAL
                if len(serial_buffer) > 1024:
                    serial_buffer.clear()
                while b"\n" in serial_buffer:
                    raw, _, rest = serial_buffer.partition(b"\n")
                    serial_buffer = bytearray(rest)
                    if len(raw) > MAX_SERIAL_LINE:
                        continue
                    feedback = decode_feedback(raw)
                    if feedback and client:
                        reply_queue.extend((json.dumps(feedback, separators=(",", ":"))
                                            + "\n").encode("ascii"))
            if client and client in ready_w and reply_queue:
                try:
                    sent = client.send(reply_queue)
                    del reply_queue[:sent]
                except OSError:
                    client.close()
                    client = None
                    reply_queue.clear()
            if len(reply_queue) > 4096 and client:
                client.close()
                client = None
                reply_queue.clear()
    except KeyboardInterrupt:
        pass
    finally:
        if client:
            client.close()
        if serial_port:
            serial_port.close()
        listener.close()


if __name__ == "__main__":
    main()
