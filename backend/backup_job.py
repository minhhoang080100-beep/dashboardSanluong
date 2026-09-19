"""Pull a verified Railway SQLite online backup to an independently stored local copy."""
import argparse
import base64
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import uuid

try:
    from .state_backup import verify_restore
except ImportError:
    from state_backup import verify_restore


EXPORT_SCRIPT = '''import base64, hashlib, json, os, tempfile
from pathlib import Path
from state_backup import create_backup
source = Path(os.environ.get('DASHBOARD_STATE_PATH', '/data/control.sqlite3')).resolve(strict=True)
with tempfile.TemporaryDirectory(prefix='dashboard-backup-export-') as folder:
    backup = Path(folder) / 'copy.sqlite3'
    summary = create_backup(source, backup)
    content = backup.read_bytes()
    print('DASHBOARD_BACKUP_JSON=' + json.dumps({'content': base64.b64encode(content).decode('ascii'), 'file_sha256': hashlib.sha256(content).hexdigest(), 'content_sha256': summary['content_sha256']}))
'''


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, data):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _job_lock(directory):
    # OS locks are released on process exit/crash; an existing lock file is harmless.
    lock = directory / '.backup-job.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if lock.stat().st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _remote(config, script):
    encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
    command = [config['npx_path'], '--yes', '@railway/cli@5.57.9', 'ssh',
               '--project', config['project'], '--service', config['service'],
               '--environment', config['environment'], '--',
               f'printf %s {encoded} | base64 -d | python']
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                            timeout=240, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        # stdout may contain a backup or account information: never log it.
        raise RuntimeError('Railway backup transport failed')
    return result.stdout


def _publish_receipt(config, receipt, remote):
    encoded = base64.b64encode(json.dumps(receipt).encode('utf-8')).decode('ascii')
    script = f'''import base64, os, tempfile
from pathlib import Path
source = Path(os.environ.get('DASHBOARD_STATE_PATH', '/data/control.sqlite3')).resolve(strict=True)
stat = source.stat()
destination = source.with_name('backup-health.json')
fd, temporary = tempfile.mkstemp(prefix='.backup-health-', dir=source.parent)
try:
    with os.fdopen(fd, 'wb') as stream:
        stream.write(base64.b64decode('{encoded}'))
    if hasattr(os, 'chown'):
        os.chown(temporary, stat.st_uid, stat.st_gid)
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
finally:
    Path(temporary).unlink(missing_ok=True)
print('BACKUP_RECEIPT_OK')
'''
    if 'BACKUP_RECEIPT_OK' not in remote(config, script):
        raise RuntimeError('Backup receipt publication failed')


def _prune(directory, entries, keep):
    """Delete only this job's unmodified, manifest-owned direct children."""
    retained = entries[-keep:]
    for record in entries[:-keep]:
        name = record.get('file', '')
        if not re.fullmatch(r'control-auto-\d{8}T\d{12}Z-[a-f0-9]{8}\.sqlite3', name):
            retained.insert(0, record)
            continue
        path = directory / name
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            retained.insert(0, record)
            continue
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != record.get('file_sha256'):
                retained.insert(0, record)
                continue
            path.unlink()
    return retained


def run_backup(config, *, remote=_remote):
    directory = Path(config['destination']).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    keep = config.get('keep', 30)
    if type(keep) is not int or not 2 <= keep <= 365:
        raise ValueError('Backup retention must be between 2 and 365 files')
    with _job_lock(directory):
        receipt_file = directory / 'backup-health.json'
        try:
            receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            receipt = {}
        receipt.update(schema=1, enabled=config.get('schedule_enabled') is True,
                       last_attempt_at=_utcnow(), last_error=None)
        destination = None
        verified = False
        try:
            output = remote(config, EXPORT_SCRIPT)
            line = next((line for line in output.splitlines() if line.startswith('DASHBOARD_BACKUP_JSON=')), None)
            if not line or len(line) > 280_000_000:
                raise ValueError('Invalid backup export')
            payload = json.loads(line.split('=', 1)[1])
            content = base64.b64decode(payload['content'], validate=True)
            file_hash = hashlib.sha256(content).hexdigest()
            if file_hash != payload['file_sha256']:
                raise ValueError('Backup transfer checksum differs')
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            destination = directory / f'control-auto-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3'
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # The exported snapshot is closed and standalone. Normalize its journal
            # so restore checks never leave WAL/SHM sidecars in the retained archive.
            with closing(sqlite3.connect(destination)) as copy:
                copy.execute('PRAGMA journal_mode=DELETE')
            restored = verify_restore(destination)
            if restored['content_sha256'] != payload['content_sha256']:
                raise ValueError('Restored contents differ from source snapshot')
            verified = True
            file_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
            manifest = directory / 'managed-backups.json'
            entries = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
            if not isinstance(entries, list):
                raise ValueError('Invalid backup manifest')
            entries.append({'file': destination.name, 'file_sha256': file_hash, 'created_at': _utcnow()})
            # Commit ownership before pruning. A crash can leave extra copies, never remove the newest one.
            _write_json(manifest, entries)
            entries = _prune(directory, entries, keep)
            _write_json(manifest, entries)
            receipt.update(last_success_at=_utcnow(), restore_verified=True, offsite_verified=True)
            _write_json(receipt_file, receipt)
            _publish_receipt(config, receipt, remote)
            return {'ok': True, 'restore_verified': True, 'offsite_verified': True,
                    'retained_backups': len(entries), 'last_success_at': receipt['last_success_at']}
        except Exception:
            if destination is not None and not verified:
                destination.unlink(missing_ok=True)
            receipt.update(last_error='backup_job_failed')
            _write_json(receipt_file, receipt)
            try:
                _publish_receipt(config, receipt, remote)
            except Exception:
                pass
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        result = run_backup(config)
    except Exception:
        print(json.dumps({'ok': False, 'message': 'Backup failed; inspect scheduler and backup-health.json. No live database was replaced.'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
