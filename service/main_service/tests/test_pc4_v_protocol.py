"""No real DB/PC4: V pulse, command status, and temporary TCP feedback tests."""
from contextlib import contextmanager
from dataclasses import replace
import socket
import threading
import time
from unittest.mock import Mock
import pytest
from app.services import door_control
from app.services.door_control import DoorController
from app.services.gui_notify import to_gui_event
from app.services.pc4_link import PC4Link, parse_feedback

class MemoryDB:
    def __init__(self): self.commands = {}
    @contextmanager
    def connect(self): yield self
    def execute(self, query, args):
        sql = ' '.join(query.split())
        if sql.startswith('SELECT 1 FROM hcc.doors'): row = {'exists': 1}
        elif sql.startswith('INSERT INTO hcc.door_commands'):
            rid = len(self.commands) + 1
            row = {'request_id': rid, 'command_id': 'command-' + str(rid),
                   'door_id': args[0], 'ack_status': 'PENDING'}
            self.commands[rid] = row
        elif sql.startswith('UPDATE hcc.door_commands'):
            ack, state, error, rid = args
            row = self.commands[rid]
            row.update(ack_status=ack, reported_state=state, error_code=error)
        else: raise AssertionError(sql)
        return Mock(fetchone=lambda: row.copy())

@pytest.fixture
def storage(monkeypatch):
    db = MemoryDB()
    monkeypatch.setattr(door_control, 'get_conn', db.connect)
    monkeypatch.setattr(door_control, 'publish', Mock())
    monkeypatch.setattr(door_control, 'record_state', Mock())
    monkeypatch.setattr(door_control, 'settings', replace(door_control.settings, gui_door_open_on_send=False))
    return db

def test_demo_successful_send_displays_open_without_fake_device_report(storage, monkeypatch):
    monkeypatch.setattr(door_control, 'settings', replace(door_control.settings, gui_door_open_on_send=True))
    link = Mock(spec=PC4Link); link.send_open_pulse.return_value = True
    controller = DoorController(link, protocol='v')
    assert controller.open('door_01', 'GUI')['status'] == 'PENDING'
    door_control.publish.assert_any_call('door_state', {
        'door_id': 'door_01', 'state': 'OPEN', 'source': 'DEMO_COMMAND_SENT'})
    assert storage.commands[1]['ack_status'] == 'PENDING'
    door_control.record_state.assert_not_called()
    controller.on_feedback(parse_feedback('DOOR,0'))
    door_control.record_state.assert_called_with('door_01', 'CLOSED', 'pc4', force=False)

def test_demo_failed_send_does_not_display_open(storage, monkeypatch):
    monkeypatch.setattr(door_control, 'settings', replace(door_control.settings, gui_door_open_on_send=True))
    link = Mock(spec=PC4Link); link.send_open_pulse.return_value = False
    assert DoorController(link, protocol='v').open('door_01', 'GUI')['status'] == 'ERROR'
    assert all(call.args[0] != 'door_state' for call in door_control.publish.call_args_list)

def test_normal_mode_waits_for_feedback(storage):
    link = Mock(spec=PC4Link); link.send_open_pulse.return_value = True
    DoorController(link, protocol='v').open('door_01', 'GUI')
    assert all(call.args[0] != 'door_state' for call in door_control.publish.call_args_list)

def test_one_rising_edge_preserves_tracking_and_rearms():
    link = PC4Link('test.invalid', 8765); link._sock = Mock()
    assert link.send('TRACK,1,1,320,200,480,280,640,480\n')
    assert link.send_open_pulse()
    lines = link._sock.sendall.call_args.args[0].decode().splitlines()
    assert lines == ['V,1,1,167,0,0', 'V,1,1,167,0,1', 'V,1,1,167,0,0']
    previous, openings = False, 0
    for line in lines * 2:
        pulse = line.split(',')[-1] == '1'
        openings += int(pulse and not previous); previous = pulse
    assert openings == 2

def test_stale_tracking_is_not_replayed():
    link = PC4Link('test.invalid', 8765); link._sock = Mock()
    link._last_tracking = (time.monotonic() - 2, (1, 1, 999, -999))
    assert link.send_open_pulse()
    assert link._sock.sendall.call_args.args[0] == b'V,0,0,0,0,0\nV,0,0,0,0,1\nV,0,0,0,0,0\n'

def test_failed_pulse_is_not_queued():
    link = PC4Link('test.invalid', 8765); link._sock = Mock()
    link._sock.sendall.side_effect = OSError('broken')
    assert not link.send_open_pulse() and link._sock is None
    link._sock = Mock()
    assert link.send('TRACK,0,0,-1,-1,-1,-1,640,480\n')
    assert b'V,' not in link._sock.sendall.call_args.args[0]

def test_actual_feedback_required_and_requests_serialized(storage):
    link = Mock(spec=PC4Link); link.send_open_pulse.return_value = True
    controller = DoorController(link, protocol='v')
    assert controller.open('door_01', 'GUI')['status'] == 'PENDING'
    door_control.record_state.assert_not_called()
    assert controller.open('door_01', 'GUI')['reason'] == 'awaiting_pc4_state'
    assert len(storage.commands) == 1
    controller.on_feedback(parse_feedback('DOOR,1'))
    assert storage.commands[1]['ack_status'] == 'ACCEPTED'
    assert controller.status()['pending'] == 0
    door_control.record_state.assert_called_with('door_01', 'OPEN', 'pc4', force=True)
    controller.on_feedback(parse_feedback('DOOR,0'))
    door_control.record_state.assert_called_with('door_01', 'CLOSED', 'pc4', force=False)

def test_immediate_reply_does_not_leave_pending_request(storage):
    link = Mock(spec=PC4Link); controller = DoorController(link, protocol='v')
    def reply():
        controller.on_feedback(parse_feedback('DOOR,1')); return True
    link.send_open_pulse.side_effect = reply
    controller.open('door_01', 'GUI')
    assert controller.status()['pending'] == 0
    assert storage.commands[1]['ack_status'] == 'ACCEPTED'

def test_failed_send_does_not_show_open(storage):
    link = Mock(spec=PC4Link); link.send_open_pulse.return_value = False
    controller = DoorController(link, protocol='v')
    assert controller.open('door_01', 'GUI')['status'] == 'ERROR'
    assert storage.commands[1]['error_code'] == 'PC4_UNAVAILABLE'
    assert controller.status()['pending'] == 0
    door_control.record_state.assert_not_called()

def test_original_protocol_still_works(storage):
    link = Mock(spec=PC4Link); link.send.return_value = True
    controller = DoorController(link, protocol='door')
    controller.open('door_01', 'GUI')
    link.send.assert_called_once_with('DOOR,OPEN,1\n')
    link.send_open_pulse.assert_not_called()
    controller.on_feedback({'type': 'EVENT', 'request_id': 1, 'result': 'OPEN_COMPLETED'})
    assert storage.commands[1]['ack_status'] == 'ACCEPTED'

def test_existing_gui_mapping():
    for raw, state in [('DOOR,1', 'OPEN'), ('DOOR,0', 'CLOSED')]:
        event = to_gui_event('door_state', {'state': parse_feedback(raw)['state']})
        assert event['type'] == 'door' and event['state'] == state

def test_v_open_and_close_over_real_temporary_tcp(storage):
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(3)
    received, errors = [], []; opened = threading.Event(); allow_close = threading.Event()
    def pc4():
        try:
            conn, _ = listener.accept()
            with conn, conn.makefile('rb') as stream:
                conn.settimeout(3); previous = False
                for raw in stream:
                    line = raw.decode().strip(); received.append(line)
                    if line.startswith('TRACK,'): continue
                    parts = line.split(','); assert len(parts) == 6 and parts[0] == 'V'
                    body, face, dx, dy, pulse = map(int, parts[1:])
                    assert body in (0, 1) and face in (0, 1) and pulse in (0, 1)
                    assert -1000 <= dx <= 1000 and -1000 <= dy <= 1000
                    if pulse == 1 and not previous:
                        conn.sendall(b'DOO'); conn.sendall(b'R,1\r\n'); opened.set()
                    previous = bool(pulse)
                    if opened.is_set() and not pulse:
                        assert allow_close.wait(3); conn.sendall(b'DOOR,0\r\n'); break
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=pc4, daemon=True); thread.start()
    link = PC4Link(*listener.getsockname()); controller = DoorController(link, protocol='v'); link.start()
    def wait(condition):
        end = time.monotonic() + 3
        while not condition() and time.monotonic() < end: time.sleep(.005)
        assert condition(), errors
    try:
        assert link.send('TRACK,1,1,320,200,480,280,640,480\n')
        controller.open('door_01', 'GUI')
        wait(lambda: storage.commands[1]['ack_status'] == 'ACCEPTED')
        door_control.record_state.assert_called_with('door_01', 'OPEN', 'pc4', force=True)
        allow_close.set(); wait(lambda: door_control.record_state.call_count == 2)
        door_control.record_state.assert_called_with('door_01', 'CLOSED', 'pc4', force=False)
        assert link.received == 2
        assert any(line.startswith('V,') and line.endswith(',1') for line in received)
    finally:
        allow_close.set(); link.close(); listener.close(); thread.join(timeout=3)
    assert not errors
