"""Receive PC1 V-protocol commands over TCP and forward them to Arduino.

Wire format (one ASCII line per command):
    V,body,face,dx,dy,doorPulse\n

TCP is a byte stream, so recv() may return a partial line or several lines.
This bridge buffers received bytes and forwards only complete, validated lines.
"""

from __future__ import annotations

import argparse
import select
import socket
import time

try:
    import serial
except ModuleNotFoundError:  # Allows --help and protocol tests before installation.
    serial = None


SERIAL_ERRORS = ((OSError,) if serial is None else
                 (OSError, serial.SerialException, serial.SerialTimeoutException))


DEFAULT_ALLOWED_IP = "192.168.15.130"
DEFAULT_TCP_PORT = 8765
DEFAULT_SERIAL_PORT = "/dev/ttyACM0"
BAUD_RATE = 115200
RECV_SIZE = 4096
MAX_LINE_BYTES = 63  # Arduino input buffer is char line[64].
MAX_TCP_BUFFER = 4096
RECONNECT_INTERVAL_SECONDS = 2.0
STOP_COMMAND = b"V,0,0,0,0,0\n"


def encode_valid_command(raw: bytes) -> bytes | None:
    """Return a canonical Arduino command, or None for invalid input."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None

    if text.endswith("\r"):
        text = text[:-1]
    parts = text.split(",")
    if len(parts) != 6 or parts[0] != "V":
        return None

    try:
        body, face, dx, dy, door_pulse = map(int, parts[1:])
    except ValueError:
        return None

    if body not in (0, 1) or face not in (0, 1):
        return None
    if not (-1000 <= dx <= 1000 and -1000 <= dy <= 1000):
        return None
    if door_pulse not in (0, 1):
        return None

    return f"V,{body},{face},{dx},{dy},{door_pulse}\n".encode("ascii")


def close_client(client: socket.socket | None) -> None:
    if client is not None:
        try:
            client.close()
        except OSError:
            pass


def close_serial(serial_port) -> None:
    if serial_port is not None:
        try:
            serial_port.close()
        except SERIAL_ERRORS:
            pass


def write_serial(serial_port, command: bytes) -> bool:
    try:
        written = serial_port.write(command)
        if written != len(command):
            print("[SERIAL] write failed: incomplete serial write")
            return False
        serial_port.flush()
        return True
    except SERIAL_ERRORS as exc:
        print(f"[SERIAL] write failed: {exc}")
        return False


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--listen-host",
        default="0.0.0.0",
        help="PC4 address to bind (default: all local network interfaces)",
    )
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT)
    parser.add_argument(
        "--allowed-ip",
        default=DEFAULT_ALLOWED_IP,
        help="Only accept this PC1 source IP; use 'any' to disable the check",
    )
    parser.add_argument("--serial-port", default=DEFAULT_SERIAL_PORT)
    parser.add_argument("--baud", type=int, default=BAUD_RATE)
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if serial is None:
        raise SystemExit("pyserial is required: python3 -m pip install pyserial")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.listen_host, args.tcp_port))
    listener.listen(1)
    listener.setblocking(False)

    client: socket.socket | None = None
    client_address: tuple[str, int] | None = None
    tcp_buffer = bytearray()
    serial_buffer = bytearray()
    serial_port = None
    next_serial_connect = 0.0

    print(f"[TCP] listening on {args.listen_host}:{args.tcp_port}")
    print(f"[TCP] allowed PC1 source IP: {args.allowed_ip}")
    print(f"[SERIAL] target: {args.serial_port} at {args.baud} baud")

    try:
        while True:
            now = time.monotonic()
            if serial_port is None and now >= next_serial_connect:
                try:
                    serial_port = serial.Serial(
                        args.serial_port,
                        args.baud,
                        timeout=0,
                        write_timeout=0.1,
                    )
                    serial_buffer.clear()
                    print(f"[SERIAL] connected: {args.serial_port}")
                except SERIAL_ERRORS as exc:
                    print(f"[SERIAL] waiting for Arduino: {exc}")
                    next_serial_connect = now + RECONNECT_INTERVAL_SECONDS

            readers = [listener]
            if client is not None:
                readers.append(client)
            ready, _, _ = select.select(readers, [], [], 0.02)

            if listener in ready:
                new_client, address = listener.accept()
                peer_ip = address[0]
                if args.allowed_ip != "any" and peer_ip != args.allowed_ip:
                    print(f"[TCP] rejected unexpected source: {address}")
                    new_client.close()
                elif client is not None:
                    print(f"[TCP] rejected second client: {address}")
                    new_client.close()
                else:
                    new_client.setblocking(False)
                    new_client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    client = new_client
                    client_address = address
                    tcp_buffer.clear()
                    print(f"[TCP] PC1 connected: {address}")

            if client is not None and client in ready:
                try:
                    chunk = client.recv(RECV_SIZE)
                except OSError as exc:
                    print(f"[TCP] receive failed: {exc}")
                    chunk = b""

                if not chunk:
                    print(f"[TCP] PC1 disconnected: {client_address}")
                    if serial_port is not None:
                        write_serial(serial_port, STOP_COMMAND)
                    close_client(client)
                    client = None
                    client_address = None
                    tcp_buffer.clear()
                else:
                    tcp_buffer.extend(chunk)
                    if len(tcp_buffer) > MAX_TCP_BUFFER:
                        print("[TCP] receive buffer overflow; closing client")
                        if serial_port is not None:
                            write_serial(serial_port, STOP_COMMAND)
                        close_client(client)
                        client = None
                        client_address = None
                        tcp_buffer.clear()

                while client is not None and b"\n" in tcp_buffer:
                    raw_line, _, remaining = tcp_buffer.partition(b"\n")
                    tcp_buffer = bytearray(remaining)
                    if len(raw_line) > MAX_LINE_BYTES:
                        print(f"[DROP] line too long: {len(raw_line)} bytes")
                        continue

                    command = encode_valid_command(raw_line)
                    if command is None:
                        print(f"[DROP] invalid command: {raw_line!r}")
                        continue
                    if serial_port is None:
                        print(f"[DROP] Arduino unavailable: {command!r}")
                        continue

                    if write_serial(serial_port, command):
                        print(f"[FORWARD] {command.decode('ascii').rstrip()}")
                    else:
                        close_serial(serial_port)
                        serial_port = None
                        serial_buffer.clear()
                        next_serial_connect = time.monotonic() + RECONNECT_INTERVAL_SECONDS

            if serial_port is not None:
                try:
                    waiting = min(serial_port.in_waiting, 1024)
                    if waiting:
                        serial_buffer.extend(serial_port.read(waiting))
                except SERIAL_ERRORS as exc:
                    print(f"[SERIAL] disconnected: {exc}")
                    close_serial(serial_port)
                    serial_port = None
                    serial_buffer.clear()
                    next_serial_connect = time.monotonic() + RECONNECT_INTERVAL_SECONDS

                if len(serial_buffer) > 1024:
                    serial_buffer.clear()
                while b"\n" in serial_buffer:
                    feedback, _, remaining = serial_buffer.partition(b"\n")
                    serial_buffer = bytearray(remaining)
                    print(f"[ARDUINO] {feedback.decode('ascii', errors='replace').rstrip()}")

    except KeyboardInterrupt:
        print("\n[STOP] bridge stopped by user")
    finally:
        if serial_port is not None:
            write_serial(serial_port, STOP_COMMAND)
        close_client(client)
        close_serial(serial_port)
        listener.close()


if __name__ == "__main__":
    main()
