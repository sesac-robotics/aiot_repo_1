"""Isolated CLI contract tests: no production DB, models or live server are used."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools import send_face_json


def write_record(folder, person_id):
    path = Path(folder) / f'{person_id}.json'
    path.write_text(json.dumps({'patient_id': person_id, 'embedding': [1.0]}))
    return path


def person(person_id, role='Staff', active=True):
    return {'person_id': person_id, 'role': role, 'name': 'Test',
            'is_active': active, 'can_open': role != 'Patient'}


def test_registered_server_id_is_kept():
    with TemporaryDirectory() as folder:
        record = write_record(folder, 'S0001')
        with patch.object(send_face_json, 'request', side_effect=[(200, person('S0001')), (200, {'person_id': 'S0001'})]) as call:
            assert send_face_json.main([str(record), '--admin-token', 'admin', '--token', 'device']) == 0
        get, post = call.call_args_list
        assert get.args[1:3] == ('GET', '/api/v1/patients/S0001')
        assert get.kwargs['token'] == 'admin'
        assert post.args[1:3] == ('POST', '/api/v1/face/templates')
        assert post.args[3]['patient_id'] == 'S0001'
        assert post.args[4] == 'device'


def test_missing_id_blocks_entire_batch_before_upload():
    with TemporaryDirectory() as folder:
        write_record(folder, 'A0001')
        write_record(folder, 'wrong_id')
        with patch.object(send_face_json, 'request', side_effect=[(200, person('A0001')), (404, {'detail': 'missing'})]) as call:
            assert send_face_json.main([folder]) == 1
        assert all(c.args[1] == 'GET' for c in call.call_args_list)


def test_inactive_or_mismatched_person_is_not_uploaded():
    with TemporaryDirectory() as folder:
        record = write_record(folder, 'P0001')
        for reply in [person('P0001', 'Patient', False), person('S0001')]:
            with patch.object(send_face_json, 'request', return_value=(200, reply)) as call:
                assert send_face_json.main([str(record)]) == 1
                call.assert_called_once()


def test_authorization_failure_does_not_upload():
    with TemporaryDirectory() as folder:
        record = write_record(folder, 'S0001')
        with patch.object(send_face_json, 'request', return_value=(401, {'detail': 'Unauthorized'})) as call:
            assert send_face_json.main([str(record)]) == 1
            call.assert_called_once()


def test_identification_keeps_existing_timestamp_compatibility():
    with TemporaryDirectory() as folder:
        path = Path(folder) / 'event.json'
        path.write_text(json.dumps({'event_id': 'event1', 'timestamp': '2026-10-23T12:00:00'}))
        with patch.object(send_face_json, 'request', return_value=(200, {'result': 'UNKNOWN'})) as call:
            assert send_face_json.main([str(path)]) == 0
        assert call.call_args.args[2] == '/api/v1/face/identify'
        assert call.call_args.args[3]['timestamp'].endswith('+09:00')
