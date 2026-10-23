"""Real HTTP + headless Qt tests, with temporary SQLite and an in-memory PC1 API."""
import json
import os
from pathlib import Path
import shutil
import socket
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.append(str(BASE.parents[2] / 'service/main_service'))
import run
from device_client import GUIClient
from event_bridge import Bridge
from pc1_db_adapter import Adapter, PC1
from start_connected import connection_env, check_server
from app.services.gui_notify import GuiNotifier


class ConnectedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'GUI'
        shutil.copytree(BASE, self.root, ignore=shutil.ignore_patterns(
            '*.sqlite3*', 'gui_event.token', 'pc1_adapter_results.*', '__pycache__', 'tests'))
        self.env = patch.dict(os.environ, {'GUI_EVENT_PORT': '0', 'GUI_DB_MODE': 'external',
                                          'GUI_EVENT_TOKEN': ''})
        self.env.start()
        self.old_root = run.ROOT
        run.ROOT = self.root
        self.a = run.App()
        self.bridge = Bridge(self.a.events, self.a.store, self.a.request_snapshot, self.root, 0)
        self.gui_url = 'http://127.0.0.1:' + str(self.bridge.server.server_address[1])
        self.gui = GUIClient(self.gui_url, self.bridge.token)
        self.rows = []
        self.seen = []
        self.failure = 0
        self.required_token = ''
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                if outer.required_token and self.headers.get('Authorization') != 'Bearer ' + outer.required_token:
                    self.reply(401, {'detail': 'Unauthorized'})
                    return False
                if outer.failure:
                    self.reply(outer.failure, {'detail': 'Unavailable'})
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                if self.path == '/health':
                    self.reply(200, {'db': 'ok', 'gui_events': {'enabled': True, 'url': outer.gui_url + '/events'}})
                else:
                    self.reply(200, {'items': outer.rows})

            def do_POST(self):
                if not self.authorized():
                    return
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.seen.append((self.path, payload))
                prefix = {'Staff': 'S', 'Patient': 'P', 'Admin': 'A'}[payload['role']]
                row = dict(payload, person_id=prefix + '0001', created_at='2026-10-23T00:00:00Z')
                outer.rows.append(row)
                self.reply(201, row)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.server_url = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.adapter = Adapter(self.gui, PC1(self.server_url, token=''), self.root / 'data/results.json')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.bridge.close()
        for window in self.a.windows.values():
            window.close()
        self.a.app.processEvents()
        run.ROOT = self.old_root
        self.env.stop()
        self.tmp.cleanup()

    def pump(self, condition=lambda: True):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            self.a.app.processEvents()
            if condition():
                return
            time.sleep(.01)
        self.fail('GUI did not receive the HTTP result')

    def fill(self, role='Staff'):
        self.a.add()
        form = self.a.windows['register']
        for key, value in {'name': 'Test person', 'birth': '1990-01-01', 'height': '170', 'weight': '60'}.items():
            getattr(form, key).setText(value)
        form.role.setCurrentText(role)
        self.assertTrue(form.id.isReadOnly())
        self.assertEqual(form.id.text(), '')

    def test_registration_uses_server_id_and_external_cache(self):
        self.assertEqual(self.a.store.people(), [])
        self.assertEqual(self.a.store.path.name, 'gui.external.sqlite3')
        self.fill()
        self.assertTrue(self.a.save())
        self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertEqual(self.a.windows['register'].id.text(), 'S0001')
        self.assertEqual(self.a.store.people()[0]['id'], 'S0001')
        self.assertEqual(self.seen[0][0], '/api/v1/patients')
        self.assertNotIn('id', self.seen[0][1])
        self.assertNotIn('person_id', self.seen[0][1])
        self.assertIn('server', self.a.windows['register'].dbStatus.text())
        self.assertIn('Waiting for card', self.a.windows['register'].cardWriteStatus.text())
        self.adapter.step()
        self.pump()
        self.assertEqual(len(self.rows), 1)

    def test_failed_save_can_retry_without_fake_local_person(self):
        self.fill('Patient')
        self.failure = 503
        self.a.save()
        self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertEqual(self.a.store.people(), [])
        self.assertTrue(self.a.windows['register'].saveButton.isEnabled())
        self.assertIn('Failed', self.a.windows['register'].dbStatus.text())
        self.failure = 0
        self.a.save()
        self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertEqual(self.a.windows['register'].id.text(), 'P0001')

    def test_connection_refused_is_reported_and_can_retry(self):
        self.fill()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            unused_port = probe.getsockname()[1]
        self.adapter.pc1 = PC1('http://127.0.0.1:' + str(unused_port), token='')
        self.a.save()
        self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertEqual(self.a.store.people(), [])
        self.assertIn('connection/response failed', self.a.store.events()[0]['details'])
        self.adapter.pc1 = PC1(self.server_url, token='')
        self.a.save()
        self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertEqual(self.a.windows['register'].id.text(), 'S0001')

    def test_gui_delivery_retry_does_not_create_duplicate_person(self):
        self.fill()
        self.a.save()
        send = self.gui.send
        def interrupted(event):
            if event['type'] == 'db_result':
                raise OSError('GUI connection interrupted after server save')
            return send(event)
        with patch.object(self.gui, 'send', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.adapter.step()
        self.assertEqual(len(self.rows), 1)
        # The adapter can even restart: the cached response survives on disk.
        self.adapter = Adapter(self.gui, PC1(self.server_url, token=''), self.adapter.cache_path)
        self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.a.windows['register'].id.text(), 'S0001')

    def test_missing_server_id_is_not_saved_as_pending_id(self):
        self.fill()
        self.a.save()
        with patch.object(self.adapter.pc1, 'save', return_value={}):
            self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertEqual(self.a.store.people(), [])
        self.assertTrue(self.a.windows['register'].saveButton.isEnabled())
        self.assertIn('missing person_id', self.a.store.events()[0]['details'])

    def test_authorization_error_then_correct_token(self):
        self.required_token = 'test-admin'
        self.fill('Admin')
        self.a.save()
        self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertIn('PC1 HTTP 401', self.a.store.events()[0]['details'])
        self.assertEqual(self.rows, [])
        self.adapter.pc1 = PC1(self.server_url, token='test-admin')
        self.a.save()
        self.adapter.step()
        self.pump(lambda: not self.a.request_snapshot())
        self.assertEqual(self.a.windows['register'].id.text(), 'A0001')

    def test_server_notifier_displays_staff_patient_and_unknown(self):
        notifier = GuiNotifier()
        notifier.url = self.gui_url + '/events'
        notifier._token = lambda: self.bridge.token
        threading.Thread(target=notifier._worker, daemon=True).start()
        window = self.a.windows['door_access']
        cases = [('MATCHED', 'S0001', 'Staff name', 'Staff', True),
                 ('MATCHED', 'P0001', 'Patient name', 'Patient', False),
                 ('UNKNOWN', None, None, 'Unknown', False)]
        for index, (result, pid, name, role, allowed) in enumerate(cases):
            notifier.on_event('face_result', {'event_id': 'test-' + str(index), 'result': result,
                                             'person_id': pid, 'name': name, 'role': role, 'allowed': allowed})
            self.pump(lambda: len(self.a.store.events()) == index + 1)
            self.assertEqual(window.identityName.text(), name or 'Unknown')
            self.assertEqual(window.identityId.text(), 'ID  ' + (pid or '—'))
            self.assertEqual(window.identityRole.text(), role)
            self.assertEqual(window.accessStatus.text(), 'Allowed' if allowed else 'Denied')
            self.assertEqual(window.doorState.text(), 'UNKNOWN')
        self.assertEqual(notifier.sent, 3)
        self.assertEqual(notifier.failed, 0)

    def test_launcher_runs_gui_and_adapter_and_stops_both(self):
        self.rows.append({'person_id': 'S0001', 'name': 'Server staff', 'role': 'Staff'})
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        self.gui_url = 'http://127.0.0.1:' + str(port)
        env = dict(os.environ, GUI_EVENT_PORT=str(port), GUI_EVENT_URL=self.gui_url,
                   TSAFE_PC1_URL=self.server_url, TSAFE_ADMIN_TOKEN='', QT_QPA_PLATFORM='offscreen')
        with tempfile.TemporaryFile(mode='w+') as output:
            process = subprocess.Popen([sys.executable, str(self.root / 'start_connected.py')],
                                       env=env, stdout=output, stderr=output)
            try:
                end = time.monotonic() + 10
                while time.monotonic() < end and process.poll() is None:
                    if any(p['id'] == 'S0001' for p in self.a.store.people()):
                        break
                    time.sleep(.05)
                self.assertIsNone(process.poll())
                self.assertEqual(self.a.store.people()[0]['id'], 'S0001')
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                process.wait(timeout=10)
            output.seek(0)
            log = output.read()
            self.assertIn('GUI + DB adapter running', log)
            self.assertEqual(process.returncode, 0, log)
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)

    def test_launcher_preflight_checks_real_http_contract(self):
        env = connection_env({'TSAFE_PC1_URL': self.server_url,
                              'GUI_EVENT_URL': self.gui_url, 'GUI_EVENT_PORT': '8767'})
        self.assertEqual(env['GUI_DB_MODE'], 'external')
        check_server(env)
        with self.assertRaises(ValueError):
            check_server(dict(env, GUI_EVENT_URL='http://127.0.0.1:1'))


if __name__ == '__main__':
    unittest.main()
