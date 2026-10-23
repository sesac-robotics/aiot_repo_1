"""Synchronize the GUI cache and pending registrations with the PC1 server."""
import json
import os
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from device_client import GUIClient


class PC1:
    def __init__(self, url=None, token=None):
        self.url = (url or os.getenv('TSAFE_PC1_URL', 'http://127.0.0.1:8101')).rstrip('/')
        self.token = os.getenv('TSAFE_ADMIN_TOKEN', '') if token is None else token

    def call(self, method, path, payload=None):
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        req = Request(self.url + path, method=method,
                      data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def patients(self):
        out = []
        offset = 0
        while True:
            rows = self.call('GET', f'/api/v1/patients?limit=500&offset={offset}')['items']
            out.extend(rows)
            if len(rows) < 500:
                return [self.convert(p) for p in out]
            offset += 500

    @staticmethod
    def convert(p):
        blood = (p.get('blood_type') or '').upper().replace(' ', '')
        rh = '-' if '-' in blood else '+' if '+' in blood else ''
        blood = blood.replace('RH', '').replace('+', '').replace('-', '')
        def text(key):
            return '' if p.get(key) is None else str(p[key])
        return {'id': p['person_id'], 'name': text('name'), 'role': p.get('role') or 'Patient',
                'sex': {'M': 'Male', 'F': 'Female'}.get(p.get('gender'), ''),
                'birth': text('birth_date')[:10], 'blood': blood, 'rh': rh,
                'height': text('height_cm'), 'weight': text('weight_kg'),
                'disease': text('medical_history'), 'medicine': text('medications'),
                'added': text('created_at')[:10]}

    def save(self, request):
        p = request['person']
        if p['role'] not in ['Patient', 'Staff', 'Admin']:
            raise ValueError('role must be Patient, Staff or Admin')
        fields = {'role': p['role'], 'name': p['name'],
                  'gender': {'Male': 'M', 'Female': 'F'}[p['sex']], 'birth_date': p['birth'],
                  'height_cm': float(p['height']), 'weight_kg': float(p['weight']),
                  'blood_type': 'Rh' + p['rh'] + ' ' + p['blood'],
                  'medical_history': p['disease'], 'medications': p['medicine']}
        if request.get('old_id'):
            return self.call('PATCH', '/api/v1/patients/' + quote(request['old_id'], safe=''), fields)
        return self.call('POST', '/api/v1/patients', fields)

    def delete(self, request):
        return self.call('DELETE', '/api/v1/patients/' + quote(request['person']['id'], safe=''))


class Adapter:
    def __init__(self, gui, pc1, cache_path=None):
        self.gui, self.pc1 = gui, pc1
        self.done = set()
        self.last_sync = None
        self.cache_path = Path(cache_path) if cache_path is not None else Path(__file__).parent / 'data/pc1_adapter_results.json'
        self.results = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}

    def step(self):
        requests = self.gui.requests()
        if self.last_sync is None or time.monotonic() - self.last_sync > 5:
            self.last_sync = time.monotonic()
            try:
                self.gui.send({'type': 'patients_sync', 'items': self.pc1.patients()})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                print('People sync failed: ' + self.error_message(exc), flush=True)
                # Still deliver save/delete failures so the GUI can offer a retry.
        for req in requests:
            if req['type'] not in ['db_save', 'db_delete'] or req['request_id'] in self.done:
                continue
            key = req['request_id']
            if key not in self.results:
                result = {'type': 'db_result' if req['type'] == 'db_save' else 'db_delete_result',
                          'request_id': key}
                try:
                    response = self.pc1.save(req) if req['type'] == 'db_save' else self.pc1.delete(req)
                    person_id = response.get('person_id')
                    if not isinstance(person_id, str) or not person_id.strip():
                        raise ValueError('PC1 response is missing person_id')
                    result.update(success=True, person_id=person_id)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    result.update(success=False, message=self.error_message(exc))
                self.results[key] = result
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.cache_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.results))
                temporary.replace(self.cache_path)
            # A GUI connection failure retries this cached result, not the server write.
            self.gui.send(self.results[key])
            self.done.add(key)
            self.last_sync = None

    @staticmethod
    def error_message(exc):
        if isinstance(exc, HTTPError):
            return 'PC1 HTTP ' + str(exc.code)
        if isinstance(exc, ValueError):
            return str(exc)
        return 'PC1 connection/response failed'


def run():
    adapter = None
    while True:
        try:
            if adapter is None:
                adapter = Adapter(GUIClient(), PC1())
            adapter.step()
        except (OSError, ValueError, KeyError, TypeError):
            print('Waiting for GUI / PC1 server. Check address, token and server state.', flush=True)
        time.sleep(.3)


if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        pass
