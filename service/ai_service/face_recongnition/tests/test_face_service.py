"""Capture queue scheduling tests, without GPU, models or a production server."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import sys

SOURCE = Path(__file__).resolve().parents[1] / 'app/face_service.py'


class StopLoop(BaseException):
    pass


class FaceServiceTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('face_service_test_module', SOURCE)
        self.service = importlib.util.module_from_spec(spec)
        common = SimpleNamespace(get_model=Mock(), load_image=Mock(), single_face=Mock(), unit=Mock())
        with patch.dict(sys.modules, {'common': common}):
            spec.loader.exec_module(self.service)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, folder in [('CAPTURE_DIR', 'captures'), ('PROCESSED_DIR', 'processed'),
                             ('RESULT_DIR', 'output'), ('OUTBOX_DIR', 'outbox')]:
            path = root / folder
            path.mkdir()
            setattr(self.service, name, path)
        self.service.PC1_URL = 'http://unused.invalid/identify'

    def loop(self, iterations, send):
        clock = Mock(wraps=time)
        # Less than five seconds since the initial empty queue check throughout.
        clock.monotonic.return_value = 100.0
        clock.sleep.side_effect = [None] * (iterations - 1) + [StopLoop()]
        result = {'status': 'OK', 'model': 'buffalo_l', 'embedding_dim': 512,
                  'embedding': [1.0] + [0.0] * 511, 'elapsed_ms': 1}
        with patch.object(self.service, 'time', clock), \
             patch.object(self.service, 'extract', return_value=result) as extract, \
             patch.object(self.service, 'send_pending', side_effect=send):
            with self.assertRaises(StopLoop):
                self.service.run()
        return clock, extract

    def test_new_result_sends_without_waiting_for_retry_timer(self):
        import os
        capture = self.service.CAPTURE_DIR / 'face.jpg'
        capture.write_bytes(b'image bytes are decoded by mocked extractor')
        os.utime(capture, (time.time() - 2, time.time() - 2))
        seen = []
        def send():
            seen.append(list(self.service.OUTBOX_DIR.glob('*.json')))
        clock, extract = self.loop(2, send)
        extract.assert_called_once()
        self.assertEqual([len(paths) for paths in seen], [0, 1])
        self.assertEqual(clock.sleep.call_args.args, (.1,))
        self.assertFalse(capture.exists())
        self.assertEqual(len(list(self.service.PROCESSED_DIR.iterdir())), 1)

    def test_recent_capture_still_waits_for_safe_file_age(self):
        capture = self.service.CAPTURE_DIR / 'face.jpg'
        capture.write_bytes(b'not ready yet')
        clock = Mock(wraps=time)
        clock.monotonic.return_value = 100.0
        clock.time_ns.return_value = capture.stat().st_mtime_ns + 200_000_000
        clock.sleep.side_effect = [None, StopLoop()]
        with patch.object(self.service, 'time', clock), \
             patch.object(self.service, 'extract') as extract, \
             patch.object(self.service, 'send_pending') as send:
            with self.assertRaises(StopLoop):
                self.service.run()
        extract.assert_not_called()
        send.assert_called_once()
        self.assertTrue(capture.exists())

    def test_failed_queue_does_not_retry_every_poll(self):
        self.service.OUTBOX_DIR.joinpath('queued.json').write_text('{}')
        calls = []
        self.loop(3, lambda: calls.append('retry'))
        self.assertEqual(calls, ['retry'])

    def test_http_failure_keeps_durable_result_and_success_removes_it(self):
        path = self.service.OUTBOX_DIR / 'event.json'
        path.write_text(json.dumps({'event_id': 'event'}))
        with patch.object(self.service, 'urlopen', side_effect=OSError('offline')):
            self.service.send_pending()
        self.assertTrue(path.exists())
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(self.service, 'urlopen', return_value=response):
            self.service.send_pending()
        self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
