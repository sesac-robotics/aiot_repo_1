"""Send the latest coordinate string to PC1 over TCP."""

import socket
import threading
import time


class CoordinateSender:
    def __init__(
        self,
        host,
        port,
        reconnect_interval=2.0,
        max_age=0.5,
    ):
        self.host = host
        self.port = port
        self.reconnect_interval = reconnect_interval
        self.max_age = max_age

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._pending = None

        self._thread = threading.Thread(
            target=self._run,
            name="coordinate-sender",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def publish(self, message):
        """Replace the pending message with the newest coordinates."""
        if self._stop.is_set():
            return

        payload = message.encode("ascii")

        with self._lock:
            self._pending = (time.monotonic(), payload)

        self._wake.set()

    def close(self):
        """Stop the worker; attempt to send N,N before disconnecting."""
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2.0)

    def _take_latest(self):
        with self._lock:
            latest = self._pending
            self._pending = None
        return latest

    def _run(self):
        connection = None

        try:
            while not self._stop.is_set():
                if connection is None:
                    try:
                        connection = socket.create_connection(
                            (self.host, self.port),
                            timeout=0.5,
                        )
                        connection.settimeout(0.3)
                        connection.setsockopt(
                            socket.IPPROTO_TCP,
                            socket.TCP_NODELAY,
                            1,
                        )
                        print(
                            f"[TCP] Connected: {self.host}:{self.port}",
                            flush=True,
                        )
                    except OSError as exc:
                        if connection is not None:
                            connection.close()
                            connection = None
                        print(
                            f"[TCP] Connection failed: {exc}",
                            flush=True,
                        )
                        if self._stop.wait(self.reconnect_interval):
                            break
                        continue

                self._wake.wait(timeout=0.1)
                self._wake.clear()

                if self._stop.is_set():
                    break

                latest = self._take_latest()
                if latest is None:
                    continue

                created_at, payload = latest

                # Do not send coordinates left over from a stalled camera.
                if time.monotonic() - created_at > self.max_age:
                    continue

                try:
                    connection.sendall(payload)
                except OSError as exc:
                    print(f"[TCP] Disconnected: {exc}", flush=True)
                    connection.close()
                    connection = None

                    if self._stop.wait(self.reconnect_interval):
                        break
        finally:
            if connection is not None:
                try:
                    connection.sendall(b"N,N,-1,-1,-1,-1,-1,-1,-1,-1\n")
                except OSError:
                    pass
                finally:
                    connection.close()

            print("[TCP] Sender stopped", flush=True)