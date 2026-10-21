"""Wire-level checks for the unified JPEG + analysis publisher."""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from central_delivery import HttpFrameAnalysisPublisher


class _Receiver(BaseHTTPRequestHandler):
    requests: list[tuple[dict[str, str], bytes]] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers["Content-Length"])
        type(self).requests.append((dict(self.headers), self.rfile.read(length)))
        self.send_response(201)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class HttpFrameAnalysisPublisherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/analysis"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        _Receiver.requests = []

    def test_raw_jpeg_and_original_coordinate_metadata_share_one_request(self) -> None:
        publisher = HttpFrameAnalysisPublisher(self.url, max_width=640, max_fps=5)
        source = np.zeros((360, 1280, 3), dtype=np.uint8)
        metadata = {
            "frame_id": 42,
            "source_size": [1280, 360],
            "coordinate_system": "original_frame_pixels",
            "persons": [{"track_id": 3, "bbox_xyxy": [200, 20, 600, 340]}],
            "event": {"current_states": [], "new_events": []},
            "image": {"source_size": [1280, 360]},
        }

        self.assertEqual(publisher.publish(source, metadata), 201)
        self.assertEqual(len(_Receiver.requests), 1)
        headers, body = _Receiver.requests[0]
        self.assertIn("multipart/form-data; boundary=TSAFEFrame", headers["Content-Type"])
        self.assertIn(b'"message_type":"frame_analysis"', body)
        self.assertIn(b'"source_size":[1280,360]', body)
        self.assertIn(b'"display_size":[640,180]', body)
        self.assertIn(b'"bbox_xyxy":[200,20,600,340]', body)
        self.assertIn(b"\xff\xd8", body)
        self.assertIn(b"\xff\xd9", body)
        self.assertIsNone(publisher.publish(source, metadata))


if __name__ == "__main__":
    unittest.main()
