"""PC2: watch captured images, extract buffalo_l face embeddings, queue/send to PC1.

Requires existing common.py (get_model, load_image, single_face, unit).
No patient ID matching or patient database in this service.
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from common import get_model, load_image, single_face, unit

DATA = Path('/app/data')
CAPTURE_DIR = DATA / 'captures'
PROCESSED_DIR = DATA / 'processed'
RESULT_DIR = DATA / 'output'
OUTBOX_DIR = DATA / 'outbox'
EXTENSIONS = {'.jpg', '.jpeg', '.png'}
PC1_URL = os.getenv('PC1_EMBEDDING_URL', '').strip()
PC1_TOKEN = os.getenv('PC1_EMBEDDING_TOKEN', '').strip()
DEVICE_ID = os.getenv('DEVICE_ID', 'PC2')
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def write_json_atomic(path, data):
    """Avoid partially written JSON files."""
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def extract(model, path):
    """Use SCRFD detection/alignment and recognition embedding, no ID matching."""
    start = time.perf_counter()
    image = load_image(path)
    face, status = single_face(model, image)
    result = {'status': status, 'model': 'buffalo_l', 'embedding_dim': None, 'embedding': None}
    if status == 'OK':
        embedding = unit(face.normed_embedding)
        result['embedding_dim'] = int(len(embedding))
        result['embedding'] = embedding.tolist()
    result['elapsed_ms'] = round((time.perf_counter() - start) * 1000, 2)
    return result


def send_pending():
    """Retry queued results; PC1 must deduplicate with event_id."""
    if not PC1_URL:
        return
    for path in sorted(OUTBOX_DIR.glob('*.json')):
        try:
            payload = path.read_bytes()
            headers = {'Content-Type': 'application/json'}
            if PC1_TOKEN:
                headers['Authorization'] = f'Bearer {PC1_TOKEN}'
            request = Request(PC1_URL, data=payload, headers=headers, method='POST')
            with urlopen(request, timeout=5) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f'HTTP {response.status}')
            path.unlink()
            print('[PC1 SENT]', path.stem, flush=True)
        except Exception as exc:
            print('[PC1 WAIT]', str(exc), flush=True)
            break


def run():
    for directory in (CAPTURE_DIR, PROCESSED_DIR, RESULT_DIR, OUTBOX_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    print('[START] Loading buffalo_l...', flush=True)
    model = get_model()  # load only once
    print(f'[WATCH] {CAPTURE_DIR}', flush=True)
    print('[PC1] ' + (PC1_URL if PC1_URL else 'not configured; save locally only'), flush=True)
    last_retry = 0.0
    previous_states = {}
    while True:
        try:
            files = sorted(p for p in CAPTURE_DIR.iterdir()
                           if p.is_file() and p.suffix.lower() in EXTENSIONS)
            present = set(files)
            for stale in set(previous_states) - present:
                previous_states.pop(stale, None)
            for path in files:
                try:
                    stat = path.stat()
                    if stat.st_size <= 0 or stat.st_size > MAX_IMAGE_BYTES:
                        print('[SKIP] Invalid size:', path.name, flush=True)
                        continue
                    signature = (stat.st_size, stat.st_mtime_ns)
                    if previous_states.get(path) != signature:
                        previous_states[path] = signature
                        continue  # wait for a second identical observation
                    if time.time_ns() - stat.st_mtime_ns < 1_000_000_000:
                        continue
                    event_id = uuid.uuid4().hex
                    try:
                        result = extract(model, path)
                    except Exception as exc:
                        result = {'status': 'Error', 'model': 'buffalo_l',
                                  'embedding_dim': None, 'embedding': None,
                                  'error': str(exc)}
                    event = {'event_id': event_id, 'device_id': DEVICE_ID,
                             'timestamp': datetime.now(timezone.utc).isoformat(),
                             'image_name': path.name, **result}
                    write_json_atomic(RESULT_DIR / f'{event_id}.json', event)
                    if PC1_URL:
                        write_json_atomic(OUTBOX_DIR / f'{event_id}.json', event)
                    dest = PROCESSED_DIR / f'{event_id}_{path.name}'
                    os.replace(path, dest)
                    previous_states.pop(path, None)
                    print('[EXTRACT]', path.name, result['status'],
                          'dim:', result.get('embedding_dim'),
                          'ms:', result.get('elapsed_ms'), flush=True)
                except FileNotFoundError:
                    previous_states.pop(path, None)
                except Exception as exc:
                    print('[PROCESS ERROR]', path.name, repr(exc), flush=True)
            if time.monotonic() - last_retry >= 5:
                send_pending()
                last_retry = time.monotonic()
        except Exception as exc:
            print('[WATCH ERROR]', repr(exc), flush=True)
        time.sleep(0.5)


if __name__ == '__main__':
    run()