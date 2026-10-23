"""Run the existing GUI and PC1 adapter together in external DB mode."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


def connection_env(source=None):
    env = dict(os.environ if source is None else source)
    env['GUI_DB_MODE'] = 'external'
    env.setdefault('TSAFE_PC1_URL', 'http://127.0.0.1:8101')
    env.setdefault('GUI_EVENT_HOST', '127.0.0.1')
    env.setdefault('GUI_EVENT_PORT', '8767')
    port = int(env['GUI_EVENT_PORT'])
    if not 1 <= port <= 65535:
        raise ValueError('GUI_EVENT_PORT must be 1..65535')
    env.setdefault('GUI_EVENT_URL', f'http://127.0.0.1:{port}')
    return env


def check_server(env):
    headers = {}
    if env.get('TSAFE_ADMIN_TOKEN'):
        headers['Authorization'] = 'Bearer ' + env['TSAFE_ADMIN_TOKEN']
    base = env['TSAFE_PC1_URL'].rstrip('/')
    with urlopen(Request(base + '/health', headers=headers), timeout=3) as response:
        health = json.load(response)
    if health.get('db') != 'ok':
        raise ValueError('PC1 DB is not ready; check the database terminal')
    gui = health.get('gui_events', {})
    expected = env['GUI_EVENT_URL'].rstrip('/') + '/events'
    if not gui.get('enabled') or gui.get('url') != expected:
        raise ValueError('PC1 GUI notifications must be enabled for ' + expected +
                         '; restart PC1 using test/start_pc1_gui.sh')
    with urlopen(Request(base + '/api/v1/patients?limit=1', headers=headers), timeout=3) as response:
        json.load(response)
    print('[READY] PC1 HTTP, DB and people API; GUI events: ' + expected, flush=True)


def wait_for_gui(process, env, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('GUI exited before its event receiver was ready')
        try:
            token = env.get('GUI_EVENT_TOKEN') or (ROOT / 'data/gui_event.token').read_text().strip()
            request = Request(env['GUI_EVENT_URL'].rstrip('/') + '/health',
                              headers={'Authorization': 'Bearer ' + token})
            with urlopen(request, timeout=.5) as response:
                if json.load(response).get('status') == 'ready':
                    return
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise RuntimeError('GUI event receiver did not start; check its port and token')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Only check server configuration; do not start GUI')
    args = parser.parse_args(argv)
    children = []
    try:
        env = connection_env()
        check_server(env)
        if args.check:
            return 0
        # Refuse to attach an adapter to a different GUI already using the same port.
        import socket
        with socket.socket() as probe:
            probe.bind((env['GUI_EVENT_HOST'], int(env['GUI_EVENT_PORT'])))
        gui = subprocess.Popen([sys.executable, str(ROOT / 'run.py')], cwd=ROOT, env=env)
        children.append(gui)
        wait_for_gui(gui, env)
        adapter = subprocess.Popen([sys.executable, str(ROOT / 'pc1_db_adapter.py')], cwd=ROOT, env=env)
        children.append(adapter)
        print('[READY] GUI + DB adapter running; server IDs are authoritative. Close GUI to stop both.', flush=True)
        while gui.poll() is None:
            if adapter.poll() is not None:
                raise RuntimeError('PC1 adapter exited; check the terminal error')
            time.sleep(.2)
        return gui.returncode
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print('[ERROR] ' + str(exc), file=sys.stderr)
        return 1
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    sys.exit(main())
