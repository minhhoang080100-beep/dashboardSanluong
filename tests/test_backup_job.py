"""Offsite backup failure, retention and health are isolated from production."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from backend.backup_health import backup_health
from backend.backup_job import EXPORT_SCRIPT, _job_lock, _prune, run_backup
from backend.control_store import ControlStore
from backend.state_backup import create_backup, verify_restore


@pytest.fixture
def transport(tmp_path):
    source = tmp_path / 'source.sqlite3'
    ControlStore(source)
    snapshot = tmp_path / 'snapshot.sqlite3'
    summary = create_backup(source, snapshot)
    content = snapshot.read_bytes()
    payload = {'content': base64.b64encode(content).decode(),
               'file_sha256': hashlib.sha256(content).hexdigest(),
               'content_sha256': summary['content_sha256']}
    calls = []

    def remote(config, script):
        calls.append(script)
        if script == EXPORT_SCRIPT:
            return 'DASHBOARD_BACKUP_JSON=' + json.dumps(payload)
        return 'BACKUP_RECEIPT_OK'
    return remote, payload, calls


def config(tmp_path):
    return {'destination': str(tmp_path / 'copies'), 'keep': 2, 'schedule_enabled': True}


def test_backup_verifies_offsite_restoration_and_prunes_only_owned_files(tmp_path, transport):
    remote, _, calls = transport
    settings = config(tmp_path)
    directory = Path(settings['destination'])
    directory.mkdir()
    manual = directory / 'manual.sqlite3'
    manual.write_bytes(b'untouched')
    for _ in range(3):
        result = run_backup(settings, remote=remote)
    assert result['ok'] and result['restore_verified'] and result['offsite_verified']
    assert result['retained_backups'] == 2
    assert len(list(directory.glob('control-auto-*.sqlite3'))) == 2
    assert manual.read_bytes() == b'untouched'
    for backup in directory.glob('control-auto-*.sqlite3'):
        assert verify_restore(backup)['restore_verified']
    assert not list(directory.glob('*-wal'))
    assert not list(directory.glob('*-shm'))
    assert backup_health(directory / 'control.sqlite3')['status'] == 'ok'
    assert len(calls) == 6


def test_bad_transport_does_not_change_previous_success_or_create_backup(tmp_path, transport):
    remote, payload, _ = transport
    settings = config(tmp_path)
    run_backup(settings, remote=remote)
    directory = Path(settings['destination'])
    before = backup_health(directory / 'control.sqlite3')['last_success_at']
    payload['file_sha256'] = 'bad checksum'
    with pytest.raises(ValueError):
        run_backup(settings, remote=remote)
    health = backup_health(directory / 'control.sqlite3')
    assert health['status'] == 'failed'
    assert health['last_success_at'] == before
    assert len(list(directory.glob('control-auto-*.sqlite3'))) == 1


def test_receipt_publication_failure_keeps_verified_copy(tmp_path, transport):
    remote, _, _ = transport
    def failing(config, script):
        if script == EXPORT_SCRIPT:
            return remote(config, script)
        raise RuntimeError('offline')
    settings = config(tmp_path)
    with pytest.raises(RuntimeError):
        run_backup(settings, remote=failing)
    directory = Path(settings['destination'])
    assert len(list(directory.glob('control-auto-*.sqlite3'))) == 1
    health = backup_health(directory / 'control.sqlite3')
    assert health['status'] == 'failed' and health['restore_verified']


def test_invalid_database_is_not_retained_or_reported_success(tmp_path, transport):
    remote, payload, _ = transport
    payload['content'] = base64.b64encode(b'not sqlite').decode()
    payload['file_sha256'] = hashlib.sha256(b'not sqlite').hexdigest()
    settings = config(tmp_path)
    with pytest.raises(Exception):
        run_backup(settings, remote=remote)
    directory = Path(settings['destination'])
    assert not list(directory.glob('control-auto-*.sqlite3'))
    assert backup_health(directory / 'control.sqlite3')['status'] == 'failed'


def test_retention_preserves_changed_and_outside_files(tmp_path):
    directory = tmp_path / 'copies'
    directory.mkdir()
    outside = tmp_path / 'outside.sqlite3'
    outside.write_bytes(b'never delete')
    altered = directory / 'control-auto-20260918T010101000000Z-0123abcd.sqlite3'
    altered.write_bytes(b'changed')
    records = [{'file': '../outside.sqlite3', 'file_sha256': hashlib.sha256(outside.read_bytes()).hexdigest()},
               {'file': altered.name, 'file_sha256': 'old hash'}, {'file': 'last1'}, {'file': 'last2'}]
    assert len(_prune(directory, records, 2)) == 4
    assert outside.exists() and altered.exists()


def test_lock_rejects_parallel_job_and_releases_after_error(tmp_path):
    with _job_lock(tmp_path):
        with pytest.raises(OSError):
            with _job_lock(tmp_path):
                pytest.fail('Concurrent lock unexpectedly acquired')
    with _job_lock(tmp_path):
        pass


def test_backup_health_missing_stale_invalid_and_no_paths(tmp_path):
    source = tmp_path / 'control.sqlite3'
    assert backup_health(source)['status'] == 'not_configured'
    now = datetime.now(timezone.utc)
    receipt = {'schema': 1, 'enabled': True, 'last_attempt_at': now.isoformat(),
               'last_success_at': (now - timedelta(hours=37)).isoformat(),
               'restore_verified': True, 'offsite_verified': True, 'destination': '/private/path'}
    path = tmp_path / 'backup-health.json'
    path.write_text(json.dumps(receipt), encoding='utf-8')
    health = backup_health(source, now=now)
    assert health['status'] == 'stale'
    assert 'destination' not in health
    assert '/private/path' not in json.dumps(health)
    path.write_text('{broken', encoding='utf-8')
    assert backup_health(source, now=now)['status'] == 'failed'
    receipt['last_success_at'] = (now + timedelta(days=1)).isoformat()
    path.write_text(json.dumps(receipt), encoding='utf-8')
    assert backup_health(source, now=now)['status'] == 'failed'


def test_export_uses_online_backup_and_no_production_db_write():
    assert 'create_backup(source, backup)' in EXPORT_SCRIPT
    assert 'TemporaryDirectory' in EXPORT_SCRIPT
    assert 'ControlStore' not in EXPORT_SCRIPT
